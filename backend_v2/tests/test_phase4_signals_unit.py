"""
Phase 4 unit/property tests for the six base signals -- pure functions, no
database. Real-data coverage/house-filter tests live in
test_phase4_signals_context.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.analytics import peers, signals, signals_run

SIGNALS_FILE = Path(signals.__file__)


def test_z_to_score_never_exceeds_one_even_at_the_extremes_seen_in_real_data():
    """z_to_score feeds 5 of the 6 signals and is the second place (after
    cosine_similarity) something computed close to a [0, 1] bound could in
    principle overshoot it under floating point. Checked directly, at the
    exact z magnitudes (48-50) the SHARE_STD_FLOOR fix guarantees as its
    worst case, and beyond: erf() is a numerically stable saturating
    function (unlike the dot-product/norm-ratio behind cosine similarity,
    which is what actually overshot in production), so it never crosses
    1.0 -- verified here, not assumed by analogy to that bug."""
    zs = np.array([0, 1, 2.5, 3, 5, 10, 40, 48, 49, 50, 100, 1000, 1e10, np.inf, -1e10])
    scores = signals.z_to_score(zs)
    assert np.isfinite(scores).all()
    assert (scores <= 1.0).all()
    assert (scores >= 0.0).all()
    assert scores[-2] == 1.0  # erf saturates to exactly 1.0, not asymptotically over it


def test_signals_output_hash_handles_pd_na_direction_without_crashing():
    """Regression: `pd.NA` (the default in _empty_result's direction
    column, e.g. every near_duplicate row and every ineligible row of any
    signal) has no truthiness -- `if row.direction` / `row.direction or
    ''` both raise `TypeError: boolean value of NA is ambiguous`. Hit in
    production in both signals_output_hash and run_signals.py's report
    sampler; fixed with pd.isna() checks in both. This covers the hash
    function directly; the report sampler's equivalent line is exercised
    by the e2e/manual report run (no unit harness for the CLI script)."""
    empty = signals._empty_result(pd.Index(["w0", "w1"], name="work_key"))
    results = {name: empty.copy() for name in signals_run.SIGNAL_ORDER}
    h = signals_run.signals_output_hash(results)  # must not raise
    assert isinstance(h, str) and len(h) == 64

    # And with a mix of real directions and pd.NA in the same frame.
    mixed = empty.copy()
    mixed.loc["w0", "direction"] = "above"
    results["cost_anomaly"] = mixed
    h2 = signals_run.signals_output_hash(results)
    assert h2 != h


def _function_body(src: str, fn_name: str) -> str:
    """Source text of a top-level `def fn_name(...):` through to the next
    top-level `def`/`class` or end of file. Only used to scope a
    string/AST check to one function, never to execute anything."""
    m = re.search(rf"^def {re.escape(fn_name)}\(", src, flags=re.MULTILINE)
    assert m, f"{fn_name} not found in {SIGNALS_FILE}"
    next_def = re.search(r"^(?:def |class )", src[m.end() :], flags=re.MULTILINE)
    end = m.end() + next_def.start() if next_def else len(src)
    return src[m.start() : end]


def base_frame(n: int, **overrides) -> pd.DataFrame:
    """A minimal, internally consistent Phase 4 frame. Every column the
    six signal functions read is present with a harmless default;
    `overrides` replaces columns (or a single cell via a post-build
    caller) to build each test's actual scenario."""
    idx = pd.Index([f"w{i}" for i in range(n)], name="work_key")
    d = dict(
        house=["LS"] * n,
        mp=[f"MP{i % 4}" for i in range(n)],
        state_id=[1] * n,
        district_authority_id=[1] * n,
        district_key=["D1"] * n,
        activity_type_id=[1] * n,
        sanction_fy=["2024-25"] * n,
        lifecycle_status=["sanctioned"] * n,
        sanction_date=pd.to_datetime(["2024-06-01"] * n),
        amount=[100000.0] * n,
        amount_basis=["sanction"] * n,
        is_usable=[True] * n,
        paid_total=[0.0] * n,
        description_normalized=["road work in village x"] * n,
        recommended_date=pd.to_datetime(["2024-05-01"] * n),
        actual_end_date=pd.Series([pd.NaT] * n),
    )
    d.update(overrides)
    return pd.DataFrame(d, index=idx)


# ---- 1. Cost anomaly ----------------------------------------------------

def test_cost_anomaly_peer_group_sizes_2_3_4():
    """Below MIN_USABLE_PEERS(=15), no level qualifies -> not eligible,
    regardless of group size (2, 3 or 4 peers is always below 15)."""
    for size in (2, 3, 4):
        mps = [f"MP{i}" for i in range(size)]
        f = base_frame(size, mp=mps, amount=[100000.0 * (i + 1) for i in range(size)])
        ctx = peers.build_context(f)
        r = signals.cost_anomaly_signal(f, ctx)
        assert not r["eligible"].any(), f"size {size} unexpectedly eligible"
        assert r["score"].isna().all()


def test_cost_anomaly_all_identical_amounts_scores_near_zero_not_inf():
    n = 25
    mps = [f"MP{i}" for i in range(20)]
    f = base_frame(n, mp=[mps[i % 20] for i in range(n)])  # all amount=100000.0 (base_frame default)
    ctx = peers.build_context(f)
    r = signals.cost_anomaly_signal(f, ctx)
    assert r["eligible"].any()
    scored = r.loc[r["eligible"], "score"]
    assert scored.apply(np.isfinite).all(), "zero peer MAD must not produce inf/NaN"
    assert (scored < 1e-6).all(), "identical-to-peers amount should score ~0, not merely finite"


