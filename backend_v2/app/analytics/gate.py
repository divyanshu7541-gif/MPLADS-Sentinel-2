"""
Phase 5 gate analyses (the evidence for choosing between v3-compatible
and v4-candidate, BLUEPRINT.md §6 / §13 G3). Pure functions over the
in-memory score matrix; scripts/run_risk.py renders them into
docs/phase5_gate_report.md.

DECISION CRITERIA -- written into this module BEFORE any real-data result
was looked at, and not edited afterwards. A config is proposed as default
only if it meets every criterion below; otherwise the report says the
gate is inconclusive (or which config failed which criterion), it does
not pick one anyway.

  K1 Missing-signal correctness: a not-evaluated signal must not be
     scored as 0 (BLUEPRINT.md §6 Confidence). Structural: checked on the
     known-defect fixture (5 signals at 0.9, the sixth not evaluated must
     give the same risk as if only those 5 existed).
  K2 Weight stability: under independent +-20% perturbation of every
     weight (PERTURB_DRAWS seeded draws), top-1000 overlap with the
     unperturbed ranking has median >= 0.80 and 5th percentile >= 0.70.
  K3 Tier reachability: CRITICAL must be reachable (all evaluated signals
     at 1.0) for at least 95% of works that have >= 3 evaluated signals.
  K4 Triage usability: HIGH + CRITICAL together <= 20% of works (a
     "prioritisation" tier that flags more than 1 in 5 works does not
     prioritise). Reported and judged as-is: thresholds are NOT tuned to
     meet it (Phase 5 brief).
  K5 Corroboration counted once: the additive pattern term re-uses the
     same active signals that m(k) already rewards. Structural; v4 passes
     by construction, v3 fails by construction. Reported with its measured
     effect (share of v3 risk contributed by the pattern term among v3
     HIGH/CRITICAL works) so the size of the double count is visible.

ITEMS 3 AND 5 ARE DESCRIPTIVE, NO THRESHOLD SET (owner's decision, 2026-09-26).
Phase 5a step 3 briefly gave them pass/fail thresholds (O1: fewer than 50%
of works with >= 5 active signals; A1: every single-signal ablation keeps
>= 30% of the top 1,000). Those numbers were judgement calls with no
principled basis, set after the v1 results were known, so they were
withdrawn. No replacement thresholds were set. The report shows the raw
numbers: the active-signal distribution and the worst single-signal
ablation retention.

Gate items -> criteria: 1 Reachability K3; 2 Distributions K4; 3 Overlap
DESCRIPTIVE; 4 Sensitivity K2; 5 Ablation DESCRIPTIVE; 6 Tier fixtures
(every fixture passes); 7 Missing-signal behaviour K1; 8 Justification: not
scored -- it is the written case for a proposed default, and no default is
proposed (the owner decides). A configuration "clears the gate" only if the
scored items (1, 2, 4, 6, 7) and K5 all pass.

Because there are no labelled outcomes at this gate, none of K1-K5 is
evidence that either configuration finds more real problems; that is
BLUEPRINT §12's validation work (injection curves, labelled duplicate
set, audit sample). Any proposal here is provisional on that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import fusion

TOP_N = 1000
PERTURB_DRAWS = 50
PERTURB_SEED = 20260925
PERTURB_FRACTION = 0.20
K2_MEDIAN_MIN = 0.80
K2_P5_MIN = 0.70
K3_MIN_SHARE = 0.95
K4_MAX_HIGH_CRITICAL = 0.20
DESCRIPTIVE = "descriptive"  # gate item reported with raw numbers, no pass/fail

GATE_ITEMS = (
    (1, "Reachability", "K3"),
    (2, "Score distributions", "K4"),
    (3, "Active-signal overlap", DESCRIPTIVE),
    (4, "Sensitivity (+-20% weights)", "K2"),
    (5, "Ablation", DESCRIPTIVE),
    (6, "Tier-boundary fixtures", "F"),
    (7, "Missing-signal behaviour", "K1"),
    (8, "Written justification", None),
)

# Tier-boundary fixtures: (risk 0-100, active base signals k, expected tier).
TIER_FIXTURES = (
    (0.0, 0, "LOW"),
    (39.999999, 2, "LOW"),
    (40.0, 1, "MODERATE"),
    (64.999999, 2, "MODERATE"),
    (65.0, 2, "HIGH"),
    (84.999999, 5, "HIGH"),
    (85.0, 3, "CRITICAL"),
    (85.0, 2, "HIGH"),  # CRITICAL demoted: fewer than 3 active base signals
    (100.0, 2, "HIGH"),
    (100.0, 6, "CRITICAL"),
    (84.99999999999999, 4, "CRITICAL"),  # float noise at the boundary rounds to 85
    (float("nan"), 0, "NOT_EVALUATED"),
)


def known_defect_scores() -> pd.DataFrame:
    """Five base signals at 0.9; cost_anomaly (the heaviest) not evaluated
    in row 'ineligible' and evaluated-at-0 in row 'eligible_zero'; row
    'five_only_reference' is the same five with cost absent from the model
    entirely (what K1 says 'ineligible' must equal)."""
    five = {s: 0.9 for s in fusion.BASE_SIGNALS if s != "cost_anomaly"}
    return pd.DataFrame(
        [{"cost_anomaly": np.nan, **five}, {"cost_anomaly": 0.0, **five}],
        index=pd.Index(["ineligible", "eligible_zero"], name="work_key"),
    )[list(fusion.BASE_SIGNALS)]


def known_defect_table() -> pd.DataFrame:
    s = known_defect_scores()
    rows = []
    for c in fusion.CONFIGS:
        f = fusion.fuse(s, c)
        for case in s.index:
            rows.append(
                {
                    "config": c,
                    "case": case,
                    "risk": f.at[case, "risk"],
                    "tier": f.at[case, "tier"],
                    "k": int(f.at[case, "k"]),
                    "m": f.at[case, "m"],
                }
            )
    return pd.DataFrame(rows)


def k1_missing_signal_correct(config: str) -> bool:
    """Not-evaluated must equal 'the same work scored on the five
    evaluated signals alone' -- i.e. the weighted mean over what exists."""
    s = known_defect_scores()
    risk = fusion.fuse(s, config).at["ineligible", "risk"]
    k = 5
    m = fusion.CORROBORATION[min(k, 4)]
    expected = 100.0 * min(1.0, m * 0.9)  # weighted mean of five 0.9s is 0.9
    return bool(abs(risk - expected) < 1e-9)


