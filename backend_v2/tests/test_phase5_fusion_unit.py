"""Phase 5 unit tests: fusion (both configs), tiers, confidence, gate
fixtures. No database needed."""
from __future__ import annotations

import ast
import importlib.util
import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.analytics import confidence, fusion, gate

REPO_ROOT = Path(__file__).resolve().parents[2]
# The old backend was archived (not deleted) at the Phase 12 cutover.
OLD_BACKEND = REPO_ROOT / "archive" / "backend_v1" / "backend"
OLD_RISK = OLD_BACKEND / "app" / "risk"
OLD_FEATURES = OLD_BACKEND / "app" / "features"
OLD_COLS = {
    "cost_anomaly": "cost_anomaly_score",
    "near_duplicate": "description_similarity_score",
    "portfolio_concentration": "mp_concentration_score",
    "district_authority_pattern": "constituency_pattern_score",
    "temporal_anomaly": "temporal_score",
    "lifecycle_delay": "lifecycle_score",
}


def _scores(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, index=pd.Index([f"w{i}" for i in range(len(rows))], name="work_key"))
    for s in fusion.BASE_SIGNALS:
        if s not in df:
            df[s] = np.nan
    return df[list(fusion.BASE_SIGNALS)].astype(float)


def _load_old(path: Path):
    if not path.exists():
        pytest.skip(f"old engine source not present at {path} (only backend_v2 mounted)")
    spec = importlib.util.spec_from_file_location(f"old_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- weights --------------------------------------------------------------

def test_v4_base_weights_sum_to_exactly_one():
    assert sum(fusion.V4_WEIGHTS_EXACT.values()) == Fraction(1)
    assert math.fsum(fusion.V4_WEIGHTS.values()) == 1.0
    assert set(fusion.V4_WEIGHTS) == set(fusion.BASE_SIGNALS)


def test_v4_weights_keep_v3_base_proportions():
    for a in fusion.BASE_SIGNALS:
        for b in fusion.BASE_SIGNALS:
            assert fusion.V4_WEIGHTS_EXACT[a] / fusion.V4_WEIGHTS_EXACT[b] == (
                Fraction(fusion.V3_WEIGHTS[a]).limit_denominator(100)
                / Fraction(fusion.V3_WEIGHTS[b]).limit_denominator(100)
            )


def test_v3_weights_plus_pattern_sum_to_one_like_the_old_engine():
    assert math.isclose(sum(fusion.V3_WEIGHTS.values()) + fusion.V3_PATTERN_WEIGHT, 1.0)


# ---- known defect ---------------------------------------------------------

def test_known_defect_five_of_six_at_point_nine():
    """Five base signals at 0.9, cost anomaly not evaluated.

    OLD (v3-compatible): 0.9 x (0.20+0.10x4) = 0.540, plus pattern
    0.15 x 0.90 = 0.135 -> 0.675, x m(5)=1.15 -> 0.77625 -> 77.6 HIGH.
    The missing cost signal is scored as 0, so this work can never be
    CRITICAL however strong the other five are.
    NEW (v4-candidate): weighted mean over the five evaluated = 0.9,
    x 1.15 = 1.035 -> capped 100 CRITICAL.
    Evaluated-at-0 (a real 0, not missing): v3 unchanged 77.6 HIGH;
    v4 0.9 x 12/17 = 0.635 x 1.15 = 0.731 -> 73.1 HIGH (the 0 counts)."""
    s = gate.known_defect_scores()
    v3 = fusion.fuse(s, "v3-compatible")
    v4 = fusion.fuse(s, "v4-candidate")

    assert v3.at["ineligible", "risk"] == pytest.approx(77.625)
    assert v3.at["ineligible", "tier"] == "HIGH"
    assert v3.at["eligible_zero", "risk"] == pytest.approx(77.625)
    assert v3.at["ineligible", "pattern_score"] == pytest.approx(0.90)

    assert v4.at["ineligible", "risk"] == pytest.approx(100.0)
    assert v4.at["ineligible", "tier"] == "CRITICAL"
    assert v4.at["ineligible", "pre_multiplier"] == pytest.approx(0.9)
    assert v4.at["eligible_zero", "risk"] == pytest.approx(100 * 1.15 * 0.9 * 12 / 17)
    assert v4.at["eligible_zero", "tier"] == "HIGH"
    assert gate.k1_missing_signal_correct("v4-candidate")
    assert not gate.k1_missing_signal_correct("v3-compatible")


def test_ineligible_signal_is_excluded_from_the_v4_denominator_and_lowers_confidence():
    """Confidence-not-zero: the not-evaluated signal is dropped from v4's
    denominator (not scored 0), and shows up as lower -- but non-zero --
    confidence instead."""
    full = {s: 0.6 for s in fusion.BASE_SIGNALS}
    missing = {**full, "near_duplicate": np.nan}
    s = _scores([full, missing])
    v4 = fusion.fuse(s, "v4-candidate")
    assert v4.at["w1", "pre_multiplier"] == pytest.approx(0.6)  # 0.6 over the 5 evaluated, not 0.6 x 13/17
    assert v4.at["w1", "n_eligible"] == 5

    results = {
        sig: pd.DataFrame(
            {"eligible": s[sig].notna(), "score": s[sig], "reliability": 0.8, "dispersion": 0.3}
        )
        for sig in fusion.BASE_SIGNALS
    }
    ctx = pd.DataFrame({"level": ["L1", "L1"]}, index=s.index)
    dq = pd.DataFrame(False, index=s.index, columns=list(confidence.DQ_FLAGS))
    conf = confidence.compute_confidence(results, ctx, dq, s.index)
    assert 0 < conf.at["w1", "confidence"] < conf.at["w0", "confidence"]
    assert conf.at["w1", "completeness"] == pytest.approx(5 / 6)
    assert conf.at["w1", "not_evaluated"] == ["near_duplicate"]


def test_nothing_evaluated_is_not_evaluated_under_v4_and_zero_under_v3():
    s = _scores([{}])
    v4 = fusion.fuse(s, "v4-candidate")
    v3 = fusion.fuse(s, "v3-compatible")
    assert pd.isna(v4.at["w0", "risk"]) and v4.at["w0", "tier"] == "NOT_EVALUATED"
    assert v3.at["w0", "risk"] == 0.0 and v3.at["w0", "tier"] == "LOW"


def test_not_evaluated_is_never_counted_active():
    s = _scores([{"cost_anomaly": 0.95}])
    for c in fusion.CONFIGS:
        assert fusion.fuse(s, c).at["w0", "k"] == 1


# ---- faithful v3 ----------------------------------------------------------

def test_v3_compatible_reproduces_the_old_engine_exactly():
    """Differential test against the untouched old code in backend/app:
    random scores (with NaN = missing) through the OLD PatternEngine ->
    SignalFusionEngine -> CorroborationEngine and through fusion.fuse
    must give the same risk, base-signal count and multiplier."""
    patterns = _load_old(OLD_FEATURES / "patterns.py")
    sf = _load_old(OLD_RISK / "signal_fusion.py")
    corr = _load_old(OLD_RISK / "corroboration.py")
    assert fusion.CORROBORATION == corr.CORROBORATION_LEVELS
    assert fusion.ACTIVE_THRESHOLD == sf.ACTIVE_THRESHOLD

    rng = np.random.default_rng(7)
    n = 3000
    raw = rng.choice([0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.9, 1.0, np.nan], size=(n, 6))
    raw = np.where(rng.random((n, 6)) < 0.5, rng.random((n, 6)), raw)
    new = pd.DataFrame(raw, columns=list(fusion.BASE_SIGNALS))
    old = pd.DataFrame({OLD_COLS[s]: new[s] for s in fusion.BASE_SIGNALS})

    old = patterns.PatternEngine().compute_signals(old)
    old = sf.SignalFusionEngine().compute_risk_scores(old)
    old = corr.CorroborationEngine().compute_corroboration(old)

    f = fusion.fuse(new, "v3-compatible")
    np.testing.assert_allclose(f["pattern_score"].to_numpy(), old["pattern_score"].to_numpy(), atol=1e-12)
    np.testing.assert_allclose(f["risk"].to_numpy(), 100 * old["risk_score"].to_numpy(), atol=1e-9)
    np.testing.assert_array_equal(f["k"].to_numpy(), old["base_signal_count"].to_numpy())
    np.testing.assert_allclose(f["m"].to_numpy(), old["corroboration_multiplier"].to_numpy())

    # Old risk_engine tiers (thresholds 0.85/0.65/0.40 on 0-1, CRITICAL
    # needs >= 3 base signals), replicated here because risk_engine.py
    # imports the old package's config and cannot be loaded standalone.
    r = old["risk_score"]
    lvl = pd.Series("LOW", index=old.index)
    lvl[r >= 0.40] = "MODERATE"
    lvl[r >= 0.65] = "HIGH"
    lvl[r >= 0.85] = "CRITICAL"
    lvl[(lvl == "CRITICAL") & (old["base_signal_count"] < 3)] = "HIGH"
    np.testing.assert_array_equal(f["tier"].to_numpy(), lvl.to_numpy())


# ---- tiers ----------------------------------------------------------------

@pytest.mark.parametrize("risk,k,expected", gate.TIER_FIXTURES)
def test_tier_boundaries(risk, k, expected):
    tier, _ = fusion.assign_tiers(pd.Series([risk]), pd.Series([k]))
    assert tier.iloc[0] == expected


def test_tier_thresholds_are_the_v3_values():
    assert dict(fusion.TIER_THRESHOLDS) == {"CRITICAL": 85.0, "HIGH": 65.0, "MODERATE": 40.0}
    assert fusion.MIN_CRITICAL_SIGNALS == 3


# ---- properties -----------------------------------------------------------

score_or_missing = st.one_of(st.none(), st.floats(0, 1))


@settings(max_examples=300, deadline=None)
@given(st.lists(score_or_missing, min_size=6, max_size=6), st.integers(0, 5), st.floats(0, 1))
def test_raising_one_evaluated_score_never_lowers_risk(vals, idx, bump):
    row = {s: (np.nan if v is None else v) for s, v in zip(fusion.BASE_SIGNALS, vals)}
    sig = fusion.BASE_SIGNALS[idx]
    if pd.isna(row[sig]):
        row[sig] = 0.0
    higher = {**row, sig: max(row[sig], bump)}
    s = _scores([row, higher])
    for c in fusion.CONFIGS:
        f = fusion.fuse(s, c)
        assert f.at["w1", "risk"] >= f.at["w0", "risk"] - 1e-9


@settings(max_examples=200, deadline=None)
@given(st.lists(score_or_missing, min_size=6, max_size=6))
def test_risk_is_bounded_and_null_only_when_nothing_evaluated(vals):
    row = {s: (np.nan if v is None else v) for s, v in zip(fusion.BASE_SIGNALS, vals)}
    s = _scores([row])
    for c in fusion.CONFIGS:
        f = fusion.fuse(s, c)
        r = f.at["w0", "risk"]
        if pd.isna(r):
            assert c == "v4-candidate" and s.notna().sum(axis=1).iloc[0] == 0
        else:
            assert 0.0 <= r <= 100.0


# ---- reproducibility ------------------------------------------------------

def test_same_input_reproduces_the_same_hash_regardless_of_row_order():
    rng = np.random.default_rng(3)
    raw = np.where(rng.random((500, 6)) < 0.1, np.nan, rng.random((500, 6)))
    s = pd.DataFrame(raw, columns=list(fusion.BASE_SIGNALS),
                     index=pd.Index([f"k{i}" for i in range(500)], name="work_key"))
    conf = pd.Series(rng.random(500), index=s.index)
    h1 = fusion.risk_output_hash({c: fusion.fuse(s, c) for c in fusion.CONFIGS}, conf)
    h2 = fusion.risk_output_hash({c: fusion.fuse(s, c) for c in fusion.CONFIGS}, conf)
    perm = s.sample(frac=1.0, random_state=1)
    h3 = fusion.risk_output_hash({c: fusion.fuse(perm, c) for c in fusion.CONFIGS}, conf.loc[perm.index])
    assert h1 == h2 == h3
    s2 = s.copy()
    s2.iloc[0, 0] = 0.123456
    h4 = fusion.risk_output_hash({c: fusion.fuse(s2, c) for c in fusion.CONFIGS}, conf)
    assert h4 != h1


def test_config_hashes_are_distinct_and_stable():
    assert fusion.config_hash("v3-compatible") != fusion.config_hash("v4-candidate")
    assert fusion.fusion_config_hash() == fusion.fusion_config_hash()


# ---- separation of risk and confidence --------------------------------------

def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_confidence_never_reads_a_signal_score():
    src = Path(confidence.__file__)
    consts = _string_constants(src)
    assert "score" not in consts and "tail_percentile" not in consts


def test_fusion_never_reads_reliability_or_dispersion():
    consts = _string_constants(Path(fusion.__file__))
    assert "reliability" not in consts and "dispersion" not in consts


# ---- gate helpers ---------------------------------------------------------

def test_top_n_breaks_ties_at_the_cap_deterministically():
    s = _scores([{sig: 1.0 for sig in fusion.BASE_SIGNALS}] * 5)
    f = fusion.fuse(s, "v4-candidate")
    assert list(gate.top_n(f, 3)) == ["w0", "w1", "w2"]


def test_sensitivity_and_ablation_run_on_a_small_population():
    rng = np.random.default_rng(5)
    raw = np.where(rng.random((300, 6)) < 0.1, np.nan, rng.random((300, 6)))
    s = pd.DataFrame(raw, columns=list(fusion.BASE_SIGNALS),
                     index=pd.Index([f"k{i}" for i in range(300)], name="work_key"))
    for c in fusion.CONFIGS:
        base = fusion.fuse(s, c)
        sens = gate.sensitivity(s, c, base)
        assert 0 <= sens["min"] <= sens["median"] <= 1
        abl = gate.ablation(s, c, base)
        assert len(abl) >= 7
