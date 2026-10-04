"""
P3 Normalise (work backfill): parses ACTIVITY_NAME/IDA_NAME across the 7
Snapshot A lifecycle files (recommended, sanctioned x2, completed x2,
expenditure x2), and backfills `work` with activity_type_id,
district_authority_id, description_normalized, and tenure_id.

Uses "first non-null wins" per column per work_key, scanning files in the
same lifecycle order Phase 1 used to build `work` itself (recommended ->
sanctioned -> completed -> expenditure), so a work's canonical
activity/description matches whichever file first described it.

Deliberately reads the source CSVs again rather than raw_row's JSONB --
Phase 1's `.iterrows()` mistake showed row-by-row Postgres round-trips
don't scale on 100k+-row files; the CSVs are the same immutable content
raw_row already has, just faster to work with as a DataFrame.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from ..models.reference import DistrictAuthority
from ..models.work import Work
from .activity_parse import parse_activity_type_series
from .constants import CORE_FILES
from .csv_utils import read_csv_body_and_footer
from .db_utils import to_records_with_nulls
from .normalize import normalize_description

# Lifecycle order matches how Phase 1 populated `work` (first-seen wins).
_ORDER = [
    "works_recommended",
    "works_sanctioned",
    "works_sanctioned",
    "works_completed",
    "works_completed",
    "expenditure",
    "expenditure",
]


def _core_specs_in_order():
    by_category: dict[str, list] = {}
    for spec in CORE_FILES:
        if spec.category in ("mp_allocation",):
            continue
        by_category.setdefault(spec.category, []).append(spec)
    ordered = []
    seen_per_cat = {}
    for cat in _ORDER:
        idx = seen_per_cat.get(cat, 0)
        ordered.append(by_category[cat][idx])
        seen_per_cat[cat] = idx + 1
    return ordered


def collect_work_attributes(data_dir: Path) -> pd.DataFrame:
    """Returns a DataFrame indexed by work_key with columns
    activity_name_raw, ida_name, description_raw -- first non-null value
    seen, in lifecycle order."""
    frames = []
    for spec in _core_specs_in_order():
        path = data_dir / spec.relative_path
        body_df, _ = read_csv_body_and_footer(path, spec.delimiter, spec.footer_col)
        cols = {spec.key_col: "work_key"}
        if spec.activity_col:
            cols[spec.activity_col] = "activity_name_raw"
        if "IDA_NAME" in body_df.columns:
            cols["IDA_NAME"] = "ida_name"
        if spec.description_col:
            cols[spec.description_col] = "description_raw"
        subset = body_df[list(cols.keys())].rename(columns=cols)
        frames.append(subset)

    combined = pd.concat(frames, ignore_index=True)
    combined["work_key"] = combined["work_key"].astype(str).str.strip()

    result = combined[["work_key"]].drop_duplicates(subset="work_key").set_index("work_key")
    for col in ("activity_name_raw", "ida_name", "description_raw"):
        if col not in combined.columns:
            continue
        non_null = combined.dropna(subset=[col])
        non_null = non_null[non_null[col].astype(str).str.strip() != ""]
        first_per_key = non_null.drop_duplicates(subset="work_key", keep="first").set_index("work_key")[col]
        result[col] = first_per_key

    return result


def backfill_work(session: Session, data_dir: Path) -> dict:
    attrs = collect_work_attributes(data_dir)
    attrs["activity_type_parsed"] = None
    mask = attrs["activity_name_raw"].notna()
    attrs.loc[mask, "activity_type_parsed"] = parse_activity_type_series(attrs.loc[mask, "activity_name_raw"])
    attrs["description_normalized"] = attrs["description_raw"].map(normalize_description)

    # -- activity_type: upsert the distinct parsed types --
    distinct_types = sorted(attrs["activity_type_parsed"].dropna().unique().tolist())
    from ..models.reference import ActivityType

    existing_types = {t.label: t.id for t in session.execute(select(ActivityType)).scalars() if t.label}
    for label in distinct_types:
        if label in existing_types:
            continue
        at = ActivityType(code=None, label=label)
        session.add(at)
        session.flush()
        existing_types[label] = at.id

    # -- district_authority lookup (populated in Phase 1) --
    ida_to_district_id = {d.ida_name: d.id for d in session.execute(select(DistrictAuthority)).scalars()}

    attrs["activity_type_id"] = attrs["activity_type_parsed"].map(existing_types)
    attrs["district_authority_id"] = attrs["ida_name"].map(ida_to_district_id)

    # Vectorized record-building (never iterrows() on a 100k+-row frame --
    # see [[project-mplads-phase1-details]]): nullable-int columns need
    # Python None, not NaN, for the bulk UPDATE bind params.
    out = attrs.reset_index().rename(columns={"work_key": "wk"})
    out["activity_type_id"] = out["activity_type_id"].astype("Int64")
    out["district_authority_id"] = out["district_authority_id"].astype("Int64")
    out = out[["wk", "activity_type_id", "district_authority_id", "description_normalized"]]
    update_records = to_records_with_nulls(out)

    # Core-level Table.update(), not update(Work) -- SQLAlchemy 2.0's ORM
    # "bulk update by primary key" shortcut insists params be keyed by the
    # exact PK column name and rejects a custom WHERE bindparam name; the
    # Core table update is a plain parameterized UPDATE (executemany),
    # exactly what a chunked bulk backfill needs.
    stmt = (
        update(Work.__table__)
        .where(Work.__table__.c.work_key == bindparam("wk"))
        .values(
            activity_type_id=bindparam("activity_type_id"),
            district_authority_id=bindparam("district_authority_id"),
            description_normalized=bindparam("description_normalized"),
        )
    )
    CHUNK = 5000
    for i in range(0, len(update_records), CHUNK):
        session.execute(stmt, update_records[i : i + CHUNK])

    return {
        "work_keys_processed": len(attrs),
        "distinct_activity_types": len(distinct_types),
        "activity_type_parse_rate_pct": round(100 * attrs["activity_type_parsed"].notna().mean(), 4),
        "district_key_parse_rate_pct": round(100 * attrs["ida_name"].notna().mean(), 4),
    }
