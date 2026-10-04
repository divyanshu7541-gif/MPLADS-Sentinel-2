"""
Phase 12: the vocabulary the protected frontend already understands.

The frontend translates a fixed set of backend values (frontend/src/i18n/
labels.js -- read-only for this build): stage names in upper case, the OLD
engine's signal names, three confidence levels, three signal strengths, and
a fixed list of recommendation sentences. v2 computes different things
under different names, so this module maps v2 values onto that vocabulary
-- and only onto it: no value here changes a score, a tier or a count.

Signal names (v2 -> contract name the frontend translates). The same mapping
fusion.py documents for its v3 weights (backend/app/core/config.py
SIGNAL_WEIGHTS: DESCRIPTION_SIMILARITY -> near_duplicate, ...). Two of the
contract names describe their v2 signal only loosely, so every response
that carries one also carries `signal_description`, the v2 definition:
  district_authority_pattern -> CONSTITUENCY_PATTERN  (v2 compares a DISTRICT
      AUTHORITY with other authorities in its state; the old engine grouped
      by constituency, which Rajya Sabha doesn't have)
  lifecycle_delay -> STAGE_CONSISTENCY  (v2 measures age since sanction and
      payment pace against peers; the old one checked stage combinations)

Confidence levels and signal strengths are the OLD engine's bands, carried
over for contract meaning, not re-tuned on v2 data (the same posture Phase
9 took for its 0.7 "high signal" cut -- docs/phase12_report.md):
  confidence  HIGH >= 0.7, MEDIUM >= 0.5, else LOW   (backend/app/risk/confidence.py)
  strength    HIGH > 0.7, MEDIUM > 0.3, else LOW     (backend/app/evidence/evidence_engine.py)
"""

from __future__ import annotations

from ..analytics import fusion

STAGES = {"recommended": "RECOMMENDED", "sanctioned": "SANCTIONED", "completed": "COMPLETED"}

CONTRACT_SIGNAL = {
    "cost_anomaly": "COST_ANOMALY",
    "near_duplicate": "DESCRIPTION_SIMILARITY",
    "portfolio_concentration": "MP_CONCENTRATION",
    "district_authority_pattern": "CONSTITUENCY_PATTERN",
    "temporal_anomaly": "TEMPORAL_ANOMALY",
    "lifecycle_delay": "STAGE_CONSISTENCY",
}
SIGNAL_LABEL = {
    "cost_anomaly": "Cost Anomaly",
    "near_duplicate": "Description Similarity",
    "portfolio_concentration": "MP Concentration",
    "district_authority_pattern": "Constituency Pattern",
    "temporal_anomaly": "Temporal Anomaly",
    "lifecycle_delay": "Stage Consistency",
}
SIGNAL_DESCRIPTION = {
    "cost_anomaly": "Amount against leave-one-out peers of the same work type, state and sanction year "
    "(robust, log scale).",
    "near_duplicate": "Description (TF-IDF) and amount closeness to another work by the same MP or "
    "authority.",
    "portfolio_concentration": "Share of the MP's own works in this work type, against other MPs in the "
    "state.",
    "district_authority_pattern": "The district authority's work-type share and mean amount, against other "
    "authorities in the state.",
    "temporal_anomaly": "Burst of sanctions in one week for this MP or authority, against its own "
    "typical week.",
    "lifecycle_delay": "Open work's age since sanction and payment pace, against completed peer works.",
}
# Lower-case keys used by the old analytics/profile signal dictionaries.
CONTRACT_SIGNAL_KEY = {s: CONTRACT_SIGNAL[s].lower() for s in CONTRACT_SIGNAL}
SIGNAL_COLUMN = {s: f"s_{s}" for s in fusion.BASE_SIGNALS}

CONFIDENCE_HIGH = 0.7
CONFIDENCE_MEDIUM = 0.5
STRENGTH_HIGH = 0.7
STRENGTH_MEDIUM = 0.3

# The old engine's exact recommendation sentences (the frontend translates
# these, joined by "; ").
ACTION = {
    "cost_anomaly": "Review cost estimates against comparable projects in the same area and category",
    "near_duplicate": "Examine potentially duplicate or templated work descriptions for consistency",
    "portfolio_concentration": "Review the MP's concentration of works in this category for unusual patterns",
    "district_authority_pattern": (
        "Analyze constituency-level patterns in work categories and amount distributions"
    ),
    "temporal_anomaly": "Investigate temporal clustering for potential batch-processing concerns",
    "lifecycle_delay": "Verify work stage status and cross-reference with implementation records",
}
ACTION_CORROBORATED = "Multiple independent indicators suggest this project warrants priority review"
ACTION_STANDARD = "Standard review recommended"

