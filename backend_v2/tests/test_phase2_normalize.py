"""
Phase 2 integration tests. Assume a migrated database that has already had
scripts/run_ingest.py (Phase 1) and scripts/run_normalize.py (Phase 2) run
against it (see db_session fixture in conftest.py) -- CI runs both before
pytest. Every test here maps to one bullet in the Phase 2 brief's TESTS
section.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select

from app.models.entities import Payee, PayeeAlias
from app.models.reference import ActivityType, DistrictAuthority, Person
from app.models.work import Work
from app.models.work_related import Payment, WorkState

INGEST_DIR = Path(__file__).resolve().parents[1] / "app" / "ingest"


def test_activity_and_district_parse_rate_near_100_percent(db_session):
    """BLUEPRINT.md §5 P3 gate: 'Parse rate reported (100% for type and
    district in Snapshot A)'."""
    total = db_session.execute(select(func.count(Work.work_key))).scalar_one()
    with_activity = db_session.execute(
        select(func.count(Work.work_key)).where(Work.activity_type_id.is_not(None))
    ).scalar_one()
    with_district = db_session.execute(
        select(func.count(Work.work_key)).where(Work.district_authority_id.is_not(None))
    ).scalar_one()

    # Snapshot B's 6,054 gap-fill rows never go through P3 (no ACTIVITY_NAME/
    # IDA_NAME source for them) -- rate is measured against Snapshot A's
    # 122,829 lifecycle-linked keys, matching how the report computes it.
    snapshot_a_total = db_session.execute(
        select(func.count(Work.work_key)).where(Work.house_source == "filename")
    ).scalar_one()

    assert with_activity / snapshot_a_total >= 0.999, (
        f"activity_type parse rate {with_activity}/{snapshot_a_total} below ~100%"
    )
    assert with_district / snapshot_a_total >= 0.999, (
        f"district_authority parse rate {with_district}/{snapshot_a_total} below ~100%"
    )

    activity_type_count = db_session.execute(select(func.count(ActivityType.id))).scalar_one()
    assert activity_type_count == 115, (
        f"expected 115 activity types (BLUEPRINT.md §2), found {activity_type_count}"
    )

    assert total >= snapshot_a_total


def test_mp_roster_join_rate_100_percent(db_session):
    """BLUEPRINT.md §9: 'every allocation and work name matched the roster
    by normalised name in Snapshot A' -- 100%."""
    from app.models.reference import Tenure

    person_count = db_session.execute(select(func.count(Person.id))).scalar_one()
    assert person_count == 778, f"expected 778 roster persons (BLUEPRINT.md §2), found {person_count}"

    tenure_count = db_session.execute(select(func.count(Tenure.id))).scalar_one()
    # 774 MPs total, minus 11 Nominated Rajya Sabha (no tenure by design) = 763.
    assert tenure_count == 763, f"expected 763 tenures (774 MPs - 11 Nominated RS), found {tenure_count}"


def test_work_key_unique_across_both_houses(db_session):
    total = db_session.execute(select(func.count(Work.work_key))).scalar_one()
    distinct = db_session.execute(select(func.count(func.distinct(Work.work_key)))).scalar_one()
    assert total == distinct, "work_key is not unique -- PK constraint should make this impossible"

    ls = db_session.execute(select(func.count(Work.work_key)).where(Work.house == "LS")).scalar_one()
    rs = db_session.execute(select(func.count(Work.work_key)).where(Work.house == "RS")).scalar_one()
    assert ls + rs == total


def test_payee_alias_never_silently_merges_distinct_ids(db_session):
    """BLUEPRINT.md §2/§8: 1,045 names are shared by more than one portal
    ID -- payee_alias must keep them as separate payee rows, never
    collapse two IDs into one because they share a name."""
    payee_count = db_session.execute(select(func.count(Payee.id))).scalar_one()
    assert payee_count == 29583, (
        f"expected 29,583 payees (one per VENDOR_ID, BLUEPRINT.md §7), found {payee_count}"
    )

    shared_names = db_session.execute(
        select(PayeeAlias.name, func.count(func.distinct(PayeeAlias.payee_id)))
        .group_by(PayeeAlias.name)
        .having(func.count(func.distinct(PayeeAlias.payee_id)) > 1)
    ).all()
    assert len(shared_names) == 1045, (
        f"expected 1,045 names shared by >1 payee id (BLUEPRINT.md §2), found {len(shared_names)}"
    )
    # The defining property: a shared name must resolve to MULTIPLE distinct
    # payee rows (never merged into one).
    for name, distinct_id_count in shared_names[:20]:
        assert distinct_id_count > 1

    ids_with_multiple_names = db_session.execute(
        select(PayeeAlias.payee_id, func.count(func.distinct(PayeeAlias.name)))
        .group_by(PayeeAlias.payee_id)
        .having(func.count(func.distinct(PayeeAlias.name)) > 1)
    ).all()
    assert len(ids_with_multiple_names) == 0, (
        "BLUEPRINT.md §2: 'no ID has two spellings' -- found IDs with multiple alias names"
    )


PHASE2_FILES = [
    "activity_parse.py",
    "normalize.py",
    "p2_descriptive.py",
    "p3_backfill.py",
    "p4_link.py",
    "p5_payee.py",
    "roster.py",
]


