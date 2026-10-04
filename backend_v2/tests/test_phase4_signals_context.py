"""
Phase 4 real-data tests: the six base signals on the ingested + normalised
database, after scripts/run_signals.py has written an analysis_run (with
peer_group/work_context from Phase 3's context engine, and signal_result
from Phase 4). CI runs run_ingest.py, run_normalize.py and run_signals.py
before pytest. Skips when no database is reachable (see db_session in
conftest.py).
"""
from __future__ import annotations

import math

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.analytics import signals, signals_run
from app.models.analytics import SignalResult, WorkContext
from app.models.work import Work

SIGNAL_NAMES = signals_run.SIGNAL_ORDER


@pytest.fixture(scope="module")
def run(db_session):
    latest = db_session.execute(
        select(func.max(SignalResult.run_id))
    ).scalar_one_or_none()
    if latest is None:
        pytest.skip("no signal_result rows -- run scripts/run_signals.py first")
    return latest


@pytest.fixture(scope="module")
def n_works(db_session, run):
    return db_session.execute(
        select(func.count(func.distinct(WorkContext.work_key))).where(WorkContext.run_id == run)
    ).scalar_one()


def test_every_work_gets_a_row_for_all_six_signals(db_session, run, n_works):
    """Acceptance criterion: all six signals produce (score, eligibility)
    for every work, no missing pairs."""
    counts = dict(
        db_session.execute(
            select(SignalResult.signal, func.count(SignalResult.id))
            .where(SignalResult.run_id == run)
            .group_by(SignalResult.signal)
        ).all()
    )
    assert set(counts) == set(SIGNAL_NAMES), (set(counts), set(SIGNAL_NAMES))
    for name in SIGNAL_NAMES:
        assert counts[name] == n_works, (name, counts[name], n_works)


def test_every_row_is_is_base_true(db_session, run):
    non_base = db_session.execute(
        select(func.count(SignalResult.id)).where(SignalResult.run_id == run, ~SignalResult.is_base)
    ).scalar_one()
    assert non_base == 0


@pytest.mark.parametrize("signal_name", SIGNAL_NAMES)
def test_no_work_is_eligible_with_a_null_score(db_session, run, signal_name):
    """Phase 4 acceptance criterion, checked per signal against the full
    real dataset: "all six signals produce (score, eligibility) for every
    work, no missing pairs" -- eligible=true and score=null must never
    coexist for any work, on any signal.

    This is exactly the condition a real bug produced (an L2-assigned
    cost_anomaly row for work_key 123541, RS, came back eligible=true with
    score=null because its peer pool was built from Phase 3's per-row
    group_key string directly, which under-pools any work that fell back
    from Level 1 -- see the cost_anomaly_signal/lifecycle_delay_signal
    fixes and their unit-test regressions in test_phase4_signals_unit.py).
    That bug tripped the `ck_signal_result_eligible_has_score` CHECK
    constraint at insert time, so it could never reach this table again --
    but a DB constraint rejecting a bad row is not the same claim as "this
    can no longer occur", and a constraint violation surfaces as a crash
    in scripts/run_signals.py, not a labelled, per-signal test failure.
    This test makes the guarantee explicit and checks it directly, not by
    inference from the fact that the run completed."""
    base = select(func.count(SignalResult.id)).where(
        SignalResult.run_id == run, SignalResult.signal == signal_name
    )
    eligible_unscored = base.where(SignalResult.eligible, SignalResult.score.is_(None))
    ineligible_scored = base.where(~SignalResult.eligible, SignalResult.score.is_not(None))
    n = db_session.execute(base).scalar_one()
    assert n > 0, f"no {signal_name} rows for run {run}"
    assert db_session.execute(eligible_unscored).scalar_one() == 0
    assert db_session.execute(ineligible_scored).scalar_one() == 0


def test_scores_in_range_and_no_stored_nan(db_session, run):
    rows = db_session.execute(
        select(SignalResult.score, SignalResult.tail_percentile, SignalResult.reliability).where(
            SignalResult.run_id == run, SignalResult.eligible
        )
    ).all()
    assert rows
    for score, tail, reliability in rows:
        assert score is not None and 0.0 <= float(score) <= 1.0
        if tail is not None:
            assert 0.0 <= float(tail) <= 1.0
        if reliability is not None:
            assert 0.0 <= float(reliability) <= 1.0


def test_each_signal_has_a_plausible_eligible_share(db_session, run, n_works):
    """A coarse sanity range, not a tight assertion -- catches a signal
    that is silently firing for (almost) no one or everyone, without
    pinning an exact figure the way Phase 3's coverage test pins
    BLUEPRINT's numbers (no such published figures exist for Phase 4)."""
    counts = dict(
        db_session.execute(
            select(SignalResult.signal, func.count(SignalResult.id))
            .where(SignalResult.run_id == run, SignalResult.eligible)
            .group_by(SignalResult.signal)
        ).all()
    )
    for name in SIGNAL_NAMES:
        pct = 100 * counts.get(name, 0) / n_works
        assert 0.0 < pct < 100.0, (name, pct)


