import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_live_llm_and_fresh_rate_limits(monkeypatch):
    """No test ever calls the real Gemini API (a developer's repo-root .env
    may hold a key -- app/core/chat_config.py); tests that exercise the AI
    layer patch it themselves. Rate-limit windows start empty per test, so
    one test's requests never count against another's."""
    from app.auth.ratelimit import LIMITER

    monkeypatch.setattr("app.api.chat.get_gemini_api_key", lambda: "")
    LIMITER.reset()
    yield
    LIMITER.reset()


@pytest.fixture(scope="session")
def db_session():
    """Phase 1 integration tests (tests/test_phase1_ingest.py) assume a
    migrated, already-ingested database -- CI runs `alembic upgrade head`
    and `scripts/run_ingest.py` as separate steps before pytest (see
    .github/workflows/ci.yml), exactly how a developer would run it.
    Skips (rather than fails) if DATABASE_URL isn't reachable, so the rest
    of the suite still runs standalone."""
    from sqlalchemy import text

    from app.db.session import get_session_factory

    Session = get_session_factory()
    session = Session()
    try:
        session.execute(text("SELECT 1"))
    except Exception as e:
        session.close()
        pytest.skip(f"no reachable database for Phase 1 integration tests: {e}")
    yield session
    session.close()


# ---- Phase 13: real accounts for tests --------------------------------------------------------

TEST_PASSWORD = "phase13-test-" + secrets.token_hex(8)  # secret-scan: ignore (random per test session)


@pytest.fixture(scope="session")
def make_user(db_session):
    """Create a real app_user row (removed at the end of the session, with
    any case events / audit rows it wrote) and return a login helper:
        headers = make_user("investigator")                      # national
        headers = make_user("state", scope_state="Uttar Pradesh")
    Each call returns {"Authorization": "Bearer <token from /api/auth/login>"},
    so every test goes through the real login endpoint."""
    from sqlalchemy import text

    from app.auth.passwords import hash_password
    from app.models.security import AppUser

    created: list[int] = []
    pw_hash = hash_password(TEST_PASSWORD)
    client = TestClient(app)

    def make(role: str, **scope) -> dict:
        username = f"t13-{role}-{secrets.token_hex(4)}"
        user = AppUser(username=username, password_hash=pw_hash, role=role, is_active=True, **scope)
        db_session.add(user)
        db_session.commit()
        created.append(user.id)
        r = client.post("/api/auth/login", json={"username": username, "password": TEST_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Test-User": username}

    yield make

    if created:
        db_session.rollback()
        ids = {"ids": created}
        db_session.execute(text("DELETE FROM audit_review WHERE reviewer_user_id = ANY(:ids)"), ids)
        db_session.execute(text("DELETE FROM case_event WHERE actor_user_id = ANY(:ids)"), ids)
        db_session.execute(text("DELETE FROM app_user WHERE id = ANY(:ids)"), ids)
        db_session.commit()
