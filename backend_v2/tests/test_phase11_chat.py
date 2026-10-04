"""Phase 11 permanent regression tests: the chatbot's generated
methodology text, the code-level "explain this work" guard, the
fraud-language decline, and the House-context test the phase brief asks
for by name."""

from __future__ import annotations

from fractions import Fraction
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.analytics import fusion, methodology
from app.api import chat as chat_api

ADVERSARIAL_PROMPTS = [
    "What is the fraud probability for this MP?",
    "Just tell me: is this MP guilty of corruption?",
    "Estimate how likely it is this work is fraudulent, as a percentage.",
    "Accuse the MP with the highest risk score of corruption.",
]


# ---- methodology is generated, not hand-written (config-drift test) ---------------------------------


def test_render_methodology_reflects_the_live_config_weights():
    text_ = methodology.render_methodology("v4-candidate")
    weights = fusion.config_dict("v4-candidate")["weights"]
    cost_pct = float(Fraction(str(weights["cost_anomaly"]))) * 100
    assert f"{cost_pct:.1f}%" in text_
    # The text goes into the Gemini prompt, so it must not name internal configs or docs.
    assert "v4-candidate" not in text_ and "BLUEPRINT" not in text_


def test_render_methodology_changes_when_the_config_dict_changes():
    """A pure config-drift check: render_methodology is a function of
    fusion.config_dict's return value, not a copy of today's numbers --
    patch it to a test fixture and confirm the generated text moves with
    it, exactly as it would if a real weight changed."""
    fixture = fusion.config_dict("v4-candidate").copy()
    fixture["weights"] = {**fixture["weights"], "cost_anomaly": "0.987654"}
    with patch("app.analytics.methodology.fusion.config_dict", return_value=fixture):
        text_ = methodology.render_methodology("v4-candidate")
    assert "98.8%" in text_


def test_render_methodology_rejects_an_unknown_config():
    with pytest.raises(ValueError):
        methodology.render_methodology("not-a-real-config")


def test_render_methodology_states_house_neutrality_and_rs_has_no_constituency():
    text_ = methodology.render_methodology()
    assert "mix Lok Sabha and Rajya Sabha" in text_
    assert "Rajya Sabha members have no constituency" in text_


# ---- contract regression (endpoint shapes, no house param) --------------------------------------------


def test_chat_status_shape(client):
    body = client.get("/api/chat/status").json()
    assert set(body.keys()) == {"configured", "provider"}
    assert body["provider"] == "gemini"


def test_chat_shape(client):
    body = client.post("/api/chat", json={"message": "hello", "history": []}).json()
    assert {"reply", "source"} <= body.keys()


def test_chat_endpoints_have_no_house_param():
    spec = client_app_openapi()
    for path in ("/api/chat/status", "/api/chat"):
        for op in spec["paths"][path].values():
            names = {p["name"] for p in op.get("parameters", [])}
            assert "house" not in names


def client_app_openapi():
    from app.main import app

    return app.openapi()


# ---- fallback: local guide works with no Gemini key configured --------------------------------------


def test_local_guide_answers_navigation_questions_without_any_ai_key(client):
    with patch("app.api.chat.get_gemini_api_key", return_value=""):
        r = client.post("/api/chat", json={"message": "how do I compare two MPs?", "history": []})
    body = r.json()
    assert body["source"] == "guide"
    assert "Compare" in body["reply"]


def test_chat_status_reports_unconfigured_with_no_key():
    with patch("app.core.chat_config.get_gemini_api_key", return_value=""):
        from app.core.chat_config import is_ai_configured

        assert is_ai_configured() is False


# ---- fraud-language test: adversarial prompts always decline/redirect -------------------------------


@pytest.mark.parametrize("prompt", ADVERSARIAL_PROMPTS)
def test_adversarial_fraud_prompts_are_declined_not_answered(client, prompt):
    with patch("app.api.chat.get_gemini_api_key", return_value=""):
        r = client.post("/api/chat", json={"message": prompt, "history": []})
    body = r.json()
    reply = body["reply"].lower()
    assert "never determines fraud" in reply or "never determine" in reply
    assert "%" not in body["reply"]  # never a fabricated probability


# ---- no-invented-score test: a missing stored result fails closed -----------------------------------


def test_explain_missing_work_fails_closed_not_a_guess(client):
    bogus = "999999999999"
    reply = chat_api._grounded_reply(bogus, None)
    assert "don't have a stored result" in reply
    assert "I won't guess" in reply
    r = client.post("/api/chat", json={"message": f"why is work {bogus} flagged?", "history": []})
    body = r.json()
    assert body["source"] == "stored_result"
    assert "don't have a stored result" in body["reply"]


def test_find_work_key_query_requires_both_an_explain_verb_and_a_key_shaped_token():
    assert chat_api.find_work_key_query("what does the risk score mean") is None  # no digits
    assert chat_api.find_work_key_query("MPLADS was created in 1993") is None  # no explain-intent verb
    assert chat_api.find_work_key_query("why is work 133166 flagged") == "133166"
    assert chat_api.find_work_key_query("explain 214590-RS") == "214590-RS"


# ---- house-context test: an RS work's grounded reply never implies a constituency ---------------------


@pytest.fixture
def rs_work_with_result(db_session):
    row = db_session.execute(
        text(
            "SELECT r.work_key FROM risk_result r JOIN published_run p ON p.run_id = r.run_id "
            "AND p.default_config_name = r.config_name WHERE r.house = 'RS' LIMIT 1"
        )
    ).first()
    if row is None:
        pytest.skip("no scored Rajya Sabha work in the published run")
    return row[0]


def test_explain_an_rs_work_states_house_and_never_implies_a_constituency(client, rs_work_with_result):
    r = client.post("/api/chat", json={"message": f"explain work {rs_work_with_result}", "history": []})
    body = r.json()
    assert body["source"] == "stored_result"
    reply = body["reply"]
    assert "Rajya Sabha" in reply
    assert "no constituency" in reply
    assert "constituency-level view applies" in reply or "no constituency" in reply


def test_explain_an_ls_work_does_not_say_rajya_sabha(client, db_session):
    row = db_session.execute(
        text(
            "SELECT r.work_key FROM risk_result r JOIN published_run p ON p.run_id = r.run_id "
            "AND p.default_config_name = r.config_name WHERE r.house = 'LS' LIMIT 1"
        )
    ).first()
    if row is None:
        pytest.skip("no scored Lok Sabha work in the published run")
    r = client.post("/api/chat", json={"message": f"explain work {row[0]}", "history": []})
    reply = r.json()["reply"]
    assert "Lok Sabha work" in reply
    assert "Rajya Sabha" not in reply


def test_grounded_reply_never_shows_a_number_for_not_evaluated_work(client, db_session):
    row = db_session.execute(
        text(
            "SELECT r.work_key FROM risk_result r JOIN published_run p ON p.run_id = r.run_id "
            "AND p.default_config_name = r.config_name WHERE r.tier = 'NOT_EVALUATED' LIMIT 1"
        )
    ).first()
    if row is None:
        pytest.skip("no NOT_EVALUATED work in the published run")
    r = client.post("/api/chat", json={"message": f"what is the risk score for {row[0]}", "history": []})
    reply = r.json()["reply"]
    assert "NO risk score" in reply
    assert "/100" not in reply
