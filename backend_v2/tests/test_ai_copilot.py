"""Gemma 4 Investigation Copilot: output validation, provider failure
handling and the endpoint contract. External inference is always mocked --
no test spends a paid DigitalOcean call."""

from __future__ import annotations

from datetime import date

import httpx
import openai
import pytest

from app.api import ai
from app.core import ai_provider
from app.db.session import get_db
from app.main import app

VALID_BRIEF = """Executive Summary
Work 133166 is a Lok Sabha work in the stored evidence pack. Its deterministic score is a \
prioritisation signal for human review, not a finding of wrongdoing.

Evidence Interpretation
The Cost Anomaly signal is active and compares the amount with leave-one-out peers. The \
Description Similarity signal is also active.

Peer Comparison
The work is compared with 42 peers at State + Category + Year level. The stored peer median \
and percentile are reported in the evidence pack above.

Recommended Verification Checklist
- Compare the sanctioned amount with the detailed estimate and measurement book.
- Confirm the work stage and completion certificate with the implementing authority.
- Review the peer comparison with an investigator before drawing any conclusion.

Limitations and Unknowns
The available evidence does not establish what happened on site. No site inspection, \
contractor record or payment audit is included in this pack.
"""


def _dossier():
    row = {
        "work_key": "133166",
        "house": "LS",
        "mp": "Test MP",
        "constituency": "Test Constituency",
        "state": "Test State",
        "district_authority": "Test Authority",
        "description": "Construction of a road near the village. Contact 9876543210.",
        "amount": 1200000,
        "amount_basis": "sanctioned",
        "record_date": date(2024, 5, 1),
        "stage": "SANCTIONED",
        "scored": True,
        "active_signal_count": 2,
        "n_eligible": 6,
    }
    item = {
        "signal": "cost_anomaly",
        "signal_label": "Cost Anomaly",
        "signal_score": 0.81,
        "signal_strength": "HIGH",
        "peer_group_size": 42,
        "explanation": "Amount is 3.20x the leave-one-out peer median.",
    }
    ra = {
        "risk_score": 73.4,
        "risk_level": "HIGH",
        "confidence_percent": 82.0,
        "active_signal_count": 2,
        "n_eligible": 6,
        "not_scored_reason": None,
        "context": {
            "peer_group_size": 42,
            "peer_group_level_name": "State + Category + Year",
            "peer_median": 375000,
            "peer_percentile": 96.3,
            "deviation_ratio": 3.2,
            "leave_one_out": True,
        },
    }
    return {"row": row, "items": [item], "ra": ra, "sig_rows": {}}


@pytest.fixture
def no_db():
    app.dependency_overrides[get_db] = lambda: None
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def record(monkeypatch, no_db):
    monkeypatch.setattr("app.api.ai.service.served", lambda db, scope: object())
    monkeypatch.setattr("app.api.ai.service.dossier", lambda db, served, record_id: _dossier())
    monkeypatch.setattr("app.api.ai.ai_provider.is_configured", lambda: True)
    return _dossier()


# ---- output validation ---------------------------------------------------------------------------


def test_parse_brief_accepts_markdown_headings_and_numbered_sections():
    text = VALID_BRIEF.replace("Executive Summary", "## 1. Executive Summary")
    text = text.replace("Evidence Interpretation", "**2. Evidence Interpretation:**")
    sections = ai.parse_brief(text)
    assert [s["key"] for s in sections] == [key for key, _ in ai.SECTION_KEYS]
    assert len(sections[3]["items"]) == 3


def test_parse_brief_rejects_missing_section():
    with pytest.raises(ai.BriefValidationError):
        ai.parse_brief(VALID_BRIEF.replace("Peer Comparison", "Something Else"))


def test_parse_brief_rejects_out_of_order_sections():
    first = VALID_BRIEF.index("Executive Summary")
    second = VALID_BRIEF.index("Evidence Interpretation")
    block = VALID_BRIEF[first:second]
    text = VALID_BRIEF.replace(block, "") + "\n" + block
    with pytest.raises(ai.BriefValidationError):
        ai.parse_brief(text)


def test_parse_brief_rejects_an_accusation():
    text = VALID_BRIEF.replace(
        "The Cost Anomaly signal is active and compares the amount with leave-one-out peers.",
        "The MP is guilty of corruption.",
    )
    with pytest.raises(ai.BriefValidationError):
        ai.parse_brief(text)


