"""
Population for the remaining Phase 2 tables that don't sit on the work-key
lifecycle: allocation (needs tenure, built in roster.py), calamity_consent,
prior_cycle_work (no key -- BLUEPRINT.md §4, never linked to `work`), and
macro_reference.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.provenance import SourceSnapshot
from ..models.work_related import Allocation, CalamityConsent, MacroReference, PriorCycleWork
from .constants import HOUSE_COLUMN_VALUE_MAP
from .csv_utils import read_csv_body_and_footer, to_amount
from .db_utils import bulk_insert, to_records_with_nulls
from .normalize import normalize_name, parse_day_mon_year


def load_allocations(
    session: Session,
    data_dir: Path,
    snapshot_a: SourceSnapshot,
    name_to_person_id: dict[str, int],
    person_to_tenure: dict[int, int],
) -> dict:
    existing_rows = session.execute(
        select(Allocation).where(Allocation.source_snapshot_id == snapshot_a.id)
    ).scalars()
    existing = {(a.tenure_id, a.source_snapshot_id) for a in existing_rows}
    records = []
    unmatched = 0
    for path in [
        data_dir / "raw/snapshot_a/mp_allocation_LokSabha_alltenures.csv",
        data_dir / "raw/snapshot_a/mp_allocation_RajyaSabha_alltenures.csv",
    ]:
        body_df, _ = read_csv_body_and_footer(path, ",", "Total_Amt")
        for row in body_df.itertuples(index=False):
            person_id = name_to_person_id.get(normalize_name(row.MP_NAME))
            tenure_id = person_to_tenure.get(person_id) if person_id else None
            if tenure_id is None:
                unmatched += 1
                continue
            if (tenure_id, snapshot_a.id) in existing:
                continue
            records.append(
                {
                    "tenure_id": tenure_id,
                    "source_snapshot_id": snapshot_a.id,
                    "allocated_amount": to_amount(row.ALLOCATED_AMT),
                }
            )
            existing.add((tenure_id, snapshot_a.id))
    bulk_insert(session, Allocation, records)
    return {"allocations_added": len(records), "unmatched_no_tenure": unmatched}


def load_calamity_consents(session: Session, data_dir: Path, snapshot_a: SourceSnapshot) -> dict:
    added = 0
    for house, filename in [
        ("LS", "calamity_LokSabha_alltenures.csv"),
        ("RS", "calamity_RajyaSabha_alltenures.csv"),
    ]:
        body_df, _ = read_csv_body_and_footer(data_dir / "raw/snapshot_a" / filename, ",", "Total_Amt")
        records = []
        for row in body_df.itertuples(index=False):
            records.append(
                {
                    "source_snapshot_id": snapshot_a.id,
                    "house": house,
                    "calamity_name": row.CALAMITY_NAME or None,
                    "tenure_label_raw": row.TENURE or None,
                    "mp_raw_name": row.MP_NAME or None,
                    "consent_date": parse_day_mon_year(row.CRT_DT),
                    "sno": int(row.Sno) if row.Sno else None,
                    "consented_amount": to_amount(row.CONSENTED_AMOUNT),
                    "calamity_type": row.TYPE or None,
                }
            )
        bulk_insert(session, CalamityConsent, records)
        added += len(records)
    return {"calamity_consents_added": added}


def _parse_iso_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def load_prior_cycle_work(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> dict:
    existing_count = (
        session.execute(select(PriorCycleWork).where(PriorCycleWork.source_snapshot_id == snapshot.id))
        .scalars()
        .first()
    )
    if existing_count is not None:
        return {"prior_cycle_work_added": 0, "skipped": "already loaded"}

    df = pd.read_csv(
        data_dir / "raw/prior_cycle/prior_cycle_backlog_2023-24.csv",
        sep=";",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    df["house"] = df["HOUSE"].str.strip().str.lower().map(HOUSE_COLUMN_VALUE_MAP)
    out = pd.DataFrame(
        {
            "source_snapshot_id": snapshot.id,
            "house": df["house"],
            "mp_raw_name": df["MP NAME"],
            "work_description": df["WORK"],
            "category_raw": df["CATEGORY"],
            "state_name": df["STATE"],
            "constituency_name": df["CONSTITUENCY"],
            "ida_name": df["IDA"],
            "city": df["CITY"],
            "ward": df["WARD"],
            "block": df["BLOCK"],
            "village": df["VILLAGE"],
            "recommended_date": df["RECOMMENDED DATE"].map(_parse_iso_date),
            "allocation_amount": df["ALLOCATION AMOUNT"].map(to_amount),
            "ida_approval_status": df["IDA APPROVAL"],
            "status": df["STATUS"],
        }
    )
    bulk_insert(session, PriorCycleWork, to_records_with_nulls(out))
    return {"prior_cycle_work_added": len(out)}


def load_macro_reference(session: Session, data_dir: Path, snapshot: SourceSnapshot) -> dict:
    existing_count = (
        session.execute(select(MacroReference).where(MacroReference.source_snapshot_id == snapshot.id))
        .scalars()
        .first()
    )
    if existing_count is not None:
        return {"macro_reference_added": 0, "skipped": "already loaded"}

    records: list[dict] = []

    # RS_Session_247_AS_175.csv -- state-wise unspent balance, point-in-time.
    # Plain vectorized column access by exact string label throughout this
    # function -- itertuples()/._asdict() mangles field names for columns
    # with spaces/slashes/dots, which every header here has.
    df1 = pd.read_csv(data_dir / "raw/macro/RS_Session_247_AS_175.csv", dtype=str, keep_default_na=False)
    state_col, value_col = df1.columns[1], df1.columns[2]
    df1 = df1[~df1[state_col].str.strip().str.lower().isin(["sub total", "nominated", "grand total"])]
    for state, val in zip(df1[state_col], df1[value_col]):
        records.append(
            {
                "source_snapshot_id": snapshot.id,
                "scope": "state",
                "state_name": state.strip(),
                "fiscal_year": None,
                "measure": "Unspent Balance (Rs. Crore)",
                "value": to_amount(val),
                "source_document": "RS_Session_247_AS_175.csv",
                "as_of_date": None,
            }
        )

    # RS_Session_247_AU_2719.csv -- national, FY-wise released/expenditure/unspent.
    df2 = pd.read_csv(data_dir / "raw/macro/RS_Session_247_AU_2719.csv", dtype=str, keep_default_na=False)
    fy_col = df2.columns[1]
    released_col, exp_col, unspent_col = df2.columns[2], df2.columns[3], df2.columns[4]
    df2 = df2[df2[fy_col].str.strip().str.lower() != "grand total"]
    for fy, released, exp, unspent in zip(df2[fy_col], df2[released_col], df2[exp_col], df2[unspent_col]):
        for measure, val in [
            ("Released by GOI (Rs. Crore)", released),
            ("Expenditure Incurred (Rs. Crore)", exp),
            ("Unspent Balance (Rs. Crore)", unspent),
        ]:
            records.append(
                {
                    "source_snapshot_id": snapshot.id,
                    "scope": "national",
                    "state_name": None,
                    "fiscal_year": fy.strip(),
                    "measure": measure,
                    "value": to_amount(val),
                    "source_document": "RS_Session_247_AU_2719.csv",
                    "as_of_date": None,
                }
            )

    # RS-Session-251-AU3002-Annexure-I.csv -- state-wise, multiple FY
    # columns; the header has a literal duplicate pair for "2016-17"
    # (pandas suffixes the second as "...crore).1" / "...Works.1" on read)
    # -- other RS answers suggest that second pair is really 2017-18, but
    # column headers are preserved verbatim as the measure label rather
    # than silently relabeled, since BLUEPRINT.md gives no basis to correct
    # a portal export quirk.
    df3 = pd.read_csv(
        data_dir / "raw/macro/RS-Session-251-AU3002-Annexure-I.csv", dtype=str, keep_default_na=False
    )
    state_col3 = df3.columns[1]
    measure_cols = [c for c in df3.columns if c not in (df3.columns[0], state_col3)]
    df3 = df3[df3[state_col3].str.strip().str.lower() != "total"]
    for _, row in df3[[state_col3] + measure_cols].iterrows():
        state = row[state_col3].strip()
        for col in measure_cols:
            records.append(
                {
                    "source_snapshot_id": snapshot.id,
                    "scope": "state",
                    "state_name": state,
                    "fiscal_year": None,
                    "measure": col,
                    "value": to_amount(row[col]),
                    "source_document": "RS-Session-251-AU3002-Annexure-I.csv",
                    "as_of_date": None,
                }
            )

    bulk_insert(session, MacroReference, records)
    return {"macro_reference_added": len(records)}
