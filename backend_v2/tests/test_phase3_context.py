"""
Phase 3 real-data tests: peer context on the ingested + normalised
database, after scripts/run_context.py has written an analysis_run. CI
runs run_ingest.py, run_normalize.py and run_context.py before pytest.
Skips when no database is reachable (see db_session in conftest.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.analytics import context_run, peers
from app.models.analytics import PeerGroup, WorkContext
from app.models.work import Work

# BLUEPRINT.md §6 "Peer hierarchy" coverage column, and the tolerance
# documented in docs/phase3_coverage_report.md.
BLUEPRINT_COVERAGE = {
    ("L1", "ge10_peers"): 92.7,
    ("L1", "ge3_mps_in_group"): 90.5,
    ("L2", "ge10_peers"): 96.8,
    ("L3", "ge10_peers"): 99.5,
    ("REF", "ge3_mps_in_group"): 19.1,
}
TOLERANCE_PP = 1.5


@pytest.fixture(scope="module")
def run(db_session):
    r = context_run.latest_run(db_session)
    if r is None:
        pytest.skip("no complete analysis_run -- run scripts/run_context.py first")
    return r


@pytest.fixture(scope="module")
def frame(db_session, run):
    return context_run.load_frame(db_session, run.source_snapshot_id)


@pytest.fixture(scope="module")
def stored(db_session, run):
    df = pd.read_sql(
        select(WorkContext).where(WorkContext.run_id == run.id), db_session.connection()
    ).set_index("work_key")
    for col in ("amount_used", "peer_median", "peer_iqr", "peer_mad", "ref_median"):
        df[col] = pd.to_numeric(df[col])
    return df


def test_coverage_reproduces_blueprint_within_tolerance(frame):
    n = len(frame)
    for (level, measure), expected in BLUEPRINT_COVERAGE.items():
        keys = peers.REFINEMENT_KEYS if level == "REF" else peers.LEVEL_KEYS[level]
        comp = peers.group_composition(frame, peers.group_key(frame, keys))
        col = "n_usable_excl_self" if measure == "ge10_peers" else "distinct_mps_in_group"
        threshold = 10 if measure == "ge10_peers" else 3
        measured = 100 * (comp[col] >= threshold).sum() / n
        assert abs(measured - expected) <= TOLERANCE_PP, (level, measure, round(measured, 2), expected)


def test_stored_l1_diagnostics_match_blueprint(stored):
    """The same L1 figures, read straight off work_context."""
    n = len(stored)
    ge10 = 100 * (stored["l1_n_usable_excl_self"] >= 10).sum() / n
    ge3 = 100 * (stored["l1_distinct_mps_in_group"] >= 3).sum() / n
    assert abs(ge10 - 92.7) <= TOLERANCE_PP
    assert abs(ge3 - 90.5) <= TOLERANCE_PP


def test_every_assigned_group_obeys_the_rule(db_session, run):
    base = select(func.count(WorkContext.id)).where(
        WorkContext.run_id == run.id, WorkContext.level.is_not(None)
    )
    assigned = db_session.execute(base).scalar_one()
    assert assigned > 0
    for violation in (
        WorkContext.max_mp_share > peers.MAX_MP_SHARE,
        WorkContext.n_usable_excl_self < peers.MIN_USABLE_PEERS,
        WorkContext.distinct_other_mps < peers.MIN_OTHER_MPS,
        WorkContext.peer_median.is_(None),
    ):
        assert db_session.execute(base.where(violation)).scalar_one() == 0, str(violation)


def test_every_refinement_group_obeys_the_capped_rule(db_session, run):
    base = select(func.count(WorkContext.id)).where(
        WorkContext.run_id == run.id, WorkContext.ref_group_key.is_not(None)
    )
    assert db_session.execute(base).scalar_one() > 0
    for violation in (
        WorkContext.ref_max_mp_share > peers.MAX_MP_SHARE,
        WorkContext.ref_n_usable_excl_self < peers.MIN_USABLE_PEERS,
        WorkContext.ref_distinct_other_mps < peers.MIN_OTHER_MPS,
        WorkContext.ref_median.is_(None),
    ):
        assert db_session.execute(base.where(violation)).scalar_one() == 0, str(violation)


def test_no_baseline_includes_its_own_amount(frame, stored):
    """Proof on real data: for a deterministic sample of assigned works,
    recompute the group from scratch. The stored median must equal the
    median of the OTHER usable works and the stored count must equal their
    number. The sample is chosen so that including the work itself would
    change the median, which makes the check able to fail."""
    assigned = stored[stored["level"].notna()].sort_index()
    usable = frame[frame["is_usable"]]
    members_by_level = {
        lvl: {g: grp["amount"] for g, grp in usable.groupby(peers.group_key(usable, k))}
        for lvl, k in peers.LEVEL_KEYS.items()
    }
    checked = 0
    for work_key, row in assigned.to_dict("index").items():
        if checked >= 200:
            break
        if not frame.at[work_key, "is_usable"]:
            continue
        members = members_by_level[row["level"]][row["group_key"]]
        with_self = members.to_numpy(dtype=float)
        others = members.drop(index=work_key).to_numpy(dtype=float)
        if np.isclose(np.median(others), np.median(with_self)):
            continue
        assert row["n_usable_excl_self"] == len(others), work_key
        assert np.isclose(row["peer_median"], np.median(others), atol=1e-3), work_key
        assert not np.isclose(row["peer_median"], np.median(with_self), atol=1e-3), work_key
        checked += 1
    assert checked >= 200


def test_level1_group_keys_are_never_constituency_keyed(db_session, run):
    rows = db_session.execute(
        select(PeerGroup.group_key).where(PeerGroup.run_id == run.id, PeerGroup.level == "L1")
    ).scalars()
    keys = list(rows)
    assert keys
    for k in keys:
        assert [part.split("=")[0] for part in k.split("|")] == list(peers.LEVEL_KEYS["L1"]), k
    groups = select(func.count(PeerGroup.id)).where(PeerGroup.run_id == run.id)
    assert db_session.execute(groups.where(PeerGroup.group_key.ilike("%constituen%"))).scalar_one() == 0


def test_peer_groups_mix_houses_and_house_is_not_a_key(db_session, run):
    groups = select(func.count(PeerGroup.id)).where(PeerGroup.run_id == run.id)
    assert db_session.execute(groups.where(PeerGroup.n_ls > 0, PeerGroup.n_rs > 0)).scalar_one() > 0
    assert db_session.execute(groups.where(PeerGroup.group_key.ilike("%house%"))).scalar_one() == 0


@pytest.mark.parametrize("house", ["LS", "RS"])
def test_house_filter_returns_only_that_house(db_session, run, stored, house):
    rows = context_run.work_contexts(db_session, run.id, house=house)
    assert rows
    assert {r.house for r in rows} == {house}
    assert len(rows) == int((stored["house"] == house).sum())
    # and the house on work_context is the work's own house
    sample = [r.work_key for r in rows[:500]]
    work_houses = set(db_session.execute(select(Work.house).where(Work.work_key.in_(sample))).scalars())
    assert work_houses == {house}


def test_house_omitted_returns_everything_and_filtering_never_changes_a_baseline(db_session, run, stored):
    everything = context_run.work_contexts(db_session, run.id)
    assert len(everything) == len(stored)
    ls = {r.work_key: r.peer_median for r in context_run.work_contexts(db_session, run.id, house="LS")}
    rs = {r.work_key: r.peer_median for r in context_run.work_contexts(db_session, run.id, house="RS")}
    assert len(ls) + len(rs) == len(everything)
    for r in everything:
        assert (ls if r.house == "LS" else rs)[r.work_key] == r.peer_median


def test_house_filter_rejects_invalid_value(db_session, run):
    with pytest.raises(ValueError):
        context_run.work_contexts(db_session, run.id, house="XX")


def test_same_input_rerun_reproduces_output_hash(frame, run):
    assert context_run.output_hash(peers.build_context(frame)) == run.output_hash
