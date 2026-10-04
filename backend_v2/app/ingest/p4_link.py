"""
P4 Link and derive lifecycle (BLUEPRINT.md §5): join recommended/
sanctioned/completed/payments on work_key, derive lifecycle_status from
FILE MEMBERSHIP AND DATES ONLY -- never from the portal's own WORK_STAGE
field (stored separately as work_state.raw_stage, never read downstream;
Phase 2 brief + acceptance criteria).

Every join here is pandas .merge() on an explicit key column. Positional
row-alignment indexing is never used anywhere in this module (grepped by
tests/test_phase2_normalize.py::test_no_positional_merge_in_ingest_code).

Scope: builds work_state/payment for Snapshot A's 128,829 lifecycle-linked
work keys (the 7 true Core files). The 6,054 Snapshot B gap-fill keys get
their own work_state row under snapshot_b, recommended-only (Snapshot B
has no matching sanctioned/completed/payment files to join against).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.provenance import SourceSnapshot
from ..models.work import Work
from ..models.work_related import Payment, WorkState
from .constants import CORE_FILES, SNAPSHOT_B_FILES
from .csv_utils import read_csv_body_and_footer, to_amount
from .db_utils import bulk_insert, to_records_with_nulls
from .normalize import parse_day_mon_year


def _spec(category: str, house: str):
    for s in CORE_FILES:
        if s.category == category and s.house_value == house:
            return s
    raise KeyError((category, house))


def build_work_state_snapshot_a(session: Session, data_dir: Path, snapshot_a: SourceSnapshot) -> dict:
    existing = {
        ws.work_key
        for ws in session.execute(
            select(WorkState).where(WorkState.source_snapshot_id == snapshot_a.id)
        ).scalars()
    }

    frames = {}
    ls_keys_by_category: dict[str, set[str]] = {}
    for category in ("works_recommended", "works_sanctioned", "works_completed"):
        houses = ["LS"] if category == "works_recommended" else ["LS", "RS"]
        parts = []
        for house in houses:
            spec = _spec(category, house)
            body_df, _ = read_csv_body_and_footer(
                data_dir / spec.relative_path, spec.delimiter, spec.footer_col
            )
            if house == "LS":
                ls_keys_by_category[category] = set(
                    body_df["WORK_RECOMMENDATION_DTL_ID"].astype(str).str.strip()
                )
            parts.append(body_df)
        frames[category] = pd.concat(parts, ignore_index=True)

    rec = frames["works_recommended"][
        ["WORK_RECOMMENDATION_DTL_ID", "RECOMMENDED_AMOUNT", "RECOMMENDATION_DATE"]
    ].rename(
        columns={
            "WORK_RECOMMENDATION_DTL_ID": "work_key",
            "RECOMMENDED_AMOUNT": "recommended_amount",
            "RECOMMENDATION_DATE": "recommended_date_raw",
        }
    )
    san = frames["works_sanctioned"][
        [
            "WORK_RECOMMENDATION_DTL_ID",
            "SANCTION_AMOUNT",
            "SANCTION_DATE",
            "WORK_STAGE",
            "FILE_STATUS",
            "FLAG",
        ]
    ].rename(
        columns={
            "WORK_RECOMMENDATION_DTL_ID": "work_key",
            "SANCTION_AMOUNT": "sanction_amount",
            "SANCTION_DATE": "sanction_date_raw",
        }
    )
    comp = frames["works_completed"][
        ["WORK_RECOMMENDATION_DTL_ID", "ACTUAL_AMOUNT", "ACTUAL_END_DATE"]
    ].rename(
        columns={
            "WORK_RECOMMENDATION_DTL_ID": "work_key",
            "ACTUAL_AMOUNT": "actual_amount",
            "ACTUAL_END_DATE": "actual_end_date_raw",
        }
    )

    for df, col in [(rec, "work_key"), (san, "work_key"), (comp, "work_key")]:
        df[col] = df[col].astype(str).str.strip()

    # First occurrence wins per work_key (a work should have one row per
    # lifecycle file; duplicates, if any, keep the first -- explicit key
    # join, never positional).
    rec = rec.drop_duplicates(subset="work_key", keep="first")
    san = san.drop_duplicates(subset="work_key", keep="first")
    comp = comp.drop_duplicates(subset="work_key", keep="first")

    merged = rec.merge(san, on="work_key", how="outer").merge(comp, on="work_key", how="outer")

    merged["recommended_amount"] = merged["recommended_amount"].map(to_amount)
    merged["sanction_amount"] = merged["sanction_amount"].map(to_amount)
    merged["actual_amount"] = merged["actual_amount"].map(to_amount)
    merged["recommended_date"] = merged["recommended_date_raw"].map(parse_day_mon_year)
    merged["sanction_date"] = merged["sanction_date_raw"].map(parse_day_mon_year)
    merged["actual_end_date"] = merged["actual_end_date_raw"].map(parse_day_mon_year)

    is_sanctioned = merged["sanction_amount"].notna() | merged["sanction_date"].notna()
    is_completed = merged["actual_amount"].notna() | merged["actual_end_date"].notna()
    merged["lifecycle_status"] = "recommended"
    merged.loc[is_sanctioned, "lifecycle_status"] = "sanctioned"
    merged.loc[is_completed, "lifecycle_status"] = "completed"

    # BLUEPRINT.md §5/§6: "361 sanctioned works absent from the recommended
    # file" -- verified (2026-09-24) to mean Lok Sabha only: comparing
    # LS-sanctioned keys against LS-recommended keys gives exactly 361.
    # Checking recommended_amount.isna() after the outer merge is wrong on
    # two counts: (a) it can't distinguish "row absent" from "row present
    # with a blank amount" (which does happen, rarely), and (b) an
    # unscoped merged frame mixes in Rajya Sabha, which has NO recommended
    # file in Snapshot A at all -- that's the separate, already-documented
    # Hard Limit 6 gap, not a referential defect, and comparing RS
    # sanctioned/completed keys against an LS-only recommended set produces
    # a large, misleading number (verified: 19,499) that conflates the two.
    ls_sanctioned = ls_keys_by_category["works_sanctioned"]
    ls_completed = ls_keys_by_category["works_completed"]
    ls_gap_keys = (ls_sanctioned | ls_completed) - ls_keys_by_category["works_recommended"]
    referential_gap_count = len(ls_gap_keys)

    merged = merged[~merged["work_key"].isin(existing)]

    records = merged[
        [
            "work_key",
            "lifecycle_status",
            "recommended_amount",
            "recommended_date",
            "sanction_amount",
            "sanction_date",
            "actual_amount",
            "actual_end_date",
            "WORK_STAGE",
            "FILE_STATUS",
            "FLAG",
        ]
    ].rename(columns={"WORK_STAGE": "raw_stage", "FILE_STATUS": "raw_file_status", "FLAG": "raw_flag"})
    records.insert(1, "source_snapshot_id", snapshot_a.id)

    bulk_insert(session, WorkState, to_records_with_nulls(records))

    return {
        "work_states_added": len(records),
        "referential_gap_sanctioned_not_recommended": referential_gap_count,
        "lifecycle_counts": merged["lifecycle_status"].value_counts().to_dict(),
    }


def build_work_state_snapshot_b_gapfill(session: Session, data_dir: Path, snapshot_b: SourceSnapshot) -> dict:
    existing = {
        ws.work_key
        for ws in session.execute(
            select(WorkState).where(WorkState.source_snapshot_id == snapshot_b.id)
        ).scalars()
    }
    spec = next(s for s in SNAPSHOT_B_FILES if s.category == "snapshot_b_recommended")
    body_df, _ = read_csv_body_and_footer(data_dir / spec.relative_path, spec.delimiter, spec.footer_col)

    gap_fill_keys = {
        w.work_key
        for w in session.execute(select(Work).where(Work.first_seen_snapshot_id == snapshot_b.id)).scalars()
    }
    body_df["Work ID"] = body_df["Work ID"].astype(str).str.strip()
    body_df = body_df[body_df["Work ID"].isin(gap_fill_keys) & ~body_df["Work ID"].isin(existing)]
    body_df = body_df.drop_duplicates(subset="Work ID", keep="first")

    records = pd.DataFrame(
        {
            "work_key": body_df["Work ID"],
            "source_snapshot_id": snapshot_b.id,
            "lifecycle_status": "recommended",
            "recommended_amount": body_df["Recommended Amount (₹)"].map(to_amount),
            "recommended_date": None,
            "sanction_amount": None,
            "sanction_date": None,
            "actual_amount": None,
            "actual_end_date": None,
            "raw_stage": None,
            "raw_file_status": None,
            "raw_flag": None,
        }
    )
    bulk_insert(session, WorkState, to_records_with_nulls(records))
    return {"work_states_added": len(records)}


def build_payments(
    session: Session, data_dir: Path, snapshot_a: SourceSnapshot, payee_id_by_vendor: dict[int, int]
) -> dict:
    from ..models.entities import ImplementingAgency

    existing_agency_rows = session.execute(select(ImplementingAgency)).scalars()
    existing_agency = {a.ia_name: a.id for a in existing_agency_rows}
    existing_count = (
        session.execute(select(Payment).where(Payment.source_snapshot_id == snapshot_a.id)).scalars().first()
    )
    if existing_count is not None:
        return {"payments_added": 0, "skipped": "already loaded"}

    frames = []
    for house in ("LS", "RS"):
        spec = _spec("expenditure", house)
        body_df, _ = read_csv_body_and_footer(data_dir / spec.relative_path, spec.delimiter, spec.footer_col)
        frames.append(body_df)
    df = pd.concat(frames, ignore_index=True)

    df["work_key"] = df["WORK_RECOMMENDATION_DTL_ID"].astype(str).str.strip()
    df["amount"] = df["FUND_DISBURSED_AMT"].map(to_amount)
    df["payment_date"] = df["EXPENDITURE_DATE"].map(parse_day_mon_year)
    df["vendor_id"] = pd.to_numeric(df["VENDOR_ID"], errors="coerce").astype("Int64")
    df["payee_id"] = df["vendor_id"].map(payee_id_by_vendor)
    df["agency_id"] = df["IA_NAME"].map(existing_agency)

    # occurrence_no: identical (work, payee, date, amount) rows are kept,
    # never dropped, distinguished by an ascending occurrence number --
    # BLUEPRINT.md §2/§4.
    dup_key = ["work_key", "payee_id", "payment_date", "amount"]
    df["occurrence_no"] = df.groupby(dup_key).cumcount() + 1

    cols = ["work_key", "payee_id", "agency_id", "payment_date", "amount", "WORK_STATUS", "occurrence_no"]
    out = df[cols].rename(columns={"WORK_STATUS": "status"})
    out.insert(0, "source_snapshot_id", snapshot_a.id)
    out = out.dropna(subset=["payee_id", "amount"])
    out["payee_id"] = out["payee_id"].astype(int)
    out["agency_id"] = out["agency_id"].astype("Int64")

    bulk_insert(session, Payment, to_records_with_nulls(out))
    return {"payments_added": len(out), "payments_skipped_no_payee": len(df) - len(out)}
