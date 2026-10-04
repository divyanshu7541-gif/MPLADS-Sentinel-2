"""
Phase 3 CLI entrypoint: P6 peer groups + leave-one-out baselines. Writes
one analysis_run (with peer_group and work_context rows) and
docs/phase3_coverage_report.md for review.

Usage (from backend_v2/, after scripts/run_ingest.py and
scripts/run_normalize.py):
    python scripts/run_context.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from app.analytics import context_run, peers  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "phase3_coverage_report.md"

# BLUEPRINT.md §6 "Peer hierarchy" coverage column.
BLUEPRINT = {
    ("L1", "ge10"): 92.7,
    ("L1", "ge3mps"): 90.5,
    ("L2", "ge10"): 96.8,
    ("L3", "ge10"): 99.5,
    ("REF", "qualifies"): 19.1,
}
TOLERANCE_PP = 1.5


def main() -> int:
    Session = get_session_factory()
    with Session() as session:
        run_row, frame, ctx, cov = context_run.run(session)
        session.commit()
        by_house = context_run.count_by_house(session, run_row.id)
        report = render_report(run_row, frame, ctx, cov, by_house)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"analysis_run {run_row.id} output_hash={run_row.output_hash}")
    print(f"Report written to {REPORT_PATH}")
    return 0


def _pct(mask, n) -> float:
    return round(100 * int(np.asarray(mask).sum()) / n, 2)


def _check(measured: float, expected: float) -> str:
    return "OK" if abs(measured - expected) <= TOLERANCE_PP else "OUTSIDE TOLERANCE"


def render_report(run_row, frame, ctx, cov, by_house) -> str:
    n = cov["works_in_scope"]
    lv = cov["by_level"]
    L: list[str] = []
    L.append("# Phase 3 Peer Context Coverage Report")
    L.append("")
    L.append(f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by `scripts/run_context.py`.")
    L.append("")
    L.append(f"analysis_run id {run_row.id}, engine `{run_row.engine_version}`")
    L.append(f"config_hash `{run_row.config_hash}`")
    L.append(f"output_hash `{run_row.output_hash}`")
    L.append("")

    L.append("## 1. Scope and definitions")
    L.append("")
    L.append(
        f"Works in scope: {n:,}. These are Snapshot A work_state rows with lifecycle "
        "'sanctioned' or 'completed' (a sanction record exists, so a sanction FY and "
        "amount can exist). Recommended-only works have no sanction FY and no cost to "
        "baseline, so they get no context row."
    )
    L.append(f"By house: {frame['house'].value_counts().to_dict()}")
    L.append("")
    L.extend([
        "- **Amount** is the actual amount for completed works and the sanction amount otherwise.",
        "- **Usable peer** means the amount is present and > 0 "
        "(BLUEPRINT.md §6: \"usable peer count excludes missing amounts\").",
        "- **Peers** are the other works in the same group. "
        "The work itself is always excluded, from counts and from statistics.",
        "- **Distinct other MPs** are MPs among the peers, excluding the work's own MP.",
        "- **MP share** is the largest single MP's share of the peers. "
        "The work's own MP's *other* works count toward it.",
        "- **House is not a grouping key.** Groups mix LS and RS works. "
        "House is only a filter on which rows are displayed.",
        "",
    ])
    missing = {
        "sanction_fy": int(frame["sanction_fy"].isna().sum()),
        "state_id": int(frame["state_id"].isna().sum()),
        "activity_type_id": int(frame["activity_type_id"].isna().sum()),
        "district_key": int(frame["district_key"].isna().sum()),
        "amount not usable": int((~frame["is_usable"]).sum()),
    }
    L.append(f"Missing key components (row can't join a group at the levels that use them): {missing}")
    L.append("")

    L.append("## 2. Coverage vs BLUEPRINT.md §6")
    L.append("")
    L.append(
        f"Tolerance: ±{TOLERANCE_PP} percentage points. The coverage column in BLUEPRINT.md §6 is "
        "*descriptive*: \"works that have N or more peers / MPs\". Its MP figures count every distinct "
        "MP in the group, **the work's own MP included**. That is the only definition that reproduces "
        "them (see the sensitivity table below). The *gating* rule is stricter: it needs ≥3 *other* MPs "
        "and applies the 50% cap. Both are reported. Only the rule decides whether a baseline exists."
    )
    L.append("")
    L.append("| Level | BLUEPRINT measure | Measured | BLUEPRINT | Check |")
    L.append("| --- | --- | --- | --- | --- |")
    peers10, mps3 = "≥10 usable peers (excl. self)", "group has ≥3 MPs (own MP included)"
    rows = [
        ("L1", peers10, lv["L1"]["pct_ge10_peers"], BLUEPRINT[("L1", "ge10")]),
        ("L1", mps3, lv["L1"]["pct_ge3_mps_in_group"], BLUEPRINT[("L1", "ge3mps")]),
        ("L2", peers10, lv["L2"]["pct_ge10_peers"], BLUEPRINT[("L2", "ge10")]),
        ("L3", peers10, lv["L3"]["pct_ge10_peers"], BLUEPRINT[("L3", "ge10")]),
        ("REF", mps3, lv["REF"]["pct_ge3_mps_in_group"], BLUEPRINT[("REF", "qualifies")]),
    ]
    for level, measure, got, exp in rows:
        L.append(f"| {level} | {measure} | {got}% | {exp}% | {_check(got, exp)} |")
    L.append("")
    L.append("All per-level figures (all rows in scope as the denominator):")
    L.append("")
    L.append(
        "| Level | ≥10 peers | ≥3 MPs in group (own incl.) | ≥3 MPs among peers | ≥3 other MPs "
        "| Qualifies under the rule |"
    )
    L.append("| --- | --- | --- | --- | --- | --- |")
    for level in ("L1", "L2", "L3", "REF"):
        s = lv[level]
        L.append(
            f"| {level} | {s['pct_ge10_peers']}% | {s['pct_ge3_mps_in_group']}% | "
            f"{s['pct_ge3_mps_in_peers']}% | {s['pct_ge3_other_mps']}% | {s['pct_qualifies']}% |"
        )
    L.append("")
    L.append(
        "- **Rule, identical at every level including refinement:** ≥15 usable peers excluding self, "
        "≥3 distinct other MPs, and no MP above 50% of the peers. Refinement uses the capped rule by "
        "the user's Phase 3 review decision (2026-09-25). BLUEPRINT.md's shorter wording for it "
        "(\"3 or more MPs and 15 or more peers\") would have qualified about 16.4% of works with no cap."
    )
    L.append("")

    L.append("### Sensitivity of the L1 figures to the denominator definition")
    L.append("")
    L.append(
        "BLUEPRINT.md doesn't spell out its denominator. For transparency, here are the same "
        "two L1 measures under the plausible alternatives. The table above uses the first row."
    )
    L.append("")
    L.append(
        "| Denominator | n | ≥10 peers | ≥3 MPs in group (own incl.) | ≥3 MPs among peers | ≥3 other MPs |"
    )
    L.append("| --- | --- | --- | --- | --- | --- |")
    l1 = ctx[[
        "l1_n_usable_excl_self", "l1_distinct_mps_in_group", "l1_distinct_mps_in_peers",
        "l1_distinct_other_mps",
    ]]
    for label, mask in (
        ("All sanctioned/completed works", np.ones(n, dtype=bool)),
        ("Works with a usable amount", frame["is_usable"].to_numpy()),
        ("Works with a complete L1 key", ctx["l1_n_usable_excl_self"].notna().to_numpy()),
        ("Lok Sabha only", (frame["house"] == "LS").to_numpy()),
        ("Rajya Sabha only", (frame["house"] == "RS").to_numpy()),
    ):
        sub = l1[mask]
        m = len(sub)
        if m == 0:
            continue
        L.append(
            f"| {label} | {m:,} | {_pct(sub['l1_n_usable_excl_self'] >= 10, m)}% | "
            f"{_pct(sub['l1_distinct_mps_in_group'] >= 3, m)}% | "
            f"{_pct(sub['l1_distinct_mps_in_peers'] >= 3, m)}% | "
            f"{_pct(sub['l1_distinct_other_mps'] >= 3, m)}% |"
        )
    L.append("")
    L.append(
        "The remaining gap is ~1.1 pp on L1 \"≥10 peers\" and ~0.9 pp on MPs, both within "
        "tolerance. Its exact cause is not determined. Bucketing by calendar year instead of FY "
        "closes about half of it: 92.1% and 90.2% in the Phase 3 experiment. BLUEPRINT.md says "
        "\"sanction FY\", so the engine keeps the Indian FY (April-March)."
    )
    L.append("")

    L.append("## 3. Level actually assigned (first qualifying of L1 → L2 → L3)")
    L.append("")
    L.append("| Level | Works | Share |")
    L.append("| --- | --- | --- |")
    for level in ("L1", "L2", "L3", "none"):
        c = cov["assigned_level_counts"].get(level, 0)
        L.append(f"| {level} | {c:,} | {round(100 * c / n, 2)}% |")
    L.append("")
    L.append(
        "\"none\" means no level satisfies the full rule, so the work gets no cost baseline. "
        "BLUEPRINT.md §6 records that as \"not evaluated\" (lowering confidence), never as zero."
    )
    L.append("")
    L.append("Assigned level by house (display filter only; the groups themselves mix houses):")
    L.append("")
    L.append("| House | L1 | L2 | L3 | none |")
    L.append("| --- | --- | --- | --- | --- |")
    for house, counts in sorted(cov["assigned_level_by_house"].items()):
        L.append(
            f"| {house} | " + " | ".join(f"{counts.get(k, 0):,}" for k in ("L1", "L2", "L3", "none")) + " |"
        )
    L.append("")
    L.append(f"work_context rows by house: {by_house}")
    L.append("")

    L.append("## 4. Correctness checks on this run")
    L.append("")
    assigned = ctx[ctx["level"].notna()]
    share_ok = (assigned["max_mp_share"] <= peers.MAX_MP_SHARE).all()
    L.append(
        f"- Max MP share among assigned groups: {assigned['max_mp_share'].max():.4f} "
        f"(rule: ≤ {peers.MAX_MP_SHARE}) — {'OK' if share_ok else 'VIOLATION'}"
    )
    L.append(
        f"- Min usable peers (excl. self) among assigned: {int(assigned['n_usable_excl_self'].min())} "
        f"(rule: ≥ {peers.MIN_USABLE_PEERS})"
    )
    L.append(
        f"- Min distinct other MPs among assigned: {int(assigned['distinct_other_mps'].min())} "
        f"(rule: ≥ {peers.MIN_OTHER_MPS})"
    )
    L.append(f"- Level-1 key: {peers.LEVEL_KEYS['L1']}. No constituency component.")
    L.append(
        "- Leave-one-out: each work's baseline is computed after removing the work from its group's value "
        "vector. `tests/test_phase3_context.py` re-derives a sample from the database and checks it."
    )
    L.append("")
    L.extend(HOUSE_TOGGLE_APPENDIX)
    return "\n".join(L)


HOUSE_TOGGLE_APPENDIX = [
    "## Appendix: House toggle (frontend) and the `house` parameter",
    "",
    "**The toggle has no visible effect on data until the Phase 12 cutover.** The frontend's `/api` "
    "proxy still points at the old backend, which has no House column and ignores `house=`. The "
    "`backend_v2` endpoints that accept `house` are still stubs. What is real now:",
    "",
    "- The toggle: `[ LOK SABHA ] [ RAJYA SABHA ]`, mounted once in the shared topbar. Neither "
    "selected means both houses. Clicking the selected house again clears it.",
    "- `house=LS|RS` is appended to exactly /api/summary, /api/queue, /api/analytics, /api/map-data, "
    "/api/map-works, /api/map-filters and /api/graph-data, and only when a house is selected. With "
    "none selected, every URL is byte-identical to before.",
    "- Under Rajya Sabha, the map's constituency drill-down and constituency performance/comparison "
    "show an explicit \"not applicable for Rajya Sabha\" notice instead of an empty result.",
    "- In `backend_v2`, `house` is optional on the seven endpoints and any other value returns 422 "
    "(`tests/test_contract.py`, structural only). Real filtering of the data is tested on "
    "`work_context` (`tests/test_phase3_context.py`).",
    "",
    "Browser test (`backend_v2/tests/e2e/test_house_toggle_e2e.py`, skipped unless `E2E_BASE_URL` is "
    "set). As run for this phase, from the repo root:",
    "",
    "```sh",
    "docker run -d --name p3-api -v \"$PWD/backend_v2\":/srv <backend_v2 dev image> \\",
    "  uvicorn app.main:app --host 0.0.0.0 --port 8000",
    "docker run -d --name p3-vite --network container:p3-api -v \"$PWD/frontend\":/app \\",
    "  -v <node_modules volume>:/app/node_modules -w /app node:20-alpine npx vite --host 0.0.0.0 --port 3000",
    "docker run --rm --network container:p3-api -v \"$PWD/backend_v2/tests/e2e\":/e2e \\",
    "  -e E2E_BASE_URL=http://localhost:3000 mcr.microsoft.com/playwright/python:v1.47.0-jammy \\",
    "  sh -c 'pip install -q pytest==8.3.3 playwright==1.47.0 &&",
    "         cd /e2e && python -m pytest -q --rootdir /e2e .'",
    "```",
    "",
]


if __name__ == "__main__":
    raise SystemExit(main())