def test_cost_anomaly_missing_amount_is_not_evaluated_never_zero():
    n = 25
    mps = [f"MP{i}" for i in range(20)]
    f = base_frame(n, mp=[mps[i % 20] for i in range(n)])
    f.loc["w0", "amount"] = np.nan
    f.loc["w0", "is_usable"] = False
    ctx = peers.build_context(f)
    r = signals.cost_anomaly_signal(f, ctx)
    assert r.loc["w0", "eligible"] is np.False_ or r.loc["w0", "eligible"] == False  # noqa: E712
    assert pd.isna(r.loc["w0", "score"]), "a missing amount must be 'not evaluated', never a score of 0"


def test_cost_anomaly_single_mp_group_does_not_reach_level_1():
    f = base_frame(30, mp=["ONLYMP"] * 30)
    ctx = peers.build_context(f)
    assert (ctx["level"] != "L1").all()
    r = signals.cost_anomaly_signal(f, ctx)
    # L2/L3 do not require distinct MPs the way L1 does, so this can still
    # be eligible -- the assertion that matters is specifically that no
    # eligible row's evidence claims a Level 1 peer group.
    eligible_levels = {r.at[k, "evidence"]["level"] for k in f.index[r["eligible"]]}
    assert "L1" not in eligible_levels


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=0, max_value=5000))
def test_cost_anomaly_score_never_decreases_when_raising_an_at_or_above_median_amount(seed):
    """Required property test: raising a work's amount above its peer
    median never lowers its cost anomaly score. Two-sided by design (see
    signals.py module docstring), so this is only claimed -- and only
    holds -- once the amount is already at or above the peer median;
    below the median, moving *toward* the median can legitimately lower a
    symmetric two-sided score before it rises again on the other side."""
    rng = np.random.default_rng(seed)
    n = 60
    mps = [f"MP{i}" for i in range(20)]
    f = base_frame(n, mp=[mps[i % 20] for i in range(n)], amount=list(rng.lognormal(12, 1, n)))
    ctx = peers.build_context(f)
    r = signals.cost_anomaly_signal(f, ctx)

    eligible = f.index[r["eligible"]]
    if len(eligible) == 0:
        return
    k = eligible[int(rng.integers(0, len(eligible)))]
    median = ctx.loc[k, "peer_median"]
    if pd.isna(median) or f.loc[k, "amount"] < median:
        return

    before = r.loc[k, "score"]
    bumped = f.copy()
    bumped.loc[k, "amount"] = f.loc[k, "amount"] * 2
    ctx_b = peers.build_context(bumped)
    r_b = signals.cost_anomaly_signal(bumped, ctx_b)
    after = r_b.loc[k, "score"]
    if pd.isna(before) or pd.isna(after):
        return
    assert after >= before - 1e-9, (k, before, after)


def test_cost_anomaly_pools_across_works_assigned_to_other_levels():
    """Regression for a real bug caught against the full dataset (work_key
    123541, an L2-assigned RS cost_anomaly row that came back eligible
    with score=NULL and tripped the DB's CHECK constraint): a work
    assigned Level 2 must be compared against EVERY usable work of that
    type+state, including ones that individually qualified at Level 1 in
    their own FY and so carry a different (FY-suffixed) `group_key`
    string -- not only the other works that also fell back to Level 2.

    20 works of (type=1, state=1, FY 2024-25) qualify at L1 on their own
    (>=15 peers, >=3 other MPs). 6 more works of the SAME type+state but a
    thin FY 2025-26 (only 6 works) cannot reach L1 and fall back to L2.
    One of those 6 is a blatant cost outlier. Its true L2 peer pool is all
    26 OTHER works of that type+state (Phase 3's own work_context agrees:
    n_usable_excl_self=25). Pooled correctly, it must be eligible with a
    real score; pooled by literal group_key string match (the bug), its
    pool would shrink to just the other 5 thin-FY works.
    """
    mps20 = [f"MP{i % 10}" for i in range(20)]
    fy24 = base_frame(
        20, mp=mps20, sanction_fy=["2024-25"] * 20,
        amount=[100000.0 + i * 1000 for i in range(20)],
        sanction_date=pd.to_datetime(["2024-06-01"] * 20),
    )
    fy24.index = [f"fy24_{i}" for i in range(20)]
    fy25 = base_frame(
        6, mp=[f"MP{i}" for i in range(6)], sanction_fy=["2025-26"] * 6,
        amount=[100000.0 + i * 1000 for i in range(5)] + [5_000_000.0],
        sanction_date=pd.to_datetime(["2025-06-01"] * 6),
    )
    fy25.index = [f"fy25_{i}" for i in range(5)] + ["target"]
    f = pd.concat([fy24, fy25])

    ctx = peers.build_context(f)
    assert ctx.at["target", "level"] == "L2"
    assert ctx.at["fy24_0", "level"] == "L1"
    assert ctx.at["target", "n_usable_excl_self"] == 25  # Phase 3's own, correct count

    r = signals.cost_anomaly_signal(f, ctx)
    assert r.at["target", "eligible"]
    assert pd.notna(r.at["target", "score"]), "eligible but score is NaN -- peer pool was under-built"
    assert r.at["target", "evidence"]["n_usable_excl_self"] == 25
    # a 46x-median outlier is the most extreme work: a huge |z|, and (Phase 5c
    # empirical percentile scores) the highest score in the evaluated population
    assert abs(r.at["target", "evidence"]["z_log_scale"]) > 5
    el = r.index[r["eligible"]]
    assert r.at["target", "score"] == r.loc[el, "score"].max()
    assert (r.loc[el.drop("target"), "score"] < r.at["target", "score"]).all()


# ---- 2. Near-duplicate ---------------------------------------------------

