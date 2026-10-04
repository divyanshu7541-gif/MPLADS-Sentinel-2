"""
P5 Entity resolution: payee dedup by portal VENDOR_ID, agency typing.

Verified against Snapshot A (2026-09-24 research, both expenditure files
combined): 29,583 distinct VENDOR_ID, 27,961 distinct VENDOR_NAME, exactly
1,045 names shared by more than one ID (BLUEPRINT.md §2, exact match),
and ZERO IDs with more than one name ("no ID has two spellings",
BLUEPRINT.md §2, confirmed exactly). Payee identity is therefore always
VENDOR_ID; VENDOR_NAME is never used to merge two IDs, and every distinct
name seen for an ID is recorded in payee_alias, not collapsed into
canonical_name.

Typing is a light, documented rule-based pass (keyword match against
government/statutory-sounding tokens), not exhaustive classification --
BLUEPRINT.md §8 calls for "every payee carries a type and review status
before any concentration metric is shown"; review_status stays
'unreviewed' for a human to confirm later, per the same section.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.entities import ImplementingAgency, Payee, PayeeAlias
from ..models.provenance import SourceSnapshot
from .constants import CORE_FILES
from .csv_utils import read_csv_body_and_footer
from .db_utils import bulk_insert

_GOVT_TOKENS = re.compile(
    r"\b(COLLECTOR|COMMISSIONER|MUNICIPAL|CORPORATION|DISTRICT|GOVT|GOVERNMENT|PANCHAYAT|"
    r"BOARD|SOCIETY|TRUST|DEPARTMENT|DIRECTOR|EXECUTIVE ENGINEER|ZILA|GRAM|BLOCK|NAGAR|"
    r"PARISHAD|SAMITI|OFFICER|AUTHORITY|COUNCIL|PWD|IDA\b)",
    re.IGNORECASE,
)
_INDIVIDUAL_TOKENS = re.compile(r"^(SHRI|SMT|MS|MR|DR|KUM|KM)\.?\s", re.IGNORECASE)


def classify_payee_type(name: str) -> str:
    if _GOVT_TOKENS.search(name):
        return "statutory_or_government"
    if _INDIVIDUAL_TOKENS.match(name.strip()):
        return "individual"
    return "private_firm"


def classify_agency_type(name: str) -> str:
    if _GOVT_TOKENS.search(name):
        return "statutory_or_government"
    return "unclassified"


def _expenditure_frames(data_dir: Path) -> pd.DataFrame:
    frames = []
    for spec in CORE_FILES:
        if spec.category != "expenditure":
            continue
        body_df, _ = read_csv_body_and_footer(data_dir / spec.relative_path, spec.delimiter, spec.footer_col)
        frames.append(body_df[["VENDOR_ID", "VENDOR_NAME", "IA_NAME"]])
    return pd.concat(frames, ignore_index=True)


def load_payees(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> tuple[dict[int, int], dict]:
    """Returns ({vendor_id: payee_id} -- identity map, vendor_id IS the
    payee_id here so this is trivial but kept explicit for callers;
    stats dict)."""
    df = _expenditure_frames(data_dir)
    df = df.dropna(subset=["VENDOR_ID"])
    df["vendor_id"] = pd.to_numeric(df["VENDOR_ID"], errors="coerce")
    df = df.dropna(subset=["vendor_id"])
    df["vendor_id"] = df["vendor_id"].astype(int)
    df["VENDOR_NAME"] = df["VENDOR_NAME"].astype(str).str.strip()

    existing_payee_ids = {p.id for p in session.execute(select(Payee)).scalars()}
    first_seen = df.drop_duplicates(subset="vendor_id", keep="first").set_index("vendor_id")
    first_name_per_id = first_seen["VENDOR_NAME"]

    payee_records = []
    for vendor_id, name in first_name_per_id.items():
        if vendor_id in existing_payee_ids:
            continue
        payee_records.append(
            {
                "id": vendor_id,
                "canonical_name": name,
                "payee_type": classify_payee_type(name),
                "review_status": "unreviewed",
                "first_seen_snapshot_id": snapshot.id,
            }
        )
    bulk_insert(session, Payee, payee_records)

    existing_aliases = {(a.payee_id, a.name) for a in session.execute(select(PayeeAlias)).scalars()}
    distinct_pairs = df[["vendor_id", "VENDOR_NAME"]].drop_duplicates()
    alias_records = [
        {"payee_id": int(r.vendor_id), "name": r.VENDOR_NAME, "source_snapshot_id": snapshot.id}
        for r in distinct_pairs.itertuples(index=False)
        if (int(r.vendor_id), r.VENDOR_NAME) not in existing_aliases
    ]
    bulk_insert(session, PayeeAlias, alias_records)

    id_to_payee = {vid: vid for vid in first_name_per_id.index}  # payee.id == vendor_id
    stats = {
        "payees_added": len(payee_records),
        "aliases_added": len(alias_records),
        "distinct_vendor_ids": df["vendor_id"].nunique(),
        "distinct_vendor_names": df["VENDOR_NAME"].nunique(),
        "names_shared_by_multiple_ids": int((df.groupby("VENDOR_NAME")["vendor_id"].nunique() > 1).sum()),
        "ids_with_multiple_names": int((df.groupby("vendor_id")["VENDOR_NAME"].nunique() > 1).sum()),
    }
    return id_to_payee, stats


def load_implementing_agencies(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> dict:
    df = _expenditure_frames(data_dir)
    df = df.dropna(subset=["IA_NAME"])
    df["IA_NAME"] = df["IA_NAME"].astype(str).str.strip()
    df = df[df["IA_NAME"] != ""]

    existing = {a.ia_name for a in session.execute(select(ImplementingAgency)).scalars()}
    distinct_names = sorted(set(df["IA_NAME"].unique().tolist()) - existing)

    records = [
        {
            "ia_name": name,
            "agency_type": classify_agency_type(name),
            "first_seen_snapshot_id": snapshot.id,
        }
        for name in distinct_names
    ]
    bulk_insert(session, ImplementingAgency, records)
    return {"agencies_added": len(records), "distinct_agencies_seen": df["IA_NAME"].nunique()}
