"""
served_work.description_normalized is an internal matching key, never output
(Phase 13.y, step 2c).

It is the lower-cased, whitespace-collapsed RAW description (app/ingest/
normalize.py), so it still holds every phone and Aadhaar number, and it is
deliberately not masked: the serving layer groups works by it (duplicate
counts and related works, app/serving/service.py), and masking would merge
works whose descriptions differ only by a number. So it must never leave the
system. This file proves that for:
  * every GET endpoint of the API (all routes, walked from the app, with real
    path values, as a national admin) and the OpenAPI schema;
  * the search index (served_work/map_work.search_text) and search results;
  * the case export and the audit-sample report and items;
  * application logs written while serving all of the above;
  * the payload sent to Gemini;
  * the source: no API/serialization code names the column outside SQL.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.main import app
from app.serving.redact import mask_personal

APP = Path(__file__).resolve().parents[1] / "app"
COLUMN = "description_normalized"


@pytest.fixture(scope="module")
def run_id(db_session):
    from app.serving import service

    s = service.served(db_session)
    assert s.run_id is not None
    return s.run_id


@pytest.fixture(scope="module")
def secret_works(db_session, run_id):
    """Works whose normalized text differs from what is served: it holds a phone
    or an Aadhaar-shaped number that the output masks. Each carries the raw
    digits that must never appear in any output."""
    rows = db_session.execute(
        text(
            "SELECT sw.work_key, sw.mp, sw.description_normalized AS dn, w.district_authority_id, "
            "mw.constituency_area_id FROM served_work sw JOIN work w ON w.work_key = sw.work_key "
            "LEFT JOIN map_work mw ON mw.run_id = sw.run_id AND mw.work_key = sw.work_key "
            "WHERE sw.run_id = :r AND sw.description_normalized ~ '(^|[^0-9])[6-9][0-9]{9}([^0-9]|$)' "
            "ORDER BY mw.latitude IS NULL, sw.work_key LIMIT 200"
        ),
        {"r": run_id},
    ).all()
    out = []
    for r in rows:
        masked = mask_personal(r.dn)
        if masked == r.dn:
            continue
        digits = re.findall(r"(?<![0-9])[6-9][0-9]{9}(?![0-9])", r.dn)
        out.append((r, digits))
        if len(out) == 5:
            break
    assert len(out) == 5
    return out


def _assert_absent(body: str, dn: str, digits: list[str], where: str):
    assert COLUMN not in body, f"{where}: names {COLUMN}"
    for d in digits:
        assert d not in body, f"{where}: carries a normalized-only number"
    assert dn not in body.lower(), f"{where}: carries the normalized text"


def _route_values(work, db_session, run_id) -> dict:
    r = work
    area = None
    if r.constituency_area_id:
        area = db_session.execute(
            text("SELECT key FROM geo_area WHERE id = :i"), {"i": r.constituency_area_id}
        ).scalar()
    return {
        "project_id": r.work_key,
        "record_id": r.work_key,
        "mp_name": r.mp,
        "area_key": area,
    }


def test_no_get_endpoint_emits_it(client, make_user, db_session, run_id, secret_works, caplog):
    """Every GET route, with this work's real path values, as a national admin;
    SQL logging switched on too, to prove bound values are hidden (the SQL text
    itself may name the column; its values never appear)."""
    admin = make_user("admin")
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    routes = [
        rt
        for rt in app.routes
        if "GET" in (getattr(rt, "methods", None) or ()) and rt.path.startswith("/api/")
    ]
    assert len(routes) >= 30
    called = set()
    for work, digits in secret_works:
        values = _route_values(work, db_session, run_id)
        values["entity_type"] = "district_authority"
        values["entity_id"] = work.district_authority_id
        for rt in routes:
            params = re.findall(r"{(\w+)}", rt.path)
            if any(values.get(p) is None for p in params):
                continue  # e.g. {sample_id}, {constituency_name}: covered by other tests
            path = rt.path
            for p in params:
                path = path.replace("{" + p + "}", str(values[p]))
            resp = client.get(path, headers=admin)
            assert resp.status_code < 500, (rt.path, resp.status_code)
            called.add(rt.path)
            _assert_absent(resp.text, work.dn, digits, rt.path)
        for path, params in (
            ("/api/queue", {"search": work.work_key.lower(), "page_size": 50}),
            ("/api/map-works", {"search": work.work_key.lower()}),
            ("/api/risk/top", {"limit": 500}),
        ):
            _assert_absent(client.get(path, params=params, headers=admin).text, work.dn, digits, path)
    assert len(called) >= 30, sorted(called)
    assert any(r.name.startswith("sqlalchemy.engine") for r in caplog.records), "SQL logging was not captured"
    for rec in caplog.records:
        msg = rec.getMessage()
        for work, digits in secret_works:
            assert not any(
                d in msg for d in digits
            ), f"log record {rec.name} carries a normalized-only number"
            assert work.dn not in msg.lower(), f"log record {rec.name} carries the normalized text"


def test_engine_hides_bound_values():
    from app.db.session import get_engine

    assert get_engine().hide_parameters is True


def test_openapi_schema_does_not_expose_it():
    assert COLUMN not in json.dumps(app.openapi())


def test_search_index_and_search_results_do_not_hold_it(client, db_session, run_id, secret_works):
    for work, digits in secret_works:
        for table in ("served_work", "map_work"):
            st = db_session.execute(
                text(f"SELECT search_text FROM {table} WHERE run_id = :r AND work_key = :wk"),
                {"r": run_id, "wk": work.work_key},
            ).scalar()
            if st is None:
                continue
            assert not any(d in st for d in digits), table
            assert work.dn not in st, table
        for d in digits:
            assert client.get("/api/queue", params={"search": d}).json()["total"] == 0
            assert client.get("/api/map-works", params={"search": d}).json() == []


def test_case_export_and_audit_sample_do_not_emit_it(client, make_user, db_session, run_id, secret_works):
    from app.models.security import AuditSample, AuditSampleItem

    inv = make_user("investigator")
    before_ev = db_session.execute(text("SELECT coalesce(max(id), 0) FROM case_event")).scalar_one()
    before_s = db_session.execute(text("SELECT coalesce(max(id), 0) FROM audit_sample")).scalar_one()
    db_session.commit()
    try:
        work, digits = secret_works[0]
        assert (
            client.post(
                f"/api/investigate/{work.work_key}", json={"decision": "InReview"}, headers=inv
            ).status_code
            == 200
        )
        for path in (f"/api/cases/{work.work_key}/events", "/api/audit-trail"):
            _assert_absent(client.get(path, headers=inv).text, work.dn, digits, path)
        s = AuditSample(
            run_id=run_id, config_name="v4-candidate", seed=2, design={"test": True}, created_by="t"
        )
        db_session.add(s)
        db_session.flush()
        for i, (w, _) in enumerate(secret_works):
            db_session.add(
                AuditSampleItem(
                    sample_id=s.id,
                    work_key=w.work_key,
                    stratum="LOW",
                    blind_code=f"t13n{i}{s.id}",
                    position=i + 1,
                )
            )
        db_session.commit()
        items = client.get(f"/api/audit/samples/{s.id}/items", headers=make_user("auditor")).text
        report = client.get(f"/api/audit/samples/{s.id}/report", headers=make_user("supervisor")).text
        for w, d in secret_works:
            _assert_absent(items, w.dn, d, "audit items")
            _assert_absent(report, w.dn, d, "audit report")
    finally:
        db_session.rollback()
        db_session.execute(text("DELETE FROM case_event WHERE id > :b"), {"b": before_ev})
        db_session.execute(text("DELETE FROM audit_sample WHERE id > :b"), {"b": before_s})
        db_session.commit()


def test_gemini_payload_does_not_hold_it(client, monkeypatch, secret_works):
    sent = []

    def fake_gemini(message, history, system_prompt):
        sent.append(json.dumps({"m": message, "h": history, "s": system_prompt}))
        return "ok", True

    monkeypatch.setattr("app.api.chat._call_gemini", fake_gemini)
    work, digits = secret_works[0]
    # a general question (goes to the model) and a work question (answered from stored results)
    for msg in ("how are works prioritised for review?", f"explain work {work.work_key}"):
        r = client.post("/api/chat", json={"message": msg, "history": []})
        assert r.status_code == 200
        _assert_absent(r.text, work.dn, digits, "chat reply")
    assert sent, "the general question must reach the (patched) model"
    for payload in sent:
        _assert_absent(payload, work.dn, digits, "Gemini payload")


def test_only_sql_names_it_outside_ingest_and_analytics():
    """The column is read by SQL for matching (WHERE / GROUP BY / SELECT into a
    row) and by the build. No dict key, keyword argument or model field outside
    ingest/analytics/models names it, so nothing can serialize it."""
    offenders = []
    for p in APP.rglob("*.py"):
        rel = p.relative_to(APP).as_posix()
        if rel.startswith(("ingest/", "analytics/", "models/")):
            continue
        for n, line in enumerate(p.read_text("utf-8").splitlines(), 1):
            if COLUMN in line and re.search(
                r"""['"]description_normalized['"]\s*:|\bdescription_normalized=(?!=)|^\s*description_normalized\s*:\s*\w""",
                line,
            ):
                offenders.append(f"{rel}:{n}")
    assert not offenders, offenders