def top_n(f: pd.DataFrame, n: int = TOP_N) -> pd.Index:
    """Top-n by risk; ties at the 100 cap broken by the uncapped value,
    then work_key, so the set is deterministic."""
    d = f[["risk", "raw"]].dropna(subset=["risk"]).copy()
    d["wk"] = d.index
    d = d.sort_values(["risk", "raw", "wk"], ascending=[False, False, True])
    return d.index[:n]


def overlap(a: pd.Index, b: pd.Index) -> float:
    return len(a.intersection(b)) / max(len(a), 1)


def tier_counts(f: pd.DataFrame) -> pd.Series:
    order = ["CRITICAL", "HIGH", "MODERATE", "LOW", "NOT_EVALUATED"]
    return f["tier"].value_counts().reindex(order, fill_value=0)


def distribution(f: pd.DataFrame) -> dict:
    r = f["risk"].dropna()
    q = r.quantile([0.5, 0.75, 0.9, 0.95, 0.99]).to_dict()
    hist = pd.cut(r, bins=[-0.001, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]).value_counts(sort=False)
    return {
        "n": len(f),
        "n_scored": len(r),
        "mean": r.mean(),
        "quantiles": q,
        "max": r.max(),
        "n_at_100": int((r >= 100 - 1e-9).sum()),
        "hist": hist,
        "tiers": tier_counts(f),
    }


def reachability(scores: pd.DataFrame, config: str) -> pd.DataFrame:
    """Best case per work: every EVALUATED signal at 1.0 (not-evaluated
    stay not-evaluated). Returns the max reachable risk/tier per work."""
    best = scores.notna().astype(float).where(scores.notna())
    f = fusion.fuse(best, config)
    return pd.DataFrame({"n_eligible": f["n_eligible"], "max_risk": f["risk"], "max_tier": f["tier"]})


