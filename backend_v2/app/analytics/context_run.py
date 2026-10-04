"""
Context run: loads Snapshot A's sanctioned/completed works, builds peer
groups and leave-one-out baselines (app.analytics.peers), and writes one
append-only analysis_run with its peer_group and work_context rows.

Scope: works with a sanction record (lifecycle 'sanctioned' or
'completed'), because every level's key and BLUEPRINT.md §6's cost
baseline need either a sanction FY or a sanction/actual amount.
Recommended-only works have neither and get no context row.

Amount basis (BLUEPRINT.md §6 signal catalogue): actual amount for
completed works, sanction amount for open ones. A work is a usable peer
only if that amount is present and > 0 ("usable peer count excludes
missing amounts", BLUEPRINT.md §6).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import numpy as np
import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..ingest.db_utils import bulk_insert, to_records_with_nulls
from ..ingest.normalize import normalize_name
from ..ingest.pipeline import get_or_create_snapshot
from ..models.analytics import AnalysisRun, PeerGroup, WorkContext
from . import peers

ENGINE_VERSION = "context_v1"

_LOAD_SQL = text(
    """
    SELECT w.work_key, w.house, w.raw_mp_name, w.activity_type_id,
           da.state_id, da.district_key,
           ws.lifecycle_status, ws.sanction_amount, ws.sanction_date, ws.actual_amount
    FROM work_state ws
    JOIN work w ON w.work_key = ws.work_key
    LEFT JOIN district_authority da ON da.id = w.district_authority_id
    WHERE ws.source_snapshot_id = :snap
      AND ws.lifecycle_status IN ('sanctioned', 'completed')
    """
)


def indian_fy(dates: pd.Series) -> pd.Series:
    """'2024-25' style financial year (April-March)."""
    d = pd.to_datetime(dates)
    start = d.dt.year.where(d.dt.month >= 4, d.dt.year - 1).astype("Int64")
    fy = start.astype(str) + "-" + ((start + 1) % 100).astype(str).str.zfill(2)
    return fy.where(d.notna())


def load_frame(session: Session, snapshot_id: int) -> pd.DataFrame:
    df = pd.read_sql(_LOAD_SQL, session.connection(), params={"snap": snapshot_id})
    df = df.set_index("work_key").sort_index()
    for col in ("sanction_amount", "actual_amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("activity_type_id", "state_id"):
        df[col] = df[col].astype("Int64")
    completed = df["lifecycle_status"] == "completed"
    df["amount"] = df["sanction_amount"].where(~completed, df["actual_amount"])
    df["amount_basis"] = np.where(completed, "actual", "sanction")
    df["is_usable"] = df["amount"].notna() & (df["amount"] > 0)
    df["sanction_fy"] = indian_fy(df["sanction_date"])
    df["mp"] = df["raw_mp_name"].map(normalize_name)
    return df


def config() -> dict:
    return {
        "engine_version": ENGINE_VERSION,
        "level_keys": {k: list(v) for k, v in peers.LEVEL_KEYS.items()},
        "refinement_keys": list(peers.REFINEMENT_KEYS),
        "min_usable_peers": peers.MIN_USABLE_PEERS,
        "min_other_mps": peers.MIN_OTHER_MPS,
        "max_mp_share": peers.MAX_MP_SHARE,
        "refinement_rule": "same as L1-L3 (>= 15 usable peers excl. self, >= 3 other MPs, <= 50% one MP)",
        "amount_basis": "actual if completed else sanction; usable if > 0",
    }


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def output_hash(ctx: pd.DataFrame) -> str:
    cols = [
        "level",
        "group_key",
        "n_usable_excl_self",
        "distinct_other_mps",
        "max_mp_share",
        "peer_median",
        "peer_iqr",
        "peer_mad",
        "ref_group_key",
        "ref_median",
        "ref_mad",
    ]
    h = hashlib.sha256()
    for work_key, row in zip(ctx.index, ctx[cols].itertuples(index=False)):
        parts = [work_key] + [
            ""
            if (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v is None
            else (f"{v:.6f}" if isinstance(v, float) else str(v))
            for v in row
        ]
        h.update(("|".join(parts) + "\n").encode())
    return h.hexdigest()


def coverage(frame: pd.DataFrame, ctx: pd.DataFrame) -> dict:
    """BLUEPRINT.md §6 coverage figures, recomputed on this run."""
    n = len(frame)
    out = {"works_in_scope": n, "by_level": {}}
    for level, keys in list(peers.LEVEL_KEYS.items()) + [("REF", peers.REFINEMENT_KEYS)]:
        comp = peers.group_composition(frame, peers.group_key(frame, keys))
        out["by_level"][level] = {
            "pct_ge10_peers": round(100 * (comp["n_usable_excl_self"] >= 10).sum() / n, 2),
            "pct_ge3_other_mps": round(100 * (comp["distinct_other_mps"] >= 3).sum() / n, 2),
            "pct_ge3_mps_in_peers": round(100 * (comp["distinct_mps_in_peers"] >= 3).sum() / n, 2),
            "pct_ge3_mps_in_group": round(100 * (comp["distinct_mps_in_group"] >= 3).sum() / n, 2),
            "pct_qualifies": round(100 * peers.qualifies(comp).fillna(False).sum() / n, 2),
        }
    out["pct_refinement_qualifies"] = round(100 * ctx["ref_group_key"].notna().sum() / n, 2)
    assigned = ctx["level"].fillna("none").value_counts()
    out["assigned_level_counts"] = {k: int(v) for k, v in assigned.items()}
    by_house = ctx.join(frame["house"]).assign(level=lambda d: d["level"].fillna("none"))
    out["assigned_level_by_house"] = {
        h: {k: int(v) for k, v in g["level"].value_counts().items()} for h, g in by_house.groupby("house")
    }
    return out


def run(session: Session) -> tuple[AnalysisRun, pd.DataFrame, pd.DataFrame, dict]:
    snapshot_a = get_or_create_snapshot(session, "snapshot_a")
    frame = load_frame(session, snapshot_a.id)

    cfg = config()
    run_row = AnalysisRun(
        source_snapshot_id=snapshot_a.id,
        engine_version=ENGINE_VERSION,
        config_hash=_hash(cfg),
        status="running",
        notes=json.dumps(cfg),
    )
    session.add(run_row)
    session.flush()

    ctx = peers.build_context(frame)

    group_records = []
    for level, keys in list(peers.LEVEL_KEYS.items()) + [("REF", peers.REFINEMENT_KEYS)]:
        summary = peers.group_summary(frame, keys).reset_index().rename(columns={"g": "group_key"})
        summary.insert(0, "level", level)
        summary.insert(0, "run_id", run_row.id)
        group_records.extend(to_records_with_nulls(summary))
    bulk_insert(session, PeerGroup, group_records)

    wc = ctx.join(frame[["house", "amount", "amount_basis", "sanction_fy"]])
    wc = wc.rename(columns={"amount": "amount_used"})
    wc.loc[wc["amount_used"].isna(), "amount_basis"] = None
    wc = wc.reset_index().rename(columns={"index": "work_key"})
    wc.insert(0, "run_id", run_row.id)
    bulk_insert(session, WorkContext, to_records_with_nulls(wc))

    run_row.output_hash = output_hash(ctx)
    run_row.status = "complete"
    run_row.finished_at = dt.datetime.now(dt.timezone.utc)
    session.flush()
    return run_row, frame, ctx, coverage(frame, ctx)


def latest_run(session: Session) -> AnalysisRun | None:
    return (
        session.execute(
            select(AnalysisRun).where(AnalysisRun.status == "complete").order_by(AnalysisRun.id.desc())
        )
        .scalars()
        .first()
    )


def work_contexts(session: Session, run_id: int, house: str | None = None) -> list[WorkContext]:
    """House is a filter on which works are returned -- never on how their
    baselines were built."""
    stmt = select(WorkContext).where(WorkContext.run_id == run_id)
    if house is not None:
        if house not in ("LS", "RS"):
            raise ValueError(f"house must be 'LS' or 'RS', got {house!r}")
        stmt = stmt.where(WorkContext.house == house)
    return list(session.execute(stmt).scalars())


def count_by_house(session: Session, run_id: int) -> dict[str, int]:
    rows = session.execute(
        select(WorkContext.house, func.count(WorkContext.id))
        .where(WorkContext.run_id == run_id)
        .group_by(WorkContext.house)
    ).all()
    return {h: int(c) for h, c in rows}
