"""
Phase 3 unit/property tests for the peer engine -- pure functions, no
database. The real-data coverage/house tests live in
test_phase3_context.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from app.analytics import peers

PEERS_FILE = Path(peers.__file__)


def _frame(amounts, mps, key="g1"):
    idx = [f"w{i}" for i in range(len(amounts))]
    return pd.DataFrame(
        {"amount": amounts, "mp": mps, "is_usable": [a is not None and a > 0 for a in amounts], "k": key},
        index=idx,
    )


def test_known_defect_self_inclusion_changes_own_median():
    """BLUEPRINT.md §12 known-defect suite: a peer group of 4 where one
    record is ~100x the others. Its leave-one-out median must differ from
    a (deliberately wrong, test-only) self-inclusive median."""
    amounts = [100.0, 110.0, 120.0, 11000.0]
    f = _frame(amounts, ["a", "b", "c", "d"])
    gkey = pd.Series("g1", index=f.index)
    loo = peers.loo_stats(f, gkey, pd.Series(True, index=f.index))

    self_inclusive_median = float(np.median(amounts))  # 115.0 -- the old engine's behaviour
    outlier_loo = loo.loc["w3", "median"]
    assert outlier_loo == 110.0  # median of the other three
    assert outlier_loo != self_inclusive_median
    # and every record's baseline is computed from the other three only
    for label in f.index:
        others = [a for a, lab in zip(amounts, f.index) if lab != label]
        assert loo.loc[label, "median"] == float(np.median(others))


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=1, max_value=1e7, allow_nan=False), min_size=2, max_size=40))
def test_loo_matches_naive_drop_one(values):
    """Exactness: every statistic equals the naive 'drop this one row and
    recompute' answer -- no approximation."""
    f = _frame(values, [f"mp{i % 5}" for i in range(len(values))])
    gkey = pd.Series("g1", index=f.index)
    loo = peers.loo_stats(f, gkey, pd.Series(True, index=f.index))
    arr = np.array(values)
    for i, label in enumerate(f.index):
        others = np.delete(arr, i)
        med = np.median(others)
        q25, q75 = np.percentile(others, [25, 75])
        assert np.isclose(loo.loc[label, "median"], med)
        assert np.isclose(loo.loc[label, "iqr"], q75 - q25)
        assert np.isclose(loo.loc[label, "mad"], np.median(np.abs(others - med)))


def test_non_usable_subject_is_not_removed_from_anyone_else():
    """A work with no usable amount still gets a baseline (from all usable
    peers) and doesn't knock a usable peer out of the pool."""
    f = _frame([100.0, 200.0, 300.0, None], ["a", "b", "c", "d"])
    gkey = pd.Series("g1", index=f.index)
    loo = peers.loo_stats(f, gkey, pd.Series(True, index=f.index))
    assert loo.loc["w3", "median"] == 200.0
    assert loo.loc["w0", "median"] == 250.0


def test_group_composition_hand_computed():
    # group of 5 usable works: MP a x3, b x1, c x1
    f = _frame([10.0, 20.0, 30.0, 40.0, 50.0], ["a", "a", "a", "b", "c"])
    comp = peers.group_composition(f, pd.Series("g1", index=f.index))
    # work w0 (MP a): peers = a,a,b,c -> 4 peers, other MPs {b,c}=2, max share a=2/4
    assert comp.loc["w0", "n_usable_excl_self"] == 4
    assert comp.loc["w0", "distinct_other_mps"] == 2
    assert comp.loc["w0", "distinct_mps_in_peers"] == 3
    assert comp.loc["w0", "max_mp_share"] == 0.5
    # work w3 (MP b): peers = a,a,a,c -> other MPs {a,c}=2, max share a=3/4
    assert comp.loc["w3", "distinct_other_mps"] == 2
    assert comp.loc["w3", "distinct_mps_in_peers"] == 2
    assert comp.loc["w3", "max_mp_share"] == 0.75


def _synthetic(seed: int = 0, n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "activity_type_id": rng.integers(1, 3, n),
            "state_id": rng.integers(1, 3, n),
            "district_key": rng.choice(["D1", "D2"], n),
            "sanction_fy": rng.choice(["2024-25", "2025-26"], n),
            "mp": rng.choice([f"MP{i}" for i in range(8)], n),
            "amount": rng.lognormal(12, 1, n).round(0),
            "house": rng.choice(["LS", "RS"], n),
        },
        index=[f"k{i}" for i in range(n)],
    )
    df.loc[df.sample(frac=0.05, random_state=seed).index, "amount"] = np.nan
    df["is_usable"] = df["amount"].notna() & (df["amount"] > 0)
    return df


