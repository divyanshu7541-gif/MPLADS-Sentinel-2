"""
Phase 4 orchestration: runs Phase 3's peer/context engine (unchanged,
reused as-is -- see context_run.run) and then computes + writes the six
BASE signals (signals.py) onto that SAME analysis_run. One call therefore
produces analysis_run + peer_group + work_context + signal_result together,
matching BLUEPRINT.md §4's "P6 Analytics: peer baselines, six base
signals..." as one run.

No columns or behaviour of app.analytics.context_run are changed by this
module; it only reads the `frame`/`ctx`/`run_row` that function already
returns and adds Phase 4-only columns via a separate query
(load_signal_frame), so Phase 3's own tests are unaffected by Phase 4.
"""

from __future__ import annotations

import hashlib

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..models.analytics import AnalysisRun, SignalResult
from ..models.provenance import SourceSnapshot
from . import context_run, signals

SIGNAL_ORDER = (
    "cost_anomaly",
    "near_duplicate",
    "portfolio_concentration",
    "district_authority_pattern",
    "temporal_anomaly",
    "lifecycle_delay",
)

_EXTRA_SQL = text(
    """
    SELECT w.work_key, w.description_normalized, w.district_authority_id,
           ws.recommended_date, ws.actual_end_date
    FROM work_state ws
    JOIN work w ON w.work_key = ws.work_key
    WHERE ws.source_snapshot_id = :snap
      AND ws.lifecycle_status IN ('sanctioned', 'completed')
    """
)

_PAYMENT_SQL = text(
    "SELECT work_key, SUM(amount) AS paid_total FROM payment"
    " WHERE source_snapshot_id = :snap GROUP BY work_key"
)


def load_signal_frame(session: Session, run_frame: pd.DataFrame, snapshot_id: int) -> pd.DataFrame:
    """`context_run.load_frame`'s frame, joined with the extra columns the
    six signals need (never part of the peer baseline itself): the
    normalised description, the work's own district authority, its
    recommendation and actual-completion dates, and total payments
    received so far.

    `context_run.load_frame` never casts `sanction_date` to a pandas
    datetime dtype -- every Phase 3 use wraps it in `pd.to_datetime()`
    before touching `.dt`, so the raw column can come back from psycopg
    as plain `datetime.date` objects (object dtype) and that was never
    visible. The six signals use `.dt` directly, so it is cast here
    rather than in context_run.py (kept untouched, see module docstring).
    """
    frame = run_frame.copy()
    frame["sanction_date"] = pd.to_datetime(frame["sanction_date"])

    extra = pd.read_sql(_EXTRA_SQL, session.connection(), params={"snap": snapshot_id})
    extra = extra.set_index("work_key")
    extra["district_authority_id"] = extra["district_authority_id"].astype("Int64")
    extra["recommended_date"] = pd.to_datetime(extra["recommended_date"])
    extra["actual_end_date"] = pd.to_datetime(extra["actual_end_date"])
    frame = frame.join(extra)

    paid_df = pd.read_sql(_PAYMENT_SQL, session.connection(), params={"snap": snapshot_id})
    paid = paid_df.set_index("work_key")["paid_total"]
    frame["paid_total"] = pd.to_numeric(paid.reindex(frame.index)).fillna(0.0)
    return frame


def signals_output_hash(results: dict[str, pd.DataFrame]) -> str:
    """Hash of every signal's (work_key, eligible, score, direction)
    across all six signals, independent of `analysis_run.output_hash`
    (which context_run.run sets from peer/context data only, unchanged by
    Phase 4). Same inputs must reproduce the same hash -- BLUEPRINT.md §5
    "Rules that keep it reproducible"."""
    h = hashlib.sha256()
    for name in SIGNAL_ORDER:
        df = results[name]
        for work_key, row in zip(df.index, df[["eligible", "score", "direction"]].itertuples(index=False)):
            score = "" if pd.isna(row.score) else f"{row.score:.9f}"
            # `pd.NA` (from _empty_result's direction column) has no truthiness
            # -- `if row.direction` raises TypeError, so isna() first, same as score.
            direction = "" if pd.isna(row.direction) else row.direction
            h.update(f"{name}|{work_key}|{row.eligible}|{score}|{direction}\n".encode())
    return h.hexdigest()