def test_near_duplicate_flags_close_cluster_and_penalizes_amount_gap():
    dates = ["2024-06-01", "2024-06-02", "2024-06-03", "2024-06-01", "2024-06-05", "2024-06-01"]
    f = base_frame(
        6, mp=["SAMEMP"] * 6, description_normalized=["identical text about a road"] * 6,
        amount=[100000.0, 100500.0, 99800.0, 500000.0, 100200.0, 99900.0],
        sanction_date=pd.to_datetime(dates),
    )
    r = signals.near_duplicate_signal(f)
    assert r.loc["w0", "score"] > 0.8
    assert r.loc["w3", "score"] < r.loc["w0", "score"]


def test_near_duplicate_never_matches_a_work_against_its_own_key():
    """A work's best match, if any, is never itself (checked by index
    label, matching signals.py's `np.fill_diagonal` guarantee)."""
    f = base_frame(
        8, mp=["SAMEMP"] * 8, description_normalized=["repaving of the same road segment yet again"] * 8,
        amount=[100000.0] * 8, sanction_date=pd.to_datetime(["2024-06-01"] * 8),
    )
    r = signals.near_duplicate_signal(f)
    for k in f.index[r["eligible"]]:
        if r.loc[k, "score"] > 0:
            assert r.loc[k, "evidence"]["matched_work_key"] != k


def test_near_duplicate_distinct_works_can_still_match_each_other():
    """The schema-level guarantee (work_key is a primary key, so a work's
    own lifecycle-stage rows -- which live in work_state -- can never
    appear twice in this frame) must not be confused with suppressing
    real matches between two DIFFERENT, genuinely similar works."""
    f = base_frame(
        2, mp=["SAMEMP"] * 2, district_authority_id=[7, 7],
        description_normalized=["construction of drain near market road"] * 2,
        amount=[200000.0, 201000.0], sanction_date=pd.to_datetime(["2024-06-01", "2024-06-03"]),
    )
    r = signals.near_duplicate_signal(f)
    assert r.loc["w0", "eligible"] and r.loc["w1", "eligible"]
    assert r.loc["w0", "score"] > 0.5
    assert r.loc["w0", "evidence"]["matched_work_key"] == "w1"
    assert r.loc["w1", "evidence"]["matched_work_key"] == "w0"


def test_near_duplicate_requires_shared_authority():
    """Two works with identical text but different district authorities
    (and no MP/type overlap driving a shared block) must not match."""
    f = base_frame(
        2, mp=["MPA", "MPB"], district_authority_id=[1, 2], activity_type_id=[1, 2],
        description_normalized=["identical description text here"] * 2,
    )
    r = signals.near_duplicate_signal(f)
    assert (r["score"].fillna(0) == 0).all()


def test_near_duplicate_missing_description_or_authority_is_not_evaluated():
    f = base_frame(3, description_normalized=[None, "", "has text"])
    f.loc["w2", "district_authority_id"] = np.nan
    r = signals.near_duplicate_signal(f)
    assert not r.loc["w0", "eligible"]
    assert not r.loc["w1", "eligible"]
    assert not r.loc["w2", "eligible"]
    assert r["score"].isna().all()


def test_near_duplicate_score_is_never_above_one_for_near_identical_text():
    """Regression for a real bug caught against the full dataset (work_key
    1302/1304, RS, near_duplicate): sklearn's cosine_similarity on
    near-identical TF-IDF vectors can round fractionally above 1.0
    (observed: 1.0000000000000002), which tripped the DB's
    ck_signal_result_score_range CHECK constraint. A block of several
    works with identical description/MP/amount/date, the case most likely
    to produce a near-1.0 cosine value, must still yield scores in [0, 1]
    exactly, for every eligible row, not merely "close to 1"."""
    n = 8
    f = base_frame(
        n, mp=["SAMEMP"] * n, description_normalized=["identical text about a road project"] * n,
        amount=[100000.0] * n, sanction_date=pd.to_datetime(["2024-06-01"] * n),
    )
    r = signals.near_duplicate_signal(f)
    scored = r.loc[r["eligible"], "score"]
    assert not scored.empty
    assert (scored <= 1.0).all(), scored[scored > 1.0]
    assert (scored >= 0.0).all()


# ---- 3. Portfolio concentration ------------------------------------------

def test_portfolio_concentration_below_minimum_portfolio_not_eligible():
    f = base_frame(9, mp=[f"MP{i % 3}" for i in range(9)])  # 3 works per MP, below MIN_PORTFOLIO_SIZE
    r = signals.portfolio_concentration_signal(f)
    assert not r["eligible"].any()


def test_portfolio_concentration_flags_the_specialist_mp():
    """One MP does only roads (type 1); peer MPs in-state do a mix ->
    the specialist MP's share is a clear standardised-residual outlier."""
    rows = []
    for mp in [f"PEER{i}" for i in range(6)]:
        rows += [{"mp": mp, "activity_type_id": t} for t in ([1] * 3 + [2] * 3 + [3] * 6)]
    rows += [{"mp": "SPECIALIST", "activity_type_id": 1} for _ in range(12)]
    df = pd.DataFrame(rows)
    f = base_frame(len(df), mp=df["mp"].tolist(), activity_type_id=df["activity_type_id"].tolist())
    r = signals.portfolio_concentration_signal(f)
    specialist_rows = f.index[f["mp"] == "SPECIALIST"]
    assert r.loc[specialist_rows, "eligible"].all()
    assert (r.loc[specialist_rows, "score"] > 0.5).all()
    assert (r.loc[specialist_rows, "direction"] == "above").all()


