"""
Phase 12 serving tables (the cutover to the protected frontend).

served_work is a per-run, denormalised read model: one row per Snapshot A
work of the run's snapshot, carrying everything the dashboard, queue,
record, analytics and MP/constituency-performance endpoints show, so each of
those endpoints is a single indexed query instead of a live join over
work / work_state / risk_result / signal_result / work_context. It is BUILT
from those tables (app/serving/build.py) and only ever READS risk_result --
the risk_result checksum is recorded before and after in serving_build.

`scored` separates the two populations the frontend shows:
  scored = TRUE   the published run's risk_result population (sanctioned or
                  completed works, both Houses) -- the ONLY rows any risk,
                  tier, queue, dashboard or analytics figure is computed over.
  scored = FALSE  recommended-only works: never scored (no sanction yet --
                  Phase 2/3 population rule), shown ONLY in the descriptive
                  stage/amount parts of MP and constituency profiles, where
                  the frontend has a "Recommended" card. They carry no tier,
                  risk or confidence (NULL), never a zero.

case_event is the append-only investigation audit trail (BLUEPRINT.md §11
"Append-only case_event, server timestamps"). The actor is NEVER taken from
the request body: since Phase 13 it is the authenticated account from the
token (app/audit/case_log.py, actor_is_placeholder=False). The flag stays
only to mark Phase 12 rows, which carried a labelled placeholder actor.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

SERVING_STATUSES = ("building", "complete", "failed")
CASE_EVENT_TYPES = ("investigation_decision", "recalculate_requested")


class ServingBuild(Base):
    """One row per run whose served_work was built. Same stale-data rule as
    Phase 9's map_build: serve the published run's build when complete,
    else the most recent complete one, labelled with its date."""

    __tablename__ = "serving_build"
    __table_args__ = (CheckConstraint(f"status IN {SERVING_STATUSES}", name="ck_serving_build_status"),)

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config_name: Mapped[str] = mapped_column(String(20), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    data_as_of: Mapped[dt.date | None] = mapped_column(Date)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    built_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServedWork(Base):
    __tablename__ = "served_work"
    __table_args__ = (
        Index("ix_served_work_run_scored_tier", "run_id", "scored", "tier"),
        Index("ix_served_work_run_scored_risk", "run_id", "scored", "risk"),
        Index("ix_served_work_run_house", "run_id", "house"),
        Index("ix_served_work_run_state", "run_id", "state"),
        Index("ix_served_work_run_mp", "run_id", "mp"),
        Index("ix_served_work_run_constituency", "run_id", "constituency"),
        Index("ix_served_work_run_desc_norm", "run_id", "mp", "description_normalized"),
        Index(
            "ix_served_work_search_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        CheckConstraint(
            "scored OR (tier IS NULL AND risk IS NULL AND confidence IS NULL)",
            name="ck_served_work_unscored_has_no_risk",
        ),
    )

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), primary_key=True)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), primary_key=True)
    scored: Mapped[bool] = mapped_column(Boolean, nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)

    mp: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    description_normalized: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(300))  # official work type (activity_type.label)
    activity_type_id: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(200))  # where the work is (Phase 9 corrected location)
    constituency: Mapped[str | None] = mapped_column(String(200))  # Lok Sabha only
    constituency_state: Mapped[str | None] = mapped_column(String(200))
    district_authority_id: Mapped[int | None] = mapped_column(Integer)
    district_authority: Mapped[str | None] = mapped_column(String(300))

    stage: Mapped[str] = mapped_column(String(20), nullable=False)  # RECOMMENDED | SANCTIONED | COMPLETED
    amount: Mapped[float | None] = mapped_column(Float)  # sanction amount (recommended amount if unscored)
    recommended_date: Mapped[dt.date | None] = mapped_column(Date)
    sanction_date: Mapped[dt.date | None] = mapped_column(Date)
    completion_date: Mapped[dt.date | None] = mapped_column(Date)
    record_date: Mapped[dt.date | None] = mapped_column(Date)  # sanction date, else recommended date

    # --- risk (scored rows only; NULL when unscored or not evaluated) ---
    tier: Mapped[str | None] = mapped_column(String(15))
    risk: Mapped[float | None] = mapped_column(Float)  # 0-100
    confidence: Mapped[float | None] = mapped_column(Float)  # 0-1
    confidence_label: Mapped[str | None] = mapped_column(String(10))
    active_signal_count: Mapped[int | None] = mapped_column(Integer)
    n_eligible: Mapped[int | None] = mapped_column(Integer)
    corroboration_factor: Mapped[float | None] = mapped_column(Float)
    pre_multiplier: Mapped[float | None] = mapped_column(Float)
    confidence_components: Mapped[dict | None] = mapped_column(JSONB)
    active_signals: Mapped[str | None] = mapped_column(Text)  # comma list, contract (old) signal names

    # --- the six base signal scores (NULL = not evaluated, never 0) ---
    s_cost_anomaly: Mapped[float | None] = mapped_column(Float)
    s_near_duplicate: Mapped[float | None] = mapped_column(Float)
    s_portfolio_concentration: Mapped[float | None] = mapped_column(Float)
    s_district_authority_pattern: Mapped[float | None] = mapped_column(Float)
    s_temporal_anomaly: Mapped[float | None] = mapped_column(Float)
    s_lifecycle_delay: Mapped[float | None] = mapped_column(Float)

    # --- peer context (Phase 3 work_context, leave-one-out) ---
    peer_level: Mapped[str | None] = mapped_column(String(3))
    peer_group_key: Mapped[str | None] = mapped_column(String(200))
    peer_group_size: Mapped[int | None] = mapped_column(Integer)
    peer_median: Mapped[float | None] = mapped_column(Float)
    peer_percentile: Mapped[float | None] = mapped_column(Float)  # 0-100, own amount among its peers
    amount_used: Mapped[float | None] = mapped_column(Float)
    amount_basis: Mapped[str | None] = mapped_column(String(10))
    deviation_ratio: Mapped[float | None] = mapped_column(Float)  # amount_used / peer_median

    search_text: Mapped[str] = mapped_column(Text, nullable=False)


class CaseEvent(Base):
    __tablename__ = "case_event"
    __table_args__ = (
        Index("ix_case_event_work", "work_key", "id"),
        CheckConstraint(f"event_type IN {CASE_EVENT_TYPES}", name="ck_case_event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(60))
    decision: Mapped[str | None] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)  # never from the request body
    actor_is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Phase 13: the authenticated account the actor came from (NULL only for
    # the Phase 12 placeholder-era rows), and a hash chain over every event
    # (tamper evidence, BLUEPRINT.md §11): event_hash = sha256(prev_hash ||
    # canonical event), prev_hash = the previous event's event_hash.
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_run.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
