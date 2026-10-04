"""
Phase 1 core pipeline: P0 Register -> P1 Parse+contract -> P2 Reconcile,
plus `work` row registration (house-tagged).

Idempotent by construction: a file already registered under its snapshot
(matched by filename, with a sha256 identity check) is skipped entirely on
re-run -- P1/P2/work-loading never re-run against it, so row counts and the
run's output hash are reproducible across repeated runs on the same inputs.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models.provenance import ControlTotal, ImportReject, RawFile, RawRow, SourceSnapshot
from ..models.work import Work
from . import schemas
from .constants import (
    ALL_FILES,
    BLUEPRINT_TOLERANCE_CRORE,
    CRORE,
    FOOTER_TOLERANCE_RUPEES,
    SNAPSHOTS,
    FileSpec,
)
from .csv_utils import read_csv_body_and_footer, sha256_of, to_amount
from .db_utils import bulk_insert, to_records_with_nulls
from .house import derive_house_for_file, derive_house_for_row

log = logging.getLogger("sentinel.ingest")


class ReconciliationFailure(Exception):
    pass


def get_or_create_snapshot(session: Session, code: str) -> SourceSnapshot:
    existing = session.execute(select(SourceSnapshot).where(SourceSnapshot.code == code)).scalar_one_or_none()
    if existing:
        return existing
    spec = SNAPSHOTS[code]
    snap = SourceSnapshot(
        code=spec.code,
        label=spec.label,
        portal_address=None,
        retrieval_method=spec.retrieval_method,
        data_as_of=spec.data_as_of,
        imported_by="phase1_pipeline",
        status="registered",
        notes=spec.notes,
    )
    session.add(snap)
    session.flush()
    return snap


def register_file(
    session: Session, snapshot: SourceSnapshot, spec: FileSpec, data_dir: Path
) -> tuple[RawFile, bool]:
    """Returns (raw_file, already_registered). already_registered=True means
    P0 found a byte-identical file already ingested this run should skip
    P1/P2/work-loading for (idempotency)."""
    path = data_dir / spec.relative_path
    digest = sha256_of(path)
    byte_size = path.stat().st_size

    existing = session.execute(
        select(RawFile).where(RawFile.source_snapshot_id == snapshot.id, RawFile.filename == path.name)
    ).scalar_one_or_none()
    if existing:
        if existing.sha256 != digest:
            raise ReconciliationFailure(
                f"{path.name} has changed since it was registered (sha256 mismatch) -- "
                "a corrected import must go under a new snapshot, not overwrite raw_file."
            )
        return existing, True

    raw_file = RawFile(
        source_snapshot_id=snapshot.id,
        filename=path.name,
        relative_path=spec.relative_path,
        sha256=digest,
        byte_size=byte_size,
        delimiter=spec.delimiter,
        row_count=0,
    )
    session.add(raw_file)
    session.flush()
    return raw_file, False


def _json_safe(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = None
        elif isinstance(v, float) and v != v:  # NaN
            out[k] = None
        else:
            out[k] = v
    return out


def build_raw_row_records(df: pd.DataFrame, raw_file_id: int) -> list[dict]:
    """Vectorized equivalent of iterating df.iterrows() and building a dict
    per row -- iterrows() is Python-level-slow (tens of minutes on a
    100k+-row DataFrame); DataFrame.to_dict('records') does the same work
    in pandas' C-backed internals."""
    row_nos = df["_row_no"].astype(int).tolist()
    data_df = df.drop(columns=["_row_no"])
    records = to_records_with_nulls(data_df)
    return [
        {"raw_file_id": raw_file_id, "row_no": row_no, "data": rec} for row_no, rec in zip(row_nos, records)
    ]


def run_p1_p2_for_file(
    session: Session, snapshot: SourceSnapshot, raw_file: RawFile, spec: FileSpec, data_dir: Path
) -> tuple[dict, pd.DataFrame]:
    path = data_dir / spec.relative_path
    body_df, footer = read_csv_body_and_footer(path, spec.delimiter, spec.footer_col)

    valid_df, rejects = schemas.validate(body_df, spec)

    reject_records = [
        {
            "raw_file_id": raw_file.id,
            "row_no": r["row_no"],
            "reason_code": r["reason_code"],
            "reason_detail": r["reason_detail"],
            "raw_data": _json_safe(r["raw_data"]),
        }
        for r in rejects
    ]
    bulk_insert(session, ImportReject, reject_records)

    row_records = build_raw_row_records(valid_df, raw_file.id)
    bulk_insert(session, RawRow, row_records)

    raw_file.row_count = len(valid_df)
    if footer is not None and spec.footer_col:
        raw_file.footer_total = to_amount(footer.get(spec.footer_col))

    summary = {
        "file": path.name,
        "category": spec.category,
        "snapshot": snapshot.code,
        "rows_total": len(body_df),
        "rows_valid": len(valid_df),
        "rows_rejected": len(rejects),
        "footer_total_rupees": float(raw_file.footer_total) if raw_file.footer_total is not None else None,
    }

    if spec.is_core:
        body_sum = float(valid_df[spec.body_amount_col].map(to_amount).sum())
        footer_total = float(raw_file.footer_total) if raw_file.footer_total is not None else None
        footer_match = footer_total is not None and abs(body_sum - footer_total) <= FOOTER_TOLERANCE_RUPEES
        computed_crore = body_sum / CRORE
        blueprint_match = abs(computed_crore - spec.control_value_crore) <= BLUEPRINT_TOLERANCE_CRORE
        status = "pass" if (footer_match and blueprint_match) else "fail"

        session.add(
            ControlTotal(
                source_snapshot_id=snapshot.id,
                raw_file_id=raw_file.id,
                measure=spec.control_measure,
                body_sum_rupees=body_sum,
                footer_total_rupees=footer_total or 0,
                portal_value_crore=spec.control_value_crore,
                computed_value_crore=computed_crore,
                footer_match=footer_match,
                blueprint_match=blueprint_match,
                status=status,
            )
        )
        summary.update(
            {
                "measure": spec.control_measure,
                "body_sum_rupees": round(body_sum, 2),
                "computed_value_crore": round(computed_crore, 4),
                "portal_value_crore": spec.control_value_crore,
                "footer_match": footer_match,
                "blueprint_match": blueprint_match,
                "status": status,
            }
        )

    return summary, valid_df