def reachability_table(scores: pd.DataFrame) -> pd.DataFrame:
    out = []
    for c in fusion.CONFIGS:
        r = reachability(scores, c)
        g = r.groupby("n_eligible")
        t = pd.DataFrame(
            {
                "works": g.size(),
                "min_max_risk": g["max_risk"].min(),
                "critical_reachable_share": g["max_tier"].apply(lambda s: (s == "CRITICAL").mean()),
            }
        )
        t["config"] = c
        out.append(t.reset_index())
    return pd.concat(out, ignore_index=True)


def synthetic_reachability() -> pd.DataFrame:
    """k signals at score s, the rest EVALUATED at 0 -- heaviest-first and
    lightest-first subsets. Shows what evidence each tier needs."""
    order_heavy = sorted(fusion.BASE_SIGNALS, key=lambda x: -fusion.V3_WEIGHTS[x])
    order_light = list(reversed(order_heavy))
    rows = []
    for label, order in (("heaviest", order_heavy), ("lightest", order_light)):
        for k in range(1, 7):
            for sc in (0.6, 0.9, 1.0):
                vals = {s: (sc if s in order[:k] else 0.0) for s in fusion.BASE_SIGNALS}
                df = pd.DataFrame([vals])[list(fusion.BASE_SIGNALS)]
                row = {"subset": label, "k": k, "score": sc}
                for c in fusion.CONFIGS:
                    f = fusion.fuse(df, c)
                    row[f"{c} risk"] = f["risk"].iloc[0]
                    row[f"{c} tier"] = f["tier"].iloc[0]
                rows.append(row)
    return pd.DataFrame(rows)


