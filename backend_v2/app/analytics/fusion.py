"""
Phase 5 fusion: the six base signals -> a 0-100 risk score and a tier,
under two configurations run side by side (BLUEPRINT.md §6 "Fusion and
corroboration"):

  v3-compatible  Faithful reproduction of the old engine
                 (backend/app/risk/signal_fusion.py, corroboration.py,
                 features/patterns.py, risk_engine.py): the 15% additive
                 "cross-signal pattern" component, the 0.1 active gate on
                 every contribution, the FIXED 1.0 denominator, and a
                 not-evaluated signal counted as 0 (old code: fillna(0)).
                 It runs on the corrected Phase 3/4 signals, so it is "the
                 old formula on corrected inputs", the G3 "before" line.

  v4-candidate   Corroboration acts as the multiplier ONLY (no additive
                 pattern term, so base information is counted once); the
                 six base weights are renormalised to sum to exactly 1.0;
                 and the denominator is the weight of the signals that were
                 actually EVALUATED, so a not-evaluated signal is excluded
                 rather than silently scored 0 (it lowers confidence
                 instead, see confidence.py). Everything the brief did not
                 ask to change is kept identical to v3 -- in particular the
                 0.1 active gate and m(k) -- so the comparison isolates the
                 two stated differences.

R = 100 * min(1, m(k) * weighted_sum), k = active base signals (score >
ACTIVE_THRESHOLD), m(k) the v3 table unchanged. Tiers and the CRITICAL
>= 3 active base signals rule are the v3 values, provisional (BLUEPRINT.md
§6 "Tiers"); they are NOT tuned here (Phase 5 brief).

Nothing in this module reads a signal's `reliability` or `dispersion`:
those feed confidence only (BLUEPRINT.md §6 corrections list).
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction

import numpy as np
import pandas as pd

BASE_SIGNALS = (
    "cost_anomaly",
    "near_duplicate",
    "portfolio_concentration",
    "district_authority_pattern",
    "temporal_anomaly",
    "lifecycle_delay",
)

# --- v3 constants, copied verbatim from backend/app (MUST NOT CHANGE there) --
# backend/app/core/config.py SIGNAL_WEIGHTS, mapped old -> new signal names:
# DESCRIPTION_SIMILARITY -> near_duplicate, MP_CONCENTRATION ->
# portfolio_concentration, CONSTITUENCY_PATTERN -> district_authority_pattern,
# STAGE_CONSISTENCY -> lifecycle_delay.
V3_WEIGHTS = {
    "cost_anomaly": 0.25,
    "near_duplicate": 0.20,
    "portfolio_concentration": 0.10,
    "district_authority_pattern": 0.10,
    "temporal_anomaly": 0.10,
    "lifecycle_delay": 0.10,
}
V3_PATTERN_WEIGHT = 0.15
V3_FIXED_DENOMINATOR = 1.0  # signal_fusion.FIXED_TOTAL_WEIGHT
ACTIVE_THRESHOLD = 0.1  # signal_fusion.ACTIVE_THRESHOLD (strict >)
# features/patterns.py per-signal "active for pattern" thresholds (>=).
V3_PATTERN_THRESHOLDS = {
    "cost_anomaly": 0.3,
    "near_duplicate": 0.3,
    "portfolio_concentration": 0.3,
    "district_authority_pattern": 0.3,
    "temporal_anomaly": 0.3,
    "lifecycle_delay": 0.2,
}
# risk/corroboration.py CORROBORATION_LEVELS (k capped at 4).
CORROBORATION = {0: 0.0, 1: 0.90, 2: 1.00, 3: 1.08, 4: 1.15}
# core/config.py RISK_THRESHOLDS (0-1 there, 0-100 here) and MIN_CRITICAL_SIGNALS.
TIER_THRESHOLDS = (("CRITICAL", 85.0), ("HIGH", 65.0), ("MODERATE", 40.0))
MIN_CRITICAL_SIGNALS = 3

# --- v4 weights: the v3 base weights renormalised over the six base signals.
# Kept as exact fractions (25:20:10:10:10:10 over 85 == 5:4:2:2:2:2 over 17)
# so "sums to exactly 1.0" is a true statement, not a float coincidence.
V4_WEIGHTS_EXACT = {k: Fraction(v).limit_denominator(100) / Fraction(85, 100) for k, v in V3_WEIGHTS.items()}
V4_WEIGHTS = {k: float(v) for k, v in V4_WEIGHTS_EXACT.items()}

CONFIGS = ("v3-compatible", "v4-candidate")
# Owner's Phase 5 decision (docs/phase5_gate_report_v3.md, "Decision"): the
# default risk configuration. Recorded per run in the published_run pointer
# by scripts/publish_run.py; both configs are still computed and stored.
DEFAULT_CONFIG = "v4-candidate"


def config_dict(name: str) -> dict:
    base = {
        "config_name": name,
        "active_threshold": ACTIVE_THRESHOLD,
        "corroboration": {str(k): v for k, v in CORROBORATION.items()},
        "tier_thresholds": dict(TIER_THRESHOLDS),
        "min_critical_signals": MIN_CRITICAL_SIGNALS,
    }
    if name == "v3-compatible":
        base.update(
            weights=V3_WEIGHTS,
            pattern_weight=V3_PATTERN_WEIGHT,
            pattern_thresholds=V3_PATTERN_THRESHOLDS,
            denominator="fixed 1.0",
            not_evaluated="counted as 0 (v3 fillna(0))",
        )
    elif name == "v4-candidate":
        base.update(
            weights={k: str(v) for k, v in V4_WEIGHTS_EXACT.items()},
            pattern_weight=0.0,
            denominator="sum of weights of evaluated signals",
            not_evaluated="excluded from denominator",
        )
    else:
        raise ValueError(name)
    return base


def config_hash(name: str) -> str:
    return hashlib.sha256(json.dumps(config_dict(name), sort_keys=True).encode()).hexdigest()


def fusion_config_hash() -> str:
    """One hash covering both configurations -- what a gate report must
    match before risk_result for a run is considered gated."""
    return hashlib.sha256("|".join(config_hash(c) for c in CONFIGS).encode()).hexdigest()


def score_matrix(results: dict[str, pd.DataFrame], index: pd.Index) -> pd.DataFrame:
    """(work x signal) scores with NaN for not-evaluated, aligned by key."""
    cols = {}
    for name in BASE_SIGNALS:
        r = results[name].reindex(index)
        s = pd.to_numeric(r["score"], errors="coerce").astype(float)
        cols[name] = s.where(r["eligible"].fillna(False).astype(bool))
    return pd.DataFrame(cols, index=index)


def corroboration_factor(k: pd.Series) -> pd.Series:
    return k.clip(upper=4).map(CORROBORATION).astype(float)


def assign_tiers(risk: pd.Series, k: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Tier from a 0-100 risk and the active base-signal count. CRITICAL
    below MIN_CRITICAL_SIGNALS active base signals is demoted to HIGH (v3
    risk_engine step 5). NaN risk -> NOT_EVALUATED. Risk is rounded to 9
    decimals first so float noise (e.g. 84.99999999999999) cannot move a
    work across a boundary."""
    r = risk.round(9)
    tier = pd.Series("LOW", index=risk.index, dtype=object)
    for name, thr in reversed(TIER_THRESHOLDS):
        tier[r >= thr] = name
    demote = (tier == "CRITICAL") & (k < MIN_CRITICAL_SIGNALS)
    tier[demote] = "HIGH"
    tier[risk.isna()] = "NOT_EVALUATED"
    return tier, demote


