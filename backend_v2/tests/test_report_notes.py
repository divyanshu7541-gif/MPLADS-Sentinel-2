"""Notes added under a generated report's title survive regeneration
(app/core/report_notes.py, used by scripts/run_signals.py)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.report_notes import apply_notes, extract_notes, write_report_preserving_notes

ROOT = Path(__file__).resolve().parents[2]
PHASE4_REPORT = ROOT / "docs" / "phase4_signals_report.md"
RUN_SIGNALS = Path(__file__).resolve().parents[1] / "scripts" / "run_signals.py"

FRESH = (
    "# Phase 4 Base Signals Report\n\nGenerated 2026-10-01 by `scripts/run_signals.py`.\n\n"
    "## 1. Body\n\ntext\n"
)


def test_any_note_survives_regeneration_once(tmp_path):
    path = tmp_path / "report.md"
    note = "> **Some note written later.** Points to [x](x.md).\n> Second line of the same note."
    path.write_text(apply_notes(FRESH.replace("2026-10-01", "2026-09-25"), [note]), encoding="utf-8")

    kept = write_report_preserving_notes(path, FRESH)
    body = path.read_text(encoding="utf-8")
    assert kept == [note]
    assert body.count("Some note written later") == 1
    assert "Generated 2026-10-01" in body and "2026-09-25" not in body  # body really regenerated
    assert body.index("Some note written later") < body.index("Generated 2026-10-01")

    write_report_preserving_notes(path, FRESH)  # regenerate again: still exactly once
    assert path.read_text(encoding="utf-8").count("Some note written later") == 1


def test_several_notes_keep_their_order(tmp_path):
    path = tmp_path / "report.md"
    notes = ["> first note", "> second note\n> continued"]
    path.write_text(apply_notes(FRESH, notes), encoding="utf-8")
    write_report_preserving_notes(path, FRESH)
    assert extract_notes(path.read_text(encoding="utf-8")) == notes


def test_no_note_leaves_the_generated_report_unchanged(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(FRESH, encoding="utf-8")
    assert write_report_preserving_notes(path, FRESH) == []
    assert path.read_text(encoding="utf-8") == FRESH
    assert write_report_preserving_notes(tmp_path / "new.md", FRESH) == []


def test_body_blockquotes_are_not_mistaken_for_notes():
    text = "# T\n\nGenerated x\n\n> a quote inside the body\n"
    assert extract_notes(text) == []


def test_the_real_phase4_supersession_note_survives_regeneration(tmp_path):
    """Whatever note docs/phase4_signals_report.md carries today is kept
    when the report is regenerated (read from the file, not hard-coded)."""
    if not PHASE4_REPORT.exists():
        pytest.skip("docs/phase4_signals_report.md not present")
    current = PHASE4_REPORT.read_text(encoding="utf-8")
    notes = extract_notes(current)
    assert notes, "the Phase 4 report has no note under its title"
    assert any("phase4_addendum.md" in n for n in notes)
    path = tmp_path / "phase4_signals_report.md"
    path.write_text(current, encoding="utf-8")
    write_report_preserving_notes(path, FRESH)
    assert extract_notes(path.read_text(encoding="utf-8")) == notes


def test_run_signals_writes_through_the_note_preserving_writer():
    tree = ast.parse(RUN_SIGNALS.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = {getattr(c.func, "id", None) or getattr(c.func, "attr", None) for c in calls}
    assert "write_report_preserving_notes" in names
    direct = [c for c in calls if getattr(c.func, "attr", None) == "write_text"
              and getattr(getattr(c.func, "value", None), "id", None) == "REPORT_PATH"]
    assert not direct, "run_signals.py writes REPORT_PATH directly, bypassing note preservation"
