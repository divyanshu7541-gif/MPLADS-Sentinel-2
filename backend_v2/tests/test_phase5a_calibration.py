"""Phase 5a step 3: empirical (percentile-rank) calibration of portfolio
concentration, district-authority pattern and temporal anomaly, and the
re-run gate (docs/phase5_gate_report_v2.md)."""
from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select, text

from app.analytics import fusion, gate, peers, risk_run, signals, signals_run
from app.models.analytics import RiskResult

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _latest_gate_report() -> Path | None:
    """Highest-numbered versioned gate report (phase5_gate_report_vN.md)."""
    found = [(int(m.group(1)), p) for p in DOCS.glob("phase5_gate_report_v*.md")
             if (m := re.fullmatch(r"phase5_gate_report_v(\d+)\.md", p.name))]
    return max(found)[1] if found else None


# ---- the mapping --------------------------------------------------------------

def test_empirical_score_is_the_share_strictly_less_extreme():
    s = pd.Series([0.0, 0.0, 1.0, 2.0, 2.0, 5.0, np.nan], index=list("abcdefg"))
    got = signals.empirical_tail_score(s)
    assert got["a"] == got["b"] == 0.0        # minimum -> 0 (ties share the lower score)
    assert got["c"] == pytest.approx(2 / 6)
    assert got["d"] == got["e"] == pytest.approx(3 / 6)
    assert got["f"] == pytest.approx(5 / 6)
    assert pd.isna(got["g"])                  # not evaluated stays not evaluated


def test_empirical_score_is_deterministic_order_independent_and_monotone():
    rng = np.random.default_rng(1)
    s = pd.Series(np.abs(rng.standard_t(2, 5000)), index=[f"k{i}" for i in range(5000)])
    a = signals.empirical_tail_score(s)
    b = signals.empirical_tail_score(s)
    shuffled = signals.empirical_tail_score(s.sample(frac=1.0, random_state=3))
    pd.testing.assert_series_equal(a, b)
    pd.testing.assert_series_equal(a, shuffled.reindex(a.index))
    order = s.sort_values().index
    assert (np.diff(a[order].to_numpy()) >= 0).all()
    # calibrated: whatever the (heavy-tailed) distribution, ~10% score >= 0.9
    assert abs((a >= 0.9).mean() - 0.10) < 0.005
    assert a.between(0.0, 1.0).all()


def test_float_noise_does_not_split_a_tie():
    s = pd.Series([0.1 + 0.2, 0.3, 0.7])  # 0.30000000000000004 vs 0.3
    got = signals.empirical_tail_score(s)
    assert got.iloc[0] == got.iloc[1] == 0.0


def _frame(n=240, seed=0):
    rng = np.random.default_rng(seed)
    mps = [f"MP{i}" for i in range(12)]
    idx = pd.Index([f"w{i}" for i in range(n)], name="work_key")
    df = pd.DataFrame({
        "house": ["LS"] * n, "mp": [mps[i % 12] for i in range(n)], "state_id": [1] * n,
        "district_authority_id": pd.array([1 + i % 8 for i in range(n)], dtype="Int64"),
        "district_key": ["D1"] * n, "activity_type_id": rng.integers(1, 6, n),
        "sanction_fy": ["2024-25"] * n,
        "lifecycle_status": ["completed" if i % 2 else "sanctioned" for i in range(n)],
        "sanction_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 200, n), unit="D"),
        "amount": rng.lognormal(12, 0.5, n), "amount_basis": ["sanction"] * n, "is_usable": [True] * n,
        "paid_total": rng.uniform(0, 1, n) * 100000.0, "description_normalized": ["road work"] * n,
        "recommended_date": pd.to_datetime("2023-12-01") + pd.to_timedelta(rng.integers(0, 200, n), unit="D"),
        "actual_end_date": pd.Series([pd.NaT] * n, index=idx),
    }, index=idx)
    done = df["lifecycle_status"] == "completed"
    df.loc[done, "actual_end_date"] = df.loc[done, "sanction_date"] + pd.to_timedelta(
        rng.integers(30, 400, int(done.sum())), unit="D")
    return df


AS_OF = pd.Timestamp("2026-01-01")


