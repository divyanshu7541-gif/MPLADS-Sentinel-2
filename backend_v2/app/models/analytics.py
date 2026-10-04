"""
Run-scoped analytics tables (BLUEPRINT.md §4 "Analytics (append-only,
keyed by run)"): analysis_run, peer_group, work_context, signal_result,
risk_result, compliance_result.

Peer groups are NOT segmented by House -- a work-type x state x FY group
mixes Lok Sabha and Rajya Sabha works, because that's the statistically
more valid comparison (Phase 3 brief). House is carried on work_context as
an indexed column purely so downstream queries can FILTER which works are
displayed; it never shapes a baseline. The same rule carries into Phase 4's
signal_result: every signal is computed from the peer/entity population as
a whole (both Houses together); `house` is copied onto each row only so a
`house` read-time filter can select which rows are returned, exactly like
work_context (Phase 4 brief's "House-neutrality" requirement).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    output_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class PeerGroup(Base):
    """One row per (run, level, group_key) at every level, used or not --
    group-level composition only. No median lives here: every baseline is
    leave-one-out and therefore per-work (see WorkContext)."""

    __tablename__ = "peer_group"
    __table_args__ = (UniqueConstraint("run_id", "level", "group_key", name="uq_peer_group_run_level_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(3), nullable=False)  # L1 | L2 | L3 | REF
    group_key: Mapped[str] = mapped_column(String(200), nullable=False)
    n_works: Mapped[int] = mapped_column(Integer, nullable=False)
    n_usable: Mapped[int] = mapped_column(Integer, nullable=False)
    n_mps: Mapped[int] = mapped_column(Integer, nullable=False)
    top_mp_share: Mapped[float | None] = mapped_column(Float)
    n_ls: Mapped[int] = mapped_column(Integer, nullable=False)
    n_rs: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkContext(Base):
    """One row per (run, work). Every count and statistic excludes the
    work itself (leave-one-out)."""

    __tablename__ = "work_context"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", name="uq_work_context_run_work"),
        Index("ix_work_context_run_house", "run_id", "house"),
        Index("ix_work_context_run_level", "run_id", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)

    amount_used: Mapped[float | None] = mapped_column(Numeric(20, 4))
    amount_basis: Mapped[str | None] = mapped_column(String(10))  # actual | sanction
    sanction_fy: Mapped[str | None] = mapped_column(String(7))

    level: Mapped[str | None] = mapped_column(String(3))  # L1 | L2 | L3, NULL = no qualifying group
    group_key: Mapped[str | None] = mapped_column(String(200))
    n_usable_excl_self: Mapped[int | None] = mapped_column(Integer)
    distinct_other_mps: Mapped[int | None] = mapped_column(Integer)
    max_mp_share: Mapped[float | None] = mapped_column(Float)
    peer_median: Mapped[float | None] = mapped_column(Numeric(20, 4))
    peer_q25: Mapped[float | None] = mapped_column(Numeric(20, 4))
    peer_q75: Mapped[float | None] = mapped_column(Numeric(20, 4))
    peer_iqr: Mapped[float | None] = mapped_column(Numeric(20, 4))
    peer_mad: Mapped[float | None] = mapped_column(Numeric(20, 4))

    # Level-1 composition recorded for every work (even ones that fell
    # back to L2/L3) so BLUEPRINT.md §6's coverage figures are reproducible
    # straight from this table.
    l1_n_usable_excl_self: Mapped[int | None] = mapped_column(Integer)
    l1_distinct_other_mps: Mapped[int | None] = mapped_column(Integer)
    l1_distinct_mps_in_peers: Mapped[int | None] = mapped_column(Integer)
    l1_distinct_mps_in_group: Mapped[int | None] = mapped_column(Integer)  # own MP included; descriptive only

    # Refinement (work type x district x FY) -- only populated when that
    # group itself passes the same capped rule as L1-L3; additional
    # context, never replaces `level`.
    ref_group_key: Mapped[str | None] = mapped_column(String(200))
    ref_n_usable_excl_self: Mapped[int | None] = mapped_column(Integer)
    ref_distinct_other_mps: Mapped[int | None] = mapped_column(Integer)
    ref_max_mp_share: Mapped[float | None] = mapped_column(Float)
    ref_median: Mapped[float | None] = mapped_column(Numeric(20, 4))
    ref_iqr: Mapped[float | None] = mapped_column(Numeric(20, 4))
    ref_mad: Mapped[float | None] = mapped_column(Numeric(20, 4))


# The six Phase 4 base signals (BLUEPRINT.md §6 "Signal catalogue"). All are
# is_base=True; the derived 7th row ("corroboration") is Phase 5's, not
# written here.
SIGNALS = (
    "cost_anomaly",
    "near_duplicate",
    "portfolio_concentration",
    "district_authority_pattern",
    "temporal_anomaly",
    "lifecycle_delay",
)
DIRECTIONS = ("above", "below")


class SignalResult(Base):
    """One row per (run, work, signal) -- BLUEPRINT.md §4. `score` and
    `tail_percentile` are the anomaly evidence a fused risk score will read
    later (Phase 5+); `reliability` and `dispersion` are quality inputs for
    Phase 5's *confidence* only and must never be read by anything computing
    a score (Phase 4 brief's corrections list / acceptance criteria).

    A signal with insufficient inputs gets `eligible=False` and
    `score=None` -- "not evaluated", never a score of 0
    (BLUEPRINT.md §6 confidence)."""

    __tablename__ = "signal_result"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", "signal", name="uq_signal_result_run_work_signal"),
        Index("ix_signal_result_run_signal", "run_id", "signal"),
        Index("ix_signal_result_run_house", "run_id", "house"),
        CheckConstraint(f"signal IN {SIGNALS}", name="ck_signal_result_signal"),
        CheckConstraint(f"direction IN {DIRECTIONS}", name="ck_signal_result_direction"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="ck_signal_result_score_range"),
        CheckConstraint(
            "tail_percentile IS NULL OR (tail_percentile >= 0 AND tail_percentile <= 1)",
            name="ck_signal_result_tail_range",
        ),
        CheckConstraint(
            "reliability IS NULL OR (reliability >= 0 AND reliability <= 1)",
            name="ck_signal_result_reliability_range",
        ),
        CheckConstraint("NOT eligible OR score IS NOT NULL", name="ck_signal_result_eligible_has_score"),
        CheckConstraint("eligible OR score IS NULL", name="ck_signal_result_ineligible_no_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)  # display filter only, see module docstring

    signal: Mapped[str] = mapped_column(String(40), nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)

    score: Mapped[float | None] = mapped_column(Float)  # 0..1, NULL means "not evaluated"
    tail_percentile: Mapped[float | None] = mapped_column(Float)  # 0..1
    direction: Mapped[str | None] = mapped_column(String(10))  # 'above' | 'below' | NULL

    # Quality-only, feeding Phase 5 confidence -- never a multiplicand of `score`.
    reliability: Mapped[float | None] = mapped_column(Float)  # 0..1
    dispersion: Mapped[float | None] = mapped_column(Float)  # raw scale, signal-specific units

    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)


# Phase 5 fusion configurations (BLUEPRINT.md §6 "Fusion and corroboration"),
# run side by side and never overwriting each other: both rows exist for
# every (run, work).
FUSION_CONFIGS = ("v3-compatible", "v4-candidate")
TIERS = ("LOW", "MODERATE", "HIGH", "CRITICAL", "NOT_EVALUATED")


class RiskResult(Base):
    """One row per (run, work, config_name) -- BLUEPRINT.md §4 risk_result,
    keyed additionally by config so v3-compatible and v4-candidate are both
    kept for the G3 comparison. `risk` is 0-100 anomaly only; `confidence`
    is 0-1 data/context quality only and is never multiplied into `risk`.

    `risk IS NULL` <=> tier 'NOT_EVALUATED': a work with no evaluable base
    signal has no risk under v4-candidate (it is not "risk 0"). The
    v3-compatible config reproduces v3 faithfully, where a missing signal
    counted as 0, so it always has a numeric risk."""

    __tablename__ = "risk_result"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", "config_name", name="uq_risk_result_run_work_config"),
        Index("ix_risk_result_run_config_tier_risk", "run_id", "config_name", "tier", "risk"),
        Index("ix_risk_result_run_house", "run_id", "house"),
        CheckConstraint(f"config_name IN {FUSION_CONFIGS}", name="ck_risk_result_config"),
        CheckConstraint(f"tier IN {TIERS}", name="ck_risk_result_tier"),
        CheckConstraint("risk IS NULL OR (risk >= 0 AND risk <= 100)", name="ck_risk_result_risk_range"),
        CheckConstraint(
            "(risk IS NULL) = (tier = 'NOT_EVALUATED')", name="ck_risk_result_null_iff_not_evaluated"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_risk_result_confidence_range"),
        CheckConstraint("n_eligible >= 0 AND n_eligible <= 6", name="ck_risk_result_n_eligible"),
        CheckConstraint(
            "base_signal_count >= 0 AND base_signal_count <= n_eligible",
            name="ck_risk_result_active_le_eligible",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)  # display filter only
    config_name: Mapped[str] = mapped_column(String(20), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    risk: Mapped[float | None] = mapped_column(Float)  # 0..100
    tier: Mapped[str] = mapped_column(String(15), nullable=False)
    critical_demoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_signal_count: Mapped[int] = mapped_column(Integer, nullable=False)  # active base signals (k)
    n_eligible: Mapped[int] = mapped_column(Integer, nullable=False)
    corroboration_factor: Mapped[float] = mapped_column(Float, nullable=False)  # m(k)
    pattern_score: Mapped[float | None] = mapped_column(Float)  # v3-compatible only
    pre_multiplier: Mapped[float | None] = mapped_column(Float)  # 0..1 weighted sum before m(k)

    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_components: Mapped[dict] = mapped_column(JSONB, nullable=False)


COMPLIANCE_CHECKS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9")
COMPLIANCE_SUBJECTS = ("work", "mp", "file")


class ComplianceResult(Base):
    """One row per (run, check, rule, subject) for every subject the check
    APPLIES to (BLUEPRINT.md §6 compliance panel; deterministic, outside
    the risk score). Most checks are per work; C7 is per MP (Snapshot B MP
    summary) and C9 per reconciled file, so the subject is typed rather
    than forcing a work_key onto them."""

    __tablename__ = "compliance_result"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "check_code",
            "rule",
            "subject_type",
            "subject_key",
            name="uq_compliance_result_run_check_subject",
        ),
        Index("ix_compliance_result_run_check", "run_id", "check_code", "passed"),
        Index("ix_compliance_result_run_work", "run_id", "work_key"),
        CheckConstraint(f"check_code IN {COMPLIANCE_CHECKS}", name="ck_compliance_result_check"),
        CheckConstraint(f"subject_type IN {COMPLIANCE_SUBJECTS}", name="ck_compliance_result_subject"),
        CheckConstraint(
            "(subject_type = 'work') = (work_key IS NOT NULL)", name="ck_compliance_result_work_key_iff_work"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    check_code: Mapped[str] = mapped_column(String(3), nullable=False)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)  # sub-rule within the check
    subject_type: Mapped[str] = mapped_column(String(5), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(300), nullable=False)
    work_key: Mapped[str | None] = mapped_column(ForeignKey("work.work_key"))
    house: Mapped[str | None] = mapped_column(String(2))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)


class PublishedRun(Base):
    """Single-row pointer (BLUEPRINT.md §4 `published_run`): which analysis
    run the system serves and which fusion configuration is the DEFAULT.
    Phase 6 records the owner's Phase 5 decision here (v4-candidate) so the
    choice is queryable in code, not only in docs/phase5_gate_report_v3.md.
    Nothing in the API reads risk_result by config yet."""

    __tablename__ = "published_run"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_published_run_single_row"),
        CheckConstraint(f"default_config_name IN {FUSION_CONFIGS}", name="ck_published_run_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    default_config_name: Mapped[str] = mapped_column(String(20), nullable=False)
    default_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_ref: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


MODEL_STATUSES = ("active", "inactive_experiment")


class ModelVersion(Base):
    """Model registry (BLUEPRINT.md §4 / §7 Governance): algorithm, feature
    specification hash, training snapshot, seed, parameters, metrics and
    artifact hash for every fitted model."""

    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("run_id", "model_name", name="uq_model_version_run_model"),
        CheckConstraint(f"status IN {MODEL_STATUSES}", name="ck_model_version_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(60), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(120), nullable=False)
    feature_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    training_snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshot.id"), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "active" = its outputs are live evidence; "inactive_experiment" = fitted and
    # validated, kept for the record, produces no stored outputs (Phase 7 A1).
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="active")
    status_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


ATYPICALITY_METHODS = ("robust_mahalanobis", "isolation_forest")


class AtypicalityResult(Base):
    """BLUEPRINT.md §7 B4 multivariate atypicality: EVIDENCE ONLY. A separate
    table from signal_result on purpose -- Phase 5 fusion reads signal_result's
    six base signals only, so nothing here can enter risk, the active-signal
    count or corroboration (gate G6). `eligible=False` <=> `score IS NULL`:
    a work with any missing feature is "not evaluated", never scored 0."""

    __tablename__ = "atypicality_result"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", "method", name="uq_atypicality_run_work_method"),
        Index("ix_atypicality_run_method", "run_id", "method"),
        CheckConstraint(f"method IN {ATYPICALITY_METHODS}", name="ck_atypicality_method"),
        CheckConstraint("(score IS NULL) = (NOT eligible)", name="ck_atypicality_null_iff_not_evaluated"),
        CheckConstraint(
            "percentile IS NULL OR (percentile >= 0 AND percentile <= 1)",
            name="ck_atypicality_percentile_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_version.id"), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)  # higher = more atypical
    percentile: Mapped[float | None] = mapped_column(Float)  # empirical rank among evaluated works
    contributions: Mapped[dict] = mapped_column(JSONB, nullable=False)  # per-feature, Mahalanobis only
    missing_features: Mapped[list] = mapped_column(JSONB, nullable=False)


EVIDENCE_FACTS = (
    "pays_same_payee_more_than_once",
    # Phase 10 (BLUEPRINT.md §8 "Metrics"): identical (payee, amount, date) rows
    # within a work -- stricter than the Phase 6 fact above (which only needs the
    # same payee more than once, any amount/date).
    "identical_payment_repeated",
    # Phase 10: four or more distinct payees paying into one work.
    "multi_payee_work",
)


class WorkEvidenceFact(Base):
    """Plain, deterministic facts about a work, shown to reviewers as evidence.
    NOT scores: they carry no number that could be ranked or fused, live
    outside signal_result, and are never read by Phase 5 fusion, the
    active-signal count or corroboration."""

    __tablename__ = "work_evidence_fact"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", "fact", name="uq_work_evidence_fact_run_work_fact"),
        Index("ix_work_evidence_fact_run_fact", "run_id", "fact"),
        CheckConstraint(f"fact IN {EVIDENCE_FACTS}", name="ck_work_evidence_fact_fact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    fact: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)


FORECAST_MODELS = ("a1_cox", "a2_day90", "a2_day180")


class ForecastResult(Base):
    """Phase 7 (BLUEPRINT.md §7 A1/A2): per-work survival evidence. Evidence
    only -- never read by Phase 5 fusion. `event`/`duration_days` record the
    censoring decision (event 0 = right-censored at the cutoff); a work
    without a usable time origin is eligible=False with no prediction.
    Rows are written only on an explicit --register run (owner sign-off).
    Named `forecast_result` per SENTINEL_REBUILD_PLAN_v2.md §7 / BLUEPRINT.md §4
    (created as survival_result, renamed at the Phase 7 close-out)."""

    __tablename__ = "forecast_result"
    __table_args__ = (
        UniqueConstraint("run_id", "work_key", "model", name="uq_forecast_result_run_work_model"),
        Index("ix_forecast_result_run_model", "run_id", "model"),
        CheckConstraint(f"model IN {FORECAST_MODELS}", name="ck_forecast_result_model"),
        CheckConstraint(
            "eligible OR prediction = '{}'::jsonb", name="ck_forecast_result_no_pred_if_ineligible"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    work_key: Mapped[str] = mapped_column(ForeignKey("work.work_key"), nullable=False)
    house: Mapped[str] = mapped_column(String(2), nullable=False)
    model: Mapped[str] = mapped_column(String(20), nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_version.id"))
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event: Mapped[int | None] = mapped_column(Integer)  # 1 completion observed, 0 censored at cutoff
    duration_days: Mapped[float | None] = mapped_column(Float)
    prediction: Mapped[dict] = mapped_column(JSONB, nullable=False)
    not_evaluated_reason: Mapped[str | None] = mapped_column(String(40))


# Phase 10 (BLUEPRINT.md §8 "Payee, agency and district analytics"): payee,
# implementing_agency, district_authority and mp_tenure profiles and
# concentration. NEVER read by risk_result / signal_result fusion (gate G6)
# -- the no-guilt-by-association test asserts risk_result has no
# payee-derived column, ever.
#
# 'concentration' rows describe an MP TENURE's or a DISTRICT AUTHORITY's own
# set of payees (BLUEPRINT.md §8 metrics table, Grain "MP tenure, district
# authority") -- entity_type is 'mp_tenure' or 'district_authority' there,
# never 'payee'. Every other metric's entity IS the payee/agency/authority
# it profiles.
ENTITY_TYPES = ("payee", "implementing_agency", "district_authority", "mp_tenure")
ENTITY_METRICS = (
    "concentration",  # Herfindahl vs. a permutation null, within (district, work type, FY) strata
    "price_position",  # payee x work-type median residual vs. level-1-style peers
    "reach",  # works/MPs/districts/amount for one payee -- descriptive
    "district_authority_profile",  # lag/backlog/payment-ageing vs. other authorities in the state
    "implementing_agency_profile",  # completion lag / price position, minimum-n gated
)


class EntityMetric(Base):
    """One row per (run, entity_type, entity_id, metric, substratum).
    `entity_id` is polymorphic (payee.id / implementing_agency.id /
    district_authority.id / tenure.id depending on entity_type) and
    deliberately carries no FK -- geo_metric's area_key sets the precedent
    for an unenforced polymorphic key in this codebase. `substratum` is only
    used by price_position, whose grain is (payee, work type): it holds the
    work type's label, else ''.

    `denominator`, `interval` and `peer_definition` are NEVER NULL (enforced
    by the Pydantic response model in app/api/entities.py, per BLUEPRINT.md
    §8 "Wording rules": every entity metric response shows its denominator,
    interval and peer definition, whether or not the metric was evaluable --
    when it wasn't, they explain why instead of being empty.

    `house` is a display filter only, exactly like signal_result/risk_result/
    work_context (see that module's docstring): the underlying population is
    never segmented by House, but a row is tagged with the single House if
    every contributing work shares it, else NULL (both)."""

    __tablename__ = "entity_metric"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "entity_type",
            "entity_id",
            "metric",
            "substratum",
            name="uq_entity_metric_run_entity_metric_substratum",
        ),
        Index("ix_entity_metric_run_entity", "run_id", "entity_type", "entity_id"),
        Index("ix_entity_metric_run_metric", "run_id", "metric"),
        CheckConstraint(f"entity_type IN {ENTITY_TYPES}", name="ck_entity_metric_entity_type"),
        CheckConstraint(f"metric IN {ENTITY_METRICS}", name="ck_entity_metric_metric"),
        CheckConstraint("(value IS NULL) = (NOT eligible)", name="ck_entity_metric_null_iff_not_evaluated"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_name: Mapped[str] = mapped_column(String(300), nullable=False)  # denormalised for display
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    substratum: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    house: Mapped[str | None] = mapped_column(String(2))

    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    not_evaluated_reason: Mapped[str | None] = mapped_column(String(80))
    n: Mapped[int] = mapped_column(Integer, nullable=False)  # the metric's own sample size
    value: Mapped[float | None] = mapped_column(Float)
    percentile: Mapped[float | None] = mapped_column(Float)  # 0..1, permutation-null rank where used

    denominator: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[dict] = mapped_column(JSONB, nullable=False)
    peer_definition: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