def test_portfolio_concentration_scores_each_work_on_its_own_type_not_the_mps_worst():
    """Regression (Phase 5 prerequisite): Phase 4 attached the MP's single
    most extreme type to EVERY work of that MP. An MP heavy in type 1 but
    ordinary in type 3 must have its type-3 works scored on type 3 (low),
    not inherit the type-1 score."""
    rows = []
    for mp in [f"PEER{i}" for i in range(6)]:
        rows += [{"mp": mp, "activity_type_id": t} for t in ([1] * 3 + [2] * 3 + [3] * 6)]
    # MIXED's type-3 share (12/24 = 0.5) equals every peer's; its type-1
    # share (0.5 vs 0.25) is extreme.
    rows += [{"mp": "MIXED", "activity_type_id": t} for t in ([1] * 12 + [3] * 12)]
    df = pd.DataFrame(rows)
    f = base_frame(len(df), mp=df["mp"].tolist(), activity_type_id=df["activity_type_id"].tolist())
    r = signals.portfolio_concentration_signal(f)
    heavy = f.index[(f["mp"] == "MIXED") & (f["activity_type_id"] == 1)]
    ordinary = f.index[(f["mp"] == "MIXED") & (f["activity_type_id"] == 3)]
    # Phase 5a step 3: scores are empirical percentile ranks, so "extreme"
    # means "the highest score in the population" (the share of evaluated
    # works strictly less extreme), not a fixed normal-theory level.
    el = r.index[r["eligible"]]
    assert (r.loc[heavy, "score"] == r.loc[el, "score"].max()).all()
    assert (r.loc[heavy, "score"] > 0.8).all()
    assert (r.loc[ordinary, "score"] < r.loc[heavy, "score"].min()).all()
    assert all(abs(r.loc[k, "evidence"]["z"]) < 0.05 for k in ordinary)
    assert all(r.loc[k, "evidence"]["activity_type_id"] == 3 for k in ordinary)

def test_portfolio_concentration_does_not_explode_when_no_peer_ever_did_the_type():
    """Regression for a real-data finding: with ~115 activity types spread
    over modest MP portfolios, "every OTHER in-state MP did zero (or
    near-zero) of this type" turned out to be the case for a large share
    of eligible rows, not a rare corner case -- and dividing by an
    unfloored, near-zero-floored, or resolution-floored peer std there
    turned a single ordinary work into a z in the thousands (three such
    formulas were tried and rejected, each checked against the real
    dataset -- see signals.py's SHARE_STD_FLOOR comment). A share is
    mathematically bounded in [0, 1], so flooring the peer std at a FIXED
    absolute constant guarantees |z| <= 1/SHARE_STD_FLOOR regardless of
    portfolio size or count -- verified here directly against
    signals.SHARE_STD_FLOOR, not a value hard-coded a second time in this
    test. A larger, sparse, randomised population (many MPs, many types,
    most (mp, type) pairs at zero) reliably reproduces the condition.
    """
    rng = np.random.default_rng(4)
    n_mps, n_types = 30, 20
    mps = [f"MP{i}" for i in range(n_mps)]
    rows = []
    for mp in mps:
        # Each MP works in only 2-3 of the 20 types -- most (mp, type)
        # pairs are zero, the condition the real-data finding measured.
        my_types = rng.choice(n_types, size=rng.integers(2, 4), replace=False)
        for t in my_types:
            rows += [{"mp": mp, "activity_type_id": int(t)} for _ in range(rng.integers(10, 30))]
    df = pd.DataFrame(rows)
    f = base_frame(len(df), mp=df["mp"].tolist(), activity_type_id=df["activity_type_id"].tolist())
    r = signals.portfolio_concentration_signal(f)
    eligible = r[r["eligible"]]
    assert not eligible.empty
    near_zero_std = sum(
        1 for k in eligible.index if eligible.at[k, "evidence"]["peer_std_share"] < signals.SHARE_STD_FLOOR
    )
    assert near_zero_std > 0, "fixture did not reproduce the near-zero peer_std_share condition"
    max_possible_z = 1.0 / signals.SHARE_STD_FLOOR
    zs = [eligible.at[k, "evidence"]["z"] for k in eligible.index]
    worst = max(zs, key=abs)
    assert max(abs(z) for z in zs) <= max_possible_z + 1e-9, f"a z exceeded the guaranteed bound: {worst}"
    assert eligible["score"].between(0.0, 1.0).all()


# ---- 4. District-authority pattern ---------------------------------------

def test_district_authority_pattern_flags_the_outlier_authority():
    rows = []
    for auth in range(1, 7):
        rows += [{"district_authority_id": auth, "activity_type_id": t, "amount": 100000.0}
                 for t in ([1] * 2 + [2] * 2 + [3] * 8)]
    rows += [{"district_authority_id": 99, "activity_type_id": 1, "amount": 100000.0} for _ in range(12)]
    df = pd.DataFrame(rows)
    f = base_frame(
        len(df), district_authority_id=df["district_authority_id"].tolist(),
        activity_type_id=df["activity_type_id"].tolist(), amount=df["amount"].tolist(),
    )
    r = signals.district_authority_pattern_signal(f)
    outlier_rows = f.index[f["district_authority_id"] == 99]
    assert r.loc[outlier_rows, "eligible"].all()
    assert (r.loc[outlier_rows, "score"] > 0.5).all()


def test_district_authority_pattern_scores_each_work_on_its_own_type():
    """Regression (Phase 5 prerequisite), same as portfolio's: an
    authority's ordinary-type works must not inherit its extreme type's
    score."""
    rows = []
    for auth in range(1, 7):
        rows += [{"district_authority_id": auth, "activity_type_id": t, "amount": 100000.0}
                 for t in ([1] * 2 + [2] * 2 + [3] * 8)]
    # Authority 99's type-3 share (20/30) equals every peer's (8/12); its
    # type-1 share (1/3 vs 1/6) is extreme.
    rows += [{"district_authority_id": 99, "activity_type_id": t, "amount": 100000.0}
             for t in ([1] * 10 + [3] * 20)]
    df = pd.DataFrame(rows)
    f = base_frame(
        len(df), district_authority_id=df["district_authority_id"].tolist(),
        activity_type_id=df["activity_type_id"].tolist(), amount=df["amount"].tolist(),
    )
    r = signals.district_authority_pattern_signal(f)
    heavy = f.index[(f["district_authority_id"] == 99) & (f["activity_type_id"] == 1)]
    ordinary = f.index[(f["district_authority_id"] == 99) & (f["activity_type_id"] == 3)]
    assert (r.loc[heavy, "score"] > 0.9).all()
    assert (r.loc[ordinary, "score"] < 0.05).all(), r.loc[ordinary, "score"].tolist()