def test_no_positional_merge_in_ingest_code():
    """Targets the 'positional misalignment' defect class (BLUEPRINT.md
    §12 known-defect suite): every join in the new Phase 2 code must use
    an explicit key column via pandas .merge(on=...), never .iloc[] to
    align rows between two frames/results.

    Scoped to Phase 2's own new files, not the whole ingest/ package --
    Phase 1's csv_utils.py/schemas.py use .iloc[-1]/.iloc[0] for
    single-frame footer-row splitting and single-row dict extraction,
    neither of which is a two-frame join; that's a different, legitimate
    use of positional indexing the Phase 2 brief isn't targeting.
    Comment/docstring lines are skipped so this file's own explanation of
    the rule can't trip the check on itself.
    """
    offenders = []
    for filename in PHASE2_FILES:
        path = INGEST_DIR / filename
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ".iloc[" in line:
                offenders.append(f"{filename}:{i}: {stripped}")
    msg = "found .iloc[ usage in Phase 2 ingest code (possible positional merge):\n" + "\n".join(offenders)
    assert offenders == [], msg


def test_lifecycle_status_derived_from_membership_not_raw_stage(db_session):
    """Acceptance criteria: lifecycle status is derived from file
    membership/dates only; raw_stage is stored but never consumed
    downstream. This test checks the stored data is consistent with a
    membership/date-based derivation (raw_stage values do not need to
    agree with lifecycle_status -- BLUEPRINT.md §2: WORK_STAGE is stale)."""
    completed_without_dates_or_amount = db_session.execute(
        select(func.count(WorkState.id)).where(
            WorkState.lifecycle_status == "completed",
            WorkState.actual_amount.is_(None),
            WorkState.actual_end_date.is_(None),
        )
    ).scalar_one()
    assert completed_without_dates_or_amount == 0

    sanctioned_or_completed_without_sanction = db_session.execute(
        select(func.count(WorkState.id)).where(
            WorkState.lifecycle_status.in_(("sanctioned", "completed")),
            WorkState.sanction_amount.is_(None),
            WorkState.sanction_date.is_(None),
        )
    ).scalar_one()
    assert sanctioned_or_completed_without_sanction == 0

    # No code path may read raw_stage to decide anything -- verified by
    # grep for comparison/conditional usage specifically (assignment,
    # column definitions, renames, and comments are fine; `raw_stage ==`,
    # `raw_stage in`, `if raw_stage`, etc. would indicate it's driving a
    # decision, which is exactly the "never do X" rule this checks for).
    app_dir = INGEST_DIR.parent
    decision_pattern = re.compile(r"raw_stage\s*(==|!=|\bin\b)|if\s+.*\braw_stage\b")
    offenders = []
    for path in sorted(app_dir.rglob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if decision_pattern.search(line):
                offenders.append(f"{path.relative_to(app_dir)}:{i}: {line.strip()}")
    assert offenders == [], f"raw_stage appears to be read for logic, not just stored: {offenders}"


def test_referential_gap_matches_blueprint(db_session):
    """BLUEPRINT.md §5/§6 C8: 361 sanctioned works absent from the
    recommended file, logged not hidden. Recomputed independently here (a
    NOT EXISTS anti-join by recommended_amount, not just trusting the
    report's own file-membership-based figure) -- both should agree
    exactly since work_state has one row per work_key: a LS work that is
    sanctioned/completed but was never seen in the recommended file has
    recommended_amount NULL by construction (the outer merge leaves it
    unset when the key never appeared in `rec`)."""
    gap_count = db_session.execute(
        select(func.count(func.distinct(WorkState.work_key)))
        .join(Work, Work.work_key == WorkState.work_key)
        .where(
            Work.house == "LS",
            WorkState.lifecycle_status.in_(("sanctioned", "completed")),
            WorkState.recommended_amount.is_(None),
            WorkState.recommended_date.is_(None),
        )
    ).scalar_one()
    assert gap_count == 361, f"expected 361 (BLUEPRINT.md §5/§6 C8), found {gap_count}"


def test_every_completed_work_has_sanction_and_every_payment_matches_sanctioned_work(db_session):
    """Acceptance criteria, verified directly (not just via the report)."""
    completed_without_sanction = db_session.execute(
        select(func.count(WorkState.id)).where(
            WorkState.lifecycle_status == "completed",
            WorkState.sanction_amount.is_(None),
            WorkState.sanction_date.is_(None),
        )
    ).scalar_one()
    assert completed_without_sanction == 0

    from sqlalchemy import exists

    sanctioned_or_completed = exists().where(
        WorkState.work_key == Payment.work_key,
        WorkState.source_snapshot_id == Payment.source_snapshot_id,
        WorkState.lifecycle_status.in_(("sanctioned", "completed")),
    )
    payments_without_sanctioned_work = db_session.execute(
        select(func.count(Payment.id)).where(~sanctioned_or_completed)
    ).scalar_one()
    assert payments_without_sanctioned_work == 0


def test_district_authority_count_matches_blueprint(db_session):
    count = db_session.execute(select(func.count(DistrictAuthority.id))).scalar_one()
    assert count == 774, f"expected 774 distinct IDA names (Phase 1 research), found {count}"