@pytest.mark.parametrize("house", ["LS", "RS"])
def test_house_filter_returns_only_that_house_with_unchanged_scores(db_session, run, house):
    """The real, no-recompute proof: filtering signal_result by house at
    read time returns exactly that House's subset of the SAME rows a full
    unfiltered read has, byte-for-byte on score/eligible/evidence -- never
    a different (recomputed) value."""
    full = {
        (r.work_key, r.signal): r
        for r in db_session.execute(select(SignalResult).where(SignalResult.run_id == run)).scalars()
    }
    filtered = db_session.execute(
        select(SignalResult).where(SignalResult.run_id == run, SignalResult.house == house)
    ).scalars().all()
    assert filtered
    assert {r.house for r in filtered} == {house}

    work_houses = dict(
        db_session.execute(select(Work.work_key, Work.house).where(Work.house == house)).all()
    )
    checked = 0
    for r in filtered:
        assert work_houses.get(r.work_key) == house  # house on the row is the work's own house
        twin = full[(r.work_key, r.signal)]
        assert twin.eligible == r.eligible
        assert twin.score == r.score
        assert twin.direction == r.direction
        assert twin.evidence == r.evidence
        checked += 1
    assert checked == len(filtered)


def test_house_omitted_equals_ls_plus_rs(db_session, run):
    everything = db_session.execute(
        select(func.count(SignalResult.id)).where(SignalResult.run_id == run)
    ).scalar_one()
    ls = db_session.execute(
        select(func.count(SignalResult.id)).where(SignalResult.run_id == run, SignalResult.house == "LS")
    ).scalar_one()
    rs = db_session.execute(
        select(func.count(SignalResult.id)).where(SignalResult.run_id == run, SignalResult.house == "RS")
    ).scalar_one()
    assert everything == ls + rs


def test_cost_anomaly_no_reliability_term_influenced_the_stored_score(db_session, run):
    """Real-data version of the source-level grep test: eligible rows with
    very different reliability values can still land on the same score
    when their z is the same, and the evidence's own z_log_scale is
    sufficient on its own to reconstruct the stored score (i.e. score is
    purely a function of z, not of reliability/peer count).

    Since Phase 5c the score is the empirical percentile rank of |z| among
    all evaluated works (signals.empirical_tail_score), so it is rebuilt
    from every row's z_log_scale; before that it was erf(|z|/sqrt(2)).
    Either way, nothing but z enters it."""
    rows = db_session.execute(
        select(SignalResult.work_key, SignalResult.score, SignalResult.evidence).where(
            SignalResult.run_id == run, SignalResult.signal == "cost_anomaly", SignalResult.eligible
        )
    ).all()
    assert rows
    keys = [k for k, _, _ in rows]
    stored = pd.Series([float(s) for _, s, _ in rows], index=keys)
    abs_z = pd.Series([abs(ev["z_log_scale"]) for _, _, ev in rows], index=keys)
    if "cost_anomaly" in signals.EMPIRICAL_SIGNALS:
        expected = signals.empirical_tail_score(abs_z)
    else:
        expected = abs_z.map(lambda z: math.erf(z / math.sqrt(2)))
    bad = (stored - expected).abs() > 1e-9
    assert not bad.any(), stored[bad].head().to_dict()


def test_near_duplicate_matched_work_key_is_never_itself(db_session, run):
    rows = db_session.execute(
        select(SignalResult.work_key, SignalResult.evidence).where(
            SignalResult.run_id == run,
            SignalResult.signal == "near_duplicate",
            SignalResult.eligible,
            SignalResult.score > 0,
        )
    ).all()
    assert rows
    for work_key, evidence in rows:
        assert evidence["matched_work_key"] != work_key


def test_lifecycle_delay_only_open_works_are_eligible(db_session, run):
    rows = db_session.execute(
        select(Work.work_key)
        .join(SignalResult, SignalResult.work_key == Work.work_key)
        .where(SignalResult.run_id == run, SignalResult.signal == "lifecycle_delay", SignalResult.eligible)
    ).scalars().all()
    assert rows
    from app.models.work_related import WorkState

    completed_among_them = db_session.execute(
        select(func.count(WorkState.id)).where(
            WorkState.work_key.in_(rows[:5000]), WorkState.lifecycle_status == "completed"
        )
    ).scalar_one()
    assert completed_among_them == 0


def test_same_input_rerun_reproduces_signals_output_hash(db_session, run):
    """BLUEPRINT.md §5: same inputs/config must reproduce the same output
    hash. Recomputes signals in-process from the stored run's own
    frame/ctx (Phase 3's engine is re-run identically -- see
    context_run's own idempotency test for that half) and compares
    against the hash stamped into analysis_run.notes by signals_run.run().
    """
    from app.analytics import context_run
    from app.models.analytics import AnalysisRun

    run_row = db_session.get(AnalysisRun, run)
    assert run_row.notes and "phase4_signals_output_hash=" in run_row.notes
    stored_hash = run_row.notes.split("phase4_signals_output_hash=")[1].split("\n")[0].strip()

    ctx_frame = context_run.load_frame(db_session, run_row.source_snapshot_id)
    from app.analytics import peers

    ctx = peers.build_context(ctx_frame)
    frame = signals_run.load_signal_frame(db_session, ctx_frame, run_row.source_snapshot_id)
    as_of = signals_run._resolve_as_of(db_session, run_row.source_snapshot_id)
    results = signals_run.compute_all_signals(frame, ctx, as_of)
    assert signals_run.signals_output_hash(results) == stored_hash