@pytest.mark.parametrize("name,fn", [
    ("cost_anomaly", lambda f: signals.cost_anomaly_signal(f, peers.build_context(f))),
    ("portfolio_concentration", signals.portfolio_concentration_signal),
    ("district_authority_pattern", signals.district_authority_pattern_signal),
    ("temporal_anomaly", signals.temporal_anomaly_signal),
    ("lifecycle_delay", lambda f: signals.lifecycle_delay_signal(f, peers.build_context(f), AS_OF)),
])
def test_calibrated_signals_score_is_the_empirical_rank_of_their_own_statistic(name, fn):
    f = _frame()
    r = fn(f)
    el = r.index[r["eligible"]]
    assert len(el) > 0
    stat = risk_run.rank_statistic(name, r.loc[el, "evidence"])
    expected = signals.empirical_tail_score(stat)
    np.testing.assert_allclose(r.loc[el, "score"].astype(float), expected.reindex(el), atol=1e-12)
    # same input -> same output; row order irrelevant
    again = fn(f.sample(frac=1.0, random_state=9)).reindex(f.index)
    pd.testing.assert_series_equal(r["score"].astype(float), again["score"].astype(float))


def test_signals_output_hash_is_deterministic_after_calibration():
    f = _frame(seed=4)
    three = {
        "portfolio_concentration": signals.portfolio_concentration_signal,
        "district_authority_pattern": signals.district_authority_pattern_signal,
        "temporal_anomaly": signals.temporal_anomaly_signal,
    }
    base = {s: signals._empty_result(f.index) for s in signals_run.SIGNAL_ORDER}

    def run(frame):
        res = dict(base)
        res.update({s: fn(frame).reindex(f.index) for s, fn in three.items()})
        return signals_run.signals_output_hash(res)
    assert run(f) == run(f) == run(f.sample(frac=1.0, random_state=2))


def test_v4_weights_still_sum_to_exactly_one():
    assert sum(fusion.V4_WEIGHTS_EXACT.values()) == Fraction(1)
    assert math.fsum(fusion.V4_WEIGHTS.values()) == 1.0


def test_known_defect_numbers_unchanged_by_calibration():
    """5 signals at 0.9, cost not evaluated: v3 77.625 HIGH, v4 100 CRITICAL;
    cost evaluated at 0: v3 77.625 HIGH, v4 73.06 HIGH (as documented in
    Phase 5). Calibration changes signal inputs only, not these formulas."""
    kd = gate.known_defect_table().set_index(["config", "case"])
    assert kd.at[("v3-compatible", "ineligible"), "risk"] == pytest.approx(77.625)
    assert kd.at[("v3-compatible", "ineligible"), "tier"] == "HIGH"
    assert kd.at[("v3-compatible", "eligible_zero"), "risk"] == pytest.approx(77.625)
    assert kd.at[("v4-candidate", "ineligible"), "risk"] == pytest.approx(100.0)
    assert kd.at[("v4-candidate", "ineligible"), "tier"] == "CRITICAL"
    assert kd.at[("v4-candidate", "eligible_zero"), "risk"] == pytest.approx(100 * 1.15 * 0.9 * 12 / 17)
    assert kd.at[("v4-candidate", "eligible_zero"), "tier"] == "HIGH"


def test_gate_items_cover_all_eight_with_3_and_5_descriptive():
    assert [n for n, _, _ in gate.GATE_ITEMS] == list(range(1, 9))
    keys = {n: k for n, _, k in gate.GATE_ITEMS}
    assert keys[3] == keys[5] == gate.DESCRIPTIVE
    assert keys[8] is None
    assert not hasattr(gate, "O1_MAX_SHARE_K5_PLUS") and not hasattr(gate, "A1_MIN_TOP_OVERLAP")


# ---- real data ------------------------------------------------------------------

@pytest.fixture(scope="module")
def cal_run(db_session):
    rid = db_session.execute(select(func.max(RiskResult.run_id))).scalar()
    if rid is None:
        pytest.skip("no risk_result run")
    notes = db_session.execute(text("SELECT notes FROM analysis_run WHERE id = :r"), {"r": rid}).scalar()
    if f"phase5_signal_calibration={signals.SIGNAL_CALIBRATION}" not in (notes or ""):
        pytest.skip("latest risk run predates the Phase 5a calibration")
    return rid