def test_district_authority_amount_component_is_on_log_scale():
    """Regression (Phase 5 prerequisite): the amount component compared raw
    rupee means, so one lakh-scale difference dominated; it now compares
    log-amount means like cost_anomaly. Doubling every amount (a pure
    scale change) must leave z_amount unchanged."""
    rng = np.random.default_rng(11)
    rows = []
    for auth in range(1, 9):
        rows += [{"district_authority_id": auth, "activity_type_id": 1,
                  "amount": float(rng.lognormal(12, 0.4))} for _ in range(12)]
    df = pd.DataFrame(rows)
    kw = dict(district_authority_id=df["district_authority_id"].tolist(),
              activity_type_id=df["activity_type_id"].tolist())
    r1 = signals.district_authority_pattern_signal(base_frame(len(df), amount=df["amount"].tolist(), **kw))
    r2 = signals.district_authority_pattern_signal(
        base_frame(len(df), amount=(df["amount"] * 2).tolist(), **kw))
    el = r1.index[r1["eligible"]]
    assert len(el) > 0
    z1 = [r1.at[k, "evidence"]["z_amount"] for k in el]
    z2 = [r2.at[k, "evidence"]["z_amount"] for k in el]
    np.testing.assert_allclose(z1, z2, atol=1e-9)

def test_district_authority_pattern_does_not_explode_when_no_peer_ever_did_the_type():
    """Same regression, and same fixed-share-std-floor guarantee, as
    portfolio_concentration's equivalent test -- see its docstring and
    signals.py's SHARE_STD_FLOOR comment."""
    rng = np.random.default_rng(5)
    n_auth, n_types = 30, 20
    auths = list(range(1, n_auth + 1))
    rows = []
    for auth in auths:
        my_types = rng.choice(n_types, size=rng.integers(2, 4), replace=False)
        for t in my_types:
            rows += [
                {"district_authority_id": auth, "activity_type_id": int(t), "amount": 100000.0}
                for _ in range(rng.integers(10, 30))
            ]
    df = pd.DataFrame(rows)
    f = base_frame(
        len(df), district_authority_id=df["district_authority_id"].tolist(),
        activity_type_id=df["activity_type_id"].tolist(), amount=df["amount"].tolist(),
    )
    r = signals.district_authority_pattern_signal(f)
    eligible = r[r["eligible"]]
    assert not eligible.empty
    near_zero_std = sum(
        1 for k in eligible.index if eligible.at[k, "evidence"]["peer_std_share"] < signals.SHARE_STD_FLOOR
    )
    assert near_zero_std > 0, "fixture did not reproduce the near-zero peer_std_share condition"
    max_possible_z = 1.0 / signals.SHARE_STD_FLOOR
    zs = [eligible.at[k, "evidence"]["z_share"] for k in eligible.index]
    worst = max(zs, key=abs)
    assert max(abs(z) for z in zs) <= max_possible_z + 1e-9, f"z_share exceeded the guaranteed bound: {worst}"
    assert eligible["score"].between(0.0, 1.0).all()


def test_district_authority_pattern_no_constituency_reference():
    """Structural check: this signal's actual grouping logic (the old
    engine's constituency_pattern replacement) must not key on
    constituency anywhere -- RS members have none. The function's own
    docstring explains, in English, what it replaces and so legitimately
    says "constituency"; that sentence is excluded here the same way
    Phase 3's equivalent grep test (test_level1_is_never_constituency_
    keyed) excludes its own docstring."""
    src = SIGNALS_FILE.read_text(encoding="utf-8")
    body = _function_body(src, "district_authority_pattern_signal")
    tree = ast.parse(body)
    fn_node = tree.body[0]
    assert isinstance(fn_node, ast.FunctionDef)
    doc_lines: set[int] = set()
    first = fn_node.body[0] if fn_node.body else None
    is_docstring = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
    if is_docstring:
        doc_lines = set(range(first.lineno, first.end_lineno + 1))
    for i, line in enumerate(body.splitlines(), start=1):
        if i in doc_lines or line.strip().startswith("#"):
            continue
        assert "constituen" not in line.lower(), f"line {i}: {line}"


# ---- 5. Temporal anomaly --------------------------------------------------