def active_overlap(scores: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    active = scores.gt(fusion.ACTIVE_THRESHOLD)
    k_dist = active.sum(axis=1).value_counts().sort_index()
    a = active.astype(int)
    co = a.T @ a
    n = np.diag(co)
    union = n[:, None] + n[None, :] - co.to_numpy()
    jac = pd.DataFrame(
        np.where(union > 0, co.to_numpy() / np.maximum(union, 1), 0.0), index=co.index, columns=co.columns
    )
    return k_dist, co, jac


def sensitivity(scores: pd.DataFrame, config: str, base: pd.DataFrame) -> dict:
    rng = np.random.default_rng(PERTURB_SEED)
    base_top = top_n(base)
    base_w = fusion.V3_WEIGHTS if config == "v3-compatible" else fusion.V4_WEIGHTS
    overlaps, tier_change, crit = [], [], []
    for _ in range(PERTURB_DRAWS):
        f_ = rng.uniform(1 - PERTURB_FRACTION, 1 + PERTURB_FRACTION, size=len(base_w))
        w = {k: v * x for (k, v), x in zip(base_w.items(), f_)}
        if config == "v4-candidate":
            tot = sum(w.values())
            w = {k: v / tot for k, v in w.items()}  # v4 weights always sum to 1
        pert = fusion.fuse(scores, config, weights=w)
        overlaps.append(overlap(base_top, top_n(pert)))
        tier_change.append(float((pert["tier"] != base["tier"]).mean()))
        crit.append(int((pert["tier"] == "CRITICAL").sum()))
    o = np.array(overlaps)
    return {
        "median": float(np.median(o)),
        "p5": float(np.percentile(o, 5)),
        "min": float(o.min()),
        "tier_change_median": float(np.median(tier_change)),
        "critical_min": min(crit),
        "critical_max": max(crit),
    }


def ablation(scores: pd.DataFrame, config: str, base: pd.DataFrame) -> pd.DataFrame:
    base_top = top_n(base)
    variants = [(f"- {s}", {"drop": s}) for s in fusion.BASE_SIGNALS]
    variants.append(("- corroboration multiplier", {"use_multiplier": False}))
    if config == "v3-compatible":
        variants.append(("- pattern term", {"use_pattern": False}))
        variants.append(("- multiplier and pattern", {"use_multiplier": False, "use_pattern": False}))
    rows = []
    for label, kw in variants:
        s = scores
        kw = dict(kw)
        if "drop" in kw:
            s = scores.copy()
            s[kw.pop("drop")] = np.nan  # removed == not evaluated (v3: counts 0; v4: excluded)
        f = fusion.fuse(s, config, **kw)
        both = base["risk"].notna() & f["risk"].notna()
        rho = spearmanr(base.loc[both, "risk"], f.loc[both, "risk"]).statistic if both.sum() > 2 else np.nan
        tc = tier_counts(f)
        rows.append(
            {
                "variant": label,
                "top1000_overlap": overlap(base_top, top_n(f)),
                "spearman": rho,
                "mean_abs_delta": float((f["risk"] - base["risk"]).abs().mean()),
                "CRITICAL": int(tc["CRITICAL"]),
                "HIGH": int(tc["HIGH"]),
            }
        )
    return pd.DataFrame(rows)


def pattern_share(scores: pd.DataFrame, base_v3: pd.DataFrame) -> float:
    """Among v3 HIGH/CRITICAL works: mean share of the pre-multiplier
    weighted sum contributed by the additive pattern term."""
    hi = base_v3["tier"].isin(["HIGH", "CRITICAL"])
    if not hi.any():
        return float("nan")
    p = base_v3.loc[hi, "pattern_score"]
    contrib = fusion.V3_PATTERN_WEIGHT * p * (p > fusion.ACTIVE_THRESHOLD)
    total = base_v3.loc[hi, "pre_multiplier"]
    return float((contrib / total.where(total > 0)).mean())


def evaluate_criteria(scores: pd.DataFrame, fused: dict, sens: dict) -> dict:
    out = {}
    for c in fusion.CONFIGS:
        reach = reachability(scores, c)
        three = reach["n_eligible"] >= 3
        k3 = float((reach.loc[three, "max_tier"] == "CRITICAL").mean()) if three.any() else 0.0
        tc = tier_counts(fused[c])
        hc = float((tc["HIGH"] + tc["CRITICAL"]) / max(len(fused[c]), 1))
        out[c] = {
            "K1": k1_missing_signal_correct(c),
            "K2": sens[c]["median"] >= K2_MEDIAN_MIN and sens[c]["p5"] >= K2_P5_MIN,
            "K3": k3 >= K3_MIN_SHARE,
            "K3_value": k3,
            "K4": hc <= K4_MAX_HIGH_CRITICAL,
            "K4_value": hc,
            "K5": c == "v4-candidate",
        }
        out[c]["all"] = all(out[c][k] for k in ("K1", "K2", "K3", "K4", "K5"))
    passing = [c for c in fusion.CONFIGS if out[c]["all"]]
    out["proposal"] = passing[0] if len(passing) == 1 else None
    out["passing"] = passing
    return out


def tier_fixtures_pass() -> bool:
    fx = pd.DataFrame(TIER_FIXTURES, columns=["risk", "k", "expected"])
    tiers, _ = fusion.assign_tiers(fx["risk"], fx["k"])
    return bool((tiers.to_numpy() == fx["expected"].to_numpy()).all())


def evaluate_gate_items(scores: pd.DataFrame, fused: dict, sens: dict, abl: dict) -> dict:
    """Pass/fail for the scored gate items and raw numbers for the
    descriptive ones (items 3 and 5), per configuration. Never proposes a
    default."""
    crit = evaluate_criteria(scores, fused, sens)
    k = scores.gt(fusion.ACTIVE_THRESHOLD).sum(axis=1)
    share_k5 = float((k >= 5).mean())
    fixtures = tier_fixtures_pass()
    out = {"share_k5_plus": share_k5, "k_distribution": k.value_counts(normalize=True).sort_index()}
    for c in fusion.CONFIGS:
        single = abl[c][~abl[c]["variant"].str.contains("corroboration|pattern")]
        worst = single.loc[single["top1000_overlap"].idxmin()]
        items = {
            "K3": crit[c]["K3"],
            "K4": crit[c]["K4"],
            "K2": crit[c]["K2"],
            "F": fixtures,
            "K1": crit[c]["K1"],
            "K5": crit[c]["K5"],
        }
        out[c] = {
            **items,
            "K3_value": crit[c]["K3_value"],
            "K4_value": crit[c]["K4_value"],
            "worst_ablation_retention": float(worst["top1000_overlap"]),
            "worst_ablation_signal": str(worst["variant"]).lstrip("- "),
            "clears": all(items.values()),
        }
    out["clearing"] = [c for c in fusion.CONFIGS if out[c]["clears"]]
    return out
