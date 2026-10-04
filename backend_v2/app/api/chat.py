"""
Phase 11 (docs/frontend_contract.md #20, #21): the in-app help chatbot.
Contract preserved exactly from Phase 0/backend/app/api/chat.py -- same two
endpoints, same request/response shapes, no `house` parameter added.

Two layers, same design as backend/app/api/chat.py (preserved, not
rewritten -- BLUEPRINT.md §11 "Copilot" / this phase's brief):
  1. A local, rule-based navigation guide, always available.
  2. An optional Gemini layer for free-form questions when GEMINI_API_KEY
     is configured, with a graceful fallback to the local guide on any
     failure (never a broken chat).

What Phase 11 actually changes:
  - The methodology portion of the system prompt is now GENERATED from the
    live fusion/confidence configuration (app/analytics/methodology.py),
    closing BLUEPRINT.md §11's confirmed gap ("hand-written, not generated
    from live config"). The hand-authored navigation/page-map text is kept
    verbatim -- it describes UI, not scoring, and doesn't drift the same way.
  - A CODE-LEVEL guard (not a prompt instruction): any message that reads as
    "explain/why/risk/score/flagged/priority" together with something that
    looks like a work key fetches that work's actual stored risk_result (the
    published run's default config) -- and forecast_result, if any -- BEFORE
    any reply is built. If no such row exists, the reply says so explicitly
    and nothing is invented; the LLM is never asked to guess a number.
  - House-context: a grounded reply always states the work's house, and a
    Rajya Sabha work's reply never implies it has a constituency.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..analytics import methodology
from ..auth import ratelimit
from ..auth.deps import Principal, reader
from ..auth.scope import NATIONAL, Scope
from ..core.chat_config import (
    get_fallback_models,
    get_gemini_api_key,
    get_gemini_model,
    is_ai_configured,
    mask_key,
)
from ..db.session import get_db
from ..serving.redact import mask_personal
from . import chat_lookup

router = APIRouter()
log = logging.getLogger("sentinel.chat")

NAVIGATION_PROMPT = """You are the in-app help assistant for MPLADS Sentinel, an investigation and \
analytics platform for the Members of Parliament Local Area Development Scheme (MPLADS).

Answer only questions about how to use this platform, what its pages/features mean, and how its \
analytical methodology works. Be concise (2-5 sentences unless a list is clearer), friendly, and precise. \
If asked something unrelated to the platform, briefly say you're focused on helping with MPLADS Sentinel \
and redirect to what you can help with. Never claim the platform makes accusations of fraud or corruption \
-- it only prioritises works for human review, based on statistical/analytical signals.

CLAIMS DISCIPLINE (never state or imply any of these; if asked, say plainly that the platform does not do it)
- No fraud detection and no fraud probability: scores prioritise works for human review, nothing more.
- No cost-overrun detection: the portal data never has an actual amount above the sanctioned amount \
(0 of 43,842 completed works), so this is reported as a data-integrity result, not detected.
- Describe the risk score only as explainable statistical scoring with ML-based similarity and atypicality \
evidence. The score is a deterministic fusion of six analytical signals; the only machine learning in it is \
the TF-IDF text similarity inside the Description Similarity signal. Isolation Forest and Mahalanobis \
atypicality are evidence-only layers that are not yet shown in the app and cannot enter the score without a \
pre-registered study; survival forecasting is inactive, and the 365-day early-warning model was closed. \
No AI model, neural network or language model is part of the score. You, the chat assistant, never \
score anything.
- No multi-Lok-Sabha history and no longitudinal MP analysis: the data is the current tenure only (18th \
Lok Sabha and sitting Rajya Sabha members) from one portal snapshot.
- No vendor or payee wrongdoing inferred from concentration alone.
- No ratings, image, photo or asset verification.
- No work-site coordinates: map markers are one representative point per constituency, not a work's site.

