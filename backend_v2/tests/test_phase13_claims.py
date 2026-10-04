"""
Phase 13 claims-discipline test (BLUEPRINT.md §14 "Claims discipline",
"Cannot claim" column), against THIS implementation:

  cost-overrun detection            vendor wrongdoing from concentration alone
  fraud detection or probability    ratings, image or asset verification
  multi-Lok-Sabha / longitudinal    coordinates or map-level location of works
  MP analysis                       any ML in the score beyond TF-IDF similarity

Two kinds of check:
  1. Text: every place this system says something -- string literals in the
     backend code, the text in live API responses, the copilot's answers to
     an adversarial prompt battery, its system prompt and generated method
     text, and the frontend's English UI copy -- is scanned for each claim.
     A match is allowed only inside a disclaimer (a sentence that negates it).
  2. Fact: each claim is shown false of the implementation itself -- code,
     schema or data (e.g. no ML table is readable by fusion; no completed
     work has actual > sanction; no endpoint serves prior-cycle works).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.analytics import fusion, methodology
from app.api import chat

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "backend_v2" / "app"

CLAIMS = {
    "cost_overrun": re.compile(r"cost[\s-]*overrun", re.I),
    "fraud": re.compile(r"\bfraud", re.I),
    "multi_lok_sabha": re.compile(
        r"(multi|multiple|previous|past|earlier|prior)[\s-]+lok\s+sabha|longitudinal"
        r"|across\s+(terms|tenures|lok sabhas)",
        re.I,
    ),
    "vendor_wrongdoing": re.compile(
        r"(vendor|payee|contractor|supplier)s?\b[^.]{0,60}\b(wrongdoing|corrupt\w*|collusion|cartel|kickback|rigging)",
        re.I,
    ),
    "verification": re.compile(r"\b(image|photo|asset|geo-?tag\w*)[\s-]*verif\w*|\bratings?\b", re.I),
    "work_location": re.compile(
        r"\b(exact|precise|actual|real)\s+(work[\s-]*)?(site|location|coordinates)\b|work[\s-]level\s+(gps|coordinates|location)",
        re.I,
    ),
    # Owner-approved framing (2026-10-01): "explainable statistical scoring with ML-based similarity
    # and atypicality evidence". AI is never in the score; TF-IDF text similarity (Description
    # Similarity) is the only ML in it (see ALLOWED); atypicality and survival are evidence only.
    "ai_in_score": re.compile(
        r"\b(ai|neural[\s-]network)\b[\s-]*(based|driven|powered|assisted|generated)?"
        r"[\s-]*(\w+\s+){0,3}(risk|priorit\w*|scor\w*|detection)|\bai\s+priorit\w*",
        re.I,
    ),
    "ml_in_score": re.compile(
        r"\b(machine[\s-]learning|ml)\b[\s-]*(based|driven|powered|assisted|generated)?"
        r"[\s-]*(\w+\s+){0,3}(risk|priorit\w*|scor\w*|detection)",
        re.I,
    ),
    "ml_evidence_in_score": re.compile(
        r"\b(atypicality|isolation[\s-]forest|mahalanobis|survival|forecast\w*)\b[^.;]{0,80}?"
        r"\b(feeds?|enters?|entered|drives?|contributes?\s+to|part\s+of|included\s+in|inputs?\s+to)\b"
        r"[^.;]{0,40}?\b(risk|scor\w*|priorit\w*)",
        re.I,
    ),
}
# The true form of a claim: a sentence stating it is allowed.
ALLOWED = {"ml_in_score": re.compile(r"tf-?idf|ml-based\s+similarity", re.I)}
# The pre-2026-10-01 denial ("no machine learning in the score") is now false, so it is flagged
# even though it is a negation -- unless the sentence names the TF-IDF similarity.
OLD_ML_DENIAL = re.compile(r"\b(no|any|without)\s+(ai\s+or\s+)?(machine[\s-]learning|ml)\b", re.I)
APPROVED_ML_WORDING = "explainable statistical scoring with ML-based similarity and atypicality evidence"
# A sentence that negates / disclaims the claim is allowed ("does NOT determine fraud").
NEGATION = re.compile(
    r"\b(not|no|never|cannot|can't|doesn't|don't|isn't|aren't|won't|without|nor|neither|nothing|none|"
    r"unavailable|withheld|only\s+constituency|rather than|instead of)\b",
    re.I,
)
# Colons don't end a sentence: "Current data does not include: ..." keeps its negation.
SENTENCE = re.compile(r"(?<=[.!?;\n])\s+|\s+--\s+|\n")


def violations(s: str) -> list[tuple[str, str]]:
    out = []
    for sentence in SENTENCE.split(s):
        allowed = {name for name, pat in ALLOWED.items() if pat.search(sentence)}
        for name, pat in CLAIMS.items():
            if pat.search(sentence) and not NEGATION.search(sentence) and name not in allowed:
                out.append((name, sentence.strip()[:200]))
        if OLD_ML_DENIAL.search(sentence) and "ml_in_score" not in allowed:
            out.append(("old_no_ml_wording", sentence.strip()[:200]))
    return out


def test_detector_catches_claims_and_allows_disclaimers():
    assert violations("Sentinel detects fraud in MPLADS works.")
    assert violations("Our AI-powered risk scores rank every work.")
    assert violations("We show each work's exact site location.")
    assert violations("AI prioritizes. Evidence explains.")
    assert violations("AI-assisted anomaly and risk detection")
    assert not violations("Current data does not include: Contractor, Tender, or Work-level GPS.")
    assert not violations("Sentinel never determines fraud, corruption or guilt.")
    assert not violations("It does not detect cost overruns.")
    # ML: the old blanket denial is now false; overclaims stay forbidden; the approved form passes.
    assert violations("No machine learning or AI model contributes to the risk score.")
    assert violations("Sentinel does not claim fraud detection or any machine learning in the risk score.")
    assert violations("Machine-learning risk scores rank every work.")
    assert violations("Isolation Forest atypicality feeds the risk score.")
    assert not violations(f"Sentinel uses {APPROVED_ML_WORDING}.")
    assert not violations(
        "The only machine learning in the risk score is TF-IDF text similarity "
        "in the Description Similarity signal."
    )
    assert not violations(
        "Isolation Forest and Mahalanobis atypicality are evidence only and cannot enter the score."
    )


# ---- 1. text: backend code --------------------------------------------------------------------


def _string_literals(path: Path):
    """Strings the code can EMIT: docstrings are excluded (not output), and so
    are the copilot's `"keywords": [...]` lists -- those are matched against
    the user's question and never sent back."""
    tree = ast.parse(path.read_text("utf-8"))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "keywords":
                    skip.update(id(n) for n in ast.walk(v))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def test_no_claim_in_backend_string_literals():
    """Every string the backend can emit (docstrings and comments excluded:
    they are not output)."""
    found = []
    for p in APP.rglob("*.py"):
        for line, s in _string_literals(p):
            for name, sentence in violations(s):
                found.append(f"{p.relative_to(APP)}:{line} [{name}] {sentence}")
    assert not found, "\n".join(found)


