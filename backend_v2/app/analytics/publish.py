"""
Phase 6: the published_run pointer (BLUEPRINT.md §4, §5 P9 "Publish: atomic
swap of published_run"). Records which analysis run is served and which
fusion configuration is the default, so the owner's Phase 5 decision
(v4-candidate, docs/phase5_gate_report_v3.md "Decision") is queryable in code.
The swap is one INSERT ... ON CONFLICT statement on a single-row table.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models.analytics import PublishedRun, RiskResult
from . import fusion

DECISION_REF = "docs/phase5_gate_report_v3.md#decision (owner decision 2026-09-26)"


def publish(
    session: Session, run_id: int | None = None, config_name: str = fusion.DEFAULT_CONFIG
) -> PublishedRun:
    """Point published_run at `run_id` (default: latest run with risk_result)
    with `config_name` as the default config. The run must have risk_result
    rows for that config."""
    if config_name not in fusion.CONFIGS:
        raise ValueError(f"unknown fusion config {config_name!r}")
    if run_id is None:
        run_id = session.execute(select(func.max(RiskResult.run_id))).scalar()
        if run_id is None:
            raise ValueError("no run has risk_result rows; nothing to publish")
    n = session.execute(
        select(func.count())
        .select_from(RiskResult)
        .where(RiskResult.run_id == run_id, RiskResult.config_name == config_name)
    ).scalar_one()
    if n == 0:
        raise ValueError(f"run {run_id} has no risk_result rows for {config_name}")
    values = {
        "id": 1,
        "run_id": run_id,
        "default_config_name": config_name,
        "default_config_hash": fusion.config_hash(config_name),
        "decision_ref": DECISION_REF,
    }
    stmt = insert(PublishedRun).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            k: stmt.excluded[k]
            for k in ("run_id", "default_config_name", "default_config_hash", "decision_ref")
        }
        | {"published_at": func.now()},
    )
    session.execute(stmt)
    session.flush()
    return session.get(PublishedRun, 1, populate_existing=True)


def published(session: Session) -> PublishedRun | None:
    """The current pointer: which run is served and which config is default."""
    return session.get(PublishedRun, 1)