def test_temporal_batch_day_exclusion():
    """A synthetic single national-day burst, shared across many MPs, is a
    batch day and must not fire the signal for any of them; a genuine
    single-MP burst on an ordinary day must still fire."""
    n_batch = 200
    batch_rows = pd.DataFrame(
        {"mp": [f"MP{i % 100}" for i in range(n_batch)],
         "sanction_date": pd.to_datetime(["2024-07-15"] * n_batch)}
    )
    # Every batch-day MP also gets a quiet baseline elsewhere, so each has
    # a "normal cadence" to compare against.
    baseline_rows = pd.DataFrame(
        {"mp": [f"MP{i % 100}" for i in range(300)],
         "sanction_date": pd.to_datetime("2024-04-01") + pd.to_timedelta(np.arange(300) % 90, unit="D")}
    )
    burst_mp = pd.DataFrame(
        {"mp": ["BURSTY"] * 20, "sanction_date": pd.to_datetime(["2024-08-05"] * 20)}
    )
    burst_baseline = pd.DataFrame(
        {"mp": ["BURSTY"] * 10,
         "sanction_date": pd.to_datetime("2024-04-01") + pd.to_timedelta(np.arange(10) * 7, unit="D")}
    )
    df = pd.concat([batch_rows, baseline_rows, burst_mp, burst_baseline], ignore_index=True)
    f = base_frame(len(df), mp=df["mp"].tolist(), sanction_date=df["sanction_date"].tolist())
    r = signals.temporal_anomaly_signal(f)

    # Works dated on the batch day are never scored on that date field (the
    # national batch says nothing about the MP), and the batch day created
    # no signal for anyone despite being a huge national spike.
    batch_day_mps = f.index[f["sanction_date"] == pd.Timestamp("2024-07-15")]
    for k in batch_day_mps:
        ev = r.loc[k, "evidence"]
        if r.loc[k, "eligible"]:
            assert ev.get("date_field") != "sanction"

    burst_rows = f.index[(f["mp"] == "BURSTY") & (f["sanction_date"] == pd.Timestamp("2024-08-05"))]
    msg = "a genuine single-MP burst on an ordinary day must still fire"
    assert r.loc[burst_rows, "eligible"].all(), msg
    assert (r.loc[burst_rows, "score"] > 0.3).all()
    assert all(r.loc[k, "evidence"]["own_week"].startswith("2024-08-05") for k in burst_rows)


def test_temporal_scores_each_work_in_its_own_week_not_the_entitys_worst():
    """Regression (Phase 5 prerequisite): the Phase 4 version attached an
    entity's single worst week to EVERY work of that entity, so the
    signal sat near 1.0 for almost all works of any MP with one heavy
    week. A work in the MP's ordinary weeks must now score ~0 while the
    burst-week works score high."""
    dates = (
        list(pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(10) * 7, unit="D"))
        + [pd.Timestamp("2024-08-05")] * 20
    )
    f = base_frame(len(dates), mp=["BURSTY"] * len(dates), sanction_date=dates,
                   recommended_date=pd.Series([pd.NaT] * len(dates)),
                   district_authority_id=[None] * len(dates))
    r = signals.temporal_anomaly_signal(f)
    quiet = f.index[:10]
    burst = f.index[10:]
    # Phase 5a step 3: empirical percentile scores. Quiet-week works have
    # burst z = 0 (the minimum) -> score 0; the 20 burst-week works are
    # more extreme than exactly the 10 quiet ones -> 10/30.
    assert r.loc[burst, "eligible"].all() and r.loc[quiet, "eligible"].all()
    assert (r.loc[quiet, "score"] == 0.0).all(), r.loc[quiet, "score"].tolist()
    assert (r.loc[burst, "score"] == 10 / 30).all(), r.loc[burst, "score"].tolist()
    assert all(r.loc[k, "evidence"]["z"] > 1.0 for k in burst)


def test_temporal_batch_day_itself_excluded_from_counts():
    counts_only = pd.Series(pd.to_datetime(["2024-07-15"] * 60 + ["2024-04-01", "2024-04-08", "2024-04-15"]))
    days = signals._batch_days(counts_only)
    assert pd.Timestamp("2024-07-15") in days
    assert pd.Timestamp("2024-04-01") not in days


# ---- 6. Lifecycle delay ---------------------------------------------------

def test_lifecycle_delay_only_scores_open_works():
    n = 40
    mps = [f"MP{i}" for i in range(20)]
    lifecycle = (["sanctioned"] * 20) + (["completed"] * 20)
    f = base_frame(
        n, mp=[mps[i % 20] for i in range(n)], lifecycle_status=lifecycle,
        sanction_date=pd.to_datetime(["2024-01-01"] * n),
        actual_end_date=pd.to_datetime([pd.NaT] * 20 + ["2024-06-01"] * 20),
    )
    ctx = peers.build_context(f)
    r = signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2026-01-01"))
    assert not r.loc[f["lifecycle_status"] == "completed", "eligible"].any()


def test_lifecycle_delay_flags_a_work_much_older_than_completed_peers():
    n = 30
    mps = [f"MP{i}" for i in range(20)]
    lifecycle = (["completed"] * 20) + (["sanctioned"] * 10)
    sanction_date = pd.to_datetime(["2024-01-01"] * 30)
    actual_end = pd.to_datetime(["2024-04-01"] * 20 + [pd.NaT] * 10)  # completed peers: ~90 days
    f = base_frame(
        n, mp=[mps[i % 20] for i in range(n)], lifecycle_status=lifecycle,
        sanction_date=sanction_date, actual_end_date=actual_end,
    )
    ctx = peers.build_context(f)
    stale_key = f.index[f["lifecycle_status"] == "sanctioned"][0]
    # stale_key: ~2 years open; completed peers took ~90 days.
    r = signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2026-01-01"))
    assert r.loc[stale_key, "eligible"]
    # The age component vs completed peers is near-maximal. Since Phase 5c the
    # SCORE ranks against other OPEN works; all 10 open works here are equally
    # old, so none is more delayed than another and all share the minimum.
    assert r.loc[stale_key, "evidence"]["age_exceedance"] > 0.9
    open_keys = f.index[f["lifecycle_status"] == "sanctioned"]
    assert (r.loc[open_keys, "score"] == r.loc[stale_key, "score"]).all()
    assert r.loc[stale_key, "direction"] == "above"