@settings(max_examples=15, deadline=None)
@given(st.integers(min_value=0, max_value=10_000))
def test_row_order_shuffle_never_changes_any_baseline(seed):
    df = _synthetic()
    base = peers.build_context(df).sort_index()
    shuffled = peers.build_context(df.sample(frac=1, random_state=seed)).sort_index()
    pd.testing.assert_frame_equal(base, shuffled, check_like=True)


def test_no_assigned_group_has_an_mp_above_half():
    ctx = peers.build_context(_synthetic(seed=3, n=600))
    assigned = ctx[ctx["level"].notna()]
    assert not assigned.empty
    assert (assigned["max_mp_share"] <= peers.MAX_MP_SHARE).all()
    assert (assigned["n_usable_excl_self"] >= peers.MIN_USABLE_PEERS).all()
    assert (assigned["distinct_other_mps"] >= peers.MIN_OTHER_MPS).all()


def test_refinement_uses_the_same_capped_rule():
    """Refinement is populated only where its group passes the full L1-L3
    rule, including the 50% cap (Phase 3 review decision)."""
    df = _synthetic(seed=5, n=900)
    # plus one district group dominated by a single MP (20 of 30 works):
    # >= 15 peers and >= 3 other MPs, so only the 50% cap can exclude it
    mps = ["MP0"] * 20 + ["MP1", "MP2", "MP3", "MP4", "MP5"] * 2
    dominated = pd.DataFrame(
        {"activity_type_id": 1, "state_id": 1, "district_key": "D9", "sanction_fy": "2024-25",
         "mp": mps, "amount": np.arange(30) * 1000.0 + 5000, "house": "LS", "is_usable": True},
        index=[f"dom{i}" for i in range(30)],
    )
    df = pd.concat([df, dominated])
    ctx = peers.build_context(df)
    comp = peers.group_composition(df, peers.group_key(df, peers.REFINEMENT_KEYS))
    expected = peers.qualifies(comp).fillna(False)
    assert expected.any()
    assert pd.isna(ctx.loc["dom0", "ref_group_key"])  # capped out
    assert comp.loc["dom0", "n_usable_excl_self"] >= peers.MIN_USABLE_PEERS
    assert comp.loc["dom0", "distinct_other_mps"] >= peers.MIN_OTHER_MPS
    pd.testing.assert_series_equal(ctx["ref_group_key"].notna(), expected, check_names=False)
    ref = ctx[ctx["ref_group_key"].notna()]
    assert (ref["ref_max_mp_share"] <= peers.MAX_MP_SHARE).all()
    assert (ref["ref_distinct_other_mps"] >= peers.MIN_OTHER_MPS).all()
    assert (ref["ref_n_usable_excl_self"] >= peers.MIN_USABLE_PEERS).all()


def test_mp_cap_excludes_a_group_that_passes_the_counts():
    """A group with 20 peers and 3 other MPs, where one MP holds 60% of the
    peers: it passes the counts but must still fail the rule."""
    mps = ["dom"] * 13 + ["b", "c", "d"] * 2 + ["e", "f"]  # 21 works
    f = _frame([100.0 + i for i in range(len(mps))], mps)
    comp = peers.group_composition(f, pd.Series("g1", index=f.index))
    row = comp.loc["w20"]  # MP "f": peers = 13 dom + 6 + 1 e = 20
    assert row["n_usable_excl_self"] == 20 and row["distinct_other_mps"] == 5
    assert row["max_mp_share"] == 13 / 20
    assert not peers.qualifies(comp).loc["w20"]


def test_level1_is_never_constituency_keyed():
    """Grep + structural check: no peer-level key in the new engine uses a
    constituency field (the old engine's Level-1 defect)."""
    for keys in list(peers.LEVEL_KEYS.values()) + [peers.REFINEMENT_KEYS]:
        assert not any("constituenc" in k.lower() for k in keys), keys
    assert peers.LEVEL_KEYS["L1"] == ("activity_type_id", "state_id", "sanction_fy")

    # No executable line of the module mentions constituency (docstrings,
    # which explain the old defect, are excluded).
    tree = ast.parse(PEERS_FILE.read_text(encoding="utf-8"))
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef)) and ast.get_docstring(node):
            ds = node.body[0]
            doc_lines.update(range(ds.lineno, ds.end_lineno + 1))
    for i, line in enumerate(PEERS_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        if i in doc_lines or line.strip().startswith("#"):
            continue
        assert not re.search(r"constituenc", line, re.IGNORECASE), f"peers.py:{i}: {line.strip()}"
