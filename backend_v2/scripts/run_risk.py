"""
Phase 5 CLI entrypoint: signals (Phase 4) -> fusion under both
configurations -> confidence -> compliance C1-C9, written onto one
analysis_run, then the G3 gate report docs/phase5_gate_report.md.

Usage (from backend_v2/, after run_ingest.py and run_normalize.py):
    python scripts/run_risk.py                 # full run + report
    python scripts/run_risk.py --report-only   # re-render from the latest stored run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from math import erf, sqrt  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.analytics import compliance, confidence, fusion, gate, risk_run  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models.analytics import AnalysisRun, RiskResult  # noqa: E402
from app.models.provenance import SourceSnapshot  # noqa: E402

# CI writes its own copy elsewhere (PHASE5_GATE_REPORT_PATH) so the committed
# report is the one the gate test checks.
REPORT_PATH = Path(
    os.environ.get("PHASE5_GATE_REPORT_PATH")
    or Path(__file__).resolve().parents[2] / "docs" / "phase5_gate_report.md"
)

# Phase 4 as approved (docs/phase4_signals_report.md, analysis_run 1): the
# three signals whose attribution was corrected at the start of Phase 5.
PHASE4_APPROVED = {
    "portfolio_concentration": {"mean": 0.966, "median": 1.000},
    "district_authority_pattern": {"mean": 0.991, "median": 1.000},
    "temporal_anomaly": {"mean": 0.985, "median": 1.000},
}

# BLUEPRINT.md §6 compliance baselines (Snapshot A).
BASELINES = {
    ("C1", "actual_le_sanction"): "0 violations in 43,842 completed works",
    ("C2", "payments_le_sanction"): "0 violations",
    ("C3", "no_payment_before_sanction"): "0 rows",
    ("C4", "dates_in_order"): "0 violations",
    ("C5", "completed_within_one_year"): "11.9% of completed works over",
    ("C5", "open_within_one_year"): "13,562 open works past one year",
    ("C6", "completed_has_payment"): "100 works without",
    ("C7", "recommended_le_allocated"): "0 violations (MP summary, Snapshot B)",
    ("C7", "expenditure_le_recommended"): "0 violations (MP summary, Snapshot B)",
    ("C8", "sanctioned_in_recommended_file"): "361 sanctioned works absent from the recommended file",
    ("C8", "flag2_has_stage_or_sanction"): "499 rows with FLAG 2 lacking stage and sanction date",
    ("C8", "one_record_per_portal_id_and_house"): "(not in BLUEPRINT; §2 assumed unique keys)",
    ("C9", "reconciles_to_portal_total"): "All nine files pass",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    Session = get_session_factory()
    with Session() as session:
        if args.report_only:
            run_id = session.execute(select(func.max(RiskResult.run_id))).scalar()
            if run_id is None:
                print("no risk_result rows; run without --report-only first")
                return 1
            run_row = session.get(AnalysisRun, run_id)
            data = risk_run.load_run(session, run_id)
            data["run_row"] = run_row
            data["as_of"] = pd.Timestamp(session.get(SourceSnapshot, run_row.source_snapshot_id).data_as_of)
        else:
            data = risk_run.run(session)
            session.commit()
            run_row = data["run_row"]
        report = render(data)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"analysis_run {run_row.id} -- risk_result and compliance_result written; report at {REPORT_PATH}")
    return 0


def _f(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:,.{nd}f}"


def _pct(x, nd=1):
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:.{nd}f}%"


def _table(df: pd.DataFrame, fmt: dict | None = None) -> list[str]:
    fmt = fmt or {}
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in df.itertuples(index=False):
        cells = []
        for c, v in zip(cols, row):
            f = fmt.get(c)
            cells.append(f(v) if f else (f"{v:,}" if isinstance(v, (int, np.integer)) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return out


def render(d: dict) -> str:
    run_row, scores, fused, conf, comp = d["run_row"], d["scores"], d["fused"], d["conf"], d["comp"]
    n = len(scores)
    v3, v4 = fused["v3-compatible"], fused["v4-candidate"]
    sens = {c: gate.sensitivity(scores, c, fused[c]) for c in fusion.CONFIGS}
    abl = {c: gate.ablation(scores, c, fused[c]) for c in fusion.CONFIGS}
    crit = gate.evaluate_criteria(scores, fused, sens)
    items = gate.evaluate_gate_items(scores, fused, sens, abl)
    calib = risk_run.note_value(run_row, "phase5_signal_calibration") or "normal-erf-v0"
    risk_hash = risk_run.note_value(run_row, "phase5_risk_output_hash") or fusion.risk_output_hash(
        fused, conf["confidence"])
    L: list[str] = []
    L += [
        "# Phase 5 Gate Report -- risk fusion: v3-compatible vs v4-candidate",
        "",
        f"<!-- gate-meta run_id={run_row.id} fusion_config_hash={fusion.fusion_config_hash()} "
        f"signal_calibration={calib} -->",
        "",
        f"**Signal calibration: `{calib}`.** Signals scored by empirical percentile rank: "
        + ", ".join(gate_calibrated_signals()) + " (section 0). Fusion formula, weights, tier boundaries, "
        "MIN_CRITICAL_SIGNALS, corroboration and the active threshold are unchanged from the first gate "
        "report.",
        "",
        f"- **analysis_run:** {run_row.id} (the risk_result / compliance_result rows this report describes)",
        f"- **Snapshot data as of:** {d['as_of'].date()} (fixed, from source_snapshot -- never wall-clock)",
        f"- **Fusion config hash (both configs):** `{fusion.fusion_config_hash()}`",
        f"- v3-compatible config hash: `{fusion.config_hash('v3-compatible')}`",
        f"- v4-candidate config hash: `{fusion.config_hash('v4-candidate')}`",
        f"- **Risk output hash:** `{risk_hash}` (same inputs + config reproduce it; tested)",
        f"- Works scored: {n:,} (the Phase 4 signal scope: Snapshot A sanctioned + completed works), "
        f"{2 * n:,} risk_result rows (one per work per config)",
        "- Generated by `backend_v2/scripts/run_risk.py`. The decision criteria are fixed in "
        "`app/analytics/gate.py`. K1-K5 were written before the first gate's results. Items 3 and 5 are "
        "descriptive, with no threshold set: the pass/fail lines briefly given to them had no principled "
        "basis and were withdrawn.",
        "",
    ]

    # ---- verdict up front
    L += ["## Verdict in plain language", ""]
    L += _verdict_lines(items)
    L += [""]

    # ---- 0. calibration
    L += ["## 0. Signal calibration", ""]
    L += [
        "The first gate found signals far outside what their score mapping assumed. Each mapped its "
        "statistic to a score as if it followed a known distribution (for a z: score = P(|Z| < |z|) "
        "under a standard normal). Their real distributions are heavier-tailed, so far too many works "
        "scored near 1. The signals listed below now use the empirical counterpart: **score = the share "
        "of evaluated works whose statistic is strictly less extreme** (`signals.empirical_tail_score`). "
        "The reference population is every evaluated work in the run, both Houses together. Ties share "
        "the lower score, so a statistic at its minimum scores 0. No curve is fitted and nothing is "
        "tuned to a target distribution.",
        "",
        "Statistic ranked, per signal (why a rank and not a fitted distribution):",
        "- **cost_anomaly** (|z| of log amount vs peers; Phase 5c): robust z's on discrete, clustered "
        "amounts. 45% are multiples of INR 50,000, and within-type dispersion is wide (BLUEPRINT §6), so "
        "the z distribution has heavy tails and spikes.",
        "- **portfolio_concentration** (|z| of the MP's type share vs peer MPs): the share residual's "
        "peer std is floored at 0.02, so the tail is heavy and there are many near-identical values.",
        "- **district_authority_pattern** (larger of |z_share| and |z_amount|): the maximum of two "
        "differently-scaled components, a mixture with no closed form.",
        "- **temporal_anomaly** (one-sided burst z >= 0): built from small integer weekly counts, so it "
        "is discrete with a large spike at 0.",
        "- **lifecycle_delay** (larger of the age exceedance vs completed peers and the payment-z tail "
        "value; Phase 5c): the age part compares open works with finished durations, and open works are "
        "systematically older. Ranking against other open works removes that built-in inflation. The "
        "meaning shifts slightly: the score now says 'more delayed than other open works', not 'older "
        "than completed peers took'. The age part caps at 1.0 for a work older than every completed peer. "
        "Works tied at that cap are ordered by days open (owner's decision, Phase 5c); otherwise the "
        "whole capped group would share one lower score and the most delayed works could not reach 0.9.",
        "",
        "near_duplicate is unchanged: it is a cosine similarity, not a z.",
        "",
    ]
    cal = d.get("calib")
    if cal is not None:
        rows = []
        for s_ in gate_calibrated_signals():
            st = cal[s_].dropna()
            # lifecycle's statistic is already on the 0-1 (pre-calibration score) scale
            old = st if s_ == "lifecycle_delay" else st.map(lambda v: erf(v / sqrt(2)))
            new = scores[s_].dropna()
            rows.append({"signal": s_, "evaluated": f"{len(st):,}", "stat median": st.median(),
                         "stat P90": st.quantile(0.9), "stat P99": st.quantile(0.99),
                         "old mapping: score >= 0.9": _pct((old >= 0.9).mean()),
                         "new: score >= 0.9": _pct((new >= 0.9).mean()),
                         "new: active (> 0.1)": _pct((new > 0.1).mean())})
        L += _table(pd.DataFrame(rows), {c: (lambda v: f"{v:.3f}") for c in
                                        ["stat median", "stat P90", "stat P99"]})
        L += ["", "'Old mapping' applies the normal mapping to this run's own statistics, the same works "
              "under both mappings. The attribution fix made earlier in Phase 5 is documented in "
              "`docs/phase4_addendum.md`.", ""]
    other = []
    for s in fusion.BASE_SIGNALS:
        sc = scores[s].dropna()
        other.append({"signal": s, "evaluated": f"{len(sc):,} ({_pct(len(sc) / n)})", "mean": sc.mean(),
                      "median": sc.median(), "P90": sc.quantile(0.9),
                      "active (>0.1)": _pct((sc > 0.1).mean())})
    L += ["All six signals as fused in this run:", ""]
    L += _table(pd.DataFrame(other), {c: (lambda v: f"{v:.3f}") for c in ["mean", "median", "P90"]})
    L += [""]

    # ---- 1. reachability
    L += ["## 1. Reachability", "",
          "What risk a work could reach if every signal that *was* evaluated for it scored 1.0 "
          "(not-evaluated signals stay not evaluated). Under v3 a not-evaluated signal counts as 0, so "
          "the ceiling falls with every missing signal; under v4 it does not.", ""]
    rt = gate.reachability_table(scores)
    rt = rt[["config", "n_eligible", "works", "min_max_risk", "critical_reachable_share"]]
    L += _table(rt, {"min_max_risk": _f, "critical_reachable_share": _pct})
    k3v3, k3v4 = crit["v3-compatible"]["K3_value"], crit["v4-candidate"]["K3_value"]
    L += ["", f"K3 (CRITICAL reachable for >= {_pct(gate.K3_MIN_SHARE, 0)} of works with >= 3 evaluated "
          f"signals): v3 {_pct(k3v3)}, v4 {_pct(k3v4)}.",
          "",
          "Evidence each tier needs -- k signals at the given score, all other signals evaluated at 0 "
          "(heaviest-weight signals first, then lightest-weight first):", ""]
    syn = gate.synthetic_reachability()
    L += _table(syn, {"v3-compatible risk": _f, "v4-candidate risk": _f, "score": lambda v: f"{v:.1f}"})
    L += [""]

    # ---- 2. distributions
    L += ["## 2. Score distributions (both configurations)", ""]
    drows = []
    for c in fusion.CONFIGS:
        dist = gate.distribution(fused[c])
        q = dist["quantiles"]
        tc = dist["tiers"]
        drows.append({"config": c, "mean": dist["mean"], "P50": q[0.5], "P75": q[0.75], "P90": q[0.9],
                      "P95": q[0.95], "P99": q[0.99], "max": dist["max"], "at 100": dist["n_at_100"],
                      **{t: int(tc[t]) for t in tc.index}})
    L += _table(pd.DataFrame(drows), {c: _f for c in ["mean", "P50", "P75", "P90", "P95", "P99", "max"]})
    L += ["", "Tier shares and CRITICAL demotions (CRITICAL needs >= 3 active base signals):", ""]
    srows = []
    for c in fusion.CONFIGS:
        tc = gate.tier_counts(fused[c])
        srows.append({"config": c, **{t: _pct(tc[t] / n) for t in tc.index},
                      "demoted CRITICAL->HIGH": int(fused[c]["critical_demoted"].sum())})
    L += _table(pd.DataFrame(srows))
    L += ["", "Histogram of risk (count of works per 10-point band):", ""]
    hist = pd.DataFrame({c: gate.distribution(fused[c])["hist"].to_numpy() for c in fusion.CONFIGS})
    hist.insert(0, "band", ["0-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", "80-90",
                            "90-100"])
    L += _table(hist)
    L += ["", "By House (display filter only -- scores are computed on both Houses together):", ""]
    hrows = []
    house = d["frame"]["house"].reindex(scores.index)
    for c in fusion.CONFIGS:
        for h in ("LS", "RS"):
            f = fused[c][house == h]
            tc = gate.tier_counts(f)
            hrows.append({"config": c, "house": h, "works": len(f), "median risk": f["risk"].median(),
                          "HIGH+CRITICAL": _pct((tc["HIGH"] + tc["CRITICAL"]) / max(len(f), 1))})
    L += _table(pd.DataFrame(hrows), {"median risk": _f})
    L += ["", "Rank agreement between the two configs: Spearman rho "
          f"{_f(_spearman(v3['risk'], v4['risk']), 3)}; top-1000 overlap "
          f"{_pct(gate.overlap(gate.top_n(v3), gate.top_n(v4)))}.", ""]

    # ---- 3. overlap
    L += ["## 3. Active-signal overlap", "",
          "How many base signals are active (score > 0.1) per work, and how often each pair is active "
          "together. m(k) rewards k, so if most works have many active signals the multiplier stops "
          "discriminating.", ""]
    k_dist, co, jac = gate.active_overlap(scores)
    L += _table(pd.DataFrame({"active signals k": k_dist.index, "works": k_dist.to_numpy(),
                              "share": [_pct(v / n) for v in k_dist.to_numpy()]}))
    L += ["", f"Works with 5 or more active base signals: {_pct(items['share_k5_plus'])} "
          "(descriptive, no threshold set). Share of each signal's evaluated works scoring >= 0.9, "
          ">= 0.99 and > 0.1 (active):", ""]
    ref = {s_: ("~10% by construction (empirical)" if s_ in gate_calibrated_signals() else
                {"cost_anomaly": "~10% if z were normal", "near_duplicate": "n/a (cosine)",
                 "lifecycle_delay": "n/a (exceedance mix)"}[s_]) for s_ in fusion.BASE_SIGNALS}
    L += _table(pd.DataFrame([{
        "signal": s_, "score >= 0.9": _pct((scores[s_].dropna() >= 0.9).mean()),
        "score >= 0.99": _pct((scores[s_].dropna() >= 0.99).mean()),
        "active (> 0.1)": _pct((scores[s_].dropna() > fusion.ACTIVE_THRESHOLD).mean()),
        "reference for >= 0.9": ref[s_],
    } for s_ in fusion.BASE_SIGNALS]))
    L += [""]
    L += ["Pairwise Jaccard (works where both are active / works where either is):", ""]
    j = jac.copy()
    j.insert(0, "signal", j.index)
    L += _table(j, {c: (lambda v: f"{v:.2f}") for c in fusion.BASE_SIGNALS})
    L += [""]
    for c in fusion.CONFIGS:
        top = gate.top_n(fused[c])
        rates = scores.loc[top].gt(fusion.ACTIVE_THRESHOLD).mean()
        L += [f"- Active rate inside the {c} top 1,000: "
              + ", ".join(f"{s} {_pct(v, 0)}" for s, v in rates.items())]
    L += [""]

    # ---- 4. sensitivity
    L += ["## 4. Sensitivity (every weight perturbed independently by up to +-20%)", "",
          f"{gate.PERTURB_DRAWS} seeded draws (seed {gate.PERTURB_SEED}); each weight multiplied by "
          "U(0.8, 1.2). v4 weights are renormalised to sum to 1 after each draw; v3 keeps its fixed 1.0 "
          "denominator, as the old code did. Top 1,000 ranked by risk, ties at the 100 cap broken by the "
          "uncapped value, then by key.", ""]
    L += _table(pd.DataFrame([{"config": c, "top-1000 overlap median": _pct(s["median"]),
                               "P5": _pct(s["p5"]), "min": _pct(s["min"]),
                               "works changing tier (median)": _pct(s["tier_change_median"], 2),
                               "CRITICAL count range": f"{s['critical_min']:,}-{s['critical_max']:,}"}
                              for c, s in sens.items()]))
    L += [""]

    # ---- 5. ablation
    L += ["## 5. Ablation (each signal, and corroboration, removed in turn)", "",
          "Removing a signal = treating it as not evaluated for every work (v3 then counts it as 0; v4 "
          "drops it from the denominator). Low top-1000 overlap means the ranking leans heavily on that "
          "signal.", ""]
    for c in fusion.CONFIGS:
        L += [f"**{c}**", ""]
        L += _table(abl[c], {"top1000_overlap": _pct, "spearman": lambda v: _f(v, 3),
                             "mean_abs_delta": lambda v: _f(v, 2)})
        L += [""]
    ps = gate.pattern_share(scores, v3)
    L += [f"Share of the v3 pre-multiplier score that comes from the additive pattern term, among v3 "
          f"HIGH/CRITICAL works: {_pct(ps)} (K5: this term re-counts the same active signals m(k) already "
          "rewards).", ""]

    # ---- 6. tier boundary fixtures
    L += ["## 6. Tier-boundary fixture tests", "",
          "Evaluated now with the same `fusion.assign_tiers` the run used (also a unit test, "
          "`test_tier_boundaries`):", ""]
    fx = pd.DataFrame(gate.TIER_FIXTURES, columns=["risk", "k", "expected"])
    tiers, _ = fusion.assign_tiers(fx["risk"], fx["k"])
    fx["actual"] = tiers.to_numpy()
    fx["result"] = np.where(fx["actual"] == fx["expected"], "PASS", "FAIL")
    L += _table(fx, {"risk": lambda v: "NaN" if pd.isna(v) else repr(float(v))})
    L += ["", f"{(fx['result'] == 'PASS').sum()} of {len(fx)} pass.", ""]

    # ---- 7. missing-signal behaviour
    L += ["## 7. Missing-signal behaviour", ""]
    kd = gate.known_defect_table()
    L += ["Known-defect fixture: five base signals at 0.9, cost anomaly (the heaviest) not evaluated vs "
          "evaluated at 0:", ""]
    L += _table(kd, {"risk": lambda v: _f(v, 2), "m": lambda v: f"{v:.2f}"})
    L += ["",
          "- v3-compatible cannot tell \"not evaluated\" from \"evaluated, found nothing\": both give the "
          "same risk (HIGH), so a work missing one input is capped below CRITICAL however strong the rest "
          "is. That is the defect.",
          "- v4-candidate scores the not-evaluated case on the five signals that exist (0.9 x m(5)=1.15 -> "
          "capped at 100, CRITICAL). The evaluated-at-0 case stays lower (the 0 is real evidence). The "
          "missing signal is not hidden: it lowers confidence (completeness 5/6) and is listed in "
          "`confidence_components.not_evaluated`.",
          ""]
    ne = pd.DataFrame({s: [int(scores[s].isna().sum())] for s in fusion.BASE_SIGNALS})
    L += ["Works with each signal not evaluated in this run:", ""]
    L += _table(ne)
    by_ne = pd.DataFrame({
        "evaluated signals": scores.notna().sum(axis=1),
        "confidence": conf["confidence"],
        "v3": v3["risk"], "v4": v4["risk"],
    }).groupby("evaluated signals").agg(
        works=("confidence", "size"), mean_confidence=("confidence", "mean"),
        v3_mean_risk=("v3", "mean"), v4_mean_risk=("v4", "mean"),
    ).reset_index()
    L += ["", "By number of evaluated signals (confidence falls as signals go missing; it is never 0 "
          "just because one is missing):", ""]
    L += _table(by_ne, {"mean_confidence": lambda v: f"{v:.3f}", "v3_mean_risk": _f, "v4_mean_risk": _f})
    n_null_used = int(((scores.isna()) & (scores.fillna(0) > 0)).sum().sum())
    L += ["",
          f"- v3 rows with NOT_EVALUATED tier: {int((v3['tier'] == 'NOT_EVALUATED').sum())}; v4: "
          f"{int((v4['tier'] == 'NOT_EVALUATED').sum())} (a work with no evaluable signal has no v4 risk, "
          "not risk 0).",
          f"- Works whose v4 denominator excludes at least one not-evaluated signal: "
          f"{int((scores.isna().any(axis=1)).sum()):,}.",
          f"- Not-evaluated scores that entered any weighted sum as a number: {n_null_used} (must be 0).",
          f"- K1 on the fixture: v3 {'PASS' if crit['v3-compatible']['K1'] else 'FAIL'}, "
          f"v4 {'PASS' if crit['v4-candidate']['K1'] else 'FAIL'}.",
          ""]

    # ---- 8. proposal
    L += ["## 8. Proposal and justification", ""]
    L += ["Pass/fail for each of the 8 gate items, per configuration. Criteria are in "
          "`app/analytics/gate.py`, and each is marked with when it was set. No default is proposed: that "
          "decision is yours whatever this table says.", ""]
    L += _items_table(items)
    L += ["", "Underlying K1-K5 criteria:", ""]
    L += _criteria_table(crit)
    L += [""] + _justification(items, crit, sens, ps) + [""]

    # ---- compliance + confidence (context for the reviewer)
    L += ["## Compliance panel C1-C9 (outside the risk score)", ""]
    summ = compliance.summarize(comp).reset_index()
    summ["BLUEPRINT baseline"] = [BASELINES.get((c, r), "") for c, r in zip(summ["check_code"], summ["rule"])]
    c5 = comp[(comp["check_code"] == "C5") & (comp["rule"] == "completed_within_one_year")]
    c5o = comp[(comp["check_code"] == "C5") & (comp["rule"] == "open_within_one_year")]
    c5o_days = pd.Series([dd["days_open_at_as_of"] for dd in c5o["detail"]], dtype=float)
    c5o_over, c5o_incl = int((c5o_days > 365).sum()), int((c5o_days >= 365).sum())
    coll_keys = compliance.key_collision_work_keys(comp)
    c4f = comp[(comp["check_code"] == "C4") & ~comp["passed"]]
    c4_fail, c4_outside = len(c4f), int((~c4f["work_key"].isin(coll_keys)).sum())
    L += _table(summ)
    L += ["",
          f"- C5 completed over one year: {_pct((~c5['passed']).mean(), 2)} of {len(c5):,} "
          "(BLUEPRINT 11.9%).",
          f"- C5 open works: counted as more than a year past sanction when the age at the snapshot's "
          f"data-as-of date ({d['as_of'].date()}) exceeds 365 days: {c5o_over:,}. Counting works at "
          f"exactly 365 days as well gives {c5o_incl:,} (BLUEPRINT: 13,562). The {c5o_incl - c5o_over:,} "
          "works on that boundary day are the only difference. The rule was not changed to match.",
          f"- C4: {c4_fail:,} violations, {c4_outside:,} of them outside the C8 cross-House key collisions "
          "below (BLUEPRINT baseline 0).",
          f"- **C8 identity:** {len(coll_keys)} works mix two Houses' data under one portal ID. "
          "Phase 5 found 136 portal IDs shared by a different LS and RS work (BLUEPRINT §2 assumed "
          "uniqueness); Phase 5a split each into two House-qualified works, so this should be 0 for "
          "any run after the split.",
          ""]
    coll = compliance.key_collision_work_keys(comp)
    if coll:
        idx = scores.index.intersection(pd.Index(sorted(coll)))
        L += [f"  Of the {len(idx)} collided works scored: v3 HIGH/CRITICAL "
              f"{int(v3.loc[idx, 'tier'].isin(['HIGH', 'CRITICAL']).sum())}, v4 HIGH/CRITICAL "
              f"{int(v4.loc[idx, 'tier'].isin(['HIGH', 'CRITICAL']).sum())}.", ""]

    L += ["## Confidence (separate from risk)", "",
          "confidence = 0.35 peer reliability + 0.15 cost-peer dispersion + 0.30 completeness + 0.20 "
          "data quality (weights provisional; `app/analytics/confidence.py`). It never reads a signal "
          "score.", ""]
    cq = conf["confidence"].quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    L += _table(pd.DataFrame([{"P5": cq[0.05], "P25": cq[0.25], "median": cq[0.5], "P75": cq[0.75],
                               "P95": cq[0.95], "min": conf["confidence"].min(),
                               "max": conf["confidence"].max()}]),
                {c: (lambda v: f"{v:.3f}") for c in ["P5", "P25", "median", "P75", "P95", "min", "max"]})
    flags = d["dq"].sum()
    L += ["", "Data-quality flags on scored works: " + ", ".join(f"{k} {int(v):,}" for k, v in flags.items()),
          "", "Confidence component means: " + ", ".join(
              f"{c} {conf[c].mean():.3f}" for c in confidence.COMPONENT_WEIGHTS), ""]
    return "\n".join(L) + "\n"


def gate_signals_before():
    return list(PHASE4_APPROVED.items())


def _spearman(a: pd.Series, b: pd.Series) -> float:
    from scipy.stats import spearmanr
    m = a.notna() & b.notna()
    return float(spearmanr(a[m], b[m]).statistic)


def _criteria_table(crit: dict) -> list[str]:
    def pf(v):
        return "PASS" if v else "FAIL"
    rows = []
    labels = {
        "K1": "Not-evaluated signal is not scored as 0",
        "K2": f"Weight stability (top-1000 overlap median >= {_pct(gate.K2_MEDIAN_MIN, 0)}, "
              f"P5 >= {_pct(gate.K2_P5_MIN, 0)})",
        "K3": f"CRITICAL reachable for >= {_pct(gate.K3_MIN_SHARE, 0)} of works with >= 3 evaluated signals",
        "K4": f"HIGH + CRITICAL <= {_pct(gate.K4_MAX_HIGH_CRITICAL, 0)} of works",
        "K5": "Corroboration counted once",
    }
    for k, lab in labels.items():
        row = {"criterion": f"{k} {lab}"}
        for c in fusion.CONFIGS:
            extra = ""
            if k == "K3":
                extra = f" ({_pct(crit[c]['K3_value'])})"
            if k == "K4":
                extra = f" ({_pct(crit[c]['K4_value'])})"
            row[c] = pf(crit[c][k]) + extra
        rows.append(row)
    return _table(pd.DataFrame(rows))


def gate_calibrated_signals():
    from app.analytics.signals import EMPIRICAL_SIGNALS
    return EMPIRICAL_SIGNALS


ITEM_LABELS = {
    "K3": lambda it: f"CRITICAL reachable for >= {_pct(gate.K3_MIN_SHARE, 0)} of works with >= 3 "
                     f"evaluated signals ({_pct(it['K3_value'])})",
    "K4": lambda it: f"HIGH + CRITICAL <= {_pct(gate.K4_MAX_HIGH_CRITICAL, 0)} ({_pct(it['K4_value'])})",
    "K2": lambda it: f"top-1000 overlap median >= {_pct(gate.K2_MEDIAN_MIN, 0)}, "
                     f"P5 >= {_pct(gate.K2_P5_MIN, 0)}",
    "F": lambda it: "every tier-boundary fixture passes",
    "K1": lambda it: "a not-evaluated signal is not scored as 0",
}


def _descriptive(num: int, items: dict, c: str) -> str:
    if num == 3:
        dist = ", ".join(f"k={int(k)} {_pct(v)}" for k, v in items["k_distribution"].items())
        return (f"descriptive, no threshold set -- {_pct(items['share_k5_plus'])} of works have >= 5 active "
                f"signals ({dist})")
    return (f"descriptive, no threshold set -- worst single-signal ablation keeps "
            f"{_pct(items[c]['worst_ablation_retention'])} of the top 1,000 "
            f"(removing {items[c]['worst_ablation_signal']})")


def _items_table(items: dict) -> list[str]:
    rows = []
    for num, name, key in gate.GATE_ITEMS:
        row = {"#": num, "gate item": name}
        for c in fusion.CONFIGS:
            if key is None:
                row[c] = "not scored (no default proposed)"
            elif key == gate.DESCRIPTIVE:
                row[c] = _descriptive(num, items, c)
            else:
                row[c] = ("PASS" if items[c][key] else "FAIL") + " -- " + ITEM_LABELS[key](items[c])
        rows.append(row)
    rows.append({"#": "+", "gate item": "K5 corroboration counted once",
                 **{c: "PASS" if items[c]["K5"] else "FAIL" for c in fusion.CONFIGS}})
    rows.append({"#": "", "gate item": "**Clears the gate (scored items 1, 2, 4, 6, 7 and K5)**",
                 **{c: "**YES**" if items[c]["clears"] else "**NO**" for c in fusion.CONFIGS}})
    return _table(pd.DataFrame(rows))


def _fails(items: dict, c: str) -> list[str]:
    names = {k: f"item {n} ({nm})" for n, nm, k in gate.GATE_ITEMS if k and k != gate.DESCRIPTIVE}
    out = [names[k] for _, _, k in gate.GATE_ITEMS if k and k != gate.DESCRIPTIVE and not items[c][k]]
    if not items[c]["K5"]:
        out.append("K5")
    return out


def _verdict_lines(items: dict) -> list[str]:
    clearing = items["clearing"]
    per = "; ".join(f"{c} fails {', '.join(_fails(items, c)) or 'nothing'}" for c in fusion.CONFIGS)
    if not clearing:
        head = "**Neither configuration clears the gate.**"
    elif len(clearing) == 1:
        head = (f"**Only {clearing[0]} clears the gate.** It is not proposed as default; that decision "
                "is yours.")
    else:
        head = ("**Both configurations clear the gate.** Neither is proposed as default; that decision "
                "is yours.")
    return [head + " " + per + ". Section 8 has the per-item table. Nothing was tuned to make either "
            "one pass."]


def _justification(items: dict, crit: dict, sens: dict, ps: float) -> list[str]:
    out = ["Written assessment (item 8). No default is proposed:"]
    for c in fusion.CONFIGS:
        f = _fails(items, c)
        out.append(f"- **{c}:** " + ("clears every scored item." if not f else "fails " + ", ".join(f) + "."))
    out.append(
        "- v3-compatible fails K1 and K5 by construction, whatever the calibration. It scores a missing "
        "input as 0, and its additive pattern term re-counts the active signals "
        f"({_pct(ps)} of the pre-multiplier score of its HIGH/CRITICAL works).")
    out.append(
        f"- Weight stability: v3 top-1000 overlap median {_pct(sens['v3-compatible']['median'])} "
        f"(P5 {_pct(sens['v3-compatible']['p5'])}); v4 median {_pct(sens['v4-candidate']['median'])} "
        f"(P5 {_pct(sens['v4-candidate']['p5'])}).")
    out.append(
        f"- Item 3 (descriptive): {_pct(items['share_k5_plus'])} of works have 5 or more active signals. "
        "With calibrated scores, a score > 0.1 means 'above the 10th percentile', which about 90% of "
        "evaluated works are by construction, so the fixed 0.1 active threshold marks most signals "
        "active for most works. The 0.1 threshold belongs to the corroboration logic, which was not "
        "changed.")
    out.append(
        "- Item 5 (descriptive): worst single-signal ablation retention is "
        + "; ".join(f"{c} {_pct(items[c]['worst_ablation_retention'])} (removing "
                    f"{items[c]['worst_ablation_signal']})" for c in fusion.CONFIGS) + ".")
    out.append(
        "- Limits: there are no labelled outcomes. None of this shows which configuration finds more "
        "genuine problems; that needs BLUEPRINT §12's injection curves, labelled duplicate set and audit "
        "sample. Tier boundaries, MIN_CRITICAL_SIGNALS and corroboration were not changed.")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
