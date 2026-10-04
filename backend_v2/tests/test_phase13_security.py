"""
Phase 13 security checklist: BLUEPRINT.md §11 "Controls", one section per
row, each checked against the running application (TestClient over the
real app and the real database) or, where a row is about the codebase
itself, by scanning it.

Rows: Authentication, Authorisation, Identity in audit, Audit trail,
Imports, CORS, Rate limits, Input handling, Secrets, Backups, Copilot,
Personal data. docs/security.md maps each row to its implementation.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import passwords, tokens
from app.auth.deps import get_principal
from app.auth.ratelimit import LIMITER
from app.core.config import get_settings
from app.main import app

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend_v2"
PUBLIC_ROUTES = {"/api/health", "/api/chat/status", "/api/auth/login"}


@pytest.fixture(scope="module")
def served_run(db_session):
    from app.serving import service

    s = service.served(db_session)
    assert s.run_id is not None, "no serving build: run scripts/run_serving.py first"
    return s.run_id


@pytest.fixture
def no_case_residue(db_session):
    before = db_session.execute(text("SELECT coalesce(max(id), 0) FROM case_event")).scalar_one()
    db_session.commit()
    yield
    db_session.rollback()
    db_session.execute(text("DELETE FROM case_event WHERE id > :b"), {"b": before})
    db_session.commit()


def _one(db, sql, **p):
    return db.execute(text(sql), p).scalar_one()


# =============================================================================================
# 1. Authentication: token login (JWT/OIDC-compatible), password hashing, short-lived tokens
# =============================================================================================


def test_auth_login_issues_a_short_lived_signed_jwt(client, make_user, db_session):
    h = make_user("ministry")
    token = h["Authorization"].split()[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    for claim in ("iss", "aud", "sub", "iat", "exp", "jti"):  # OIDC-style registered claims
        assert claim in claims, claim
    assert claims["exp"] - claims["iat"] == get_settings().access_token_minutes * 60 == 15 * 60
    me = client.get("/api/auth/me", headers=h).json()
    assert me["authenticated"] and me["role"] == "ministry" and me["scope"]["national"]
    # stored only as a salted scrypt hash
    stored = _one(db_session, "SELECT password_hash FROM app_user WHERE username = :u", u=h["X-Test-User"])
    assert stored.startswith("scrypt$") and "phase13-test" not in stored


def test_auth_wrong_or_unknown_credentials_get_the_same_401(client, make_user):
    h = make_user("ministry")
    bad = client.post("/api/auth/login", json={"username": h["X-Test-User"], "password": "wrong-password-x"})
    unknown = client.post(
        "/api/auth/login", json={"username": "no-such-user-x", "password": "wrong-password-x"}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()  # no user enumeration


def test_auth_rejects_tampered_expired_unsigned_and_disabled_tokens(client, make_user, db_session):
    h = make_user("investigator")
    token = h["Authorization"].split()[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    forged_role = jwt.encode({**claims, "role": "admin"}, "not-the-key-" * 4, algorithm="HS256")
    expired, _ = tokens.issue(
        int(claims["sub"]),
        claims["username"],
        "investigator",
        now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
    )
    unsigned = jwt.encode({**claims}, key=None, algorithm="none")
    other_audience = jwt.encode({**claims, "aud": "someone-else"}, tokens.signing_key(), algorithm="HS256")
    for bad in (forged_role, expired, unsigned, other_audience, token[:-3] + "abc", "garbage"):
        r = client.get("/api/audit-trail", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad[:20]
    # a token for a disabled account stops working at once (role/scope re-read per request)
    assert client.get("/api/audit-trail", headers=h).status_code == 200
    db_session.execute(
        text("UPDATE app_user SET is_active = false WHERE username = :u"), {"u": h["X-Test-User"]}
    )
    db_session.commit()
    assert client.get("/api/audit-trail", headers=h).status_code == 401
    # an invalid token is never silently downgraded to the anonymous role
    assert client.get("/api/summary", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_auth_password_hashing_and_signing_key_rules(monkeypatch):
    h = passwords.hash_password("correct horse battery")
    assert passwords.verify_password("correct horse battery", h)
    assert not passwords.verify_password("correct horse batterY", h)
    assert h != passwords.hash_password("correct horse battery")  # per-password salt
    with pytest.raises(ValueError):
        passwords.hash_password("short")
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "jwt_secret", "")
    with pytest.raises(RuntimeError):
        tokens.signing_key()  # no key outside development = refuse
    monkeypatch.setattr(s, "jwt_secret", "too-short")
    with pytest.raises(RuntimeError):
        tokens.signing_key()


def test_startup_refuses_a_missing_database_url_outside_development(monkeypatch):
    """The database_url default carries the local dev password: outside
    development it is never used, and the app refuses to start instead."""
    from app.core.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(_env_file=None).check_configuration()
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(_env_file=None).check_configuration()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app@db.example.invalid/sentinel")
    Settings(_env_file=None).check_configuration()  # supplied: starts
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("APP_ENV", "development")
    Settings(_env_file=None).check_configuration()  # local runs keep the default

    # wiring: the app's startup runs the check before it serves anything
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "jwt_secret", "x" * 48)
    monkeypatch.setattr(s, "database_url", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        with TestClient(app):
            pass


# =============================================================================================
# 2. Authorisation: roles and scopes by state or district enforced IN QUERIES
# =============================================================================================


def _route_dependencies(route: APIRoute) -> set:
    seen, stack = set(), [route.dependant]
    while stack:
        d = stack.pop()
        for sub in d.dependencies:
            seen.add(sub.call)
            stack.append(sub)
    return seen


def test_authz_every_data_route_resolves_the_caller():
    """No /api route can skip the principal: each one (except health,
    chat status and login itself) depends on get_principal."""
    missing = [
        r.path
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path.startswith("/api/")
        and r.path not in PUBLIC_ROUTES
        and get_principal not in _route_dependencies(r)
    ]
    assert not missing, missing


def _state_with_ls_and_other(db, run):
    return db.execute(
        text(
            "SELECT state FROM served_work WHERE run_id = :r AND scored AND house = 'LS' "
            "GROUP BY state ORDER BY count(*) DESC OFFSET 3 LIMIT 1"
        ),
        {"r": run},
    ).scalar_one()


def test_authz_state_scope_limits_every_figure_to_that_state(client, make_user, db_session, served_run):
    state = _state_with_ls_and_other(db_session, served_run)
    h = make_user("state", scope_state=state)
    n_state = _one(
        db_session,
        "SELECT count(*) FROM served_work WHERE run_id = :r AND scored AND state = :s",
        r=served_run,
        s=state,
    )
    summary = client.get("/api/summary", headers=h).json()
    assert summary["total_records"] == n_state
    q = client.get("/api/queue", params={"page_size": 200}, headers=h).json()
    assert q["total"] == n_state and {r["state"] for r in q["records"]} == {state}
    # a filter for another state can only narrow, never widen
    other = client.get("/api/queue", params={"state": "Kerala" if state != "Kerala" else "Bihar"}, headers=h)
    assert other.json()["total"] == 0
    assert client.get("/api/states", headers=h).json()["states"] == [state]
    analytics = client.get("/api/analytics", headers=h).json()
    assert sum(analytics["risk_distribution"].values()) == n_state
    assert set(analytics["state_flags"]) == {state}
    # out-of-scope record reads as absent
    outside = _one(
        db_session,
        "SELECT work_key FROM served_work WHERE run_id = :r AND scored AND state <> :s "
        "ORDER BY work_key LIMIT 1",
        r=served_run,
        s=state,
    )
    inside = _one(
        db_session,
        "SELECT work_key FROM served_work WHERE run_id = :r AND scored AND state = :s "
        "ORDER BY work_key LIMIT 1",
        r=served_run,
        s=state,
    )
    assert client.get(f"/api/record/{outside}", headers=h).status_code == 404
    assert client.get(f"/api/risk/{outside}", headers=h).status_code == 404
    assert client.get(f"/api/record/{inside}", headers=h).status_code == 200
    # map endpoints: live, scoped aggregation (never the national precomputed tables)
    works = client.get("/api/map-works", params={"limit": 3000}, headers=h).json()
    keys = [w["record_id"] for w in works]
    assert (
        keys
        and _one(
            db_session,
            "SELECT count(*) FROM served_work WHERE run_id = :r AND work_key = ANY(:k) AND state <> :s",
            r=served_run,
            k=keys,
            s=state,
        )
        == 0
    )
    md = client.get("/api/map-data", headers=h).json()
    n_md = sum(a["total"] for a in md)
    n_mw = _one(
        db_session,
        "SELECT count(*) FROM map_work mw JOIN served_work sw ON sw.run_id = mw.run_id "
        "AND sw.work_key = mw.work_key WHERE mw.run_id = :r AND sw.state = :s "
        "AND mw.constituency_area_id IS NOT NULL",
        r=served_run,
        s=state,
    )
    assert n_md == n_mw
    filters = client.get("/api/map-filters", headers=h).json()
    assert filters["total_works"] <= n_state + 1 and filters["total_works"] > 0
    # the copilot does not reveal an out-of-scope work
    reply = client.post("/api/chat", json={"message": f"why is work {outside} flagged"}, headers=h).json()
    assert "don't have a stored result" in reply["reply"]
    # national-only aggregates are refused, not silently shown
    assert client.get("/api/graph-data", headers=h).status_code == 403


def test_authz_district_mp_and_house_scopes(client, make_user, db_session, served_run):
    da = _one(
        db_session,
        "SELECT district_authority_id FROM served_work WHERE run_id = :r AND scored "
        "AND district_authority_id IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
        r=served_run,
    )
    h = make_user("district", scope_district_authority_id=da)
    n = _one(
        db_session,
        "SELECT count(*) FROM served_work WHERE run_id = :r AND scored " "AND district_authority_id = :d",
        r=served_run,
        d=da,
    )
    assert client.get("/api/summary", headers=h).json()["total_records"] == n
    mp = _one(
        db_session,
        "SELECT mp FROM served_work WHERE run_id = :r AND scored AND house = 'LS' "
        "GROUP BY mp ORDER BY count(*) DESC LIMIT 1",
        r=served_run,
    )
    hm = make_user("mp", scope_mp=mp)
    assert client.get("/api/mps", headers=hm).json()["mps"] == [mp]
    other_mp = _one(
        db_session,
        "SELECT mp FROM served_work WHERE run_id = :r AND mp <> :m ORDER BY mp LIMIT 1",
        r=served_run,
        m=mp,
    )
    assert client.get(f"/api/mp-performance/{other_mp}", headers=hm).status_code == 404
    assert client.get(f"/api/mp-performance/{mp}", headers=hm).status_code == 200
    hr = make_user("ministry", scope_house="RS")
    s = client.get("/api/summary", headers=hr).json()
    assert s["total_records"] == client.get("/api/summary", params={"house": "RS"}).json()["total_records"]
    assert client.get("/api/summary", params={"house": "LS"}, headers=hr).json()["total_records"] == 0


def test_authz_roles_gate_actions(client, make_user, served_run, db_session):
    wk = _one(
        db_session,
        "SELECT work_key FROM served_work WHERE run_id = :r AND scored ORDER BY work_key " "LIMIT 1",
        r=served_run,
    )
    state = make_user("state", scope_state="Kerala")
    auditor = make_user("auditor")
    # read-only roles cannot write case events; anonymous cannot either
    assert client.post(f"/api/investigate/{wk}", json={"decision": "x"}, headers=state).status_code == 403
    assert client.post(f"/api/investigate/{wk}", json={"decision": "x"}).status_code == 401
    assert client.post(f"/api/risk/recalculate/{wk}").status_code == 401
    # auditors are blind to tiers: refused by every risk-bearing endpoint
    for path in ("/api/summary", "/api/queue", f"/api/record/{wk}", "/api/analytics", "/api/map-data"):
        assert client.get(path, headers=auditor).status_code == 403, path
    # payee profiles: never anonymous, never to a scoped account
    payee = _one(db_session, "SELECT id FROM payee ORDER BY id LIMIT 1")
    assert client.get(f"/api/entities/payee/{payee}").status_code == 401
    assert client.get(f"/api/entities/payee/{payee}", headers=state).status_code == 404
    assert client.get(f"/api/entities/payee/{payee}", headers=make_user("ministry")).status_code == 200


def test_authz_anonymous_read_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "anonymous_read", False)
    assert client.get("/api/summary").status_code == 401
    assert client.get("/api/health").status_code == 200


# =============================================================================================
# 3. Identity in audit: actor always from the token, never the body
# =============================================================================================


def test_identity_actor_comes_from_the_token(client, make_user, db_session, served_run, no_case_residue):
    h = make_user("investigator")
    wk = _one(
        db_session,
        "SELECT work_key FROM served_work WHERE run_id = :r AND scored ORDER BY work_key " "LIMIT 1",
        r=served_run,
    )
    r = client.post(f"/api/investigate/{wk}", json={"decision": "Escalate", "reviewer": "Mallory"}, headers=h)
    assert r.status_code == 200
    entry = r.json()["entry"]
    assert entry["reviewer"].startswith(h["X-Test-User"]) and "Mallory" not in entry["reviewer"]
    row = db_session.execute(
        text("SELECT actor, actor_user_id FROM case_event WHERE id = :i"), {"i": entry["event_id"]}
    ).one()
    uid = _one(db_session, "SELECT id FROM app_user WHERE username = :u", u=h["X-Test-User"])
    assert row.actor_user_id == uid and "Mallory" not in row.actor
    # the audit-sample review schema refuses a reviewer field outright
    from app.api.audit import ReviewBody

    with pytest.raises(Exception):
        ReviewBody(outcome="no_follow_up", reviewer="Mallory")


# =============================================================================================
# 4. Audit trail: append-only, server timestamps, hash chain, exportable per case
# =============================================================================================


def test_audit_trail_is_append_only_in_code():
    """Nothing in the application updates or deletes case_event rows."""
    pattern = re.compile(
        r"(UPDATE\s+case_event|DELETE\s+FROM\s+case_event|delete\(CaseEvent|\.delete\(ev)", re.I
    )
    hits = [
        str(p.relative_to(BACKEND))
        for p in (BACKEND / "app").rglob("*.py")
        if pattern.search(p.read_text("utf-8"))
    ]
    assert not hits, hits


def test_audit_trail_hash_chain_detects_tampering(client, make_user, db_session, served_run, no_case_residue):
    h = make_user("supervisor")
    wks = (
        db_session.execute(
            text("SELECT work_key FROM served_work WHERE run_id = :r AND scored ORDER BY work_key LIMIT 3"),
            {"r": served_run},
        )
        .scalars()
        .all()
    )
    for wk in wks:
        assert (
            client.post(f"/api/investigate/{wk}", json={"decision": "InReview"}, headers=h).status_code == 200
        )
    ok = client.get("/api/audit-trail/verify", headers=h)
    assert ok.status_code == 200 and ok.json()["valid"]
    export = client.get(f"/api/cases/{wks[0]}/events", headers=h).json()
    ev = export["events"][-1]
    assert ev["event_hash"] and export["chain"]["valid"]
    # server timestamps: created_at is set by the server, within this test's run
    ts = dt.datetime.fromisoformat(ev["timestamp"])
    assert abs((dt.datetime.now(dt.timezone.utc) - ts).total_seconds()) < 600
    # tamper with a middle event (then undo): verification must fail at that event
    mid = export["events"][-1]["event_id"]
    db_session.execute(text("UPDATE case_event SET note = 'edited' WHERE id = :i"), {"i": mid})
    db_session.flush()
    from app.audit import case_log

    broken = case_log.verify_chain(db_session)
    db_session.rollback()
    assert not broken["valid"] and broken["first_break_event_id"] == mid
    assert case_log.verify_chain(db_session)["valid"]


# =============================================================================================
# 5. Imports: admin-only job over registered snapshots; no endpoint accepts a filesystem path
# =============================================================================================


def test_imports_no_endpoint_accepts_a_path_and_no_import_endpoint():
    bad_names = re.compile(r"(path|file|dir|folder|filename|filepath)", re.I)
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        assert "load" not in r.path and "import" not in r.path, r.path
        for param in r.dependant.query_params + r.dependant.path_params + r.dependant.body_params:
            assert not bad_names.search(param.name), (r.path, param.name)
    # imports run only as server-side CLI jobs over registered snapshots
    assert (BACKEND / "scripts" / "run_ingest.py").exists()


# =============================================================================================
# 6. CORS: explicit origin allowlist; no wildcard combined with credentials
# =============================================================================================


def test_cors_allowlist_only():
    c = TestClient(app)
    allowed = get_settings().cors_origin_list[0]
    pre = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = c.options("/api/summary", headers={"Origin": allowed, **pre})
    assert ok.headers.get("access-control-allow-origin") == allowed
    assert ok.headers.get("access-control-allow-credentials") != "true"
    evil = c.options("/api/summary", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in evil.headers
    simple = c.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers
    assert "*" not in get_settings().cors_origin_list


def test_cors_wildcard_configuration_refuses_to_start():
    env = {
        "CORS_ORIGINS": "*",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DATABASE_URL": "postgresql+psycopg://x@y/z",
    }
    r = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert r.returncode != 0 and "CORS_ORIGINS" in r.stderr


# =============================================================================================
# 7. Rate limits: login, copilot and search
# =============================================================================================


def _hammer(n, call):
    codes = [call().status_code for _ in range(n)]
    return codes


def test_rate_limits_login_chat_and_search(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_login_per_minute", 3)
    monkeypatch.setattr(s, "rate_limit_chat_per_minute", 3)
    monkeypatch.setattr(s, "rate_limit_search_per_minute", 3)
    LIMITER.reset()
    codes = _hammer(4, lambda: client.post("/api/auth/login", json={"username": "x-rl", "password": "y"}))
    assert codes[:3] == [401, 401, 401] and codes[3] == 429
    last = client.post("/api/auth/login", json={"username": "x-rl2", "password": "y"})
    assert last.status_code == 429 and int(last.headers["Retry-After"]) >= 1  # per-address limit too
    LIMITER.reset()
    assert _hammer(4, lambda: client.post("/api/chat", json={"message": "help"}))[-1] == 429
    LIMITER.reset()
    for path in ("/api/queue", "/api/map-data", "/api/map-works"):
        LIMITER.reset()
        codes = _hammer(4, lambda path=path: client.get(path, params={"search": "road"}))
        assert codes[:3] == [200, 200, 200] and codes[3] == 429, (path, codes)
    LIMITER.reset()
    # browsing without a search is not limited by the search bucket
    assert set(_hammer(5, lambda: client.get("/api/queue", params={"page_size": 1}))) == {200}


# =============================================================================================
# 8. Input handling: Pydantic request models; parameterised SQL
# =============================================================================================


def test_input_every_body_is_a_pydantic_model():
    from pydantic import BaseModel

    for r in app.routes:
        if isinstance(r, APIRoute):
            for bp in r.dependant.body_params:
                assert isinstance(bp.type_, type) and issubclass(bp.type_, BaseModel), (r.path, bp.name)


@pytest.mark.parametrize(
    "payload",
    [
        "' OR '1'='1",
        "'; DROP TABLE served_work; --",
        '" OR 1=1 --',
        "%' UNION SELECT password_hash FROM app_user --",
        "1); SELECT pg_sleep(5); --",
    ],
)
def test_input_injection_payloads_are_inert(client, payload, db_session):
    for path, key in (
        ("/api/queue", "search"),
        ("/api/queue", "state"),
        ("/api/queue", "mp"),
        ("/api/queue", "constituency"),
        ("/api/map-data", "state"),
        ("/api/map-works", "search"),
    ):
        r = client.get(path, params={key: payload})
        assert r.status_code == 200, (path, key, r.status_code)
        body = r.json()
        total = body["total"] if isinstance(body, dict) else len(body)
        assert total == 0, (path, key, payload)  # matched literally -- nothing contains it
    from urllib.parse import quote

    assert client.get(f"/api/record/{quote(payload, safe='')}").status_code == 404
    assert _one(db_session, "SELECT count(*) FROM served_work") > 0  # tables intact


def test_input_bounds_are_enforced(client, make_user, db_session, served_run):
    h = make_user("investigator")
    wk = _one(
        db_session,
        "SELECT work_key FROM served_work WHERE run_id = :r AND scored ORDER BY work_key " "LIMIT 1",
        r=served_run,
    )
    assert client.post(f"/api/investigate/{wk}", json={"decision": "x" * 61}, headers=h).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 2001}).status_code == 422
    assert client.get("/api/queue", params={"search": "x" * 201}).status_code == 422
    assert client.get("/api/queue", params={"page_size": 10_000}).status_code == 422
    assert client.post("/api/auth/login", json={"username": "", "password": ""}).status_code == 422


def test_input_sql_values_are_bound_not_formatted():
    """Static check: no SQL string in the app is built with %-formatting or
    .format(); f-string SQL may only interpolate server-side identifiers /
    fragments (column names from whitelists, scope clauses, WHERE lists),
    never a request value -- request values always go through bind
    parameters. Here: no text() call receives a %-formatted or .format() string."""
    offenders = []
    for p in (BACKEND / "app").rglob("*.py"):
        tree = ast.parse(p.read_text("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "text"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod):
                        offenders.append(f"{p.name}:{node.lineno}")
                    if isinstance(arg, ast.Call) and getattr(arg.func, "attr", "") == "format":
                        offenders.append(f"{p.name}:{node.lineno}")
    assert not offenders, offenders


# =============================================================================================
# 9. Secrets: environment only; nothing committed
# =============================================================================================

SECRET_PATTERNS = re.compile(
    r"(AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}"
    r"|ghp_[0-9A-Za-z]{30,}|sk-[0-9A-Za-z]{30,})"
)


def _committed_files():
    """Every file a commit would include: .gitignore'd paths excluded."""
    ignored_dirs = {
        "node_modules",
        ".git",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "dist",
        "geo_data_src",
        "data",
        "e2e-logs",
        "ci-artifacts",
    }
    for p in REPO.rglob("*"):
        if not p.is_file() or ignored_dirs & set(p.relative_to(REPO).parts):
            continue
        if p.name == ".env" or (p.name.startswith(".env.") and p.name != ".env.example"):
            continue  # gitignored: .env, .env.* (except .env.example)
        if p.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".ico",
            ".pdf",
            ".parquet",
            ".shp",
            ".dbf",
            ".shx",
            ".pyc",
            ".woff",
            ".woff2",
            ".ttf",
            ".zip",
            ".gz",
        }:
            continue
        yield p


