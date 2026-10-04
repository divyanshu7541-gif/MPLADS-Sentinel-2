"""Phase 5a step 1: portal IDs shared across Houses are split into two
House-qualified works. Real-data tests; they assume run_identity_split.py
has run (CI runs it after run_normalize.py) and skip otherwise."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.ingest import p6_identity_split
from app.ingest.pipeline import get_or_create_snapshot
from app.models.analytics import ComplianceResult, RiskResult
from app.models.work import Work, WorkIdentitySplit
from app.models.work_related import Payment, WorkState

N_SHARED = 136


@pytest.fixture(scope="module")
def split_rows(db_session):
    rows = db_session.execute(select(WorkIdentitySplit)).scalars().all()
    if not rows:
        pytest.skip("identity split not applied -- run scripts/run_identity_split.py first")
    return rows


@pytest.fixture(scope="module")
def post_split_run(db_session, split_rows):
    rid = db_session.execute(select(func.max(RiskResult.run_id))).scalar()
    if rid is None:
        pytest.skip("no risk_result run after the split")
    return rid


def test_the_136_shared_ids_now_exist_as_272_independent_records(db_session, split_rows):
    assert len(split_rows) == N_SHARED
    ids = [s.portal_id for s in split_rows]
    recs = db_session.execute(select(Work).where(Work.portal_id.in_(ids))).scalars().all()
    assert len(recs) == 2 * N_SHARED
    by_house = {"LS": [r for r in recs if r.house == "LS"], "RS": [r for r in recs if r.house == "RS"]}
    assert len(by_house["LS"]) == len(by_house["RS"]) == N_SHARED
    # the compound identity is unique across the whole table
    dups = db_session.execute(text(
        "SELECT COUNT(*) FROM (SELECT portal_id, house FROM work GROUP BY 1, 2 HAVING COUNT(*) > 1) x"
    )).scalar_one()
    assert dups == 0
    for s in split_rows:
        assert s.ls_work_key == s.portal_id and s.rs_work_key == f"{s.portal_id}-RS"


def test_each_half_carries_its_own_houses_attributes(db_session, split_rows):
    """Row-level attribution against the raw files: the LS record's MP and
    district are the LS recommended row's; the RS record's are the RS
    sanctioned row's; they differ in every pair."""
    raw = db_session.execute(text(
        """
        SELECT TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') AS pid, rf.filename,
               rr.data->>'MP_NAME' AS mp, rr.data->>'IDA_NAME' AS ida
        FROM raw_row rr JOIN raw_file rf ON rf.id = rr.raw_file_id
        WHERE rf.filename IN ('works_recommended_LokSabha_alltenures.csv',
                              'works_sanctioned_RajyaSabha_alltenures.csv')
          AND TRIM(rr.data->>'WORK_RECOMMENDATION_DTL_ID') IN
              (SELECT portal_id FROM work_identity_split)
        """
    )).all()
    src = {(pid, "LS" if "LokSabha" in fn else "RS"): (mp, ida) for pid, fn, mp, ida in raw}
    ida_name = dict(db_session.execute(text("SELECT id, ida_name FROM district_authority")).all())
    for s in split_rows:
        ls = db_session.get(Work, s.ls_work_key)
        rs = db_session.get(Work, s.rs_work_key)
        assert (ls.house, rs.house) == ("LS", "RS")
        assert (ls.raw_mp_name, ida_name[ls.district_authority_id]) == src[(s.portal_id, "LS")]
        assert (rs.raw_mp_name, ida_name[rs.district_authority_id]) == src[(s.portal_id, "RS")]
        assert ls.raw_mp_name != rs.raw_mp_name
        assert rs.activity_type_id is not None and rs.description_normalized


def test_lifecycle_and_payments_follow_the_right_half(db_session, split_rows):
    snap = db_session.execute(text("SELECT id FROM source_snapshot WHERE code = 'snapshot_a'")).scalar_one()
    for s in split_rows:
        ls = db_session.execute(select(WorkState).where(
            WorkState.work_key == s.ls_work_key, WorkState.source_snapshot_id == snap)).scalar_one()
        rs = db_session.execute(select(WorkState).where(
            WorkState.work_key == s.rs_work_key, WorkState.source_snapshot_id == snap)).scalar_one()
        assert ls.lifecycle_status == "recommended" and ls.recommended_date is not None
        assert ls.sanction_date is None and ls.sanction_amount is None and ls.actual_end_date is None
        assert rs.lifecycle_status in ("sanctioned", "completed") and rs.sanction_date is not None
        assert rs.recommended_date is None and rs.recommended_amount is None
    on_ls = db_session.execute(select(func.count(Payment.id)).where(
        Payment.work_key.in_([s.ls_work_key for s in split_rows]))).scalar_one()
    assert on_ls == 0


def test_split_is_idempotent(db_session, split_rows):
    snap = get_or_create_snapshot(db_session, "snapshot_a")
    stats = p6_identity_split.split_shared_portal_ids(db_session, snap)
    db_session.rollback()
    assert stats["split_now"] == 0 and stats["already_split"] == N_SHARED


def test_no_work_mixes_two_houses_after_the_split(db_session, post_split_run):
    failed = db_session.execute(select(func.count()).select_from(ComplianceResult).where(
        ComplianceResult.run_id == post_split_run, ComplianceResult.check_code == "C8",
        ComplianceResult.rule == "one_record_per_portal_id_and_house",
        ComplianceResult.passed.is_(False))).scalar_one()
    assert failed == 0


def test_c4_after_the_split_is_the_reported_number(db_session, post_split_run):
    """Phase 5a report: C4 went 132 -> 0. Every one of the 132 was a merged
    LS/RS pair; after the split no work carries dates from two works."""
    failed = db_session.execute(select(func.count()).select_from(ComplianceResult).where(
        ComplianceResult.run_id == post_split_run, ComplianceResult.check_code == "C4",
        ComplianceResult.passed.is_(False))).scalar_one()
    assert failed == 0


def test_flag2_rows_unchanged_and_no_longer_attached_to_sanctioned_work(db_session, post_split_run):
    rows = db_session.execute(text(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE ws.lifecycle_status IN ('sanctioned', 'completed')),
               COUNT(*) FILTER (WHERE c.work_key IN (SELECT ls_work_key FROM work_identity_split))
        FROM compliance_result c
        JOIN work_state ws ON ws.work_key = c.work_key
          AND ws.source_snapshot_id = (SELECT id FROM source_snapshot WHERE code = 'snapshot_a')
        WHERE c.run_id = :r AND c.check_code = 'C8' AND c.rule = 'flag2_has_stage_or_sanction'
          AND NOT c.passed
        """), {"r": post_split_run}).one()
    total, on_sanctioned, on_split_ls = rows
    assert total == 499
    assert on_sanctioned == 0
    assert on_split_ls == N_SHARED


def test_scored_population_equals_the_sanctioned_file_counts(db_session, post_split_run):
    counts = dict(db_session.execute(
        text("SELECT house, COUNT(*) FROM work_context WHERE run_id = :r GROUP BY house"),
        {"r": post_split_run},
    ).all())
    assert counts == {"LS": 78232, "RS": 19274}