def load_work_rows(
    session: Session,
    snapshot: SourceSnapshot,
    raw_file: RawFile,
    spec: FileSpec,
    valid_df: pd.DataFrame,
    *,
    only_house: str | None = None,
) -> int:
    """Upserts `work` rows with ON CONFLICT DO NOTHING on work_key, so a
    work_key already registered by an earlier-processed file (e.g. seen in
    works_recommended, then again in works_sanctioned) keeps its first raw
    field values -- later files just confirm the key exists, they don't
    overwrite it. `only_house` restricts to one house's rows (used for
    Snapshot B's recommended-works gap-fill, which should only add the
    documented-missing Rajya Sabha rows, not duplicate Lok Sabha works
    already present via Snapshot A)."""
    house_static = derive_house_for_file(spec)
    work = pd.DataFrame(index=valid_df.index)
    work["work_key"] = valid_df[spec.key_col].astype(str).str.strip()
    if house_static is not None:
        work["house"] = house_static
        work["house_source"] = "filename"
    else:
        work["house"] = valid_df[spec.house_col].map(derive_house_for_row)
        work["house_source"] = "house_column"
    work["first_seen_snapshot_id"] = snapshot.id
    work["first_seen_raw_file_id"] = raw_file.id
    work["raw_mp_name"] = valid_df[spec.mp_name_col] if spec.mp_name_col else None
    work["raw_activity_name"] = valid_df[spec.activity_col] if spec.activity_col else None
    work["raw_description"] = valid_df[spec.description_col] if spec.description_col else None

    if only_house is not None:
        work = work[work["house"] == only_house]

    records = to_records_with_nulls(work)

    inserted = 0
    for i in range(0, len(records), 5000):
        chunk = records[i : i + 5000]
        # cursor.rowcount is unreliable (-1, "not determinable") for a
        # multi-VALUES INSERT ... ON CONFLICT DO NOTHING via psycopg3 --
        # RETURNING only yields the rows actually inserted, so counting
        # those is the correct way to measure how many were new.
        stmt = (
            pg_insert(Work)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["work_key"])
            .returning(Work.work_key)
        )
        result = session.execute(stmt)
        inserted += len(result.fetchall())
    return inserted


def compute_output_hash(session: Session) -> str:
    rows = session.execute(
        select(RawFile.filename, RawFile.sha256, RawFile.row_count).order_by(RawFile.filename)
    ).all()
    h = hashlib.sha256()
    for filename, sha256, row_count in rows:
        h.update(f"{filename}|{sha256}|{row_count}\n".encode())
    return h.hexdigest()


def run_all(session: Session, data_dir: Path) -> dict:
    file_summaries: list[dict] = []
    work_rows_added_total = 0
    ida_state_pairs: set[tuple[str, str]] = set()

    for spec in ALL_FILES:
        snapshot = get_or_create_snapshot(session, spec.snapshot_code)
        raw_file, already = register_file(session, snapshot, spec, data_dir)

        if already:
            file_summaries.append(
                {
                    "file": raw_file.filename,
                    "category": spec.category,
                    "snapshot": snapshot.code,
                    "status": "skipped_already_ingested",
                    "rows_valid": raw_file.row_count,
                }
            )
            continue

        summary, valid_df = run_p1_p2_for_file(session, snapshot, raw_file, spec, data_dir)
        file_summaries.append(summary)

        if spec.feeds_work:
            only_house = "RS" if spec.category == "snapshot_b_recommended" else None
            added = load_work_rows(session, snapshot, raw_file, spec, valid_df, only_house=only_house)
            summary["work_rows_added"] = added
            work_rows_added_total += added

        if "IDA_NAME" in valid_df.columns and "STATE_NAME" in valid_df.columns:
            pairs = valid_df[["IDA_NAME", "STATE_NAME"]].dropna().drop_duplicates()
            ida_state_pairs.update((str(a).strip(), str(b).strip()) for a, b in pairs.itertuples(index=False))

    session.flush()
    output_hash = compute_output_hash(session)

    # Reconciliation status must reflect current DB state, not just this
    # run's freshly-processed files -- on an idempotent re-run where every
    # file is already registered, file_summaries has no "measure" entries
    # at all even though all 9 core files are (still) reconciled.
    core_totals = session.execute(select(ControlTotal)).scalars().all()
    all_core_pass = len(core_totals) == 9 and all(c.status == "pass" for c in core_totals)

    return {
        "files": file_summaries,
        "work_rows_added_total": work_rows_added_total,
        "output_hash": output_hash,
        "all_core_reconciled": all_core_pass,
        "core_file_count": len(core_totals),
        "ida_state_pairs": ida_state_pairs,
    }