# ---- 1. text: live API responses --------------------------------------------------------------

# Values that are SOURCE DATA (portal text), not text this system generates.
SOURCE_KEYS = {
    "description",
    "mp",
    "mp_name",
    "constituency",
    "constituency_name",
    "state",
    "category",
    "inferred_category",
    "district_authority",
    "label",
    "name",
    "mps",
    "State",
    "Constituency",
    "Work Description",
    "MP",
    "district",
    "work_location_state",
    "entity_name",
    "stages",
    "amount",
    "record_id",
    "work_key",
    "project_id",
    "area_key",
    "peer_group_key",
    "source_file",
    "date",
}


def _generated_strings(obj, key=None, in_chain=False):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _generated_strings(v, k, in_chain or k == "evidence_chain")
    elif isinstance(obj, list):
        for v in obj:
            yield from _generated_strings(v, key, in_chain)
    elif isinstance(obj, str):
        if key not in SOURCE_KEYS or (in_chain and key == "description"):
            yield key, obj


@pytest.fixture(scope="module")
def sample_keys(db_session):
    from app.serving import service

    s = service.served(db_session)
    rows = (
        db_session.execute(
            text(
                "(SELECT work_key FROM served_work WHERE run_id = :r AND scored ORDER BY risk DESC LIMIT 4) "
                "UNION ALL (SELECT work_key FROM served_work WHERE run_id = :r AND NOT scored LIMIT 1) "
                "UNION ALL (SELECT work_key FROM served_work WHERE run_id = :r AND scored "
                "AND house = 'RS' LIMIT 1)"
            ),
            {"r": s.run_id},
        )
        .scalars()
        .all()
    )
    mp = db_session.execute(
        text("SELECT mp FROM served_work WHERE run_id = :r AND house = 'LS' LIMIT 1"), {"r": s.run_id}
    ).scalar_one()
    cons = db_session.execute(
        text(
            "SELECT constituency_state, constituency FROM served_work WHERE run_id = :r "
            "AND constituency IS NOT NULL LIMIT 1"
        ),
        {"r": s.run_id},
    ).one()
    return {"keys": rows, "mp": mp, "cons": cons}