# ---- provider configuration / failure mapping -----------------------------------------------------


def test_provider_reports_missing_key(monkeypatch):
    monkeypatch.setattr(ai_provider, "get_api_key", lambda: "")
    with pytest.raises(ai_provider.ProviderNotConfigured):
        ai_provider.generate_text("system", "user")


def test_provider_maps_timeout(monkeypatch):
    monkeypatch.setattr(ai_provider, "get_api_key", lambda: "test-key")

    class FailingCompletions:
        def create(self, **kwargs):
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    class FakeClient:
        chat = type("Chat", (), {"completions": FailingCompletions()})()

    monkeypatch.setattr(ai_provider, "_get_client", lambda *args: FakeClient())
    with pytest.raises(ai_provider.ProviderTimeout):
        ai_provider.generate_text("system", "user")


def test_provider_never_puts_the_key_in_an_exception(monkeypatch):
    secret = "do-secret-key-value"
    monkeypatch.setattr(ai_provider, "get_api_key", lambda: secret)
    monkeypatch.setattr(
        ai_provider,
        "_get_client",
        lambda *args: (_ for _ in ()).throw(RuntimeError(f"boom {secret}")),
    )
    with pytest.raises(ai_provider.ProviderError) as exc:
        ai_provider.generate_text("system", "user")
    assert secret not in str(exc.value)


# ---- endpoint contract ----------------------------------------------------------------------------


def test_status_shape(client):
    body = client.get("/api/ai/status").json()
    assert body["provider"] == "gemma"
    assert body["configured"] is False
    assert "key" not in str(body).lower()


def test_missing_key_returns_503_before_touching_a_record(client):
    r = client.post("/api/ai/investigation-brief/133166")
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_valid_record_returns_five_sections_and_never_a_new_score(client, record, monkeypatch):
    captured = {}

    def fake_generate(system_prompt, user_prompt, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return VALID_BRIEF

    monkeypatch.setattr("app.api.ai.ai_provider.generate_text", fake_generate)
    r = client.post("/api/ai/investigation-brief/133166")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_id"] == "133166"
    assert body["provider"] == "gemma"
    assert [s["key"] for s in body["sections"]] == [key for key, _ in ai.SECTION_KEYS]
    assert "risk_score" not in body  # the authoritative score is never replaced
    assert "73.4" in captured["user"]  # the stored score is supplied, not invented
    assert "9876543210" not in captured["user"]  # phone numbers are masked
    assert "[[phone removed]]" not in captured["user"]
    assert "[phone removed]" in captured["user"]


def test_invalid_record_returns_404(client, monkeypatch, no_db):
    monkeypatch.setattr("app.api.ai.ai_provider.is_configured", lambda: True)
    monkeypatch.setattr("app.api.ai.service.served", lambda db, scope: object())
    monkeypatch.setattr("app.api.ai.service.dossier", lambda db, served, record_id: None)
    r = client.post("/api/ai/investigation-brief/does-not-exist")
    assert r.status_code == 404


def test_timeout_returns_504(client, record, monkeypatch):
    monkeypatch.setattr(
        "app.api.ai.ai_provider.generate_text",
        lambda *a, **k: (_ for _ in ()).throw(ai_provider.ProviderTimeout("slow")),
    )
    r = client.post("/api/ai/investigation-brief/133166")
    assert r.status_code == 504
    assert "unchanged" in r.json()["detail"]


def test_invalid_output_returns_502(client, record, monkeypatch):
    monkeypatch.setattr("app.api.ai.ai_provider.generate_text", lambda *a, **k: "Not a brief.")
    r = client.post("/api/ai/investigation-brief/133166")
    assert r.status_code == 502
    assert "failed validation" in r.json()["detail"]


def test_unsafe_accusation_in_output_is_rejected(client, record, monkeypatch):
    bad = VALID_BRIEF.replace(
        "The Cost Anomaly signal is active and compares the amount with leave-one-out peers.",
        "This work is fraudulent.",
    )
    monkeypatch.setattr("app.api.ai.ai_provider.generate_text", lambda *a, **k: bad)
    r = client.post("/api/ai/investigation-brief/133166")
    assert r.status_code == 502
