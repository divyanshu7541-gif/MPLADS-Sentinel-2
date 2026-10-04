"""Copilot constituency/MP lookups (app/api/chat_lookup.py).

NOT YET RUN when written (2026-10-02): listed in DEPLOY_NOTES.md "Not yet run".

Every name in the no-DB tests is SYNTHETIC (Synth..., Testyadav, Synthstate) --
no real MP or constituency name is hard-coded. The stored constituency name
comes in two forms in the data, so both are tested on synthetic names:
  FORM_TITLE  title case, no reservation tag        e.g. "Synthpur"
  FORM_UPPER  upper case with a reservation tag     e.g. "SYNTHPUR(ST)"
The DB tests at the bottom read their names from served_work at run time.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.api import chat as chat_api
from app.api import chat_lookup as L
from tests.test_phase11_chat import ADVERSARIAL_PROMPTS
from tests.test_phase13_claims import ADVERSARIAL, violations

# ---- synthetic data ------------------------------------------------------------------------------

SYNTH_MPS = [
    "Shri Synth Alpha Kumar",
    "Smt Synth Beta Devi (2022-28)",  # tenure tag, as Rajya Sabha names carry
    "DR.K.SYNTHGAMMA",  # title and initial run together
    "Synth Delta Testyadav",
    "Synth Epsilon Testyadav",
]


def _cons(form_upper: bool) -> list[dict]:
    main = "SYNTHPUR(ST)" if form_upper else "Synthpur"
    return [
        {"State": "Synthstate One", "Constituency": main},
        {"State": "Synthstate Two", "Constituency": "North East Synthnagar"},
        {"State": "Synthstate Two", "Constituency": "North West Synthnagar(SC)"},
        {"State": "Synthstate Two", "Constituency": "East Synthnagar"},
        {"State": "Synthstate Three", "Constituency": "Synthgarh and Testdiu"},
    ]


IDX_TITLE = L.build_index(SYNTH_MPS, _cons(False))  # FORM_TITLE
IDX_UPPER = L.build_index(SYNTH_MPS, _cons(True))  # FORM_UPPER
SYNTH_PERSON = "Synthperson"
SYNTH_FATHER = "Synthfather"


def _works(n=10):
    return [
        {
            "record_id": str(900000 + i),
            "description": (
                f"Handpump for beneficiary name Shri {SYNTH_PERSON} Kumar s/o Shri {SYNTH_FATHER} work {i}"
            ),
            "amount": 100000.0 * (i + 1),
            "stage": "COMPLETED",
            "priority": "HIGH",
            "risk_level": "HIGH",
        }
        for i in range(n)
    ]


def synth_profile(kind: str, name: str, house: str = "LS", n_works: int = 10) -> dict:
    base = {
        "total_works": 40,
        "scored_works": 30,
        "critical_count": 2,
        "high_priority_count": 8,
        "review_recommended_count": 5,
        "low_risk_count": 15,
        "amount_by_priority": {"CRITICAL": {"total": 500000.0}, "HIGH": {"total": 1200000.0}},
        "top_flagged_works": _works(n_works),
        "served": {"run_id": 1, "config": "synthetic-config", "data_as_of": "2026-01-01", "is_latest": True},
    }
    if kind == "constituency":
        return {
            **base, "constituency_name": name, "state": "Synthstate One", "mps": [SYNTH_MPS[0]], "house": "LS"
        }
    return {
        **base,
        "mp_name": name,
        "constituency": "Synthpur" if house == "LS" else "",
        "state": "Synthstate One",
        "house": house,
    }


ANON = SimpleNamespace(scope=None, authenticated=False)
LOGGED_IN = SimpleNamespace(scope=None, authenticated=True)


@pytest.fixture
def fake_db(monkeypatch):
    """answer() with no database: the served build, the name lists and the
    profile/comparison functions are replaced by synthetic ones."""
    calls = {"profile": [], "comparison": []}
    state = {"index": IDX_TITLE, "house": "LS"}
    monkeypatch.setattr(L.service, "served", lambda db, scope=None: SimpleNamespace())
    monkeypatch.setattr(L, "_index", lambda db, s: state["index"])

    def cons_profile(db, s, name):
        calls["profile"].append(("constituency", name))
        return synth_profile("constituency", name)

    def mp_profile(db, s, name):
        calls["profile"].append(("mp", name))
        return synth_profile("mp", name, house=state["house"])

    def comparison(db, s, names, kind):
        calls["comparison"].append((tuple(names), kind))
        rows = [{"name": n, **synth_profile("constituency", n)} for n in names]
        return {"comparison": rows, "entity_type": kind}

    monkeypatch.setattr(L.service, "constituency_profile", cons_profile)
    monkeypatch.setattr(L.service, "mp_profile", mp_profile)
    monkeypatch.setattr(L.service, "comparison", comparison)
    return SimpleNamespace(calls=calls, state=state)


def ask(message, principal=ANON, context=None):
    return L.answer(None, principal, message, context, chat_api._KB_KEYWORDS, chat_api._CLAIM_KEYWORDS)


def _all_text(r: dict) -> list[str]:
    return (
        [r["reply"]]
        + [x["label"] for x in r.get("links", []) + r.get("suggestions", [])]
        + [x["message"] for x in r.get("suggestions", [])]
    )


# ---- normalising ---------------------------------------------------------------------------------


def test_norm_strips_titles_reservation_tags_and_tenure():
    assert L.norm("SYNTHPUR(ST)") == "synthpur"
    assert L.norm("North West Synthnagar(SC)") == "north west synthnagar"
    assert L.norm("Smt Synth Beta Devi (2022-28)") == "synth beta devi"
    assert L.norm("DR.K.SYNTHGAMMA") == "k synthgamma"
    assert L.norm("Smt Synth Roy (Synthbanerjee)") == "synth roy synthbanerjee"  # a name in brackets stays


# ---- matching: both stored forms -----------------------------------------------------------------


@pytest.mark.parametrize("idx", [IDX_TITLE, IDX_UPPER], ids=["FORM_TITLE", "FORM_UPPER"])
@pytest.mark.parametrize(
    "msg", ["tell me about Synthpur", "show works in SYNTHPUR(ST)", "synthpur", "Synthpur constituency"]
)
def test_both_stored_forms_match_directly(idx, msg):
    m = L.resolve(msg, idx)
    assert m.how == "direct"
    assert m.entities[0].kind == "constituency" and m.entities[0].key == "synthpur"


def test_exact_phrase_keeps_the_longest_name():
    m = L.resolve("show works in North East Synthnagar", IDX_TITLE)
    assert m.how == "direct" and m.entities[0].name == "North East Synthnagar"  # not "East Synthnagar"


def test_word_containment_single_match_answers_directly():
    m = L.resolve("who is synthgamma", IDX_TITLE)
    assert m.how == "direct" and m.entities[0].name == "DR.K.SYNTHGAMMA"


def test_ambiguous_name_gives_at_most_five_choices():
    m = L.resolve("tell me about North Synthnagar", IDX_TITLE)
    assert m.how == "choices"
    assert {e.name for e in m.entities} == {"North East Synthnagar", "North West Synthnagar(SC)"}
    r = L.choices_reply(m)
    assert r["reply"].startswith("Did you mean")
    assert len(r["suggestions"]) <= L.MAX_CHOICES
    many = L.build_index([f"Synth Person{i} Testyadav" for i in range(9)], [])
    m2 = L.resolve("testyadav", many)
    assert m2.how == "choices" and len(m2.entities) == L.MAX_CHOICES and m2.more == 4


def test_fuzzy_only_match_is_offered_never_answered():
    m = L.resolve("tell me about synthpr", IDX_TITLE)  # misspelt: fuzzy only
    assert m.how == "choices" and len(m.entities) == 1
    r = L.choices_reply(m)
    assert r["suggestions"][0]["message"] == "Tell me about Synthpur"


def test_name_containing_and_splits_correctly_for_compare():
    a, rest = L._split_compare("Compare Synthgarh and Testdiu with East Synthnagar", IDX_TITLE)
    assert a.name == "Synthgarh and Testdiu" and rest == "East Synthnagar"


def test_no_match_is_honest_and_examples_come_from_the_live_list():
    m = L.resolve("tell me about Nowhereville", IDX_TITLE)
    assert m.how == "none" and m.candidate == "nowhereville"
    r = L.no_match_reply(m.candidate, IDX_TITLE)
    assert "couldn't find" in r["reply"] and "nowhereville" in r["reply"]
    names = {e.name for e in IDX_TITLE}
    assert r["suggestions"] and all(s["label"] in names for s in r["suggestions"])


def test_who_is_my_mp_asks_for_the_constituency_with_live_examples(fake_db):
    r = ask("who is my MP?")
    assert "Tell me your Lok Sabha constituency" in r["reply"]
    names = {e.name for e in IDX_TITLE if e.kind == "constituency"}
    assert r["suggestions"] and all(s["label"] in names for s in r["suggestions"])


# ---- falling through to the existing copilot -----------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "help",
        "hello",
        "What is this platform?",
        "How do I compare two MPs?",
        "what signals",
        "how is the risk score calculated",
        "tell me about the risk score",
        "tell me about MPLADS",
    ]
    + ADVERSARIAL_PROMPTS
    + ADVERSARIAL,
)
def test_help_and_claim_questions_are_not_turned_into_lookups(fake_db, msg):
    assert ask(msg) is None


def test_claim_question_naming_a_constituency_still_gets_the_decline(fake_db):
    assert ask("is the MP of Synthpur corrupt?") is None  # chat.py then gives the fraud decline


# ---- reply content -------------------------------------------------------------------------------


def test_constituency_reply_content(fake_db):
    r = ask("show works in Synthpur")
    text_ = r["reply"]
    expected = (
        "Synthpur (Synthstate One)", "Lok Sabha constituency", f"MP: {SYNTH_MPS[0]}", "Works on record: 40",
        "CRITICAL 2", "HIGH 8", "MODERATE 5", "LOW 15", "Financial exposure", "₹17,00,000", L.PRIORITY_NOTE,
    )
    for part in expected:
        assert part in text_, part
    assert len(re.findall(r"^\d+\. ", text_, re.M)) == L.TOP_WORKS
    assert [s["label"] for s in r["suggestions"]] == [
        "Show more works", "Explain the top work", "Compare with another constituency"]
    assert r["suggestions"][1]["message"] == "Explain work 900000"
    # the existing work-key guard in chat.py takes this message
    assert chat_api.find_work_key_query(r["suggestions"][1]["message"]) == "900000"


def test_show_more_lists_works_six_to_ten(fake_db):
    r = ask("Show more works for Synthpur")
    nums = [int(n) for n in re.findall(r"^(\d+)\. ", r["reply"], re.M)]
    assert nums == [6, 7, 8, 9, 10]


def test_rajya_sabha_member_has_no_constituency_and_gets_the_state_view(fake_db):
    fake_db.state["house"] = "RS"
    r = ask("tell me about Synth Beta Devi")
    assert "Rajya Sabha member for Synthstate One" in r["reply"]
    assert "not a constituency" in r["reply"]
    assert "Lok Sabha MP for" not in r["reply"]
    state_link = {
        "label": "All works in Synthstate One (Priority Queue)",
        "href": "/queue?state=Synthstate%20One",
    }
    assert state_link in r["links"]


def test_links_use_only_existing_routes_and_params(fake_db):
    fake_db.state["house"] = "RS"
    allowed = re.compile(r"^/(mp-performance\?(mp|constituency)|queue\?(mp|state))=[^&?#/ ]+$")
    msgs = (
        "tell me about Synthpur",
        "tell me about Synth Beta Devi",
        "Compare Synthpur with East Synthnagar",
    )
    for msg in msgs:
        for link in ask(msg)["links"]:
            assert allowed.match(link["href"]), link["href"]


def test_anonymous_reply_masks_beneficiary_names(fake_db):
    r = ask("tell me about Synthpur", ANON)
    assert SYNTH_PERSON not in r["reply"] and SYNTH_FATHER not in r["reply"]
    assert "[name removed]" in r["reply"]


def test_logged_in_reply_follows_the_existing_rule(fake_db):
    r = ask("tell me about Synthpur", LOGGED_IN)
    assert SYNTH_PERSON in r["reply"]  # public_view leaves a logged-in caller's text unchanged


def test_replies_make_no_claim_and_use_no_fraud_or_internal_words(fake_db):
    fake_db.state["house"] = "RS"
    replies = [
        ask("tell me about Synthpur"),
        ask("Show more works for Synthpur"),
        ask("tell me about Synth Beta Devi"),
        ask("tell me about North Synthnagar"), ask("tell me about Nowhereville"), ask("who is my MP"),
        ask("Compare Synthpur with East Synthnagar"), ask("Compare Synthpur with Synth Delta Testyadav"),
        ask("Compare Synthpur with another constituency"),
    ]
    banned = re.compile(r"\b(fraud\w*|corrupt\w*|guilt\w*|run|config|configuration|version|gate)\b", re.I)
    for r in replies:
        assert r is not None
        for t in _all_text(r):
            assert not violations(t), (t, violations(t))
            assert not banned.search(t), t
            assert "synthetic-config" not in t


# ---- compare and follow-up context ---------------------------------------------------------------


def test_compare_two_constituencies(fake_db):
    r = ask("Compare Synthpur with East Synthnagar")
    assert fake_db.calls["comparison"] == [(("Synthpur", "East Synthnagar"), "Constituency")]
    assert r["reply"].startswith("Side by side:") and L.PRIORITY_NOTE in r["reply"]


def test_compare_refuses_a_constituency_with_an_mp(fake_db):
    assert ask("Compare Synthpur with Synth Delta Testyadav")["reply"] == L.MIXED_COMPARE
    assert fake_db.calls["comparison"] == []


def test_compare_with_another_returns_context_then_uses_it(fake_db):
    r = ask("Compare Synthpur with another constituency")
    assert r["context"] == {"compare_with": {"kind": "constituency", "name": "Synthpur"}}
    ctx = chat_api.ChatContext(**r["context"])
    r2 = ask("East Synthnagar", context=ctx)
    assert r2["reply"].startswith("Side by side:")
    assert fake_db.calls["comparison"][-1] == (("Synthpur", "East Synthnagar"), "Constituency")


def test_context_with_an_unknown_name_is_ignored(fake_db):
    ctx = chat_api.ChatContext(compare_with={"kind": "constituency", "name": "Not A Stored Synthname"})
    r = ask("East Synthnagar", context=ctx)
    assert fake_db.calls["comparison"] == []
    assert "East Synthnagar" in r["reply"] and not r["reply"].startswith("Side by side")


def test_context_model_caps_size_and_ignores_unknown_keys():
    ctx = chat_api.ChatContext(compare_with={"kind": "mp", "name": "Synth X", "extra": 1}, unknown={"a": 1})
    assert ctx.model_dump() == {"compare_with": {"kind": "mp", "name": "Synth X"}}
    with pytest.raises(ValidationError):
        chat_api.ChatContext(compare_with={"kind": "mp", "name": "x" * 201})
    with pytest.raises(ValidationError):
        chat_api.ChatContext(compare_with={"kind": "state", "name": "Synthstate One"})


# ---- with the database (CI) ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_names(db_session):
    from app.serving import service

    s = service.served(db_session)
    cons = db_session.execute(
        text("SELECT constituency FROM served_work WHERE run_id = :r AND constituency IS NOT NULL "
             "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"),
        {"r": s.run_id},
    ).scalar_one()
    rs_mp = db_session.execute(
        text("SELECT mp FROM served_work WHERE run_id = :r AND house = 'RS' AND mp IS NOT NULL "
             "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"),
        {"r": s.run_id},
    ).scalar_one()
    return {"cons": cons, "rs_mp": rs_mp}


def test_endpoint_answers_a_live_constituency(client, live_names):
    msg = f"Tell me about {live_names['cons']}"
    body = client.post("/api/chat", json={"message": msg, "history": []}).json()
    assert body["source"] == "lookup"
    assert {"reply", "source", "links", "suggestions"} <= body.keys()
    assert live_names["cons"] in body["reply"]
    for t in _all_text(body):
        assert not violations(t), t


def test_endpoint_rajya_sabha_member_gets_the_state_view(client, live_names):
    msg = f"Tell me about {live_names['rs_mp']}"
    body = client.post("/api/chat", json={"message": msg, "history": []}).json()
    assert body["source"] == "lookup"
    assert "Rajya Sabha" in body["reply"] and "not a constituency" in body["reply"]
    assert any(link["href"].startswith("/queue?state=") for link in body["links"])


def test_endpoint_hello_still_has_the_contract_shape(client):
    body = client.post("/api/chat", json={"message": "hello", "history": []}).json()
    assert {"reply", "source"} <= body.keys()
    assert body["source"] != "lookup"