def v3_pattern_score(s0: pd.DataFrame) -> pd.Series:
    """backend/app/features/patterns.py, vectorised identically."""
    act = pd.DataFrame({c: s0[c] >= V3_PATTERN_THRESHOLDS[c] for c in BASE_SIGNALS})
    n = act.sum(axis=1)
    base = np.where(n >= 2, 0.3 + (n - 2) * 0.15, 0.0)
    base = base + np.where(act["cost_anomaly"] & act["near_duplicate"], 0.1, 0.0)
    base = base + np.where(act["temporal_anomaly"], 0.05, 0.0)
    base = base + np.where(act["portfolio_concentration"] | act["district_authority_pattern"], 0.05, 0.0)
    base = base + np.where(act["lifecycle_delay"], 0.05, 0.0)
    return pd.Series(np.where(n >= 2, np.minimum(1.0, base), 0.0), index=s0.index)


def fuse(
    scores: pd.DataFrame,
    config: str,
    weights: dict[str, float] | None = None,
    use_multiplier: bool = True,
    use_pattern: bool = True,
) -> pd.DataFrame:
    """Returns risk (0-100, NaN = not evaluated), tier, critical_demoted,
    k (active base signals), n_eligible, m, pattern_score, pre_multiplier,
    and `raw` (m * weighted sum before the 100 cap -- used only to rank
    works that tie at the cap, never stored as the risk).

    `weights`, `use_multiplier` and `use_pattern` exist only for the G3
    sensitivity/ablation analysis; production calls use the defaults."""
    eligible = scores.notna()
    n_eligible = eligible.sum(axis=1).astype(int)
    active = scores.gt(ACTIVE_THRESHOLD)  # NaN > x is False: not-evaluated is never active
    k = active.sum(axis=1).astype(int)
    m = corroboration_factor(k) if use_multiplier else pd.Series(np.where(k > 0, 1.0, 0.0), index=k.index)

    if config == "v3-compatible":
        w = pd.Series(weights or V3_WEIGHTS)[list(BASE_SIGNALS)]
        s0 = scores.fillna(0.0)  # v3: a not-evaluated signal counts as 0
        weighted = (s0 * active * w).sum(axis=1)
        pattern = v3_pattern_score(s0) if use_pattern else pd.Series(0.0, index=scores.index)
        weighted = weighted + V3_PATTERN_WEIGHT * pattern * (pattern > ACTIVE_THRESHOLD)
        pre = (weighted / V3_FIXED_DENOMINATOR).clip(0, 1)
        raw = weighted / V3_FIXED_DENOMINATOR * m
        risk = 100.0 * (pre * m).clip(0, 1)
    elif config == "v4-candidate":
        w = pd.Series(weights or V4_WEIGHTS)[list(BASE_SIGNALS)]
        num = (scores.fillna(0.0) * active * w).sum(axis=1)
        den = (eligible * w).sum(axis=1)
        pre = (num / den.where(den > 0)).clip(0, 1)  # NaN when nothing was evaluated
        raw = num / den.where(den > 0) * m
        risk = 100.0 * (pre * m).clip(0, 1)
        pattern = pd.Series(np.nan, index=scores.index)
    else:
        raise ValueError(config)

    tier, demoted = assign_tiers(risk, k)
    return pd.DataFrame(
        {
            "risk": risk,
            "tier": tier,
            "critical_demoted": demoted,
            "k": k,
            "n_eligible": n_eligible,
            "m": m,
            "pattern_score": pattern,
            "pre_multiplier": pre,
            "raw": raw,
        },
        index=scores.index,
    )


def risk_output_hash(fused: dict[str, pd.DataFrame], confidence: pd.Series) -> str:
    """Order-independent hash of every (config, work, risk, tier, k,
    confidence) -- BLUEPRINT.md §4 "same inputs, config and seed must
    reproduce the same output hash"."""
    h = hashlib.sha256()
    for name in CONFIGS:
        df = fused[name].sort_index()
        conf = confidence.reindex(df.index)
        for work_key, r, c in zip(df.index, df[["risk", "tier", "k"]].itertuples(index=False), conf):
            risk = "" if pd.isna(r.risk) else f"{r.risk:.6f}"
            h.update(f"{name}|{work_key}|{risk}|{r.tier}|{r.k}|{c:.6f}\n".encode())
    return h.hexdigest()
