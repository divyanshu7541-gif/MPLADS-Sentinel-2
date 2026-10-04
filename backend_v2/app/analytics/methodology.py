"""
Phase 11: generate the chatbot's methodology text from the live
configuration, closing BLUEPRINT.md §11's confirmed gap ("The chatbot's
method text is generated from the same configuration the engine uses, so
the two cannot drift apart again").

render_methodology() reads app.analytics.fusion (weights, active threshold,
corroboration table, tier thresholds) and app.analytics.confidence
(component weights) directly -- so a changed weight or threshold shows up
here automatically, the next time this function runs, with no separate
text to remember to update. It reads the DEFAULT config unless told
otherwise and always states the House-scoping rule. It does not name the
config or internal docs, because the text goes into the copilot prompt.

The hand-authored navigation/page-map section of the old SYSTEM_PROMPT
(backend/app/api/chat.py) is kept as-is in app/api/chat.py: it describes
UI structure, which this build's config objects have no opinion on and
which doesn't drift the way a weight or threshold does.
"""

from __future__ import annotations

from fractions import Fraction

from . import confidence, fusion

SIGNAL_LABELS = {
    "cost_anomaly": "Cost Anomaly",
    "near_duplicate": "Description Similarity",
    "portfolio_concentration": "MP Concentration",
    "district_authority_pattern": "Constituency/Authority Pattern",
    "temporal_anomaly": "Temporal Anomaly",
    "lifecycle_delay": "Lifecycle Delay",
}


def render_methodology(config_name: str = fusion.DEFAULT_CONFIG) -> str:
    if config_name not in fusion.CONFIGS:
        raise ValueError(f"unknown fusion config {config_name!r}")
    cfg = fusion.config_dict(config_name)
    weights = cfg["weights"]
    weight_lines = "\n".join(
        f"  - {SIGNAL_LABELS[s]}: {float(Fraction(str(weights[s]))) * 100:.1f}%" for s in fusion.BASE_SIGNALS
    )
    corrob_lines = "\n".join(
        f"  - {k} active signal{'s' if k != '1' else ''}: x{v:.2f}" for k, v in cfg["corroboration"].items()
    )
    tier_lines = "\n".join(f"  - {name}: risk >= {thr:.0f}" for name, thr in cfg["tier_thresholds"].items())
    conf_lines = "\n".join(
        f"  - {name.replace('_', ' ')}: {w * 100:.0f}%" for name, w in confidence.COMPONENT_WEIGHTS.items()
    )
    denom = (
        "the sum of the weights of the signals that were actually evaluated"
        if config_name == "v4-candidate"
        else "a fixed total of 1.0 (a signal that could not be evaluated counts as 0)"
    )

    return f"""RISK METHODOLOGY (generated from the live scoring configuration)
The risk score (0-100) combines six analytical base signals, each contributing its configured weight
when it is "active" (its own 0-1 score is above {fusion.ACTIVE_THRESHOLD}):
{weight_lines}
The weighted sum is divided by {denom}, then multiplied by a corroboration factor that rewards \
multiple independently-active signals agreeing:
{corrob_lines}
Tiers (over the resulting 0-100 score):
{tier_lines}
CRITICAL additionally requires at least {cfg['min_critical_signals']} active base signals; otherwise it is \
shown as HIGH. A work with no evaluable base signal has NO risk score at all (tier NOT_EVALUATED) -- it is \
never shown as a numeric zero.

CONFIDENCE is a separate 0-1 number reflecting data and context quality, never the \
anomaly itself -- it combines:
{conf_lines}
A signal or a work with missing required data lowers confidence; it is never scored as if the missing \
value were zero.

HOUSE SCOPING: peer groups and every baseline mix Lok Sabha and Rajya Sabha works together, because that \
is the statistically valid comparison -- 'house' only filters which rows are DISPLAYED, and never changes \
which works are compared against which. Rajya Sabha members have no constituency, so any constituency-only \
feature (the map's constituency drill-down, constituency-level profiles) explicitly says "not applicable \
for Rajya Sabha" rather than guessing or omitting silently.

This text is generated, not hand-written -- if a weight or threshold above changes, this description \
changes with it automatically."""
