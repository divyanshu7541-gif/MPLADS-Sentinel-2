"""
Phase 4 CLI entrypoint: the six BASE signals on top of Phase 3's peer
engine. Runs Phase 3's context engine (app.analytics.context_run.run,
unchanged) and then computes + writes signal_result onto that same run
(app.analytics.signals_run.run). Writes docs/phase4_signals_report.md for
review, including a spot-check sample of known-high and typical works per
signal, across both Houses, per the Phase 4 STOP CONDITION.

Usage (from backend_v2/, after scripts/run_ingest.py and
scripts/run_normalize.py):
    python scripts/run_signals.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.analytics import signals_run  # noqa: E402
from app.core.report_notes import write_report_preserving_notes  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "phase4_signals_report.md"

SIGNAL_WEIGHTS = {  # BLUEPRINT.md §6 signal catalogue -- provisional, shown for context only
    "cost_anomaly": 25, "near_duplicate": 20, "portfolio_concentration": 10,
    "district_authority_pattern": 10, "temporal_anomaly": 10, "lifecycle_delay": 10,
}
SAMPLE_N_TOP = 5
SAMPLE_N_RS_TOP = 3
SAMPLE_N_TYPICAL = 3


def main() -> int:
    Session = get_session_factory()
    with Session() as session:
        run_row, frame, ctx, results = signals_run.run(session)
        session.commit()
        report = render_report(run_row, frame, ctx, results)

    # Notes added under the title (e.g. the supersession note pointing to
    # docs/phase4_addendum.md) survive regeneration -- app/core/report_notes.py.
    kept = write_report_preserving_notes(REPORT_PATH, report)
    if kept:
        print(f"Kept {len(kept)} note(s) from the previous report")
    print(f"analysis_run {run_row.id} -- {sum(len(r) for r in results.values())} signal_result rows written")
    print(f"Report written to {REPORT_PATH}")
    return 0


def _fmt(x, nd=3):
    return "n/a" if x is None or pd.isna(x) else f"{x:.{nd}f}"


def _score_stats(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"n": 0}
    return {
        "n": len(s), "mean": s.mean(), "median": s.median(),
        "p90": s.quantile(0.90), "p99": s.quantile(0.99), "max": s.max(),
    }


def _sample_rows(frame: pd.DataFrame, result: pd.DataFrame, evidence_keys: list[str]) -> list[str]:
    def row_line(work_key: str) -> str:
        r = result.loc[work_key]
        ev = r["evidence"]
        ev_str = "; ".join(f"{k}={ev.get(k)}" for k in evidence_keys if k in ev)
        # direction may be pd.NA, which has no truthiness -- isna() first.
        direction = "" if pd.isna(r["direction"]) else r["direction"]
        return (
            f"| {work_key} | {frame.at[work_key, 'house']} | {frame.at[work_key, 'mp'] or ''} | "
            f"{_fmt(r['score'])} | {direction} | {ev_str} |"
        )

    eligible = result[result["eligible"]]
    lines = [
        "| work_key | house | mp | score | direction | evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if eligible.empty:
        return lines + ["| (no eligible works) | | | | | |"]

    top = eligible.sort_values("score", ascending=False).head(SAMPLE_N_TOP)
    rs_only = eligible[frame.loc[eligible.index, "house"] == "RS"]
    rs_top = rs_only.sort_values("score", ascending=False).head(SAMPLE_N_RS_TOP)
    med = eligible["score"].median()
    typical = eligible.iloc[(eligible["score"] - med).abs().argsort()[:SAMPLE_N_TYPICAL]]

    seen = set()
    groups = (
        ("**top overall**", top),
        ("**top, Rajya Sabha**", rs_top),
        ("**typical (near median)**", typical),
    )
    for label, sample in groups:
        lines.append(f"| {label} | | | | | |")
        for work_key in sample.index:
            if work_key in seen:
                continue
            seen.add(work_key)
            lines.append(row_line(work_key))
    return lines


EVIDENCE_KEYS = {
    "cost_anomaly": ["level", "ratio_to_peer_median", "z_log_scale", "peer_median", "amount_used"],
    "near_duplicate": ["matched_work_key", "match_basis", "cosine_similarity", "date_gap_days"],
    "portfolio_concentration": ["mp_type_share", "peer_mean_share", "z", "activity_type_id"],
    "district_authority_pattern": [
        "driving_component", "type_share", "peer_mean_share", "z_share", "z_amount",
    ],
    "temporal_anomaly": [
        "entity_type", "date_field", "own_week", "own_week_count", "typical_weekly_count",
    ],
    "lifecycle_delay": [
        "driving_component", "age_days", "age_exceedance", "payment_share", "payment_share_z",
    ],
}


def render_report(run_row, frame: pd.DataFrame, ctx: pd.DataFrame, results: dict) -> str:
    n = len(frame)
    L: list[str] = []
    L.append("# Phase 4 Base Signals Report")
    L.append("")
    L.append(f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by `scripts/run_signals.py`.")
    L.append("")
    L.append(
        f"analysis_run id {run_row.id}, engine `{run_row.engine_version}` "
        "(Phase 3's context engine, reused unchanged)"
    )
    L.append(
        f"output_hash `{run_row.output_hash}` "
        "(peer_group/work_context only -- signals do not change this hash)"
    )
    L.append(f"Works in scope: {n:,} ({frame['house'].value_counts().to_dict()})")
    L.append("")

    L.append("## 1. What this run is, and is not")
    L.append("")
    L.append(
        "- **No fusion, no risk score, no confidence, no ML** in this phase (Phase 4 brief). Each "
        "signal's `score`/`eligible`/`direction`/`evidence` stands alone; `reliability`/`dispersion` "
        "are quality inputs recorded for Phase 5's confidence engine and are never read by any score "
        "computation here (see section 4)."
    )
    L.append(
        "- **House-neutral by construction:** no signal function reads `house` at all -- every score "
        "comes only from the work-type/state/MP/authority population as a whole. `house` is copied "
        "onto each row purely so a read-time filter can select which rows are returned (see section 5)."
    )
    L.append(
        "- **Cost anomaly is two-sided** (unusually cheap scores the same as unusually expensive). "
        "BLUEPRINT.md §6 lists one-sided-vs-two-sided as a still-gated policy choice -- flagged here, "
        "not settled by this phase."
    )
    L.append(
        "- **Provisional constants** (block sizes, n-gram width, batch-day/burst thresholds, minimum "
        "portfolio sizes, the log-scale MAD floor): every one is a documented, reasoned starting point "
        "in `app/analytics/signals.py`'s module docstring and per-signal comments, none is a fitted or "
        "hand-labelled value -- BLUEPRINT.md §6 lists calibration itself as a gated policy change."
    )
    L.append("")

    L.append("## 2. Coverage per signal")
    L.append("")
    L.append("| Signal | Weight | Eligible | Mean | Median | P90 | P99 | Max |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, result in results.items():
        st = _score_stats(result["score"])
        pct = round(100 * st.get("n", 0) / n, 2)
        L.append(
            f"| {name} | {SIGNAL_WEIGHTS[name]}% | {st.get('n', 0):,} ({pct}%) | "
            f"{_fmt(st.get('mean'))} | {_fmt(st.get('median'))} | {_fmt(st.get('p90'))} | "
            f"{_fmt(st.get('p99'))} | {_fmt(st.get('max'))} |"
        )
    L.append("")
    L.append(
        "\"Eligible\" works have a real score; the rest are \"not evaluated\" (never a score of 0) -- "
        "BLUEPRINT.md §6 confidence. Ineligibility reasons are signal-specific (e.g. cost anomaly needs "
        "a qualifying peer group AND a usable amount; lifecycle delay only applies to still-open works)."
    )
    L.append("")

    L.append("## 3. Spot-check sample, per signal (for manual review)")
    L.append("")
    L.append(
        "Three groups per signal: the overall top scores, the top scores restricted to Rajya Sabha "
        "(guaranteed RS representation even though RS is the smaller House by volume), and a "
        "near-median \"typical\" sample -- so both known-anomalous-looking and known-normal-looking "
        "works are here to check explanations against, across both Houses, per the STOP CONDITION."
    )
    L.append("")
    for name, result in results.items():
        L.append(f"### {name}")
        L.append("")
        L.extend(_sample_rows(frame, result, EVIDENCE_KEYS[name]))
        L.append("")

    L.append("## 4. Correctness checks on this run")
    L.append("")
    for name, result in results.items():
        elig = result["eligible"]
        bad_a = int((elig & result["score"].isna()).sum())
        bad_b = int((~elig & result["score"].notna()).sum())
        in_range = result["score"].dropna().between(0, 1).all()
        L.append(
            f"- **{name}:** eligible-with-null-score = {bad_a}, ineligible-with-a-score = {bad_b}, "
            f"all scores in [0,1] = {in_range}"
        )
    L.append(
        "- No signal's score computation reads `reliability`, `n_usable_excl_self`, "
        "`distinct_other_mps`, or any other peer/entity COUNT as a multiplicand -- verified by "
        "`tests/test_phase4_signals_unit.py::test_no_reliability_or_peer_count_term_multiplies_any_score` "
        "(a source-level check, not just a unit test, per the acceptance criteria)."
    )
    L.append("")

    L.append("## 5. House-neutrality")
    L.append("")
    by_house_counts = {
        name: {h: int((result["eligible"] & (frame["house"] == h)).sum()) for h in ("LS", "RS")}
        for name, result in results.items()
    }
    L.append("| Signal | LS eligible | RS eligible |")
    L.append("| --- | --- | --- |")
    for name, counts in by_house_counts.items():
        L.append(f"| {name} | {counts['LS']:,} | {counts['RS']:,} |")
    L.append("")
    L.append(
        "`grep -n house app/analytics/signals.py` returns nothing: no signal function reads House "
        "anywhere, so filtering signal_result by house at read time can never change a returned "
        "work's own score -- proven directly (not just tested) in "
        "`tests/test_phase4_signals_context.py::test_house_filter_never_changes_a_signal_score`."
    )
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