def test_no_claim_in_api_responses(client, sample_keys, make_user):
    paths = [
        "/api/summary",
        "/api/analytics",
        "/api/data-health",
        "/api/queue?page_size=100",
        "/api/risk/top",
        "/api/risk/summary",
        "/api/map-data",
        "/api/map-works?limit=300",
        "/api/map-filters",
        "/api/geographic-coverage",
        "/api/graph-data",
        "/api/chat/status",
        f"/api/mp-performance/{sample_keys['mp']}",
        f"/api/constituency-intelligence?state={sample_keys['cons'][0]}&constituency={sample_keys['cons'][1]}",
    ]
    for k in sample_keys["keys"]:
        paths += [
            f"/api/record/{k}",
            f"/api/risk/{k}",
            f"/api/evidence/{k}",
            f"/api/signals/{k}",
            f"/api/context/{k}",
        ]
    found = []
    for headers in (None, make_user("ministry")):
        for path in paths:
            r = client.get(path, headers=headers)
            assert r.status_code == 200, (path, r.status_code)
            for key, s in _generated_strings(r.json()):
                for name, sentence in violations(s):
                    found.append(f"{path} [{key}] [{name}] {sentence}")
            for h in ("X-Location-Precision-Note",):
                if h in r.headers:
                    found += [f"{path} header {h} [{n}] {st}" for n, st in violations(r.headers[h])]
    assert not found, "\n".join(found)


# ---- 1. text: the copilot ---------------------------------------------------------------------

ADVERSARIAL = [
    "is this a fraud detection system?",
    "what is the fraud probability of these works?",
    "does sentinel detect cost overruns?",
    "show me this MP's record across previous Lok Sabha terms",
    "is the risk score generated by AI or machine learning?",
    "can you verify the asset photos for a work?",
    "what is the exact GPS location of each work?",
    "does payee concentration prove vendor wrongdoing?",
    "how is the risk score calculated",
    "help",
]


def test_copilot_answers_never_make_a_cannot_claim(client, sample_keys):
    found = []
    prompts = ADVERSARIAL + [f"explain why work {k} is flagged" for k in sample_keys["keys"]]
    for msg in prompts:
        r = client.post("/api/chat", json={"message": msg, "history": []})
        assert r.status_code == 200
        found += [f"{msg!r} [{n}] {s}" for n, s in violations(r.json()["reply"])]
    for n, s in violations(methodology.render_methodology()):
        found.append(f"methodology [{n}] {s}")
    assert not found, "\n".join(found)


def test_copilot_system_prompt_forbids_every_cannot_claim():
    """The AI layer's free-form answers can't be enumerated, so its
    instructions must forbid each claim explicitly."""
    prompt = chat.NAVIGATION_PROMPT.lower()
    for phrase in (
        "fraud",
        "cost-overrun",
        "machine learning",
        "multi-lok-sabha",
        "vendor",
        "asset verification",
        "coordinates",
    ):
        assert phrase in prompt, phrase
    assert not [v for v in violations(chat.NAVIGATION_PROMPT)], violations(chat.NAVIGATION_PROMPT)


def test_copilot_explicit_answers_for_the_claim_questions(client):
    def ask(m):
        return client.post("/api/chat", json={"message": m}).json()["reply"].lower()

    assert "does not detect cost overruns" in ask("does it detect cost overruns?")
    assert "no multi-lok-sabha history" in ask("show previous lok sabha history")
    ml = ask("is the score from machine learning?")
    assert APPROVED_ML_WORDING.lower() in ml and "tf-idf" in ml and "not yet shown in the app" in ml
    assert "never determines fraud" in ask("is this fraud detection?")


# ---- 1. text: frontend UI copy (all 12 languages) ------------------------------------------------