@pytest.mark.parametrize("name", signals.EMPIRICAL_SIGNALS)
def test_stored_scores_are_the_empirical_rank_on_real_data(db_session, cal_run, name):
    rows = db_session.execute(text(
        "SELECT work_key, score, evidence FROM signal_result WHERE run_id = :r AND signal = :s AND eligible"
    ), {"r": cal_run, "s": name}).all()
    keys = [k for k, _, _ in rows]
    stored = pd.Series([sc for _, sc, _ in rows], index=keys)
    stat = risk_run.rank_statistic(name, pd.Series([ev for _, _, ev in rows], index=keys))
    np.testing.assert_allclose(stored.to_numpy(), signals.empirical_tail_score(stat).to_numpy(), atol=1e-12)
    assert (stored >= 0.9).mean() <= 0.10 + 1e-9  # ties share the lower score, never inflate


def test_missing_signal_rule_holds_on_recalibrated_real_inputs(db_session, cal_run):
    """Known-defect behaviour on real recalibrated inputs: for every work
    with a not-evaluated signal, stored v4 pre-multiplier = weighted mean
    over its EVALUATED signals only, and v3's = fixed-denominator sum."""
    data = risk_run.load_run(db_session, cal_run)
    s = data["scores"]
    miss = s.index[s.isna().any(axis=1) & s.notna().any(axis=1)]
    assert len(miss) > 1000
    stored = pd.read_sql(text(
        "SELECT work_key, config_name, pre_multiplier FROM risk_result WHERE run_id = :r"),
        db_session.connection(), params={"r": cal_run}).pivot(index="work_key", columns="config_name",
                                                             values="pre_multiplier")
    w4 = pd.Series(fusion.V4_WEIGHTS)[list(fusion.BASE_SIGNALS)]
    sub = s.loc[miss]
    act = sub.gt(fusion.ACTIVE_THRESHOLD)
    v4 = (sub.fillna(0) * act * w4).sum(axis=1) / (sub.notna() * w4).sum(axis=1)
    np.testing.assert_allclose(stored.loc[miss, "v4-candidate"], v4.clip(0, 1), atol=1e-9)
    w3 = pd.Series(fusion.V3_WEIGHTS)[list(fusion.BASE_SIGNALS)]
    base3 = (sub.fillna(0) * act * w3).sum(axis=1)
    assert (stored.loc[miss, "v3-compatible"] >= base3 - 1e-9).all()  # plus pattern term, never less


def test_latest_gate_report_fixtures_pass_and_items_are_scored():
    latest = _latest_gate_report()
    if latest is None:
        pytest.skip("no versioned gate report generated yet")
    body = latest.read_text(encoding="utf-8")
    assert "signal_calibration=" + signals.SIGNAL_CALIBRATION in body
    fixture_rows = re.findall(r"^\| [^|]+ \| \d+ \| (\w+) \| (\w+) \| (PASS|FAIL) \|$", body, flags=re.M)
    assert len(fixture_rows) == len(gate.TIER_FIXTURES)
    assert all(r[2] == "PASS" for r in fixture_rows)
    for n, name, _ in gate.GATE_ITEMS:
        assert re.search(rf"^\| {n} \| {re.escape(name)} \|", body, flags=re.M), f"item {n} missing"
    assert "Clears the gate" in body
    assert "Proposed default" not in body
    for n, name, _ in (i for i in gate.GATE_ITEMS if i[0] in (3, 5)):
        row = re.search(rf"^\| {n} \| {re.escape(name)} \|(.*)$", body, flags=re.M).group(1)
        assert "descriptive, no threshold set" in row and "PASS" not in row and "FAIL" not in row


def test_lifecycle_ties_at_the_cap_are_ordered_by_days_open():
    """Owner's decision (Phase 5c): works tied at the cap (older than every
    completed peer) are ordered by days open; below the cap, the statistic
    alone decides and days open is ignored."""
    combined = pd.Series([0.5, 0.5, 1.0, 1.0, 1.0, np.nan], index=list("abcdef"))
    age = pd.Series([900, 100, 400, 800, 400, 50], index=list("abcdef"))
    score = signals.empirical_tail_score(signals.lifecycle_rank_key(combined, age))
    assert score["a"] == score["b"] == 0.0            # tied below the cap: age ignored
    assert score["c"] == score["e"] < score["d"]      # at the cap: older ranks higher
    assert score["c"] > score["a"]                    # any capped work outranks any uncapped one
    assert score["d"] == max(score.dropna())
    assert pd.isna(score["f"])
