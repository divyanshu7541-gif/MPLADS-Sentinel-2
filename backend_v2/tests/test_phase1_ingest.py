"""
Phase 1 integration tests. Assume a migrated, already-ingested database
(see db_session fixture in conftest.py) -- CI runs `alembic upgrade head`
and `scripts/run_ingest.py` before pytest, exactly the sequence a developer
runs locally. Every test here maps directly to one bullet in the Phase 1
brief's TESTS section.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from app.core.config import get_settings
from app.ingest import pipeline
from app.ingest.constants import CORE_FILES
from app.models.geo import GeoArea, GeoNameCrosswalk
from app.models.provenance import ControlTotal, ImportReject, RawFile, RawRow
from app.models.reference import State
from app.models.work import Work


def test_all_nine_core_files_reconcile(db_session):
    """BLUEPRINT.md §2: every one of the 9 core files' body sum matches its
    footer total and BLUEPRINT.md's control total, within rounding
    tolerance."""
    totals = db_session.execute(select(ControlTotal)).scalars().all()
    assert len(totals) == 9, f"expected 9 core-file reconciliations, found {len(totals)}"
    for ct in totals:
        assert ct.footer_match, f"{ct.measure}: body sum does not match its own file's footer total"
        assert ct.blueprint_match, f"{ct.measure}: computed crore value does not match BLUEPRINT.md §2"
        assert ct.status == "pass"


def test_works_recommended_rajyasabha_absent_is_expected(db_session):
    """The RS variant of works_recommended is EXPECTED to be absent from
    snapshot_a -- assert this as expected, not as a failure (BLUEPRINT.md
    §2 hard limit 6). The gap is filled from Snapshot B instead."""
    data_dir = Path(get_settings().data_dir)
    assert not (data_dir / "raw/snapshot_a/works_recommended_RajyaSabha_alltenures.csv").exists()

    filenames = {r[0] for r in db_session.execute(select(RawFile.filename))}
    assert "works_recommended_RajyaSabha_alltenures.csv" not in filenames

    # The gap-fill: Snapshot B's recommended-works file contributed RS work
    # rows via the house_column rule.
    gap_fill_count = db_session.execute(
        select(func.count(Work.work_key)).where(Work.house_source == "house_column", Work.house == "RS")
    ).scalar_one()
    assert gap_fill_count > 0, "expected Snapshot B to have gap-filled at least some RS recommended works"


def test_import_reject_empty_for_core_files(db_session):
    """Acceptance criteria: import_reject has zero rows for the 9 core files."""
    core_filenames = {Path(f.relative_path).name for f in CORE_FILES}
    rejected_core_files = db_session.execute(
        select(RawFile.filename, func.count(ImportReject.id))
        .join(ImportReject, ImportReject.raw_file_id == RawFile.id)
        .where(RawFile.filename.in_(core_filenames))
        .group_by(RawFile.filename)
    ).all()
    assert rejected_core_files == [], f"core files have rejected rows: {rejected_core_files}"


def test_raw_row_complete_and_matches_row_count(db_session):
    """Acceptance criteria: raw_row is complete -- every raw_file's
    recorded row_count matches the number of raw_row rows actually stored
    for it (insert-only: nothing should ever cause these to drift)."""
    mismatches = db_session.execute(
        select(RawFile.filename, RawFile.row_count, func.count(RawRow.id))
        .join(RawRow, RawRow.raw_file_id == RawFile.id, isouter=True)
        .group_by(RawFile.filename, RawFile.row_count)
        .having(RawFile.row_count != func.count(RawRow.id))
    ).all()
    assert mismatches == [], f"raw_row count doesn't match raw_file.row_count for: {mismatches}"

    total = db_session.execute(select(func.count(RawRow.id))).scalar_one()
    assert total > 500_000, f"expected the real dataset's ~650k rows, found only {total}"


def test_house_tagging_manifest(db_session):
    """Every ingested work row has house in {'LS','RS'}, and house_source
    correctly reflects which of the two rules tagged it (filename for
    Snapshot A's core files + calamity, in-file House column for
    prior_cycle/Snapshot B) -- verified against the actual source file each
    sampled row came from."""
    rows = db_session.execute(
        select(Work.house, Work.house_source, RawFile.filename)
        .join(RawFile, Work.first_seen_raw_file_id == RawFile.id)
        .limit(2000)
    ).all()
    assert rows, "no work rows found -- ingest did not populate `work`"

    for house, house_source, filename in rows:
        assert house in ("LS", "RS"), f"{filename}: invalid house {house!r}"
        if house_source == "filename":
            assert "LokSabha" in filename or "RajyaSabha" in filename, (
                f"{filename}: house_source='filename' but filename doesn't encode a house"
            )
            expected = "LS" if "LokSabha" in filename else "RS"
            assert house == expected, f"{filename}: house={house} but filename implies {expected}"
        elif house_source == "house_column":
            assert filename.startswith("mplads_") or filename.startswith("prior_cycle_"), (
                f"{filename}: house_source='house_column' but filename isn't a Snapshot B / prior_cycle file"
            )
        else:
            raise AssertionError(f"unexpected house_source {house_source!r}")

    total_work = db_session.execute(select(func.count(Work.work_key))).scalar_one()
    assert total_work > 100_000, f"expected well over 100k work rows, found {total_work}"


def test_geo_area_has_national_and_state_polygons(db_session):
    """Acceptance criteria: geo_area has at least national + state-level
    polygons with recorded source/licence."""
    national = db_session.execute(select(GeoArea).where(GeoArea.level == "national")).scalars().all()
    states = db_session.execute(select(GeoArea).where(GeoArea.level == "state")).scalars().all()
    assert len(national) >= 1
    assert len(states) >= 30, f"expected ~36 Indian states/UTs, found {len(states)}"
    for area in national + states:
        assert area.source, f"{area.name}: missing source"
        assert area.licence, f"{area.name}: missing licence"
        assert area.geometry, f"{area.name}: missing geometry"


def test_geo_crosswalk_match_rate_is_reported_not_hard_failed(db_session):
    """The crosswalk match rate is reported, not hard-failed on -- assert
    it's recorded (every portal state has a crosswalk row, matched or not),
    not that it's 100%."""
    portal_state_count = db_session.execute(select(func.count(State.id))).scalar_one()
    crosswalk_count = db_session.execute(
        select(func.count(GeoNameCrosswalk.id)).where(GeoNameCrosswalk.level == "state")
    ).scalar_one()
    assert crosswalk_count == portal_state_count, (
        "every portal state should have a crosswalk row (matched or 'unmatched'), none silently dropped"
    )
    matched = db_session.execute(
        select(func.count(GeoNameCrosswalk.id)).where(
            GeoNameCrosswalk.level == "state", GeoNameCrosswalk.geo_area_id.is_not(None)
        )
    ).scalar_one()
    assert matched > 0  # not asserting 100% -- reported, not hard-failed


def test_idempotent_rerun_reproduces_same_output_hash(db_session):
    """Re-running P0-P2 on the same files reproduces the same row counts
    and output hash."""
    data_dir = Path(get_settings().data_dir)

    before_raw_row = db_session.execute(select(func.count(RawRow.id))).scalar_one()
    before_work = db_session.execute(select(func.count(Work.work_key))).scalar_one()
    before_hash = pipeline.compute_output_hash(db_session)

    result = pipeline.run_all(db_session, data_dir)
    db_session.commit()

    after_raw_row = db_session.execute(select(func.count(RawRow.id))).scalar_one()
    after_work = db_session.execute(select(func.count(Work.work_key))).scalar_one()

    assert after_raw_row == before_raw_row, "re-running added raw_row rows -- not idempotent"
    assert after_work == before_work, "re-running added work rows -- not idempotent"
    assert result["output_hash"] == before_hash, "output hash changed on a no-op re-run"
    assert all(f.get("status") == "skipped_already_ingested" for f in result["files"]), (
        "expected every file to be skipped as already-ingested on the second run"
    )