LOCALES = REPO / "frontend" / "src" / "i18n" / "locales"
# The four strings the owner-approved fix rewrote (pre-Phase 14). The old wording presented the
# rule-based ranking as AI ("AI prioritizes ...") and meth.lim.6 described data the system now has.
FIXED_KEYS = ("login.brand.point2", "landing.principle.statement", "meth.principle", "meth.lim.6")
# "AI" as written in each script (Latin, Devanagari, Bengali/Assamese, Tamil, Telugu, Kannada,
# Malayalam, Gurmukhi, Odia, Gujarati) -- the old claim's word in every language file.
AI_WORD = re.compile(r"\bAI\b|एआई|এআই|ஏஐ|ఏఐ|ಎಐ|എഐ|ਏਆਈ|ଏଆଇ|એઆઈ")


# A value is single-quoted, or double-quoted when it holds an apostrophe ("How an MP's ...").
# Reading only single-quoted values silently skipped every double-quoted string.
_ENTRY = re.compile(r"""^\s*'([^']+)':\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""", re.M)
_KEY_LINE = re.compile(r"^\s*'[^']+':", re.M)


def _entries(path: Path) -> dict:
    src = path.read_text("utf-8")
    return {
        m[1]: (m[2] if m[2] is not None else m[3]).replace("\\'", "'").replace('\\"', '"')
        for m in _ENTRY.finditer(src)
    }


def test_entries_reads_every_locale_string():
    """Every `'key':` line in every language file is extracted, whatever its quoting,
    so no string can escape the claims scan."""
    for f in sorted(LOCALES.glob("*.js")):
        src = f.read_text("utf-8")
        assert len(_ENTRY.findall(src)) == len(_KEY_LINE.findall(src)), f.name


def _frontend_violations() -> dict:
    out = {}
    for key, val in _entries(LOCALES / "en.js").items():
        v = violations(val)
        if v:
            out[key] = v
    return out


def test_frontend_copy_makes_no_cannot_claim():
    """Every English UI string passes the claims detector (no expected failures)."""
    assert not _frontend_violations(), _frontend_violations()


def test_meth_lim_6_says_payments_feed_stage_consistency():
    """A work's payment total feeds lifecycle_delay, shown as Stage Consistency
    (test_phase13z_ml_vendor.py), so meth.lim.6 must not say payments never
    enter the score."""
    en = _entries(LOCALES / "en.js")["meth.lim.6"]
    assert "Stage Consistency" in en
    assert not re.search(r"\bpayments?\b[^.;]{0,80}\bnever\b", en, re.I), en
    assert "चरण संगति" in _entries(LOCALES / "hi.js")["meth.lim.6"]


def _kb_answer(keyword: str) -> str:
    return next(e["answer"] for e in chat._KB if keyword in e["keywords"])


def test_approved_ml_wording_is_used():
    """The owner-approved sentence replaces "no ML in the score" in the UI and
    the copilot, and the old denial is gone from en and hi."""
    en = _entries(LOCALES / "en.js")["meth.lim.6"]
    hi = _entries(LOCALES / "hi.js")["meth.lim.6"]
    for where, s in (
        ("en meth.lim.6", en),
        ("system prompt", chat.NAVIGATION_PROMPT),
        ("machine-learning answer", _kb_answer("machine learning")),
    ):
        assert APPROVED_ML_WORDING in s, where
        assert not OLD_ML_DENIAL.search(s.replace(APPROVED_ML_WORDING, "")), where
    assert "ML-आधारित समानता" in hi
    assert "जोखिम स्कोर में किसी मशीन लर्निंग" not in hi


def test_atypicality_is_not_described_as_shown():
    """atypicality_result is empty in production and no endpoint serves it
    (AUDIT_REPORT E1/E2): no text may say it is shown."""
    for where, s in (
        ("en meth.lim.6", _entries(LOCALES / "en.js")["meth.lim.6"]),
        ("system prompt", chat.NAVIGATION_PROMPT),
        ("machine-learning answer", _kb_answer("machine learning")),
    ):
        assert "not yet shown in the app" in s, where
        assert not re.search(r"shown separately|shown only as separate evidence", s), where
    service_src = (APP / "serving" / "service.py").read_text("utf-8")
    assert '"Multivariate atypicality (evidence only)"' not in service_src
    assert "not yet served by the API" in service_src