def test_secrets_nothing_committed():
    hits = []
    for p in _committed_files():
        try:
            txt = p.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        for m in SECRET_PATTERNS.finditer(txt):
            hits.append(f"{p.relative_to(REPO)}: {m.group(0)[:6]}...")
    assert not hits, hits


def test_secrets_env_files_are_gitignored_and_config_comes_from_env():
    gi = (REPO / ".gitignore").read_text("utf-8").splitlines()
    for pattern in (".env", ".env.*", "**/.env", "**/.env.*", "!.env.example", "!**/.env.example"):
        assert pattern in gi, pattern
    fields = get_settings().model_fields
    assert fields["jwt_secret"].default == ""  # no usable default secret in code


def test_secrets_no_env_file_in_repo_or_docker_context():
    """The archived prototype's .env (it held a live key) is gone, no Docker
    context can carry any .env into an image, and no Dockerfile copies one."""
    assert not (REPO / "archive" / "backend_v1" / "backend" / ".env").exists()
    for di in (REPO / ".dockerignore", BACKEND / ".dockerignore"):
        lines = di.read_text("utf-8").splitlines()
        for pattern in (".env", ".env.*", "**/.env", "**/.env.*"):
            assert pattern in lines, (di, pattern)
    for dockerfile in REPO.rglob("Dockerfile*"):
        if "node_modules" in dockerfile.parts:
            continue
        for line in dockerfile.read_text("utf-8").splitlines():
            assert not re.search(r"^\s*(COPY|ADD)\b.*\.env", line, re.I), (dockerfile, line)