# L1/L2 reuse the frontend's own level names (labels.js PEER_LEVELS), which
# mean the same grouping ("Category" there = the work type here), so they
# translate. L3 (national, per sanction year) has no old equivalent -- the
# old "National Category" had no year -- so it keeps its own name.
PEER_LEVEL_NAME = {
    "L1": "State + Category + Year",
    "L2": "State + Category",
    "L3": "National Category + Year",
}
PEER_LEVEL_NUMBER = {"L1": 1, "L2": 2, "L3": 3}


def confidence_label(c: float | None) -> str | None:
    if c is None:
        return None
    if c >= CONFIDENCE_HIGH:
        return "HIGH"
    if c >= CONFIDENCE_MEDIUM:
        return "MEDIUM"
    return "LOW"


def strength(score: float) -> str:
    if score > STRENGTH_HIGH:
        return "HIGH"
    if score > STRENGTH_MEDIUM:
        return "MEDIUM"
    return "LOW"


def is_active(score: float | None) -> bool:
    return score is not None and score > fusion.ACTIVE_THRESHOLD


def rupees(v) -> str:
    """Indian digit grouping, no decimals: 1234567 -> '12,34,567'."""
    if v is None:
        return ""
    n = int(round(float(v)))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + s


def _pct(x) -> str:
    v = float(x) * 100
    return f"{v:.1f}%" if abs(v) < 10 else f"{v:.0f}%"


def explanation(signal: str, ev: dict, score: float | None = None) -> str:
    """One plain sentence from the signal's own stored evidence
    (signal_result.evidence) -- facts only, no accusation."""
    ev = ev or {}
    try:
        if signal == "cost_anomaly":
            ratio = ev.get("ratio_to_peer_median")
            lvl = PEER_LEVEL_NAME.get(ev.get("level"), ev.get("level") or "peer group")
            return (
                f"Amount Rs.{rupees(ev.get('amount_used'))} ({ev.get('amount_basis') or 'recorded'}) is "
                f"{ratio:.2f}x the leave-one-out peer median of Rs.{rupees(ev.get('peer_median'))} "
                f"({ev.get('n_usable_excl_self')} peers, {lvl})."
            )
        if signal == "near_duplicate":
            basis = {"mp": "by the same MP", "activity_type": "of the same work type"}.get(
                ev.get("match_basis"), "in the same comparison block"
            )
            gap = ev.get("date_gap_days")
            gap_txt = f", {gap} days apart" if gap is not None else ""
            return (
                f"Description is {_pct(ev.get('cosine_similarity', 0))} similar to work "
                f"{ev.get('matched_work_key')} {basis} (Rs.{rupees(ev.get('amount'))} vs "
                f"Rs.{rupees(ev.get('matched_amount'))}{gap_txt})."
            )
        if signal == "portfolio_concentration":
            return (
                f"{ev.get('mp_type_count')} of this MP's {ev.get('mp_total_works')} works "
                f"({_pct(ev.get('mp_type_share', 0))}) are this work type, against an average share of "
                f"{_pct(ev.get('peer_mean_share', 0))} across {ev.get('n_peer_mps')} other MPs in the state."
            )
        if signal == "district_authority_pattern":
            txt = (
                "This district authority's share of this work type is "
                f"{_pct(ev.get('type_share', 0))} of its {ev.get('authority_total_works')} works, against "
                f"{_pct(ev.get('peer_mean_share', 0))} across "
                f"{ev.get('n_peer_authorities')} other authorities in the state"
            )
            if ev.get("driving_component") == "amount":
                txt += "; its mean amount for this type is the stronger deviation"
            return txt + "."
        if signal == "temporal_anomaly":
            who = "MP" if ev.get("entity_type") == "mp" else "district authority"
            return (
                f"{ev.get('own_week_count')} works dated ({ev.get('date_field') or 'sanction'}) in the week "
                f"{ev.get('own_week')}, against a typical {ev.get('typical_weekly_count')} per active week "
                f"for this {who}."
            )
        if signal == "lifecycle_delay":
            return (
                f"Open {ev.get('age_days')} days since sanction -- longer than "
                f"{_pct(ev.get('age_exceedance', 0))} of {ev.get('n_completed_peers')} completed peer works; "
                f"{_pct(ev.get('payment_share', 0))} of the sanction already paid."
            )
    except (TypeError, ValueError):
        pass
    return (
        f"{SIGNAL_LABEL.get(signal, signal)} score {score:.2f}."
        if score is not None
        else SIGNAL_LABEL.get(signal, signal)
    )