def test_lifecycle_delay_ranks_the_oldest_open_work_highest():
    """Phase 5c: lifecycle is scored against other OPEN works. With open works
    of increasing age, score rises with age and the oldest scores highest."""
    n = 30
    mps = [f"MP{i}" for i in range(20)]
    lifecycle = (["completed"] * 20) + (["sanctioned"] * 10)
    sanction_date = pd.to_datetime(["2024-01-01"] * 20 + [f"2025-{m:02d}-01" for m in range(1, 11)])
    actual_end = pd.to_datetime(["2024-04-01"] * 20 + [pd.NaT] * 10)
    f = base_frame(
        n, mp=[mps[i % 20] for i in range(n)], lifecycle_status=lifecycle,
        sanction_date=sanction_date, actual_end_date=actual_end,
    )
    ctx = peers.build_context(f)
    r = signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2026-01-01"))
    open_keys = f.index[f["lifecycle_status"] == "sanctioned"]  # oldest first
    scores = r.loc[open_keys, "score"].astype(float).to_numpy()
    assert (np.diff(scores) <= 0).all()
    assert scores[0] == scores.max() > scores[-1]


def test_lifecycle_delay_flags_payment_far_ahead_of_open_peers():
    n = 20
    mps = [f"MP{i}" for i in range(20)]
    f = base_frame(
        n, mp=mps, lifecycle_status=["sanctioned"] * n, sanction_date=pd.to_datetime(["2025-01-01"] * n),
        amount=[100000.0] * n, paid_total=[10000.0] * n,
    )
    f.loc["w0", "paid_total"] = 95000.0  # nearly fully paid while everyone else is at 10%
    ctx = peers.build_context(f)
    r = signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2025-02-01"))
    assert r.loc["w0", "eligible"]
    assert r.loc["w0", "evidence"]["driving_component"] == "payment_ahead"
    assert r.loc["w0", "score"] > 0.5


def test_lifecycle_delay_pools_across_works_assigned_to_other_levels():
    """Same regression pattern as
    test_cost_anomaly_pools_across_works_assigned_to_other_levels, for
    lifecycle_delay's completed-peer and open-peer pools: an L2-assigned
    open work must see completed peers that individually qualified at L1
    in their own FY too, not just the other works that also fell back."""
    mps20 = [f"MP{i % 10}" for i in range(20)]
    fy24 = base_frame(
        20, mp=mps20, sanction_fy=["2024-25"] * 20, lifecycle_status=["completed"] * 20,
        sanction_date=pd.to_datetime(["2024-01-01"] * 20),
        actual_end_date=pd.to_datetime(["2024-03-01"] * 20),  # ~60-day completions
    )
    fy24.index = [f"fy24_{i}" for i in range(20)]
    fy25 = base_frame(
        6, mp=[f"MP{i}" for i in range(6)], sanction_fy=["2025-26"] * 6,
        lifecycle_status=["completed"] * 5 + ["sanctioned"],
        sanction_date=pd.to_datetime(["2025-01-01"] * 6),
        actual_end_date=pd.to_datetime(["2025-03-01"] * 5 + [pd.NaT]),
    )
    fy25.index = [f"fy25_{i}" for i in range(5)] + ["target"]
    f = pd.concat([fy24, fy25])

    ctx = peers.build_context(f)
    assert ctx.at["target", "level"] == "L2"

    r = signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2026-01-01"))  # target open ~1 year
    assert r.at["target", "eligible"]
    # Correctly pooled: 25 completed peers (20 fy24 + 5 fy25), all finishing
    # in ~60 days -- a work still open after a year is a clear exceedance.
    assert r.at["target", "evidence"]["n_completed_peers"] == 25
    # the age component vs those peers is a clear exceedance (the score itself
    # is now a rank among open works; target is the only open work here)
    assert r.at["target", "evidence"]["age_exceedance"] > 0.9


# ---- Cross-signal: alignment, no reliability multiplying score, house-neutrality ----

ALL_SIGNAL_FUNCS = {
    "cost_anomaly": lambda f, ctx: signals.cost_anomaly_signal(f, ctx),
    "near_duplicate": lambda f, ctx: signals.near_duplicate_signal(f),
    "portfolio_concentration": lambda f, ctx: signals.portfolio_concentration_signal(f),
    "district_authority_pattern": lambda f, ctx: signals.district_authority_pattern_signal(f),
    "temporal_anomaly": lambda f, ctx: signals.temporal_anomaly_signal(f),
    "lifecycle_delay": lambda f, ctx: signals.lifecycle_delay_signal(f, ctx, pd.Timestamp("2026-01-01")),
}


def _rich_synthetic(seed: int, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mps = [f"MP{i}" for i in range(24)]
    auths = list(range(1, 13))
    # A district authority has exactly one state, as in real data (an
    # authority's state is a fixed foreign key, never a per-row fact) --
    # fixed up front so a row shuffle can't make `.first()`/`.mode()`
    # pick a different state for the same authority between runs.
    auth_state = {a: int(rng.integers(1, 4)) for a in auths}
    lifecycle = rng.choice(["sanctioned", "completed"], n, p=[0.4, 0.6])
    sanction_date = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 600, n), unit="D")
    duration = rng.integers(20, 500, n)
    chosen_auths = [auths[i] for i in rng.integers(0, len(auths), n)]
    f = base_frame(
        n,
        mp=[mps[i] for i in rng.integers(0, len(mps), n)],
        state_id=[auth_state[a] for a in chosen_auths],
        district_authority_id=chosen_auths,
        activity_type_id=list(rng.integers(1, 5, n)),
        sanction_fy=list(rng.choice(["2024-25", "2025-26"], n)),
        lifecycle_status=list(lifecycle),
        sanction_date=sanction_date,
        amount=list(rng.lognormal(12, 1, n).round(0)),
        paid_total=list(rng.lognormal(11, 1, n).round(0)),
        description_normalized=[f"construction near village {i % 40}" for i in rng.integers(0, 999, n)],
        recommended_date=sanction_date - pd.to_timedelta(rng.integers(1, 90, n), unit="D"),
        house=list(rng.choice(["LS", "RS"], n)),
    )
    f["district_key"] = "D" + f["district_authority_id"].astype(str)
    completed_end = f["sanction_date"] + pd.to_timedelta(duration, unit="D")
    f["actual_end_date"] = pd.to_datetime(
        np.where(f["lifecycle_status"] == "completed", completed_end, pd.NaT)
    )
    return f


