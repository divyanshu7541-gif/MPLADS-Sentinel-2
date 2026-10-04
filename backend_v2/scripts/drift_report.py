"""
Phase 13.z: drift report between two analysis runs -- read-only.

    python scripts/drift_report.py --ref 1 --cur 44 --out ../docs/drift_report_run1_vs_run44.md

BLUEPRINT §7 Governance: "Drift is watched through distribution shift on
inputs and scores." For each model input and score it reports the Population
Stability Index (PSI) of the current run against the reference run, using the
reference run's deciles as bins, plus means and the share of works whose value
changed. Inputs: the Phase 3 context columns the models read (amount used,
peer median, peer level). Scores: the six base signals, risk and confidence
(v4-candidate), and both B4 atypicality scores.

PSI conventions (industry rule of thumb, NOT a Sentinel threshold -- no alert
threshold is set here; that is an owner decision): < 0.1 small, 0.1-0.25
moderate, > 0.25 large. Writes nothing to the database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.analytics.atypicality_run import risk_result_checksum  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402

QUERIES = {
    "input: amount_used (log)": (
        "SELECT work_key, ln(NULLIF(amount_used, 0)) AS v FROM work_context WHERE run_id = :r"
    ),
    "input: peer_median (log)": (
        "SELECT work_key, ln(NULLIF(peer_median, 0)) AS v FROM work_context WHERE run_id = :r"
    ),
    **{
        f"signal: {s}": f"SELECT work_key, score AS v FROM signal_result WHERE run_id = :r AND signal = '{s}'"
        for s in (
            "cost_anomaly",
            "near_duplicate",
            "portfolio_concentration",
            "district_authority_pattern",
            "temporal_anomaly",
            "lifecycle_delay",
        )
    },
    "score: risk (v4-candidate)": (
        "SELECT work_key, risk AS v FROM risk_result WHERE run_id = :r AND config_name = 'v4-candidate'"
    ),
    "score: confidence (v4-candidate)": (
        "SELECT work_key, confidence AS v FROM risk_result WHERE run_id = :r AND config_name = 'v4-candidate'"
    ),
    "ML: B4 robust Mahalanobis": (
        "SELECT work_key, score AS v FROM atypicality_result "
        "WHERE run_id = :r AND method = 'robust_mahalanobis'"
    ),
    "ML: B4 Isolation Forest": (
        "SELECT work_key, score AS v FROM atypicality_result "
        "WHERE run_id = :r AND method = 'isolation_forest'"
    ),
}


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if len(ref) == 0 or len(cur) == 0:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # (near-)constant reference: compare the share at the constant
        return float("nan")
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(ref, edges)[0] / len(ref)
    c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-6, None), np.clip(c, 1e-6, None)
    return float(np.sum((c - r) * np.log(c / r)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=int, required=True)
    ap.add_argument("--cur", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = []
    with get_session_factory()() as s:
        conn = s.connection()
        md5 = {r: risk_result_checksum(s, r) for r in (args.ref, args.cur)}
        tiers = pd.read_sql(
            text(
                "SELECT run_id, tier, count(*) n FROM risk_result WHERE run_id IN (:a, :b) "
                "AND config_name = 'v4-candidate' GROUP BY 1, 2 ORDER BY 2, 1"
            ),
            conn,
            params={"a": args.ref, "b": args.cur},
        )
        for name, sql in QUERIES.items():
            a = pd.read_sql(text(sql), conn, params={"r": args.ref}).set_index("work_key")["v"].astype(float)
            b = pd.read_sql(text(sql), conn, params={"r": args.cur}).set_index("work_key")["v"].astype(float)
            j = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner")
            changed = ((j["a"] - j["b"]).abs() > 1e-9) | (j["a"].isna() != j["b"].isna())
            p = psi(a.to_numpy(), b.to_numpy())
            rows.append(
                {
                    "measure": name,
                    "n ref": int(a.notna().sum()),
                    "n cur": int(b.notna().sum()),
                    "mean ref": round(float(a.mean()), 4),
                    "mean cur": round(float(b.mean()), 4),
                    "works changed": f"{int(changed.sum()):,} ({changed.mean():.1%})",
                    "PSI": "n/a" if not np.isfinite(p) else f"{p:.4f}",
                    "band": "n/a"
                    if not np.isfinite(p)
                    else ("small" if p < 0.1 else "moderate" if p <= 0.25 else "large"),
                }
            )
        s.rollback()
    df = pd.DataFrame(rows)
    lines = [
        f"# Drift report: run {args.ref} (reference) vs run {args.cur} (current)",
        "",
        "Generated by `backend_v2/scripts/drift_report.py` (read-only). BLUEPRINT §7: "
        '"Drift is watched through distribution shift on inputs and scores."',
        "",
        f"- risk_result md5: run {args.ref} `{md5[args.ref]}`, run {args.cur} `{md5[args.cur]}`.",
        "- Both runs use the same snapshot (Snapshot A); the difference between them is the Phase 13.y "
        "authority-state fix, so this report measures that fix's effect, not data drift over time. "
        "Drift over time needs a second snapshot run through the pipeline.",
        "- PSI uses the reference run's deciles as bins. Bands are a common rule of thumb (< 0.1 small, "
        "0.1-0.25 moderate, > 0.25 large), not a Sentinel alert threshold; none is set (owner decision).",
        "",
        "| " + " | ".join(df.columns) + " |",
        "| " + " | ".join("---" for _ in df.columns) + " |",
    ]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    lines += ["", "Tier counts (v4-candidate):", "", "| run | tier | works |", "| --- | --- | --- |"]
    lines += [f"| {r.run_id} | {r.tier} | {r.n:,} |" for r in tiers.itertuples(index=False)]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"drift report written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
