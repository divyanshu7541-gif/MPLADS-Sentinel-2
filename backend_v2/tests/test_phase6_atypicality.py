"""Phase 6/8: default-config pointer (published_run) and the BLUEPRINT §7 B4
multivariate atypicality evidence layer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.analytics import atypicality, atypicality_run, fusion, publish
from app.models.analytics import AtypicalityResult, ModelVersion, PublishedRun, SignalResult, WorkContext

ANALYTICS = Path(atypicality.__file__).resolve().parent


def _X(n=600, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        rng.normal(size=(n, len(atypicality.FEATURES))),
        columns=list(atypicality.FEATURES),
        index=pd.Index([f"k{i}" for i in range(n)], name="work_key"),
    )
    X.iloc[:5] += 8.0  # a few clear outliers
    return X


def _raw(n=50) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    san = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 200, n), unit="D")
    return pd.DataFrame(
        {
            "amount_used": rng.lognormal(12, 0.4, n),
            "peer_median": rng.lognormal(12, 0.1, n),
            "recommended_date": san - pd.to_timedelta(rng.integers(1, 100, n), unit="D"),
            "sanction_date": san,
            "actual_end_date": [pd.NaT] * n,
            "lifecycle_status": ["sanctioned"] * n,
            "n_payments": rng.integers(0, 4, n),
            "n_payees": rng.integers(0, 3, n),
            "description": ["construction of cc road"] * n,
        },
        index=pd.Index([f"w{i}" for i in range(n)], name="work_key"),
    )


# ---- leakage -----------------------------------------------------------------


@pytest.mark.parametrize(
    "col",
    [
        "mp",
        "raw_mp_name",
        "mp_id",
        "member_name",
        "payee_id",
        "payee_name",
        "vendor_id",
        "risk_score",
        "risk",
        "tier",
        "score",
        "work_key",
    ],
)
def test_injecting_identity_or_risk_score_raises(col):
    X = _X()
    X[col] = 1.0
    with pytest.raises(atypicality.LeakageError):
        atypicality.assert_no_leakage(X.columns)
    with pytest.raises(atypicality.LeakageError):
        atypicality.score(X)


def test_renaming_a_feature_to_a_forbidden_name_raises():
    X = _X().rename(columns={"payment_count": "payee_id"})
    with pytest.raises(atypicality.LeakageError):
        atypicality.score(X)


def test_an_unexpected_but_innocent_column_also_raises():
    X = _X()
    X["something_else"] = 0.0
    with pytest.raises(atypicality.LeakageError):
        atypicality.score(X)


def test_feature_spec_is_clean_and_features_are_built_exactly():
    atypicality.assert_no_leakage(atypicality.FEATURES)
    X = atypicality.build_features(_raw(), pd.Timestamp("2026-08-30"))
    assert tuple(X.columns) == atypicality.FEATURES


# ---- determinism ---------------------------------------------------------------


def test_same_input_and_seed_give_identical_scores_for_both_methods():
    X = _X()
    a, b = atypicality.score(X), atypicality.score(X.copy())
    for m in atypicality.METHODS:
        pd.testing.assert_series_equal(a[m]["frame"]["score"], b[m]["frame"]["score"])
        assert a[m]["artifact_hash"] == b[m]["artifact_hash"]
    top = a["robust_mahalanobis"]["frame"]["score"].sort_values(ascending=False).index[:5]
    assert set(top) == {f"k{i}" for i in range(5)}


def test_mahalanobis_contributions_sum_to_the_squared_distance():
    fr = atypicality.score(_X())["robust_mahalanobis"]["frame"]
    for k in fr.index[:50]:
        assert sum(fr.at[k, "contributions"].values()) == pytest.approx(
            fr.at[k, "score"] ** 2, rel=1e-4, abs=1e-4
        )


# ---- missing data --------------------------------------------------------------


def test_missing_feature_is_not_evaluated_never_zero():
    X = _X()
    X.iloc[10:20, X.columns.get_loc("days_rec_to_sanction")] = np.nan
    for m, res in atypicality.score(X).items():
        fr = res["frame"]
        gone = fr.index[10:20]
        assert not fr.loc[gone, "eligible"].any()
        assert fr.loc[gone, "score"].isna().all() and fr.loc[gone, "percentile"].isna().all()
        assert all(fr.at[k, "missing_features"] == ["days_rec_to_sanction"] for k in gone)
        assert fr.loc[fr["eligible"], "score"].notna().all()


def test_missing_recommendation_date_or_description_gives_missing_features():
    raw = _raw()
    raw.loc[raw.index[:3], "recommended_date"] = pd.NaT
    raw.loc[raw.index[3], "description"] = None
    raw.loc[raw.index[4], "peer_median"] = None
    X = atypicality.build_features(raw, pd.Timestamp("2026-08-30"))
    assert X.loc[raw.index[:3], "days_rec_to_sanction"].isna().all()
    assert pd.isna(X.at[raw.index[3], "description_length"])
    assert pd.isna(X.at[raw.index[4], "log_peer_deviation_ratio"])
    assert X.loc[raw.index[5:]].notna().all().all()
    # zero payments is a real value, not missing
    assert (X.loc[raw["n_payments"] == 0, "payment_count"] == 0).all()


# ---- never counted by Phase 5 --------------------------------------------------


def test_atypicality_never_enters_fusion():
    idx = pd.Index([f"w{i}" for i in range(50)], name="work_key")
    rng = np.random.default_rng(3)
    results = {
        s: pd.DataFrame({"eligible": True, "score": rng.random(50)}, index=idx) for s in fusion.BASE_SIGNALS
    }
    base = {c: fusion.fuse(fusion.score_matrix(results, idx), c) for c in fusion.CONFIGS}
    with_atyp = dict(results)
    with_atyp["robust_mahalanobis"] = pd.DataFrame({"eligible": True, "score": 1.0}, index=idx)
    with_atyp["isolation_forest"] = pd.DataFrame({"eligible": True, "score": 1.0}, index=idx)
    S = fusion.score_matrix(with_atyp, idx)
    assert tuple(S.columns) == fusion.BASE_SIGNALS
    for c in fusion.CONFIGS:
        pd.testing.assert_frame_equal(fusion.fuse(S, c), base[c])
    assert not set(atypicality.METHODS) & set(fusion.BASE_SIGNALS)


def test_phase5_code_never_references_the_atypicality_layer():
    for name in ("fusion.py", "risk_run.py", "confidence.py", "signals.py", "signals_run.py", "gate.py"):
        src = (ANALYTICS / name).read_text(encoding="utf-8").lower()
        assert "atypicality" not in src and "isolation" not in src and "mahalanobis" not in src, name


def test_default_config_constant_is_v4():
    assert fusion.DEFAULT_CONFIG == "v4-candidate" and fusion.DEFAULT_CONFIG in fusion.CONFIGS


# ---- real data -------------------------------------------------------------------


@pytest.fixture(scope="module")
def atyp_run(db_session):
    rid = db_session.execute(select(func.max(AtypicalityResult.run_id))).scalar()
    if rid is None:
        pytest.skip("no atypicality run -- run scripts/run_atypicality.py first")
    return rid


def test_signal_result_rejects_an_atypicality_row(db_session):
    rid = db_session.execute(select(func.max(SignalResult.run_id))).scalar()
    if rid is None:
        pytest.skip("no signal_result")
    wk = db_session.execute(select(SignalResult.work_key).where(SignalResult.run_id == rid).limit(1)).scalar()
    db_session.add(
        SignalResult(
            run_id=rid,
            work_key=wk,
            house="LS",
            signal="robust_mahalanobis",
            is_base=False,
            eligible=True,
            score=0.5,
            evidence={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_published_run_marks_v4_as_default_and_is_queryable(db_session):
    row = publish.published(db_session)
    if row is None:
        pytest.skip("published_run not set -- run scripts/publish_run.py")
    assert row.default_config_name == "v4-candidate" == fusion.DEFAULT_CONFIG
    assert row.default_config_hash == fusion.config_hash("v4-candidate")
    n = db_session.execute(
        text("SELECT COUNT(*) FROM risk_result WHERE run_id = :r AND config_name = :c"),
        {"r": row.run_id, "c": row.default_config_name},
    ).scalar_one()
    assert n > 0
    assert db_session.execute(select(func.count()).select_from(PublishedRun)).scalar_one() == 1


def test_publish_rejects_an_unknown_config(db_session):
    with pytest.raises(ValueError):
        publish.publish(db_session, config_name="v5-imaginary")
    db_session.rollback()


def test_both_scores_stored_for_every_work_and_registered(db_session, atyp_run):
    n_ctx = db_session.execute(
        select(func.count()).select_from(WorkContext).where(WorkContext.run_id == atyp_run)
    ).scalar_one()
    for m in atypicality.METHODS:
        rows = db_session.execute(
            text(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE eligible),"
                " COUNT(*) FILTER (WHERE eligible AND score IS NULL)"
                " FROM atypicality_result WHERE run_id = :r AND method = :m"
            ),
            {"r": atyp_run, "m": m},
        ).one()
        assert rows[0] == n_ctx and rows[1] > 0 and rows[2] == 0
        mv = db_session.execute(
            select(ModelVersion).where(
                ModelVersion.run_id == atyp_run, ModelVersion.model_name == f"b4_atypicality_{m}"
            )
        ).scalar_one()
        assert mv.feature_spec_hash == atypicality.feature_spec_hash()
        assert mv.seed == atypicality.SEED and mv.artifact_hash and mv.training_snapshot_id


def test_risk_result_byte_identical_across_the_ml_step(db_session, atyp_run):
    now = atypicality_run.risk_result_checksum(db_session, atyp_run)
    b4 = db_session.execute(select(ModelVersion).where(
        ModelVersion.run_id == atyp_run, ModelVersion.model_name.like("b4_atypicality_%"))).scalars().all()
    assert len(b4) == len(atypicality.METHODS)  # only this layer's entries; later phases record their own
    for mv in b4:
        assert mv.metrics["risk_result_md5_before"] == mv.metrics["risk_result_md5_after"] == now


def test_no_rajya_sabha_work_is_evaluated_and_the_reason_is_recorded(db_session, atyp_run):
    ev, total, with_reason = db_session.execute(
        text(
            "SELECT COUNT(*) FILTER (WHERE eligible), COUNT(*),"
            " COUNT(*) FILTER (WHERE missing_features ? 'days_rec_to_sanction')"
            " FROM atypicality_result WHERE run_id = :r AND house = 'RS' AND method = 'robust_mahalanobis'"
        ),
        {"r": atyp_run},
    ).one()
    assert ev == 0 and total > 0 and with_reason == total


# ---- owner's fix: Mahalanobis without distinct_payee_count; repeat payments as a fact ----


def test_mahalanobis_excludes_distinct_payee_count_but_isolation_forest_keeps_all_seven():
    assert "distinct_payee_count" not in atypicality.MAHALANOBIS_FEATURES
    assert set(atypicality.MAHALANOBIS_FEATURES) == set(atypicality.FEATURES) - {"distinct_payee_count"}
    res = atypicality.score(_X())
    assert res["robust_mahalanobis"]["params"]["features"] == list(atypicality.MAHALANOBIS_FEATURES)
    assert res["isolation_forest"]["params"]["features"] == list(atypicality.FEATURES)
    fr = res["robust_mahalanobis"]["frame"]
    el = fr.index[fr["eligible"]]
    assert all(set(fr.at[k, "contributions"]) == set(atypicality.MAHALANOBIS_FEATURES) for k in el[:20])


def test_payments_equal_payees_in_the_core_no_longer_makes_the_fit_singular():
    """Regression for the real-data finding: when most works have exactly as
    many payments as payees, the old 7-feature MCD covariance was singular."""
    rng = np.random.default_rng(7)
    n = 2000
    X = _X(n, seed=7)
    pay = np.log1p(rng.choice([0, 1, 1, 1, 2], n).astype(float))
    X["payment_count"] = pay
    X["distinct_payee_count"] = pay  # identical for everyone ...
    extra = rng.random(n) < 0.15
    X.loc[extra, "payment_count"] = np.log1p(np.expm1(pay[extra]) + 2)  # ... except 15% with repeats
    met = atypicality.score(X)["robust_mahalanobis"]["metrics"]
    assert met["covariance_condition_number"] is not None and met["covariance_condition_number"] < 1e8


def test_repeat_payee_fact_is_a_fact_not_a_score():
    raw = pd.DataFrame(
        {
            "house": ["LS", "LS", "RS", "LS"],
            "n_payments": [3, 1, 2, 0],
            "n_payees": [1, 1, 2, 0],
            "max_to_one_payee": [3, 1, 1, 0],
        },
        index=pd.Index(["a", "b", "c", "d"], name="work_key"),
    )
    facts = atypicality_run.repeat_payee_facts(raw, run_id=1)
    assert list(facts["work_key"]) == ["a"]
    assert facts.iloc[0]["detail"] == {
        "n_payments": 3,
        "n_distinct_payees": 1,
        "max_payments_to_one_payee": 3,
    }
    assert not {"score", "percentile", "risk"} & set(facts.columns)


def test_phase5_code_never_references_evidence_facts():
    for name in ("fusion.py", "risk_run.py", "confidence.py", "signals.py", "signals_run.py", "gate.py"):
        src = (ANALYTICS / name).read_text(encoding="utf-8").lower()
        assert "evidence_fact" not in src and "same_payee" not in src, name


def test_repeat_payee_facts_match_payments_on_real_data(db_session, atyp_run):
    stored, expected, eligible_hits, expected_hits = db_session.execute(
        text(
            """
        WITH pp AS (SELECT work_key, payee_id, COUNT(*) c FROM payment
                    WHERE source_snapshot_id = (SELECT source_snapshot_id FROM analysis_run WHERE id = :r)
                    GROUP BY 1, 2),
             w AS (SELECT work_key FROM pp GROUP BY work_key HAVING SUM(c) > COUNT(*))
        SELECT (SELECT COUNT(*) FROM work_evidence_fact WHERE run_id = :r
                  AND fact = 'pays_same_payee_more_than_once'),
               (SELECT COUNT(*) FROM w JOIN work_context wc ON wc.work_key = w.work_key AND wc.run_id = :r),
               (SELECT COUNT(*) FROM work_evidence_fact f JOIN atypicality_result a
                  ON a.run_id = f.run_id AND a.work_key = f.work_key AND a.method = 'robust_mahalanobis'
                 WHERE f.run_id = :r AND f.fact = 'pays_same_payee_more_than_once' AND a.eligible),
               (SELECT COUNT(*) FROM w JOIN work_context wc ON wc.work_key = w.work_key AND wc.run_id = :r
                  JOIN atypicality_result a ON a.run_id = :r AND a.work_key = w.work_key
                   AND a.method = 'robust_mahalanobis' AND a.eligible)
        """
        ),
        {"r": atyp_run},
    ).one()
    assert stored == expected > 0
    # "The works the singular fit had been blind to" (Phase 6 report): repeat-payee works that
    # the robust distance evaluates. The expected count is computed live from the payment table
    # (works paying one payee more than once), intersected with the run's Mahalanobis-eligible
    # works -- not pinned, since it moves with the published run: run 1 12,173 (the Phase 6
    # report's figure), run 44 12,176 (Phase 13.y: 3 Punjab works gained a qualifying peer group
    # when 8 Punjab districts moved out of "Chandigarh"). Until Phase 13.y this count lacked the
    # fact filter and so, once Phase 10 added two more fact types, counted all three (13,659).
    assert eligible_hits == expected_hits > 0


def test_stored_mahalanobis_fit_is_well_conditioned(db_session, atyp_run):
    mv = db_session.execute(
        select(ModelVersion).where(
            ModelVersion.run_id == atyp_run, ModelVersion.model_name == "b4_atypicality_robust_mahalanobis"
        )
    ).scalar_one()
    assert mv.params["features"] == list(atypicality.MAHALANOBIS_FEATURES)
    assert mv.metrics["covariance_condition_number"] is not None
    assert mv.metrics["covariance_condition_number"] < 1e8
