"""
Phase 5a: split portal IDs shared by two different works across Houses.

BLUEPRINT.md §2 states WORK_RECOMMENDATION_DTL_ID is unique across both
Houses. In Snapshot A it is not: 136 IDs appear in the LS recommended file
AND in the RS sanctioned / completed / expenditure files for a different
MP, district and description. Phase 1 keyed `work` by the bare ID, so each
pair became ONE work (LS MP, district and description; RS sanction,
completion and payments).

This step re-keys each pair as two works under the compound identity
(portal_id, house) -- see app/models/work.py -- without re-ingesting:

  LS half  keeps work_key = portal_id. Attributes come from its LS
           recommended-file row only; its work_state keeps the
           recommendation and drops the RS sanction/completion
           (lifecycle -> 'recommended', exactly what Phase 2's
           membership rule gives a recommended-only work).
  RS half  new work_key "<portal_id>-RS", house RS. Attributes come from
           the RS files, first non-null in Phase 2's lifecycle order
           (sanctioned -> completed -> expenditure); it takes the
           sanction/completion values and every payment row.

No other Phase 1/2 logic is changed; this runs after run_normalize.py.
Idempotent: an ID already recorded in work_identity_split is skipped.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from ..models.provenance import SourceSnapshot
from ..models.reference import ActivityType, DistrictAuthority
from ..models.work import Work, WorkIdentitySplit
from ..models.work_related import Payment, WorkState
from .activity_parse import parse_activity_type
from .constants import CORE_FILES
from .normalize import normalize_description

RS_SUFFIX = "-RS"
_RS_ORDER = ("works_sanctioned", "works_completed", "expenditure")

_KEYS_SQL = text(
    """
    SELECT rf.id AS raw_file_id, rf.relative_path, rr.row_no, rr.data,
           TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') AS portal_id
    FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id
    WHERE rf.source_snapshot_id = :snap
      AND TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') = ANY(:ids)
    """
)


def _filename_specs() -> dict[str, object]:
    return {f.relative_path: f for f in CORE_FILES if f.house_mode == "filename" and f.key_col}


def shared_portal_ids(session: Session, snapshot_id: int) -> pd.DataFrame:
    """Portal IDs used by BOTH Houses' core files in one snapshot. House per
    file comes from the Phase 1 file registry, never from filename text.
    Returns one row per ID with the houses and files it appears in."""
    specs = _filename_specs()
    df = pd.read_sql(
        text(
            "SELECT DISTINCT rf.relative_path, TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') AS portal_id"
            " FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id"
            " WHERE rf.source_snapshot_id = :snap AND rr.data ? 'WORK_RECOMMENDATION_DTL_ID'"
        ),
        session.connection(),
        params={"snap": snapshot_id},
    )
    df["house"] = df["relative_path"].map(lambda p: specs[p].house_value if p in specs else None)
    df = df.dropna(subset=["house"])
    g = df.groupby("portal_id")
    out = pd.DataFrame(
        {
            "houses": g["house"].agg(lambda x: sorted(set(x))),
            "files": g["relative_path"].agg(lambda x: sorted({p.rsplit("/", 1)[-1] for p in x})),
        }
    )
    return out[out["houses"].map(len) > 1]


def _first_non_null(rows: list[tuple[object, dict]], field_of) -> object:
    for spec, data in rows:
        col = field_of(spec)
        if not col:
            continue
        v = data.get(col)
        if v is not None and str(v).strip() not in ("", "NA"):
            return v
    return None


def _attributes(rows: list[tuple[object, dict]], type_ids: dict, ida_ids: dict) -> dict:
    mp = _first_non_null(rows, lambda s: s.mp_name_col)
    activity = _first_non_null(rows, lambda s: s.activity_col)
    desc = _first_non_null(rows, lambda s: s.description_col)
    ida = _first_non_null(rows, lambda s: "IDA_NAME")
    parsed = parse_activity_type(activity) if activity is not None else None
    return {
        "raw_mp_name": mp,
        "raw_activity_name": activity,
        "raw_description": desc,
        "description_normalized": normalize_description(desc),
        "activity_type_id": type_ids.get(parsed),
        "district_authority_id": ida_ids.get(ida),
        "_ida_name": ida,
        "_activity_type": parsed,
    }


def split_shared_portal_ids(session: Session, snapshot_a: SourceSnapshot) -> dict:
    shared = shared_portal_ids(session, snapshot_a.id)
    done = set(session.execute(select(WorkIdentitySplit.portal_id)).scalars())
    todo = [p for p in shared.index if p not in done]
    if not todo:
        return {"shared_portal_ids": len(shared), "split_now": 0, "already_split": len(done)}

    specs = _filename_specs()
    raw = pd.read_sql(_KEYS_SQL, session.connection(), params={"snap": snapshot_a.id, "ids": todo})
    raw = raw[raw["relative_path"].isin(specs)]
    type_ids = {t.label: t.id for t in session.execute(select(ActivityType)).scalars() if t.label}
    ida_ids = {d.ida_name: d.id for d in session.execute(select(DistrictAuthority)).scalars()}

    n_payments = 0
    for portal_id in sorted(todo, key=int):
        rows = raw[raw["portal_id"] == portal_id]
        by_house: dict[str, list] = {"LS": [], "RS": []}
        rs_first_file = None
        for cat in ("works_recommended",) + _RS_ORDER:
            for r in rows.sort_values(["raw_file_id", "row_no"]).itertuples(index=False):
                spec = specs[r.relative_path]
                if spec.category != cat:
                    continue
                by_house[spec.house_value].append((spec, r.data))
                if spec.house_value == "RS" and rs_first_file is None:
                    rs_first_file = r.raw_file_id
        ls_rows = [(s, d) for s, d in by_house["LS"] if s.category == "works_recommended"]
        other_ls = [s.relative_path for s, _ in by_house["LS"] if s.category != "works_recommended"]
        if len(ls_rows) != 1 or other_ls or not by_house["RS"]:
            raise ValueError(
                f"portal_id {portal_id}: expected exactly one LS recommended row and RS rows only elsewhere;"
                f" got LS rec={len(ls_rows)}, other LS files={other_ls}, RS rows={len(by_house['RS'])}"
            )

        ls_attr = _attributes(ls_rows, type_ids, ida_ids)
        rs_attr = _attributes(by_house["RS"], type_ids, ida_ids)
        rs_key = f"{portal_id}{RS_SUFFIX}"
        ls_work = session.get(Work, portal_id)
        before_ls = {
            k: getattr(ls_work, k)
            for k in ("raw_mp_name", "district_authority_id", "activity_type_id", "description_normalized")
        }

        for k, v in ls_attr.items():
            if not k.startswith("_"):
                setattr(ls_work, k, v)
        session.add(
            Work(
                work_key=rs_key,
                house="RS",
                house_source="filename",
                first_seen_snapshot_id=snapshot_a.id,
                first_seen_raw_file_id=rs_first_file,
                **{k: v for k, v in rs_attr.items() if not k.startswith("_")},
            )
        )
        session.flush()

        ws = session.execute(
            select(WorkState).where(
                WorkState.work_key == portal_id, WorkState.source_snapshot_id == snapshot_a.id
            )
        ).scalar_one()
        moved = {
            c: getattr(ws, c)
            for c in (
                "lifecycle_status",
                "sanction_amount",
                "sanction_date",
                "actual_amount",
                "actual_end_date",
                "raw_stage",
                "raw_file_status",
                "raw_flag",
            )
        }
        session.add(
            WorkState(
                work_key=rs_key,
                source_snapshot_id=snapshot_a.id,
                **moved,
                recommended_amount=None,
                recommended_date=None,
            )
        )
        ws.lifecycle_status = "recommended"
        for c in (
            "sanction_amount",
            "sanction_date",
            "actual_amount",
            "actual_end_date",
            "raw_stage",
            "raw_file_status",
            "raw_flag",
        ):
            setattr(ws, c, None)

        res = session.execute(
            update(Payment)
            .where(Payment.work_key == portal_id, Payment.source_snapshot_id == snapshot_a.id)
            .values(work_key=rs_key)
        )
        n_payments += res.rowcount or 0

        session.add(
            WorkIdentitySplit(
                portal_id=portal_id,
                ls_work_key=portal_id,
                rs_work_key=rs_key,
                evidence={
                    "files": shared.at[portal_id, "files"],
                    "ls": {
                        "mp": ls_attr["raw_mp_name"],
                        "ida": ls_attr["_ida_name"],
                        "activity_type": ls_attr["_activity_type"],
                    },
                    "rs": {
                        "mp": rs_attr["raw_mp_name"],
                        "ida": rs_attr["_ida_name"],
                        "activity_type": rs_attr["_activity_type"],
                    },
                    "ls_fields_changed": sorted(k for k, v in before_ls.items() if v != getattr(ls_work, k)),
                    "moved_lifecycle_status": moved["lifecycle_status"],
                },
            )
        )
        session.flush()

    return {
        "shared_portal_ids": len(shared),
        "split_now": len(todo),
        "already_split": len(done),
        "payments_moved": n_payments,
    }
