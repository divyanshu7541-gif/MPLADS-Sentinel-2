"""Phase 5 real-data tests. They assume scripts/run_risk.py has run on a
migrated, ingested, normalised database (CI runs it as a step before
pytest) and skip when no risk_result exists yet."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.analytics import fusion, risk_run, signals
from app.models.analytics import AnalysisRun, ComplianceResult, RiskResult

DOCS = Path(__file__).resolve().parents[2] / "docs"
LEGACY_CALIBRATION = "normal-erf-v0"
GATE_SECTIONS = (
    "## 1. Reachability",
    "## 2. Score distributions",
    "## 3. Active-signal overlap",
    "## 4. Sensitivity",
    "## 5. Ablation",
    "## 6. Tier-boundary fixture tests",
    "## 7. Missing-signal behaviour",
    "## 8. Proposal and justification",
)


@pytest.fixture(scope="module")
def run_id(db_session):
    rid = db_session.execute(select(func.max(RiskResult.run_id))).scalar()
    if rid is None:
        pytest.skip("no risk_result rows -- run scripts/run_risk.py first")
    return rid


def _fails(db, run_id, check, rule):
    return db.execute(
        select(func.count()).select_from(ComplianceResult).where(
            ComplianceResult.run_id == run_id, ComplianceResult.check_code == check,
            ComplianceResult.rule == rule, ComplianceResult.passed.is_(False))
    ).scalar_one()


def _evaluated(db, run_id, check, rule):
    return db.execute(
        select(func.count()).select_from(ComplianceResult).where(
            ComplianceResult.run_id == run_id, ComplianceResult.check_code == check,
            ComplianceResult.rule == rule)
    ).scalar_one()


# ---- CI gate check ----------------------------------------------------------

def _gate_reports() -> list[dict]:
    """Every committed gate report (phase5_gate_report.md, *_v2.md, ...)
    with its meta line. A report without signal_calibration predates the
    Phase 5a calibration and covers the original normal mapping."""
    out = []
    for path in sorted(DOCS.glob("phase5_gate_report*.md")):
        body = path.read_text(encoding="utf-8")
        m = re.search(
            r"<!-- gate-meta run_id=(\d+) fusion_config_hash=([0-9a-f]{64})"
            r"(?: signal_calibration=(\S+))? -->", body)
        assert m, f"{path.name} has no gate-meta line"
        for section in GATE_SECTIONS:
            assert section in body, f"{path.name} is missing section {section!r}"
        out.append({"name": path.name, "run": int(m.group(1)), "hash": m.group(2),
                    "calibration": m.group(3) or LEGACY_CALIBRATION})
    return out


def test_every_run_with_risk_results_is_covered_by_a_gate_report(db_session):
    """CI gate: risk_result may exist for a run only if a committed gate
    report was produced for that run or an earlier one (run_id <= it), for
    the SAME fusion configuration AND the same signal calibration, and has
    all eight required sections. The code's current fusion config and
    calibration must themselves be covered by a gate report."""
    reports = _gate_reports()
    assert any(r["hash"] == fusion.fusion_config_hash() and r["calibration"] == signals.SIGNAL_CALIBRATION
               for r in reports), "the code's fusion config + signal calibration has no gate report"
    runs = [r for (r,) in db_session.execute(select(RiskResult.run_id).distinct()).all()]
    for rid in runs:
        row = db_session.get(AnalysisRun, rid)
        run_hash = risk_run.note_value(row, "phase5_fusion_config_hash")
        run_cal = risk_run.note_value(row, "phase5_signal_calibration") or LEGACY_CALIBRATION
        covering = [r["name"] for r in reports
                    if r["run"] <= rid and r["hash"] == run_hash and r["calibration"] == run_cal]
        assert covering, (
            f"run {rid} (calibration {run_cal}) has risk_result but no gate report dated at or before it "
            f"for that fusion config and calibration; reports: {reports}"
        )


# ---- storage ----------------------------------------------------------------

def test_both_configs_are_stored_for_every_scored_work(db_session, run_id):
    n_signal_works = db_session.execute(
        text("SELECT COUNT(DISTINCT work_key) FROM signal_result WHERE run_id = :r"), {"r": run_id}
    ).scalar_one()
    for c in fusion.CONFIGS:
        n = db_session.execute(
            select(func.count(func.distinct(RiskResult.work_key))).where(
                RiskResult.run_id == run_id, RiskResult.config_name == c)
        ).scalar_one()
        assert n == n_signal_works, f"{c}: {n} works vs {n_signal_works} scored"


def test_stored_eligible_and_active_counts_match_signal_result(db_session, run_id):
    """n_eligible / base_signal_count on every risk row agree with the
    signal rows they were fused from (no NULL score ever counted)."""
    mismatches = db_session.execute(text(
        """
        WITH s AS (
          SELECT work_key, COUNT(*) FILTER (WHERE eligible) AS n_el,
                 COUNT(*) FILTER (WHERE eligible AND score > :thr) AS k
          FROM signal_result WHERE run_id = :r GROUP BY work_key)
        SELECT COUNT(*) FROM risk_result rr JOIN s USING (work_key)
        WHERE rr.run_id = :r AND (rr.n_eligible <> s.n_el OR rr.base_signal_count <> s.k)
        """), {"r": run_id, "thr": fusion.ACTIVE_THRESHOLD}).scalar_one()
    assert mismatches == 0


def test_stored_risk_hash_reproduces_from_stored_inputs(db_session, run_id):
    row = db_session.get(AnalysisRun, run_id)
    stored = risk_run.note_value(row, "phase5_risk_output_hash")
    assert stored
    data = risk_run.load_run(db_session, run_id)
    assert fusion.risk_output_hash(data["fused"], data["conf"]["confidence"]) == stored


# ---- compliance baselines (BLUEPRINT.md §6) ------------------------------------

def test_c1_c2_c3_zero_violations(db_session, run_id):
    assert _evaluated(db_session, run_id, "C1", "actual_le_sanction") == 43842
    for check, rule in (("C1", "actual_le_sanction"), ("C2", "payments_le_sanction"),
                        ("C3", "no_payment_before_sanction")):
        assert _fails(db_session, run_id, check, rule) == 0, check


def test_c4_matches_blueprint_zero(db_session, run_id):
    """BLUEPRINT baseline 0. Before the Phase 5a identity split all 132
    violations were merged LS/RS pairs; the split removed them
    (tests/test_phase5a_identity_split.py)."""
    assert _fails(db_session, run_id, "C4", "dates_in_order") == 0


def test_c5_matches_blueprint(db_session, run_id):
    ev = _evaluated(db_session, run_id, "C5", "completed_within_one_year")
    over = _fails(db_session, run_id, "C5", "completed_within_one_year")
    assert abs(100 * over / ev - 11.9) < 0.05
    # Deliberate rule (docs/phase4_addendum.md section 5): open works "more
    # than 365 days" past sanction at the snapshot date = 13,476. BLUEPRINT's
    # 13,562 is exactly "365 days or more"; the 86-work gap is the works
    # sanctioned exactly one year before. Pin both so the boundary is explicit.
    open_over = _fails(db_session, run_id, "C5", "open_within_one_year")
    assert open_over == 13476
    inclusive = db_session.execute(text(
        "SELECT COUNT(*) FROM compliance_result WHERE run_id = :r AND check_code = 'C5'"
        " AND rule = 'open_within_one_year' AND (detail->>'days_open_at_as_of')::int >= 365"),
        {"r": run_id}).scalar_one()
    assert inclusive == 13562


def test_c6_c7_c8_c9_match_blueprint(db_session, run_id):
    assert _fails(db_session, run_id, "C6", "completed_has_payment") == 100
    assert _fails(db_session, run_id, "C7", "recommended_le_allocated") == 0
    assert _fails(db_session, run_id, "C7", "expenditure_le_recommended") == 0
    assert _fails(db_session, run_id, "C8", "sanctioned_in_recommended_file") == 361
    assert _fails(db_session, run_id, "C8", "flag2_has_stage_or_sanction") == 499
    assert _evaluated(db_session, run_id, "C9", "reconciles_to_portal_total") == 9
    assert _fails(db_session, run_id, "C9", "reconciles_to_portal_total") == 0


def test_c8_every_portal_id_and_house_has_its_own_record(db_session, run_id):
    assert _evaluated(db_session, run_id, "C8", "one_record_per_portal_id_and_house") > 0
    assert _fails(db_session, run_id, "C8", "one_record_per_portal_id_and_house") == 0


# ---- house filter -------------------------------------------------------------

def test_house_filter_partitions_risk_rows(db_session, run_id):
    total = db_session.execute(
        select(func.count()).select_from(RiskResult).where(RiskResult.run_id == run_id)).scalar_one()
    by_house = sum(
        db_session.execute(select(func.count()).select_from(RiskResult).where(
            RiskResult.run_id == run_id, RiskResult.house == h)).scalar_one()
        for h in ("LS", "RS")
    )
    assert by_house == total
