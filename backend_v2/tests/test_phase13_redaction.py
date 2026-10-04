"""
Phone-number masking in public output (owner decision before Phase 14;
app/serving/redact.py).

Checks, against the real served run:
  * the masking rules: every phone form is masked; amounts, IDs, pincodes,
    years, dates, school codes, letter numbers, Aadhaar-shaped numbers,
    long codes and decimals are left alone;
  * no phone survives in either read model (description AND search text);
  * the raw stored text (work.raw_description) is unchanged;
  * every endpoint that emits a description, the audit-sample list, the
    copilot's model context and the case export/audit trail are masked;
  * searching by a phone's digits finds nothing.

Test phone numbers are synthetic (built here), never real ones.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from app.serving.redact import (
    ID_MASK,
    PHONE_MASK,
    mask_personal,
    mask_personal_counted,
    mask_phones,
    mask_phones_counted,
)

M = PHONE_MASK
IDM = ID_MASK
# synthetic mobile / landline digits, assembled so no literal phone sits in this file
MOB = "98" + "76543" + "210"
MOB2 = "7" + "012345678"
LAND = "0522" + "-" + "2345678"
AAD = "2345" + "6789" + "0123"  # Aadhaar-shaped, synthetic


# ---- rules ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, masked",
    [
        (f"contact no {MOB}", f"contact no {M}"),
        (f"Mobile-{MOB}.", f"Mobile-{M}."),
        (f"mob.{MOB}", f"mob.{M}"),
        (f"(+91 {MOB})", f"({M})"),
        (f"+91-{MOB}", M),
        (f"91-{MOB}", M),
        (f"0{MOB}", M),
        (f"{MOB[:5]} {MOB[5:]}", M),
        (f"{MOB[:5]}-{MOB[5:]}", M),
        (f"{MOB[:3]} {MOB[3:6]} {MOB[6:]}", M),
        (f"{MOB} {MOB2}", f"{M} {M}"),
        (f"{MOB}{MOB2}", f"{M} {M}"),  # two mobiles glued together
        (f"ph {LAND}", f"ph {M}"),
        (LAND, M),
        (f"mobile number {MOB}1", f"mobile number {M}"),  # 11-digit typo after a phone word
        (f"pradhan {MOB}1", f"pradhan {M}"),  # 11-digit run starting 6-9
        (f"contact - Sh. Ram Lal ji 0{'5' + MOB[1:]}", f"contact - Sh. Ram Lal ji {M}"),
        (f"sampark sutra {MOB}/{MOB2}", f"sampark sutra {M}/{M}"),
        # Phase 13.y: every +91 / 91 prefix form, separated mobiles, STD landlines
        (f"+91 {MOB}", M),
        (f"+91 {MOB[:5]} {MOB[5:]}", M),
        (f"+91-{MOB[:5]}-{MOB[5:]}", M),
        (f"(+91) {MOB}", M),
        (f"(+91){MOB}", M),
        (f"0091 {MOB}", M),
        (f"91 {MOB}", M),
        (f"91{MOB}", M),  # 91 + mobile, no separator (12 digits)
        (f"91-{MOB[:5]} {MOB[5:]}", M),
        (f"0-{MOB}", M),
        (f"{MOB[:4]} {MOB[4:7]} {MOB[7:]}", M),  # 4-3-3
        (f"{MOB[:4]}-{MOB[4:7]}-{MOB[7:]}", M),
        (f"{MOB[:3]}-{MOB[3:6]}-{MOB[6:]}", M),  # 3-3-4 with hyphens
        (f"Contact number {MOB[:4]} {MOB[4:7]} {MOB[7:]}", f"Contact number {M}"),
        ("0522 2345678", M),
        ("(0522) 2345678", M),
        ("(0522)2345678", M),
        ("(0522)-2345678", M),
        ("011-23456789", M),  # 3-digit STD code, 8-digit number
        ("+91-522-2345678", M),  # the international form drops the trunk 0
        ("+91 522 2345678", M),
        ("(0522)2345678, 2345679", f"{M}, {M}"),  # a second number of the same exchange
        (f"{LAND}, 226001", f"{M}, 226001"),  # ...but never a pincode after it
        (f"Contact-Sh. Ram ji\n0{'5' + MOB[1:]}", f"Contact-Sh. Ram ji\n{M}"),  # name, then a line break
    ],
)
def test_phone_forms_are_masked(raw, masked):
    assert mask_phones(raw) == masked
    assert mask_personal(raw) == masked


@pytest.mark.parametrize(
    "raw, masked",
    [
        (f"aadhar no {AAD}", f"aadhar no {IDM}"),
        (f"(Aadhar No. {AAD[:4]} {AAD[4:8]} {AAD[8:]}).", f"(Aadhar No. {IDM})."),
        (f"uid {AAD[:4]}-{AAD[4:8]}-{AAD[8:]}", f"uid {IDM}"),
        (f"par Ram Lal {AAD}", f"par Ram Lal {IDM}"),  # no keyword needed
        (f"contact no {MOB}. aadhar no {AAD}", f"contact no {M}. aadhar no {IDM}"),
    ],
)
def test_aadhaar_shaped_numbers_are_masked(raw, masked):
    assert mask_personal(raw) == masked
    assert mask_phones(raw) == raw.replace(MOB, M)  # the phone rules alone never touch them


@pytest.mark.parametrize(
    "keep",
    [
        "property no 1234 5678 9012",  # starts with 1: never an Aadhaar number
        "Property No.1234 5678 9012 3456",  # 16-digit grouped property number
        "land record 2345 6789 0123 4567",  # 16 digits even though it starts 2-9
        "account 012345678901",  # starts with 0
        f"code {AAD[:4]} {AAD[4:8]}-{AAD[8:]}",  # mixed separators: not a 4-4-4 group
        "asset 2345678901234",  # 13 digits
        "letter no 99-1234-5678 dated 1-6-2024",  # 2-4-4 letter number
        "letter no 95 1234 5678 dated 1-6-2024",
        "ARPAN ID - RJ 2345 6789012",
        "Sarve no 12 34 567 890 123",
        "D.No 12345 67890 upto Bore Point",
        "road from KM 12-345 - 13-456",
    ],
)
def test_near_misses_of_the_new_rules_are_left_alone(keep):
    assert mask_personal(keep) == keep
    assert mask_personal_counted(keep)[1:] == (0, 0)


@pytest.mark.parametrize(
    "keep",
    [
        "Rs. 1500000 for 15 benches",  # amount
        "Rs 10,00,000 sanctioned",  # Indian commas
        "cost 7500000.00",  # 7-digit amount with decimals
        "work id 251224, sanction no 102938",  # work / sanction IDs
        "Village Anandpur, pin-226001",  # 6-digit pincode
        "pin code 226 001",
        "FY 2024-25, dated 12-08-2024",  # years and dates
        "on 2025/01/31 and 31.01.2025",
        "school dise code 20171234567",  # 11-digit UDISE code (state code 01-38)
        "letter no 123-45678-9012 dated 1-06-2024",  # letter number starting 1
        "asset code 123456789012345678",  # 18-digit code
        "unicode 1234567890",  # 10 digits starting 1-5
        "payment_share 0.7123456789",  # decimal fraction
        "km 3/400-4/200 road",
        "ward no 12, 13, 14",
    ],
)
def test_non_phones_are_left_alone(keep):
    assert mask_phones(keep) == keep
    assert mask_phones_counted(keep)[1] == 0
    assert mask_personal(keep) == keep


def test_counting_and_empty_values():
    assert mask_phones_counted(f"a {MOB} b {MOB2}") == (f"a {M} b {M}", 2)
    assert mask_phones(None) is None and mask_phones("") == ""
    assert mask_personal_counted(f"a {MOB} b {AAD} c (0522)2345678, 2345679") == (
        f"a {M} b {IDM} c {M}, {M}",
        3,
        1,
    )
    assert mask_personal(None) is None and mask_personal("") == ""


# ---- read models and raw text -------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_id(db_session):
    from app.serving import service

    s = service.served(db_session)
    assert s.run_id is not None
    return s.run_id


@pytest.fixture(scope="module")
def phone_works(db_session, run_id):
    """Scored, located works whose RAW description holds a phone number."""
    rows = db_session.execute(
        text(
            "SELECT w.work_key, w.raw_description, sw.mp, sw.house, mw.latitude IS NOT NULL AS on_map "
            "FROM work w "
            "JOIN served_work sw ON sw.work_key = w.work_key AND sw.run_id = :r "
            "JOIN map_work mw ON mw.work_key = w.work_key AND mw.run_id = :r "
            "WHERE sw.scored AND w.raw_description ~ '(^|[^0-9])[6-9][0-9]{9}([^0-9]|$)' "
            "ORDER BY mw.latitude IS NULL, w.work_key LIMIT 400"
        ),
        {"r": run_id},
    ).all()
    works = [r for r in rows if mask_phones(r.raw_description) != r.raw_description]
    assert len(works) >= 20
    return works


def test_no_phone_survives_in_either_read_model(db_session, run_id):
    for table in ("served_work", "map_work"):
        rows = db_session.execute(
            text(f"SELECT work_key, description, search_text FROM {table} WHERE run_id = :r"), {"r": run_id}
        ).all()
        assert rows
        leaks = [
            r.work_key for r in rows for t in (r.description, r.search_text) if t and mask_personal(t) != t
        ]
        assert not leaks, (table, leaks[:10])


def test_raw_stored_text_is_unchanged(db_session):
    """Masking never writes back: the raw descriptions still hold their numbers
    (1,897 of them carry at least one phone), and the build code never
    updates work or raw_row."""
    raw = db_session.execute(
        text("SELECT raw_description FROM work WHERE raw_description ~ '[0-9]{3}'")
    ).scalars()
    counted = [mask_personal_counted(d) for d in raw]
    assert sum(1 for c in counted if c[1]) >= 1897  # descriptions with a phone
    assert sum(c[2] for c in counted) >= 4  # Aadhaar-shaped numbers
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    for p in [app / "serving" / "build.py", app / "geo" / "build.py", app / "serving" / "redact.py"]:
        src = p.read_text("utf-8")
        assert not re.search(r"UPDATE\s+(work|raw_row|work_state)\b", src, re.I), p


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def _assert_masked(resp, where):
    assert resp.status_code == 200, (where, resp.status_code)
    leaks = [s for s in _strings(resp.json()) if mask_personal(s) != s]
    assert not leaks, (where, [re.sub(r"[0-9]", "#", s)[:80] for s in leaks[:3]])
    return resp.json()


def test_every_description_endpoint_is_masked(client, phone_works, db_session, run_id, make_user):
    ministry = make_user("ministry")
    for w in phone_works[:6]:
        wk = w.work_key
        body = _assert_masked(client.get(f"/api/record/{wk}"), f"record {wk}")
        assert M in body["source_record"]["description"]
        _assert_masked(client.get(f"/api/risk/{wk}"), "risk")
        _assert_masked(client.get(f"/api/evidence/{wk}"), "evidence")
        q = _assert_masked(client.get("/api/queue", params={"search": wk.lower(), "page_size": 50}), "queue")
        assert any(r["record_id"] == wk and M in r["description"] for r in q["records"])
        mw = _assert_masked(
            client.get("/api/map-works", params={"search": wk.lower(), "limit": 50}), "map-works"
        )
        if w.on_map:  # only works with a marker; descriptions are cut at 120 chars (leak check above)
            assert any(r["record_id"] == wk for r in mw)
        _assert_masked(client.get(f"/api/mp-performance/{w.mp}"), "mp-performance")
        _assert_masked(client.get(f"/api/record/{wk}", headers=ministry), "record (authenticated)")
    area = db_session.execute(
        text(
            "SELECT ga.key FROM map_work mw JOIN geo_area ga ON ga.id = mw.constituency_area_id "
            "WHERE mw.run_id = :r AND mw.work_key = :wk"
        ),
        {"r": run_id, "wk": phone_works[0].work_key},
    ).scalar()
    if area:
        _assert_masked(client.get(f"/api/geo/areas/{area}/works", params={"page_size": 200}), "area works")
    cons = db_session.execute(
        text("SELECT constituency_state, constituency FROM served_work WHERE run_id = :r AND work_key = :wk"),
        {"r": run_id, "wk": phone_works[0].work_key},
    ).one()
    if cons.constituency:
        _assert_masked(
            client.get("/api/constituency-intelligence", params={"state": cons[0], "constituency": cons[1]}),
            "constituency-intelligence",
        )
    for path in (
        "/api/summary",
        "/api/risk/top?limit=500",
        "/api/investigations",
        "/api/queue?page_size=200",
    ):
        _assert_masked(client.get(path), path)


def test_searching_by_phone_digits_finds_nothing(client, phone_works):
    raw = phone_works[0].raw_description
    digits = re.search(r"(?<![0-9])[6-9][0-9]{9}(?![0-9])", raw).group(0)
    assert client.get("/api/queue", params={"search": digits}).json()["total"] == 0
    assert client.get("/api/map-works", params={"search": digits}).json() == []
    assert client.get("/api/queue", params={"search": digits[:7]}).json()["total"] == 0


def test_audit_sample_items_are_masked(client, make_user, db_session, run_id, phone_works):
    from app.models.security import AuditSample, AuditSampleItem

    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM audit_sample")).scalar_one()
    db_session.commit()
    try:
        s = AuditSample(
            run_id=run_id, config_name="v4-candidate", seed=1, design={"test": True}, created_by="t"
        )
        db_session.add(s)
        db_session.flush()
        for i, w in enumerate(phone_works[:5]):
            db_session.add(
                AuditSampleItem(
                    sample_id=s.id,
                    work_key=w.work_key,
                    stratum="LOW",
                    blind_code=f"t13r{i}{s.id}",
                    position=i + 1,
                )
            )
        db_session.commit()
        body = _assert_masked(
            client.get(f"/api/audit/samples/{s.id}/items", headers=make_user("auditor")), "audit"
        )
        assert all(M in it["description"] for it in body["items"])
    finally:
        db_session.rollback()
        db_session.execute(text("DELETE FROM audit_sample WHERE id > :b"), {"b": before})
        db_session.commit()


def test_copilot_context_sent_to_the_model_is_masked(client, monkeypatch):
    captured = {}

    def fake_gemini(message, history, system_prompt):
        captured.update(message=message, history=history, system_prompt=system_prompt)
        return "ok", True

    monkeypatch.setattr("app.api.chat._call_gemini", fake_gemini)
    r = client.post(
        "/api/chat",
        json={
            "message": f"call the pradhan on {MOB} about the road, aadhar {AAD}",
            "history": [
                {"role": "user", "content": f"his landline is {LAND}"},
                {"role": "assistant", "content": f"noted {MOB2}"},
            ],
        },
    )
    assert r.status_code == 200
    blob = repr(captured)
    for number in (MOB, MOB2, LAND, LAND.replace("-", ""), AAD):
        assert number not in blob
    assert M in captured["message"] and IDM in captured["message"]
    assert all(M in h["content"] for h in captured["history"])
    assert mask_phones(captured["system_prompt"]) == captured["system_prompt"]


def test_case_export_and_audit_trail_are_masked(client, make_user, db_session, run_id, phone_works):
    h = make_user("investigator")
    wk = phone_works[0].work_key
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM case_event")).scalar_one()
    db_session.commit()
    try:
        r = client.post(
            f"/api/investigate/{wk}",
            json={"decision": "InReview", "note": f"spoke to {MOB}, aadhar {AAD}"},
            headers=h,
        )
        assert r.status_code == 200 and M in r.json()["entry"]["note"]
        export = _assert_masked(client.get(f"/api/cases/{wk}/events", headers=h), "case export")
        assert any(M in (e["note"] or "") for e in export["events"])
        _assert_masked(client.get("/api/audit-trail", headers=h), "audit trail")
        # the stored note is the record as written (and the hash chain covers it)
        stored = db_session.execute(
            text("SELECT note FROM case_event WHERE id = :i"), {"i": r.json()["entry"]["event_id"]}
        ).scalar_one()
        assert MOB in stored and AAD in stored and export["chain"]["valid"]
    finally:
        db_session.rollback()
        db_session.execute(text("DELETE FROM case_event WHERE id > :b"), {"b": before})
        db_session.commit()


def test_aadhaar_works_are_masked_on_the_record_page(client, db_session, run_id):
    rows = db_session.execute(
        text(
            "SELECT sw.work_key, w.raw_description FROM served_work sw "
            "JOIN work w ON w.work_key = sw.work_key "
            "WHERE sw.run_id = :r AND w.raw_description ~ '[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}'"
        ),
        {"r": run_id},
    ).all()
    hits = [r for r in rows if mask_personal_counted(r.raw_description)[2]]
    assert len(hits) >= 4
    for r in hits:
        body = _assert_masked(client.get(f"/api/record/{r.work_key}"), f"record {r.work_key}")
        assert IDM in body["source_record"]["description"]