@pytest.mark.parametrize("signal_name", list(ALL_SIGNAL_FUNCS))
def test_shuffled_row_order_never_misaligns_a_signal_result(signal_name):
    f = _rich_synthetic(seed=7)
    ctx = peers.build_context(f)
    base = ALL_SIGNAL_FUNCS[signal_name](f, ctx).sort_index()

    shuffled = f.sample(frac=1, random_state=3)
    ctx_shuffled = peers.build_context(shuffled)
    result_shuffled = ALL_SIGNAL_FUNCS[signal_name](shuffled, ctx_shuffled).sort_index()

    pd.testing.assert_series_equal(base["eligible"], result_shuffled["eligible"], check_names=False)
    pd.testing.assert_series_equal(
        base["score"], result_shuffled["score"], check_names=False, check_exact=False
    )
    for k in base.index:
        _assert_evidence_close(base.at[k, "evidence"], result_shuffled.at[k, "evidence"], k)


def _assert_evidence_close(a, b, ctx_label) -> None:
    """Same content, tolerant of last-bit float noise from a different
    pandas/numpy internal summation order after a row shuffle -- not a
    misalignment (keys, non-numeric values and NaN/None placement must
    still match exactly)."""
    assert a.keys() == b.keys(), (ctx_label, a.keys(), b.keys())
    for key in a:
        va, vb = a[key], b[key]
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            assert va == pytest.approx(vb, rel=1e-9, abs=1e-9), (ctx_label, key, va, vb)
        else:
            assert va == vb, (ctx_label, key, va, vb)


@pytest.mark.parametrize("signal_name", list(ALL_SIGNAL_FUNCS))
def test_house_is_never_read_by_any_signal_function_source(signal_name):
    """Code-inspection test (not just behavioural): the EXECUTABLE code of
    each signal function never mentions `house` at all (docstring prose
    is excluded -- one function's docstring explains, in English, that it
    doesn't need House; that sentence is not a code reference), so House
    cannot possibly change how a score is computed.
    """
    fn_name = {
        "cost_anomaly": "cost_anomaly_signal", "near_duplicate": "near_duplicate_signal",
        "portfolio_concentration": "portfolio_concentration_signal",
        "district_authority_pattern": "district_authority_pattern_signal",
        "temporal_anomaly": "temporal_anomaly_signal", "lifecycle_delay": "lifecycle_delay_signal",
    }[signal_name]
    tree = ast.parse(SIGNALS_FILE.read_text(encoding="utf-8"))
    fn_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    body = fn_node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # drop the docstring statement; everything else is real code
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                continue  # string literals inside the code (e.g. evidence keys) are not a House reference
            if isinstance(node, ast.Name) and "house" in node.id.lower():
                pytest.fail(f"{fn_name} references a name containing 'house': {node.id}")
            if isinstance(node, ast.Attribute) and "house" in node.attr.lower():
                pytest.fail(f"{fn_name} references an attribute containing 'house': {node.attr}")


def test_recomputing_on_a_house_restricted_population_would_change_scores():
    """Canary for the exact mistake the Phase 4 brief warns about: if a
    future change fed only one House's rows into a signal function (e.g.
    "optimise" by loading `house=LS` before computing), peer/entity
    populations would shrink and scores WOULD change -- this is precisely
    why the real design (see test_house_is_never_read_by_any_signal_
    function_source) never restricts the computation population by House,
    and only ever filters the already-computed result at read time. This
    test fails loudly if that boundary is ever crossed by accident.
    """
    f = _rich_synthetic(seed=11)
    if f["house"].nunique() < 2:  # extremely unlikely with n=300, but keep the test meaningful
        pytest.skip("synthetic sample did not include both Houses")
    ctx_full = peers.build_context(f)

    ls_only = f[f["house"] == "LS"]
    ctx_ls_only = peers.build_context(ls_only)

    common = ls_only.index
    differed = False
    full_cost = signals.cost_anomaly_signal(f, ctx_full)
    restricted_cost = signals.cost_anomaly_signal(ls_only, ctx_ls_only)
    for k in common:
        a, b = full_cost.at[k, "score"], restricted_cost.at[k, "score"]
        if pd.notna(a) and pd.notna(b) and abs(a - b) > 1e-9:
            differed = True
            break
        if pd.isna(a) != pd.isna(b):
            differed = True
            break
    msg = "restricting by House did not change any score -- fixture too small to be meaningful"
    assert differed, msg


def test_no_reliability_or_peer_count_term_multiplies_any_score():
    """Acceptance criterion: grep for peer_reliability/n_usable/
    peer_group_size (or this codebase's names for the same concepts) used
    as a multiplicand of the returned score -- a source-level check, not
    just a unit test. Old engine's cost.py did exactly this
    (`peer_reliability * 0.1` summed into `cost_anomaly_score`); this
    module must never do it for any of the six signals.
    """
    src = SIGNALS_FILE.read_text(encoding="utf-8")
    banned = (
        "reliability", "n_usable", "peer_group_size", "distinct_other_mps",
        "n_peer", "n_completed_peers", "n_open_peers",
    )
    tree = ast.parse(src)

    def names_in(node) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
        }

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            operand_names = names_in(node)
            if any(any(b in name.lower() for b in banned) for name in operand_names):
                violations.append(ast.dump(node)[:200])
    assert not violations, violations

    # Belt-and-braces textual check on the literal terms named in the acceptance criteria.
    for pattern in (r"peer_reliability\s*\*", r"\*\s*peer_reliability", r"n_usable\w*\s*\*", r"\*\s*n_usable",
                    r"peer_group_size\s*\*", r"\*\s*peer_group_size"):
        assert not re.search(pattern, src), pattern