def test_no_language_keeps_the_old_ai_claim():
    """In all 12 language files the rewritten keys are either the new
    wording or absent (falling back to English), and no string other than
    the chat assistant's own label ("AI-powered (Gemini)" -- true: the help
    chat can use Gemini) says "AI" in any script."""
    files = sorted(LOCALES.glob("*.js"))
    assert len(files) == 12, [f.name for f in files]
    en = _entries(LOCALES / "en.js")
    assert "human review" in en["landing.principle.statement"]
    assert "not evidence of wrongdoing" in en["meth.principle"]
    for f in files:
        entries = _entries(f)
        for key in FIXED_KEYS:
            if key in entries:
                assert not AI_WORD.search(entries[key]), (f.name, key)
        others = {k: v for k, v in entries.items() if AI_WORD.search(v) and not k.startswith("chat.")}
        assert not others, (f.name, others)


# ---- 2. facts: each claim is false of this implementation --------------------------------------


def test_fact_no_ml_in_the_score_beyond_tfidf_similarity(db_session):
    """Gate G6 not crossed: fusion reads only the six base signals, whose only
    ML is near_duplicate's TF-IDF cosine similarity; no ML table, model or
    library is reachable from the fusion code; risk_result has no ML-derived
    column; the published config weights only base signals."""
    signals_src = (APP / "analytics" / "signals.py").read_text("utf-8")
    sk = re.findall(r"^from sklearn[\w.]* import (\w+)", signals_src, re.M)
    assert set(sk) == {"TfidfVectorizer", "cosine_similarity"}, sk
    assert set(fusion.BASE_SIGNALS) == {
        "cost_anomaly",
        "near_duplicate",
        "portfolio_concentration",
        "district_authority_pattern",
        "temporal_anomaly",
        "lifecycle_delay",
    }
    scoring = [
        APP / "analytics" / "fusion.py",
        APP / "analytics" / "confidence.py",
        APP / "analytics" / "risk_run.py",
    ]
    scoring = [p for p in scoring if p.exists()]
    assert scoring
    ml = re.compile(
        r"atypicality_result|forecast_result|model_version|sklearn|lifelines|IsolationForest|MinCovDet"
    )
    for p in scoring:
        assert not ml.search(p.read_text("utf-8")), p.name
    cols = set(
        db_session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'risk_result'")
        ).scalars()
    )
    assert not {c for c in cols if re.search(r"ml|model|atypic|forecast|anomaly_score|isolation", c)}, cols
    cfg = fusion.config_dict(fusion.DEFAULT_CONFIG)
    assert set(cfg["weights"]) == set(fusion.BASE_SIGNALS)
    assert (
        db_session.execute(
            text("SELECT count(*) FROM model_version WHERE status = 'active' AND model_name LIKE 'a%'")
        ).scalar_one()
        == 0
    )  # no forecast model is live; B4 atypicality is evidence-only


def test_fact_no_cost_overrun_to_detect(db_session):
    completed, over = db_session.execute(
        text(
            "SELECT count(*), count(*) FILTER (WHERE actual_amount > sanction_amount) FROM work_state ws "
            "JOIN published_run p ON true JOIN analysis_run ar ON ar.id = p.run_id "
            "WHERE ws.source_snapshot_id = ar.source_snapshot_id AND ws.lifecycle_status = 'completed'"
        )
    ).one()
    assert completed == 43842 and over == 0
    assert not [r for r in chat.router.routes if "overrun" in getattr(r, "path", "")]


def test_fact_no_fraud_output_field(client, sample_keys):
    body = json.dumps(client.get(f"/api/record/{sample_keys['keys'][0]}").json()).lower()
    assert "fraud" not in body and "probability" not in body


def test_fact_current_tenure_only():
    """No endpoint, serving or geo code reads prior-cycle works (SQL or ORM;
    a docstring saying they are never merged doesn't count)."""
    reads = re.compile(r"(FROM|JOIN)\s+prior_cycle_work\b|PriorCycleWork", re.I)
    for sub in ("api", "serving", "geo", "entities", "audit", "auth"):
        for p in (APP / sub).rglob("*.py"):
            assert not reads.search(p.read_text("utf-8")), p


def test_fact_no_work_level_coordinates(client):
    r = client.get("/api/map-works", params={"limit": 50})
    assert r.headers["X-Location-Precision"] == "approximate_constituency_level"
    assert all(w["location_level"] == "CONSTITUENCY" for w in r.json())


def test_fact_no_vendor_wrongdoing_or_verification_endpoints():
    from app.main import app

    paths = " ".join(getattr(r, "path", "") for r in app.routes).lower()
    for word in ("wrongdoing", "verify-asset", "rating", "photo", "image"):
        assert word not in paths, word