PLATFORM MAP
- Overview ("/dashboard"): headline KPIs, a risk-distribution donut chart, works-by-category bar chart, \
implementation stage funnel (Recommended -> Sanctioned -> Completed), and a table of top cost anomalies.
- Investigation Map ("/map"): a Leaflet map plotting flagged works geographically (constituency-level \
approximate locations only -- one representative point per constituency, never an individual work's real \
site), colour-coded by risk priority, with filters and marker clustering. Rajya Sabha has no constituency \
drill-down; the map says so explicitly rather than showing an empty or misleading view.
- Priority Queue ("/queue"): a filterable, sortable, paginated table of every work record ranked by \
priority/risk score. Filters include priority tier, implementation stage, state, MP, and free-text search.
- Analytics ("/analytics"): platform-wide statistics -- risk distribution, amount-distribution histogram, \
signal distribution, and flag-rate rankings by category and by state.
- Browse MP / Constituency ("/mp-performance"): search any MP or constituency to see their works, \
completion rate, \
recorded fund amounts by stage, category breakdown, a trend chart, and their flagged works. Supports \
comparing 2+ MPs or 2+ constituencies side by side.
- Data Health ("/data-health"): dataset quality report -- completeness, duplicate/near-duplicate counts, \
per-column missingness, which analytical signals are active vs unavailable, and category-inference stats.
- Methodology ("/methodology"): explains the guiding principle (human reviewers decide; the platform only \
prioritises, using explainable statistical scoring with ML-based similarity and atypicality evidence), \
the processing pipeline, the analytical signals and their weights, the evidence model, and explicit \
limitations of the system.
- Case / Record Detail ("/record/:id", opened by clicking any work): full case file for one work -- the \
stored risk score and tier, the active signals, the evidence chain, raw source/normalized fields, and \
related/similar records.

Keep answers grounded in the RISK METHODOLOGY section below and in what you are told about a specific \
work when asked about one. If you don't know a specific number (e.g. "how many high-priority works are \
there right now"), tell the user to check the Overview or Analytics page rather than guessing.
Never mention internal configuration, run, version or gate names, or internal document references."""

_KB = [
    {
        "keywords": [
            "risk score",
            "priority score",
            "what does the score mean",
            "how is the score calculated",
        ],
        "answer": (
            "The risk score is a weighted combination of six analytical base signals, mapped to one "
            "of four bands: LOW, MODERATE, HIGH, or CRITICAL. It's a prioritisation signal for human "
            "review -- not a finding of wrongdoing. Ask me for the exact weights and thresholds, or "
            "see the Methodology page, for the full current configuration."
        ),
    },
    {
        "keywords": ["find high", "high priority", "high-risk works", "most risky"],
        "answer": (
            'Open Priority Queue from the sidebar and set the Priority filter to "High Priority '
            'Review" -- or click the red slice/legend row on the Overview donut chart, which jumps '
            "straight there."
        ),
    },
    {
        "keywords": ["compare", "comparison", "two mps", "vs"],
        "answer": (
            'Go to Browse MP / Constituency and use the "Compare MP / Constituency" section: '
            "choose MPs or Constituencies, then search and "
            "add two or more to the comparison list before running it. You'll get a side-by-side "
            "table of works, completion rate, recorded amounts and risk counts."
        ),
    },
    {
        "keywords": ["map", "geospatial", "where are the works", "location"],
        "answer": (
            "The Investigation Map plots flagged works by constituency (Lok Sabha only) or state -- "
            "one representative point per constituency, never an individual work's real site. "
            "Markers are colour-coded by risk priority and cluster together when zoomed out; use the "
            "filters panel to narrow by state, priority or stage. Rajya Sabha has no constituency, so "
            "its drill-down explicitly says so."
        ),
    },
    {
        "keywords": ["evidence chain", "how was this flagged", "why flagged", "evidence"],
        "answer": (
            "Open any work's Case Detail page (click a row in the Priority Queue or a marker on the "
            "map) and look at the Evidence Chain section -- it traces the path from the source record "
            "through normalization, peer-group comparison, individual signals, and the fused risk "
            "score. Ask me about a specific work's stored result directly and I'll look it up rather "
            "than guess."
        ),
    },
    {
        "keywords": ["data quality", "data health", "missing data", "completeness"],
        "answer": (
            "The Data Health page reports dataset completeness, duplicate/near-duplicate candidate "
            "counts, per-column missing-value rates, and which analytical signals are currently "
            "active vs unavailable (e.g. if a required field is missing from the source data). Data "
            "quality affects confidence, not the risk score."
        ),
    },
    {
        "keywords": ["signal", "how many signals", "what signals"],
        "answer": (
            "There are six analytical base signals: Cost Anomaly, Description Similarity, MP "
            "Concentration, Constituency/Authority Pattern, Temporal Anomaly, and Lifecycle Delay. "
            "Ask me for their current weights, or see the Methodology page."
        ),
    },
    {
        "keywords": ["export", "download", "csv"],
        "answer": (
            "MPLADS Sentinel doesn't currently have a built-in export/download button -- the "
            "Priority Queue and Analytics pages are the best views for reviewing records in bulk "
            "on-screen."
        ),
    },
    {
        "claim": True,
        "keywords": ["cost overrun", "overrun", "over budget", "exceeds sanction", "above sanction"],
        "answer": (
            "MPLADS Sentinel does not detect cost overruns and doesn't claim to: in the portal data the "
            "actual amount never exceeds the sanctioned amount (0 of 43,842 completed works), so there is "
            "nothing to detect. That fact is reported as a data-integrity result instead."
        ),
    },
    {
        "claim": True,
        "keywords": [
            "previous lok sabha",
            "past lok sabha",
            "earlier lok sabha",
            "previous term",
            "past term",
            "historical",
            "longitudinal",
            "over the years",
            "multiple lok sabha",
        ],
        "answer": (
            "MPLADS Sentinel covers the current tenure only -- the 18th Lok Sabha and sitting Rajya Sabha "
            "members, from one portal snapshot. It has no multi-Lok-Sabha history and no longitudinal "
            "analysis of an MP across terms."
        ),
    },
    {
        "claim": True,
        "keywords": [
            "machine learning",
            "ml model",
            "ai model",
            "artificial intelligence",
            "ai score",
            "is the score ai",
            "neural",
        ],
        "answer": (
            "MPLADS Sentinel uses explainable statistical scoring with ML-based similarity and atypicality "
            "evidence. The risk score is a deterministic fusion of six analytical signals; the only machine "
            "learning in it is the TF-IDF text similarity inside the Description Similarity signal. "
            "Isolation Forest and Mahalanobis atypicality results are evidence only: they are computed "
            "offline, are not yet shown in the app, and cannot enter the score without a pre-registered "
            "study; survival forecasting is inactive, and the 365-day early-warning model was closed. "
            "This chat assistant only answers help questions; it never scores."
        ),
    },
    {
        "claim": True,
        "keywords": ["fraud", "corrupt", "guilty", "accuse"],
        "answer": (
            "MPLADS Sentinel never determines fraud, corruption or guilt. It only identifies "
            "statistically unusual patterns and prioritises works for human investigators to verify "
            '-- see the Methodology page\'s "Important Limitations" section for details.'
        ),
    },
    {
        "keywords": ["navigate", "pages", "sections", "what can this do", "features", "help", "what is this"],
        "answer": (
            "MPLADS Sentinel has seven main sections in the sidebar: Overview (KPIs & headline "
            "charts), Investigation Map (geospatial view), Priority Queue (filterable ranked table), "
            "Analytics (platform-wide statistics), Browse MP / Constituency (per-MP/constituency profiles & "
            "comparison), Data Health (dataset quality report), and Methodology (how the scoring "
            "works). Click any work row or map marker to open its full Case Detail file."
        ),
    },
]


# The guide's keywords, and those of its claim answers (fraud, cost overruns, history, ML): a message
# that asks one of those is never turned into a constituency/MP lookup, so the decline is unchanged.
_KB_KEYWORDS = [kw for entry in _KB for kw in entry["keywords"]]
_CLAIM_KEYWORDS = [kw for entry in _KB if entry.get("claim") for kw in entry["keywords"]]


def _local_answer(message: str, ai_failed: bool = False) -> str:
    text_ = message.lower()
    best_score, best_answer = 0, None
    for entry in _KB:
        score = sum(1 for kw in entry["keywords"] if kw in text_)
        if score > best_score:
            best_score, best_answer = score, entry["answer"]
    if best_answer:
        return best_answer
    if ai_failed:
        return (
            "I can help you navigate MPLADS Sentinel and explain how it works -- try asking about a "
            "specific page, what the risk score or an analytical signal means, how to compare two "
            "MPs, or ask me to explain a specific work by its record id. The AI-powered assistant is "
            "configured but couldn't be reached just now, so you're getting the built-in guide "
            "instead."
        )
    return (
        "I can help you navigate MPLADS Sentinel and explain how it works -- try asking about a "
        "specific page (Overview, Investigation Map, Priority Queue, Analytics, "
        "Browse MP / Constituency, Data "
        "Health, Methodology), what the risk score or an analytical signal means, how to compare two "
        "MPs, or ask me to explain a specific work by its record id. Free-form answers are not "
        "available right now."
    )


# ---- code-level "explain this work" guard --------------------------------------------------------


_EXPLAIN_INTENT = re.compile(r"\b(explain|why|risk|score|flagged|priority|evidence)\b", re.IGNORECASE)
_WORK_KEY_TOKEN = re.compile(r"\b(\d{3,}(?:-RS)?)\b")

_RISK_SQL = text(
    """
    SELECT r.work_key, r.house, r.risk, r.tier, r.confidence, r.base_signal_count, r.n_eligible,
           r.config_name, p.run_id
    FROM risk_result r
    JOIN published_run p ON p.run_id = r.run_id AND p.default_config_name = r.config_name
    WHERE r.work_key = :wk
    """
)
_FORECAST_SQL = text(
    "SELECT model, eligible, prediction FROM forecast_result WHERE work_key = :wk AND run_id = :run"
)
# Plain names for forecast_result.model (models/analytics.py FORECAST_MODELS); raw codes are internal.
_FORECAST_LABELS = {
    "a1_cox": "completion-time model",
    "a2_day90": "365-day early-warning model",
    "a2_day180": "365-day early-warning model",
}


def find_work_key_query(message: str) -> Optional[str]:
    """A message is treated as an "explain this work" query only when it
    both names something work-key-shaped AND uses an explain/why/risk-type
    verb -- so an ordinary question that happens to contain a number (a
    year, an amount) is never misread as a record lookup."""
    if not _EXPLAIN_INTENT.search(message):
        return None
    m = _WORK_KEY_TOKEN.search(message)
    return m.group(1) if m else None


def lookup_stored_result(db: Session, work_key: str, scope: Scope = NATIONAL) -> Optional[dict]:
    row = db.execute(_RISK_SQL, {"wk": work_key}).mappings().first()
    if row is None:
        return None
    # Phase 13: a work outside the caller's scope is answered exactly like a
    # work with no stored result -- the copilot never reveals it exists.
    if not scope.national:
        sc, sp = scope.direct()
        visible = db.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM served_work WHERE run_id = :run AND work_key = :wk{sc})"),
            {"run": row["run_id"], "wk": work_key, **sp},
        ).scalar_one()
        if not visible:
            return None
    forecasts = db.execute(_FORECAST_SQL, {"wk": work_key, "run": row["run_id"]}).mappings().all()
    return {**dict(row), "forecasts": [dict(f) for f in forecasts]}


def _grounded_reply(work_key: str, stored: Optional[dict]) -> str:
    if stored is None:
        return (
            f"I don't have a stored result for work \"{work_key}\" in the published analysis -- I won't "
            "guess a risk score or explanation for it. Check the record id in the Priority Queue or Case "
            "Detail page, or ask me again once it has been scored."
        )
    house_note = (
        "This is a Rajya Sabha work -- it has no constituency (Rajya Sabha members represent a state, not a "
        "seat), so no constituency-level view applies to it."
        if stored["house"] == "RS"
        else "This is a Lok Sabha work."
    )
    if stored["tier"] == "NOT_EVALUATED":
        risk_line = "It has NO risk score: no base signal had enough data to be evaluated for it."
    else:
        risk_line = (
            f"Its stored risk score is {stored['risk']:.1f}/100 (tier {stored['tier']}), "
            f"with confidence {stored['confidence']:.2f} and "
            f"{stored['base_signal_count']} of {stored['n_eligible']} evaluated signals active."
        )
    forecast_line = ""
    if stored["forecasts"]:
        eligible = [f["model"] for f in stored["forecasts"] if f["eligible"]]
        names = ", ".join(dict.fromkeys(_FORECAST_LABELS.get(m, "forecast model") for m in eligible))
        if names:
            forecast_line = f" Forecast evidence is also on file for this work ({names})."
    return f'Work "{work_key}": {risk_line} {house_note}{forecast_line}'


# ---- Gemini layer (unchanged design from backend/app/api/chat.py) --------------------------------


@lru_cache(maxsize=4)
def _get_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _build_contents(message: str, history: List[dict], system_prompt: str):
    from google.genai import types

    contents = []
    for turn in history[-8:]:
        role = "model" if turn.get("role") == "assistant" else "user"
        text_ = str(turn.get("content", "")).strip()
        if text_:
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text_)]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    return contents


def _call_gemini(message: str, history: List[dict], system_prompt: str) -> Tuple[Optional[str], bool]:
    api_key = get_gemini_api_key()
    if not api_key:
        return None, False
    try:
        from google.genai import errors, types
    except ImportError:
        log.warning("Gemini call skipped: 'google-genai' isn't installed.")
        return None, True
    try:
        client = _get_client(api_key)
    except Exception as e:
        log.warning(
            "Gemini call failed: could not create SDK client (key: %s) -- %s: %s",
            mask_key(api_key),
            type(e).__name__,
            e,
        )
        return None, True

    contents = _build_contents(message, history, system_prompt)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt, temperature=0.4, max_output_tokens=500
    )
    models_to_try = [get_gemini_model(), *get_fallback_models()]
    last_error = None
    for i, model in enumerate(models_to_try):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
        except errors.ClientError as e:
            code = getattr(e, "code", None)
            last_error = f"ClientError {code}: {getattr(e, 'message', e)}"
            if code == 404 and i < len(models_to_try) - 1:
                continue
            log.warning("Gemini call failed (key: %s): %s", mask_key(api_key), last_error)
            return None, True
        except (errors.ServerError, errors.APIError) as e:
            log.warning(
                "Gemini call failed (key: %s): %s %s: %s",
                mask_key(api_key),
                type(e).__name__,
                getattr(e, "code", "?"),
                getattr(e, "message", e),
            )
            return None, True
        except Exception as e:
            log.warning("Gemini call failed (key: %s): %s: %s", mask_key(api_key), type(e).__name__, e)
            return None, True
        try:
            reply_text = (response.text or "").strip()
        except Exception:
            reply_text = ""
        if not reply_text:
            return None, True
        return reply_text, True
    log.warning(
        "Gemini call failed (key: %s): all candidate models unavailable. Last error: %s",
        mask_key(api_key),
        last_error,
    )
    return None, True


class ChatMessage(BaseModel):
    role: str = Field(max_length=20)
    content: str = Field(max_length=4000)


class ChatEntityRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["mp", "constituency"]
    name: str = Field(max_length=200)


class ChatContext(BaseModel):
    # Follow-up context the frontend echoes back from the previous reply (stateless server).
    # Size-capped, unknown keys dropped, and the name is re-checked against the served lists.
    model_config = ConfigDict(extra="ignore")
    compare_with: Optional[ChatEntityRef] = None


class ChatRequest(BaseModel):
    # Bounded input (BLUEPRINT.md §11 "Input handling"); the copilot never
    # receives credentials -- only this text and the generated method text.
    message: str = Field(max_length=2000)
    history: List[ChatMessage] = Field(default_factory=list, max_length=20)
    context: Optional[ChatContext] = None


@router.get("/api/chat/status")
async def chat_status():
    return {"configured": is_ai_configured(), "provider": "gemini"}


@router.post("/api/chat")
def chat(request: Request, body: ChatRequest, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    ratelimit.limit("chat", request, f"user:{p.user_id}" if p.authenticated else "")
    message = (body.message or "").strip()
    if not message:
        return {
            "reply": "Ask me anything about navigating MPLADS Sentinel or how its scoring works, or ask "
            "me to explain a specific work by its record id.",
            "source": "guide",
        }

    work_key = find_work_key_query(message)
    if work_key:
        try:
            stored = lookup_stored_result(db, work_key, p.scope)
        except SQLAlchemyError:
            stored = None
        return {"reply": _grounded_reply(work_key, stored), "source": "stored_result"}

    # Constituency / MP questions: answered from the served data, never by the model.
    found = chat_lookup.answer(db, p, message, body.context, _KB_KEYWORDS, _CLAIM_KEYWORDS)
    if found is not None:
        return {**found, "source": "lookup"}

    system_prompt = f"{NAVIGATION_PROMPT}\n\n{methodology.render_methodology()}"
    # Phone and Aadhaar-shaped numbers are masked in everything sent to the model.
    history = [{"role": h.role, "content": mask_personal(h.content)} for h in body.history]
    reply, ai_attempted = _call_gemini(mask_personal(message), history, system_prompt)
    if reply:
        return {"reply": reply, "source": "gemini"}
    return {"reply": _local_answer(message, ai_failed=ai_attempted), "source": "guide"}