def _secret_scan_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("secret_scan", REPO / "ops" / "ci" / "secret_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_secrets_ci_scanner_passes_on_the_repo_and_catches_secrets():
    """The CI step (ops/ci/secret_scan.py) is clean on this tree, and it really
    detects each secret shape (synthetic values built at run time, so no
    real-looking secret sits in this file) without printing any value."""
    scanner = str(REPO / "ops" / "ci" / "secret_scan.py")
    r = subprocess.run([sys.executable, scanner], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:]
    scan = _secret_scan_module().scan_text
    fake_google = "AIza" + "B" * 35
    assert scan(f"GEMINI_API_KEY={fake_google}", "x.yml")
    assert scan(f'key = "{fake_google}"', "a.py")
    assert scan("-----BEGIN RSA " + "PRIVATE KEY-----", "k.pem")
    assert scan('JWT_SECRET = "' + "q7" * 12 + '"', "a.py")
    assert scan("DB_PASSWORD=" + "h4" * 8, ".env.production")  # secret-scan: ignore (synthetic probe)
    # not secrets: code expressions, compose substitutions, placeholders
    assert not scan("api_key = get_gemini_api_key()", "a.py")
    assert not scan('JWT_SECRET: "${JWT_SECRET:-}"', "docker-compose.yml")
    assert not scan("GEMINI_API_KEY=", ".env.example")  # secret-scan: ignore (probe)
    # a finding never contains the matched value
    hits = scan(f"GEMINI_API_KEY={fake_google}", "x.yml")
    assert all(fake_google not in str(h) for h in hits)


# =============================================================================================
# 10. Backups: scheduled backups and an executed restore test
# =============================================================================================


def test_backups_are_scheduled_and_the_restore_was_executed():
    compose = (REPO / "docker-compose.yml").read_text("utf-8")
    assert re.search(r"^\s{2}backup:", compose, re.M), "no scheduled backup service in docker-compose.yml"
    assert (REPO / "ops" / "backup" / "backup.sh").exists()
    assert (REPO / "ops" / "backup" / "restore_test.sh").exists()
    report = (REPO / "docs" / "restore_test_report.md").read_text("utf-8")
    assert "RESULT: PASS" in report, "the restore test has not been executed successfully"


# =============================================================================================
# 11. Copilot: grounded, rate-limited (row 7), never receives credentials, never scores
# =============================================================================================


def test_copilot_never_receives_credentials_or_scores(client, make_user, monkeypatch, db_session, served_run):
    from app.analytics.atypicality_run import risk_result_checksum

    captured = {}

    def fake_gemini(message, history, system_prompt):
        captured.update(message=message, history=history, system_prompt=system_prompt)
        return "ok", True

    monkeypatch.setattr("app.api.chat._call_gemini", fake_gemini)
    h = make_user("investigator")
    before = risk_result_checksum(db_session, served_run)
    r = client.post("/api/chat", json={"message": "how does the queue work", "history": []}, headers=h)
    assert r.status_code == 200 and r.json()["source"] == "gemini"
    token = h["Authorization"].split()[1]
    blob = repr(captured)
    assert token not in blob and "Bearer" not in blob and "password" not in blob.lower()
    assert risk_result_checksum(db_session, served_run) == before
    src = (BACKEND / "app" / "api" / "chat.py").read_text("utf-8")
    assert "risk_result" in src and "INSERT" not in src.upper() and "UPDATE " not in src.upper()


def test_copilot_is_grounded_on_stored_results(client, db_session, served_run):
    wk, risk = db_session.execute(
        text(
            "SELECT r.work_key, r.risk FROM risk_result r JOIN published_run p ON p.run_id = r.run_id "
            "AND p.default_config_name = r.config_name ORDER BY r.work_key LIMIT 1"
        )
    ).one()
    reply = client.post("/api/chat", json={"message": f"why is work {wk} flagged"}).json()
    assert reply["source"] == "stored_result" and f"{risk:.1f}/100" in reply["reply"]


# =============================================================================================
# 12. Personal data: sole-proprietor payees are not published by name
# =============================================================================================


def test_personal_data_public_graph_withholds_individual_payee_names(client, make_user, db_session):
    public = client.get("/api/graph-data").json()
    named = client.get("/api/graph-data", headers=make_user("ministry")).json()
    types = dict(
        db_session.execute(
            text("SELECT 'payee_' || id::text, payee_type FROM payee WHERE 'payee_' || id::text = ANY(:ids)"),
            {"ids": [n["id"] for n in named["nodes"] if n["type"] == "Payee"]},
        ).all()
    )
    pub = {n["id"]: n["label"] for n in public["nodes"]}
    for n in named["nodes"]:
        if n["type"] != "Payee":
            continue
        if types.get(n["id"], "unclassified") in ("individual", "unclassified"):
            assert pub[n["id"]] == "Payee (name withheld in public view)" and n["label"] != pub[n["id"]]
        else:
            assert pub[n["id"]] == n["label"]
    assert any(n["type"] == "Payee" for n in public["nodes"])
