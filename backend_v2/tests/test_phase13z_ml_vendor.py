"""
Phase 13.z: vendor-data and ML compliance (docs/ml_vendor_compliance_audit.md).

Pins, with evidence from code and the database, the audit's findings:
  * the ONLY vendor-derived value on the risk-score path is each work's total
    paid amount (SUM(payment.amount) per work, lifecycle_delay component B,
    BLUEPRINT §8 "Payment ahead of completion ... Lifecycle delay signal
    input"); no payee or agency identity, typing or metric reaches fusion;
  * the Phase 8 registry-completeness requirement over every model_version row;
  * the published run has its B4 models registered and scored, and no A1/A2
    output is live;
  * the A1/A2 out-of-time split is disjoint in works and in time (the split
    overlap test the audit's verification recipe re-runs).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

from app.analytics import survival

APP = Path(__file__).resolve().parents[1] / "app"
SCORE_PATH = [
    APP / "analytics" / name
    for name in (
        "signals.py",
        "signals_run.py",
        "fusion.py",
        "confidence.py",
        "risk_run.py",
        "peers.py",
        "context_run.py",
    )
]
SPLIT = pd.Timestamp("2025-03-01")


# ---- vendor data on the score path ---------------------------------------------------------


def test_the_only_vendor_table_read_on_the_score_path_is_payment_amount_per_work():
    reads = {}
    for p in SCORE_PATH:
        src = p.read_text("utf-8")
        for m in re.finditer(
            r"FROM\s+(payment|payee|payee_alias|implementing_agency|entity_metric)\b", src, re.I
        ):
            reads.setdefault(p.name, []).append(m.group(1).lower())
    assert reads == {"signals_run.py": ["payment"]}, reads
    sql = (APP / "analytics" / "signals_run.py").read_text("utf-8")
    stmt = sql[sql.index("_PAYMENT_SQL") : sql.index("_PAYMENT_SQL") + 200]
    assert "SUM(amount) AS paid_total" in stmt and "GROUP BY work_key" in stmt


def test_no_payee_or_agency_identity_reaches_signals_fusion_or_confidence():
    for p in SCORE_PATH:
        src = p.read_text("utf-8")
        for token in ("payee_id", "agency_id", "payee_type", "entity_metric", "work_evidence_fact"):
            assert token not in src, f"{p.name} reads {token}"


def test_payment_share_is_a_lifecycle_delay_input_only():
    src = (APP / "analytics" / "signals.py").read_text("utf-8")
    uses = [m.start() for m in re.finditer(r"paid_total", src)]
    assert uses, "lifecycle_delay's payment component is gone"
    body = src[src.index("def lifecycle_delay_signal") :]
    assert all(u > src.index("def lifecycle_delay_signal") for u in uses)
    assert "payment_share" in body


# ---- ML registry and live outputs ------------------------------------------------------------


def test_every_model_version_row_is_complete(db_session):
    """Plan Phase 8: every model has algorithm, feature-spec hash, training
    snapshot, seed, artifact hash and at least one recorded metric."""
    rows = (
        db_session.execute(
            text(
                "SELECT id, model_name, algorithm, feature_spec_hash, training_snapshot_id, seed, "
                "artifact_hash, metrics FROM model_version"
            )
        )
        .mappings()
        .all()
    )
    assert rows
    for r in rows:
        missing = [
            k
            for k in ("algorithm", "feature_spec_hash", "training_snapshot_id", "seed", "artifact_hash")
            if r[k] in (None, "")
        ]
        assert not missing and r["metrics"], (r["id"], r["model_name"], missing)


def test_the_published_run_has_both_b4_models_registered_and_scored(db_session):
    run = db_session.execute(text("SELECT run_id FROM published_run")).scalar_one()
    models = set(
        db_session.execute(
            text("SELECT model_name FROM model_version WHERE run_id = :r AND status = 'active'"), {"r": run}
        ).scalars()
    )
    assert {"b4_atypicality_robust_mahalanobis", "b4_atypicality_isolation_forest"} <= models
    n = db_session.execute(
        text("SELECT count(DISTINCT method) FROM atypicality_result WHERE run_id = :r"), {"r": run}
    ).scalar()
    assert n == 2


def test_no_a1_or_a2_output_is_live(db_session):
    assert db_session.execute(text("SELECT count(*) FROM forecast_result")).scalar() == 0
    live = db_session.execute(
        text("SELECT count(*) FROM model_version WHERE model_name LIKE 'a%' AND status = 'active'")
    ).scalar()
    assert live == 0


# ---- the out-of-time split -------------------------------------------------------------------


@pytest.fixture(scope="module")
def split_frame(db_session):
    run = db_session.execute(text("SELECT run_id FROM published_run")).scalar_one()
    snap, as_of = db_session.execute(
        text(
            "SELECT ar.source_snapshot_id, s.data_as_of FROM analysis_run ar "
            "JOIN source_snapshot s ON s.id = ar.source_snapshot_id WHERE ar.id = :r"
        ),
        {"r": run},
    ).one()
    raw = pd.read_sql(
        text(
            "SELECT wc.work_key, ws.sanction_date, ws.actual_end_date, ws.lifecycle_status "
            "FROM work_context wc JOIN work_state ws "
            "ON ws.work_key = wc.work_key AND ws.source_snapshot_id = :s "
            "WHERE wc.run_id = :r"
        ),
        db_session.connection(),
        params={"r": run, "s": snap},
    ).set_index("work_key")
    return raw, pd.Timestamp(as_of)


def test_out_of_time_split_is_disjoint_in_works_and_in_time(split_frame):
    raw, cutoff = split_frame
    sf = survival.survival_frame(raw, cutoff)
    ev = sf[sf["eligible"]]
    san = pd.to_datetime(raw["sanction_date"])
    train, test = ev.index[san[ev.index] < SPLIT], ev.index[san[ev.index] >= SPLIT]
    assert len(train) > 10_000 and len(test) > 10_000
    assert set(train).isdisjoint(test)
    assert san[train].max() < SPLIT <= san[test].min()
    # A2's labelled population is also split by sanction date, and its test window
    # ends where full 365-day follow-up ends (no later period scores an earlier one)
    for t in survival.LANDMARKS:
        pop = survival.a2_population(sf, raw, cutoff, t)
        tr, te = pop.index[san[pop.index] < SPLIT], pop.index[san[pop.index] >= SPLIT]
        assert set(tr).isdisjoint(te) and san[tr].max() < SPLIT <= san[te].min()
        assert san[pop.index].max() <= cutoff - pd.Timedelta(days=survival.HORIZON)