def compute_all_signals(
    frame: pd.DataFrame, ctx: pd.DataFrame, as_of: pd.Timestamp
) -> dict[str, pd.DataFrame]:
    return {
        "cost_anomaly": signals.cost_anomaly_signal(frame, ctx),
        "near_duplicate": signals.near_duplicate_signal(frame),
        "portfolio_concentration": signals.portfolio_concentration_signal(frame),
        "district_authority_pattern": signals.district_authority_pattern_signal(frame),
        "temporal_anomaly": signals.temporal_anomaly_signal(frame),
        "lifecycle_delay": signals.lifecycle_delay_signal(frame, ctx, as_of),
    }


def _records_for_signal(
    run_id: int, signal_name: str, frame: pd.DataFrame, result: pd.DataFrame
) -> list[dict]:
    df = result.copy()
    df["run_id"] = run_id
    df["signal"] = signal_name
    df["is_base"] = True
    df["house"] = frame["house"]
    df["evidence"] = [signals.json_safe(e) for e in df["evidence"]]
    df = df.reset_index()  # frame's index is named "work_key" (set in context_run.load_frame)
    assert "work_key" in df.columns, "signal result frame lost its work_key index"
    return to_records_with_nulls(df)


def write_signal_results(
    session: Session, run_id: int, frame: pd.DataFrame, results: dict[str, pd.DataFrame]
) -> int:
    total = 0
    for name in SIGNAL_ORDER:
        records = _records_for_signal(run_id, name, frame, results[name])
        bulk_insert(session, SignalResult, records)
        total += len(records)
    return total


def run(session: Session) -> tuple[AnalysisRun, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """Runs Phase 3's context engine (unchanged) to get a fresh run_id with
    peer_group/work_context already written, extends the frame with the
    Phase 4-only columns, computes the six base signals, and writes them
    onto that same run. `as_of` for lifecycle_delay's "age since sanction"
    is the snapshot's own recorded `data_as_of` date, never wall-clock
    time -- BLUEPRINT.md §5's "same inputs...reproduce the same output
    hash" would otherwise be impossible to satisfy on a re-run."""
    run_row, ctx_frame, ctx, _coverage = context_run.run(session)
    session.flush()

    source_snapshot = run_row.source_snapshot_id
    frame = load_signal_frame(session, ctx_frame, source_snapshot)
    as_of = _resolve_as_of(session, source_snapshot)

    results = compute_all_signals(frame, ctx, as_of)
    n_written = write_signal_results(session, run_row.id, frame, results)
    sig_hash = signals_output_hash(results)

    run_row.notes = (
        (run_row.notes or "")
        + f"\nphase4_signals_written={n_written}"
        + f"\nphase4_as_of={as_of.date()}"
        + f"\nphase4_signals_output_hash={sig_hash}"
        + f"\nphase4_signal_calibration={signals.SIGNAL_CALIBRATION}"
    )
    session.flush()
    return run_row, frame, ctx, results


def _resolve_as_of(session: Session, snapshot_id: int) -> pd.Timestamp:
    """The reference "today" for lifecycle_delay's age-since-sanction
    component. Must be a fixed, data-derived date, never wall-clock time,
    or a re-run could never reproduce the same output hash."""
    snap = session.get(SourceSnapshot, snapshot_id)
    if not snap or not snap.data_as_of:
        raise ValueError(f"source_snapshot {snapshot_id} has no data_as_of; as-of date is not deterministic")
    return pd.Timestamp(snap.data_as_of)
