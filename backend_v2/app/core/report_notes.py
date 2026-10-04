"""
Keep human-added notes on generated reports across regeneration.

Convention: a note is a Markdown blockquote (lines starting with ">") placed
directly under the report's "# " title, before the generated body. Several
notes may be stacked, separated by blank lines. Whatever notes exist when a
report is regenerated are carried over verbatim into the new file, under
the new title, so a supersession note (e.g. "see phase4_addendum.md") is not
silently wiped. No note text is hard-coded here.
"""

from __future__ import annotations

from pathlib import Path


def extract_notes(text: str) -> list[str]:
    """Blockquote blocks directly under the first '# ' title, in order."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("# ")) + 1
    except StopIteration:
        return []
    notes, block = [], []
    for ln in lines[start:]:
        if ln.startswith(">"):
            block.append(ln)
        elif not ln.strip():
            if block:
                notes.append("\n".join(block))
                block = []
        else:
            break
    if block:
        notes.append("\n".join(block))
    return notes


def apply_notes(report: str, notes: list[str]) -> str:
    """Insert notes under the report's title. Notes the freshly generated
    body already carries are not added twice."""
    present = set(extract_notes(report))
    notes = [n for n in notes if n not in present]
    if not notes:
        return report
    lines = report.splitlines()
    try:
        t = next(i for i, ln in enumerate(lines) if ln.startswith("# "))
    except StopIteration:
        return "\n\n".join(notes) + "\n\n" + report
    head, rest = lines[: t + 1], lines[t + 1 :]
    while rest and not rest[0].strip():
        rest = rest[1:]
    out = head + [""] + [ln for n in notes for ln in (*n.splitlines(), "")] + rest
    return "\n".join(out) + ("\n" if report.endswith("\n") else "")


def write_report_preserving_notes(path: Path, report: str) -> list[str]:
    """Write `report` to `path`, carrying over any notes the existing file
    has. Returns the notes that were carried over."""
    notes = extract_notes(path.read_text(encoding="utf-8")) if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(apply_notes(report, notes), encoding="utf-8")
    return notes
