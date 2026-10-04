"""
Phase 5 confidence: "how reliable is the evidence", kept separate from
risk (BLUEPRINT.md §6 "Confidence"). It combines ONLY data and context
quality and never reads a signal's anomaly score (enforced by
test_confidence_never_reads_a_signal_score, a source-level check):

  peer_reliability  mean of the evaluated signals' own `reliability`
                    (usable peers / distinct other entities, Phase 4),
                    scaled by the cost peer level used (L1 most specific)
  dispersion        1 / (1 + robust log-scale sd) of the cost peer group;
                    wide peer groups mean a cost distance says less.
                    Unknown (cost not evaluated) counts as 0, never as
                    "tight"
  completeness      evaluated base signals / 6 -- a not-evaluated signal
                    lowers confidence here (it is never scored 0 in v4)
  data_quality      1 - 0.25 per data-quality flag: missing description,
                    referential gap (sanctioned but absent from the
                    recommended file), inconsistent FLAG, unusable amount,
                    cross-House key collision (compliance C8)

confidence = 0.35 peer + 0.15 dispersion + 0.30 completeness + 0.20 quality,
in [0, 1]. The component weights are provisional and documented, not
tuned; the same confidence is stored on both fusion configurations' rows
because it does not depend on the fusion formula.
"""

from __future__ import annotations

import pandas as pd

from .fusion import BASE_SIGNALS
from .signals import MAD_TO_STD

COMPONENT_WEIGHTS = {"peer_reliability": 0.35, "dispersion": 0.15, "completeness": 0.30, "data_quality": 0.20}
LEVEL_FACTOR = {"L1": 1.0, "L2": 0.9, "L3": 0.8}
LEVEL_FACTOR_NONE = 0.7
FLAG_PENALTY = 0.25
DQ_FLAGS = (
    "missing_description",
    "referential_gap",
    "flag_inconsistent",
    "amount_unusable",
    "cross_house_key_collision",
)


def compute_confidence(
    results: dict[str, pd.DataFrame], ctx: pd.DataFrame, dq_flags: pd.DataFrame, index: pd.Index
) -> pd.DataFrame:
    eligible = pd.DataFrame(
        {s: results[s]["eligible"].reindex(index).fillna(False).astype(bool) for s in BASE_SIGNALS}
    )
    reliability = pd.DataFrame(
        {s: pd.to_numeric(results[s]["reliability"].reindex(index), errors="coerce") for s in BASE_SIGNALS}
    ).where(eligible)
    level = ctx["level"].reindex(index)
    level_factor = level.map(LEVEL_FACTOR).fillna(LEVEL_FACTOR_NONE).astype(float)
    peer = (reliability.mean(axis=1).fillna(0.0) * level_factor).clip(0, 1)

    cost_disp = pd.to_numeric(results["cost_anomaly"]["dispersion"].reindex(index), errors="coerce")
    cost_disp = cost_disp.where(eligible["cost_anomaly"])
    dispersion = (1.0 / (1.0 + MAD_TO_STD * cost_disp.clip(lower=0))).fillna(0.0)

    completeness = eligible.sum(axis=1) / len(BASE_SIGNALS)

    flags = dq_flags.reindex(index)[list(DQ_FLAGS)].fillna(False).astype(bool)
    data_quality = (1.0 - FLAG_PENALTY * flags.sum(axis=1)).clip(0, 1)

    comp = pd.DataFrame(
        {
            "peer_reliability": peer,
            "dispersion": dispersion,
            "completeness": completeness,
            "data_quality": data_quality,
        },
        index=index,
    )
    conf = sum(comp[c] * w for c, w in COMPONENT_WEIGHTS.items()).clip(0, 1)
    comp["confidence"] = conf
    comp["n_flags"] = flags.sum(axis=1)
    comp["flags"] = [[f for f, on in zip(DQ_FLAGS, row) if on] for row in flags.itertuples(index=False)]
    comp["not_evaluated"] = [
        [s for s, on in zip(BASE_SIGNALS, row) if not on] for row in eligible.itertuples(index=False)
    ]
    return comp


def components_json(comp: pd.DataFrame) -> list[dict]:
    out = []
    for r in comp.itertuples(index=False):
        out.append(
            {
                "peer_reliability": round(float(r.peer_reliability), 6),
                "dispersion": round(float(r.dispersion), 6),
                "completeness": round(float(r.completeness), 6),
                "data_quality": round(float(r.data_quality), 6),
                "flags": list(r.flags),
                "not_evaluated": list(r.not_evaluated),
                "weights": COMPONENT_WEIGHTS,
            }
        )
    return out
