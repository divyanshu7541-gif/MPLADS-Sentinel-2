"""Phase 7: A1 survival / A2 365-day delay -- censoring, leakage and
determinism tests (written before the real-data run)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.analytics import fusion, survival

CUTOFF = pd.Timestamp("2026-08-30")
ANALYTICS = Path(survival.__file__).resolve().parent


def _raw(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.Index([f"w{i}" for i in range(len(df))], name="work_key")
    for c in ("sanction_date", "actual_end_date"):
        df[c] = pd.to_datetime(df[c])
    return df


# ---- censoring ------------------------------------------------------------------


def test_open_work_is_right_censored_at_the_cutoff_not_dropped_or_an_event():
    raw = _raw([{"sanction_date": "2025-01-01", "actual_end_date": None, "lifecycle_status": "sanctioned"}])
    sf = survival.survival_frame(raw, CUTOFF)
    r = sf.iloc[0]
    assert r["eligible"] and r["event"] == 0
    assert r["duration_days"] == (CUTOFF - pd.Timestamp("2025-01-01")).days


def test_completion_after_the_cutoff_is_censored_at_the_cutoff():
    raw = _raw(
        [{"sanction_date": "2026-01-01", "actual_end_date": "2026-10-01", "lifecycle_status": "completed"}]
    )
    r = survival.survival_frame(raw, CUTOFF).iloc[0]
    assert r["eligible"] and r["event"] == 0 and r["completed_after_cutoff"]
    assert r["duration_days"] == (CUTOFF - pd.Timestamp("2026-01-01")).days


def test_observed_completion_is_an_event_and_same_day_completion_is_kept():
    raw = _raw(
        [
            {"sanction_date": "2025-01-01", "actual_end_date": "2025-04-11", "lifecycle_status": "completed"},
            {"sanction_date": "2025-01-01", "actual_end_date": "2025-01-01", "lifecycle_status": "completed"},
        ]
    )
    sf = survival.survival_frame(raw, CUTOFF)
    assert list(sf["event"]) == [1, 1] and list(sf["duration_days"]) == [100, 0]


@pytest.mark.parametrize(
    "row,reason",
    [
        (
            {"sanction_date": None, "actual_end_date": None, "lifecycle_status": "sanctioned"},
            "no_sanction_date",
        ),
        (
            {"sanction_date": "2025-01-01", "actual_end_date": None, "lifecycle_status": "completed"},
            "completed_without_end_date",
        ),
        (
            {"sanction_date": "2025-05-01", "actual_end_date": "2025-01-01", "lifecycle_status": "completed"},
            "end_before_sanction",
        ),
        (
            {"sanction_date": "2026-09-10", "actual_end_date": None, "lifecycle_status": "sanctioned"},
            "sanctioned_after_cutoff",
        ),
    ],
)
def test_missing_or_impossible_dates_are_not_evaluated_never_imputed(row, reason):
    r = survival.survival_frame(_raw([row]), CUTOFF).iloc[0]
    assert not r["eligible"] and r["not_evaluated_reason"] == reason
    assert pd.isna(r["event"]) and pd.isna(r["duration_days"])


def test_keeping_censored_works_changes_kaplan_meier_the_right_way():
    """100 works: 50 finished at day 100, 50 still open at day 400. Dropping the
    open ones (the bug) would say everything finishes by day 100; censoring
    correctly leaves S(365) at 0.5."""
    rows = [
        {"sanction_date": "2025-01-01", "actual_end_date": "2025-04-11", "lifecycle_status": "completed"}
    ] * 50 + [
        {
            "sanction_date": str((CUTOFF - pd.Timedelta(days=400)).date()),
            "actual_end_date": None,
            "lifecycle_status": "sanctioned",
        }
    ] * 50
    sf = survival.survival_frame(_raw(rows), CUTOFF)
    right = survival.km(sf["duration_days"], sf["event"]).survival_function_at_times(365).iloc[0]
    kept = sf["event"] == 1
    dropped = (
        survival.km(sf.loc[kept, "duration_days"], sf.loc[kept, "event"])
        .survival_function_at_times(365)
        .iloc[0]
    )
    assert right == pytest.approx(0.5) and dropped == pytest.approx(0.0)


# ---- A2 population / labels ----------------------------------------------------------


def test_a2_uses_only_full_follow_up_cohorts_and_the_at_risk_set():
    rows = [
        # full follow-up (sanctioned 2025-01-01, > 365 days before cutoff)
        {
            "sanction_date": "2025-01-01",
            "actual_end_date": "2025-03-01",
            "lifecycle_status": "completed",
        },  # done by day 90
        {
            "sanction_date": "2025-01-01",
            "actual_end_date": "2025-09-01",
            "lifecycle_status": "completed",
        },  # 243 d -> 0
        {
            "sanction_date": "2025-01-01",
            "actual_end_date": "2026-03-01",
            "lifecycle_status": "completed",
        },  # 424 d -> 1
        {
            "sanction_date": "2025-01-01",
            "actual_end_date": None,
            "lifecycle_status": "sanctioned",
        },  # open 606 d -> 1
        # recent cohort: excluded entirely, fast finisher AND open work
        {"sanction_date": "2026-01-01", "actual_end_date": "2026-03-01", "lifecycle_status": "completed"},
        {"sanction_date": "2026-01-01", "actual_end_date": None, "lifecycle_status": "sanctioned"},
    ]
    raw = _raw(rows)
    pop = survival.a2_population(survival.survival_frame(raw, CUTOFF), raw, CUTOFF, 90)
    assert list(pop.index) == ["w1", "w2", "w3"]
    assert list(pop["label"]) == [0, 1, 1]


# ---- leakage ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "col",
    [
        "actual_end_date",
        "actual_amount",
        "lifecycle_status",
        "amount_used",
        "peer_median",
        "paid_total",
        "event",
        "duration_days",
        "label",
        "cost_anomaly",
        "lifecycle_delay",
        "risk",
        "risk_score",
        "tier",
        "confidence",
        "c5_over_one_year",
        "atypicality_score",
        "mahalanobis",
        "evidence_fact",
        "mp",
        "payee_id",
    ],
)
def test_post_event_or_hindsight_fields_raise(col):
    for stage in ("A1", "A2"):
        with pytest.raises(survival.LeakageError):
            survival.assert_allowed(list(survival.A1_BASE) + [col], stage)


def test_unlisted_columns_raise_and_payment_features_are_a2_only():
    with pytest.raises(survival.LeakageError):
        survival.assert_allowed(list(survival.A1_BASE) + ["something_new"], "A1")
    with pytest.raises(survival.LeakageError):
        survival.assert_allowed(list(survival.A1_BASE) + ["payments_by_t"], "A1")
    survival.assert_allowed(list(survival.A1_BASE) + list(survival.A2_PAYMENT) + ["type_3"], "A2")


def _feature_raw():
    raw = _raw(
        [{"sanction_date": "2025-01-01", "actual_end_date": None, "lifecycle_status": "sanctioned"}] * 2
    )
    raw["sanction_amount"] = [100000.0, 100000.0]
    raw["amount_used"] = [100000.0, 999999.0]  # the post-completion actual amount must be ignored
    raw["house"] = ["LS", "RS"]
    raw["activity_type_id"] = [3, 4]
    return raw


def test_features_use_sanction_amount_never_the_actual_amount_used():
    X = survival.a1_covariates(_feature_raw(), [3, 4])
    assert X["log_sanction_amount"].nunique() == 1
    assert not any("amount_used" in c for c in X.columns)


def test_payments_after_the_landmark_day_are_never_counted():
    raw = _feature_raw()
    pays = pd.DataFrame(
        {
            "work_key": ["w0", "w0", "w0"],
            "payment_date": ["2025-02-01", "2025-03-31", "2025-04-02"],  # days 31, 89, 91
            "amount": [10000.0, 20000.0, 50000.0],
        }
    )
    X = survival.a2_features(raw, pays, 90, [3, 4])
    assert X.at["w0", "payments_by_t"] == pytest.approx(np.log1p(2))
    assert X.at["w0", "paid_share_by_t"] == pytest.approx(0.3)
    assert X.at["w1", "any_payment_by_t"] == 0.0


# ---- models ------------------------------------------------------------------------------


def _synthetic(n=600, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        {
            "log_sanction_amount": rng.normal(12, 1, n),
            "is_rs": rng.integers(0, 2, n).astype(float),
            "type_99": 0.0,  # a work type absent from this sample: constant
        },
        index=pd.Index([f"k{i}" for i in range(n)]),
    )
    hazard = np.exp(0.8 * (X["log_sanction_amount"] - 12))
    t = rng.exponential(200 / hazard)
    c = rng.uniform(50, 700, n)
    return X, pd.Series(np.minimum(t, c), index=X.index), pd.Series((t <= c).astype(int), index=X.index)


def test_cox_is_deterministic_and_c_index_is_oriented_correctly():
    X, d, e = _synthetic()
    m1, m2 = survival.fit_cox(X, d, e), survival.fit_cox(X.copy(), d.copy(), e.copy())
    pd.testing.assert_series_equal(m1.params_, m2.params_)
    p = survival.cox_predict(m1, X, ages=d)
    assert survival.c_index(d, p["partial_hazard"], e) > 0.6  # higher hazard -> completes sooner
    assert p["s365"].between(0, 1).all() and p["s_at_age"].between(0, 1).all()


def test_a2_model_is_deterministic():
    X, d, e = _synthetic(seed=1)
    Xa = X.assign(payments_by_t=0.0, paid_share_by_t=0.0, any_payment_by_t=0.0)
    y = (d > 365).astype(int)
    a = survival.fit_a2(Xa, y).predict_proba(Xa.to_numpy(float))[:, 1]
    b = survival.fit_a2(Xa.copy(), y.copy()).predict_proba(Xa.to_numpy(float))[:, 1]
    np.testing.assert_array_equal(a, b)


# ---- evidence only -------------------------------------------------------------------------


def test_phase5_code_never_references_the_survival_layer():
    for name in ("fusion.py", "risk_run.py", "confidence.py", "signals.py", "signals_run.py", "gate.py"):
        src = (ANALYTICS / name).read_text(encoding="utf-8")
        # no import or use of the survival layer, nor of its result table
        needles = (
            "import survival", "survival.", "forecast_result", "survival_result", "survival_run", "lifelines",
        )
        for needle in needles:
            assert needle not in src, (name, needle)
    assert "survival" not in fusion.BASE_SIGNALS


def test_constant_covariates_are_dropped_not_fatal():
    X, d, e = _synthetic()
    m = survival.fit_cox(X, d, e)
    assert m.dropped_constant_ == ["type_99"]
    assert not set(m.dropped_constant_) & set(m.params_.index)


@pytest.mark.parametrize("col", ["sanction_q2", "sanction_q4", "sanction_quarter"])
def test_calendar_quarter_is_no_longer_a_covariate(col):
    """Owner's Phase 7 decision: calendar quarter encoded the cohort, not
    seasonality. It is gone from A1/A2 and now refused if reintroduced."""
    X = survival.a1_covariates(_feature_raw(), [3, 4])
    assert not any("quarter" in c or c.startswith("sanction_q") for c in X.columns)
    for stage in ("A1", "A2"):
        with pytest.raises(survival.LeakageError):
            survival.assert_allowed(list(survival.A1_BASE) + [col], stage)


# ---- end state after the owner's Phase 7 decisions (real data) ---------------------------


def test_no_phase7_output_is_live(db_session):
    from sqlalchemy import text

    if not db_session.execute(text("SELECT to_regclass('forecast_result')")).scalar():
        pytest.skip("Phase 7 migration not applied")
    assert db_session.execute(text("SELECT COUNT(*) FROM forecast_result")).scalar_one() == 0
    rows = db_session.execute(
        text(
            "SELECT model_name, status FROM model_version"
            " WHERE model_name LIKE 'a1_%' OR model_name LIKE 'a2_%'"
        )
    ).all()
    assert all(status == "inactive_experiment" for _, status in rows), rows
    assert not [n for n, _ in rows if n.startswith("a2_")], "A2 was closed, not shipped"
    phase6 = (
        db_session.execute(text("SELECT DISTINCT status FROM model_version WHERE model_name LIKE 'b4_%'"))
        .scalars()
        .all()
    )
    assert set(phase6) <= {"active"}


def test_a1_is_recorded_as_an_inactive_experiment_with_its_metrics(db_session):
    from sqlalchemy import text

    if not db_session.execute(text("SELECT to_regclass('forecast_result')")).scalar():
        pytest.skip("Phase 7 migration not applied")
    row = db_session.execute(
        text(
            "SELECT status, status_note, metrics, params FROM model_version"
            " WHERE model_name = 'a1_cox_completion_time'"
        )
    ).first()
    if row is None:
        pytest.skip("A1 not recorded yet (scripts/run_survival.py --record-inactive)")
    status, note, metrics, params = row
    assert status == "inactive_experiment" and "revisit" in note.lower()
    assert 0.5 <= metrics["test_c_index"] < 0.6
    assert not any("quarter" in c or c.startswith("sanction_q") for c in params["covariates"])
    from app.analytics import atypicality_run

    run_id = db_session.execute(text(
        "SELECT run_id FROM model_version WHERE model_name = 'a1_cox_completion_time'")).scalar_one()
    assert metrics["risk_result_md5"] == atypicality_run.risk_result_checksum(db_session, run_id)


def test_phase7_table_is_named_forecast_result_per_the_plan(db_session):
    """SENTINEL_REBUILD_PLAN_v2.md §7 / BLUEPRINT.md §4 name it forecast_result;
    the pre-rename name must be gone, and nothing live may sit in it."""
    from sqlalchemy import text

    from app.models.analytics import ForecastResult

    assert ForecastResult.__tablename__ == "forecast_result"
    if not db_session.execute(text("SELECT to_regclass('forecast_result')")).scalar():
        pytest.skip("Phase 7 migrations not applied")
    assert db_session.execute(text("SELECT to_regclass('survival_result')")).scalar() is None
    note = db_session.execute(text(
        "SELECT status_note FROM model_version WHERE model_name = 'a1_cox_completion_time'")).scalar()
    if note is not None:
        assert "forecast_result" in note and "survival_result" not in note
