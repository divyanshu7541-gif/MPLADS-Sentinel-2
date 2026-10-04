"""
Phase 14: the Vercel frontend deployment and the backend it reaches.

  * CORS: a preflight from the exact production origin (set through
    CORS_ORIGINS, as the deploy stack does) passes; one from any other origin,
    a Vercel preview URL included, is refused.
  * Client IP behind the tunnel: forwarded headers count only when the TCP
    peer is a configured proxy (TRUSTED_PROXIES); a spoofed X-Forwarded-For
    from anyone else is ignored. Behind Vercel (VERCEL or TRUST_PROXY_HEADERS)
    the right-most X-Forwarded-For entry, else X-Real-IP, counts if it is an IP.
  * Access: the investigation log, case export, audit and reviewer endpoints
    and payee profiles answer 401 to an anonymous caller (what the public site
    is); ANONYMOUS_READ is the explicit switch for the rest.
  * Deploy files: frontend/vercel.json is the SPA rewrite only; the root
    vercel.json is the Services layout (frontend + backend container, /api/* to
    the backend, SPA fallback inside the frontend service); the only frontend
    source change is the API_BASE line; env files are ignored.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import ratelimit
from app.core.config import get_settings

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend_v2"
PROD = "https://sentinel-mplads.vercel.app"

_PREFLIGHT = """
import json, sys
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
out = {}
for origin in sys.argv[1:]:
    r = c.options("/api/summary", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    g = c.get("/api/health", headers={"Origin": origin})
    out[origin] = [r.status_code, r.headers.get("access-control-allow-origin"),
                   g.headers.get("access-control-allow-origin")]
print(json.dumps(out))
"""


def _preflight(env_extra: dict, *origins: str) -> dict:
    env = {**os.environ, **env_extra}
    r = subprocess.run(
        [sys.executable, "-c", _PREFLIGHT, *origins], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_cors_preflight_from_the_production_origin_passes_and_others_are_refused():
    preview = "https://sentinel-mplads-abc123-team.vercel.app"
    out = _preflight(
        {"CORS_ORIGINS": PROD, "CORS_ORIGIN_REGEX": ""}, PROD, preview, "https://evil.example.com"
    )
    status, allow, simple = out[PROD]
    assert status == 200 and allow == PROD and simple == PROD
    for bad in (preview, "https://evil.example.com"):
        status, allow, simple = out[bad]
        assert status == 400 and allow is None and simple is None, (bad, out[bad])


def test_cors_single_preview_origin_can_be_added_and_removed():
    """Option (i): the one preview URL under test is listed next to production
    for the test window, then removed -- no regex, no wildcard."""
    preview = "https://sentinel-mplads-abc123-team.vercel.app"
    with_preview = _preflight({"CORS_ORIGINS": f"{PROD},{preview}", "CORS_ORIGIN_REGEX": ""}, preview)
    assert with_preview[preview][1] == preview
    removed = _preflight({"CORS_ORIGINS": PROD, "CORS_ORIGIN_REGEX": ""}, preview)
    assert removed[preview][1] is None


def _req(peer: str, **headers) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=peer), headers={k.lower(): v for k, v in headers.items()}
    )


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.30.14.10")


def test_client_ip_without_trusted_proxies_is_the_peer_whatever_the_headers(monkeypatch):
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    r = _req("203.0.113.9", **{"X-Forwarded-For": "1.1.1.1", "CF-Connecting-IP": "2.2.2.2"})
    assert ratelimit.client_key(r) == "203.0.113.9"


def test_client_ip_behind_the_tunnel_is_cloudflares_connecting_ip(proxy):
    r = _req(
        "172.30.14.10", **{"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "6.6.6.6, 198.51.100.7"}
    )
    assert ratelimit.client_key(r) == "198.51.100.7"


def test_client_ip_uses_the_rightmost_untrusted_hop_never_the_client_written_one(proxy):
    # the client sent "6.6.6.6"; the proxy appended the real address
    r = _req("172.30.14.10", **{"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
    assert ratelimit.client_key(r) == "198.51.100.7"


def test_forwarded_headers_from_an_untrusted_peer_are_ignored(proxy):
    r = _req("203.0.113.9", **{"CF-Connecting-IP": "6.6.6.6", "X-Forwarded-For": "6.6.6.6"})
    assert ratelimit.client_key(r) == "203.0.113.9"


@pytest.fixture(params=["VERCEL", "TRUST_PROXY_HEADERS"])
def platform_proxy(request, monkeypatch):
    """Behind Vercel's proxy (no fixed address for TRUSTED_PROXIES), switched
    on either by the platform's VERCEL variable or by TRUST_PROXY_HEADERS."""
    s = get_settings()
    monkeypatch.setattr(s, "trusted_proxies", "")
    monkeypatch.setattr(s, "vercel", "1" if request.param == "VERCEL" else "")
    monkeypatch.setattr(s, "trust_proxy_headers", request.param == "TRUST_PROXY_HEADERS")


def test_client_ip_behind_a_platform_proxy_is_the_rightmost_forwarded_for_entry(platform_proxy):
    # the client sent "6.6.6.6"; the platform's proxy appended the real address
    r = _req("10.1.2.3", **{"X-Forwarded-For": "6.6.6.6, 198.51.100.7", "X-Real-IP": "192.0.2.1"})
    assert ratelimit.client_key(r) == "198.51.100.7"
    r = _req("10.1.2.3", **{"X-Forwarded-For": "2001:db8::7"})
    assert ratelimit.client_key(r) == "2001:db8::7"


def test_client_ip_behind_a_platform_proxy_falls_back_to_x_real_ip(platform_proxy):
    r = _req("10.1.2.3", **{"X-Real-IP": "198.51.100.7"})
    assert ratelimit.client_key(r) == "198.51.100.7"
    # a non-IP right-most entry is skipped for X-Real-IP, never for a client-written hop to its left
    r = _req("10.1.2.3", **{"X-Forwarded-For": "6.6.6.6, unknown", "X-Real-IP": "198.51.100.7"})
    assert ratelimit.client_key(r) == "198.51.100.7"


def test_client_ip_behind_a_platform_proxy_ignores_non_ip_values(platform_proxy):
    r = _req("10.1.2.3", **{"X-Forwarded-For": "not-an-ip", "X-Real-IP": "<script>"})
    assert ratelimit.client_key(r) == "10.1.2.3"
    r = _req("10.1.2.3", **{"X-Forwarded-For": " , ", "X-Real-IP": ""})
    assert ratelimit.client_key(r) == "10.1.2.3"


def test_client_ip_is_the_peer_when_no_proxy_flag_is_set(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "trusted_proxies", "")
    monkeypatch.setattr(s, "vercel", "")
    monkeypatch.setattr(s, "trust_proxy_headers", False)
    r = _req("203.0.113.9", **{"X-Forwarded-For": "198.51.100.7", "X-Real-IP": "198.51.100.7"})
    assert ratelimit.client_key(r) == "203.0.113.9"
    monkeypatch.setattr(s, "vercel", "0")  # VERCEL=0 is not "on Vercel"
    assert ratelimit.client_key(r) == "203.0.113.9"


def test_spoofed_forwarded_for_cannot_evade_the_login_limit(client, proxy, monkeypatch):
    """TestClient's peer is 'testclient' -- not a trusted proxy -- so rotating
    X-Forwarded-For does not give a caller fresh rate-limit windows."""
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 3)
    codes = [
        client.post(
            "/api/auth/login",
            json={"username": f"nobody{i}", "password": "wrong-password"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        ).status_code
        for i in range(5)
    ]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/api/investigate/251224"),
        ("GET", "/api/audit-trail"),
        ("GET", "/api/audit-trail/verify"),
        ("GET", "/api/cases/251224/events"),
        ("GET", "/api/audit/samples"),
        ("POST", "/api/audit/samples"),
        ("GET", "/api/audit/samples/42/items"),
        ("GET", "/api/audit/samples/42/report"),
        ("POST", "/api/audit/items/abc/reviews"),
        ("GET", "/api/entities/payee/1"),
        ("POST", "/api/risk/recalculate/251224"),
    ],
)
def test_case_audit_and_reviewer_endpoints_refuse_anonymous_callers(client, method, path):
    body = (
        {"decision": "x"}
        if "investigate" in path
        else ({"outcome": "no_follow_up"} if "reviews" in path else {})
    )
    r = client.request(method, path, json=body if method == "POST" else None)
    assert r.status_code == 401, (path, r.status_code)


def test_anonymous_read_is_an_explicit_env_switch(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ANONYMOUS_READ", "false")
    assert Settings().anonymous_read is False
    monkeypatch.setenv("ANONYMOUS_READ", "true")
    assert Settings().anonymous_read is True
    compose = (REPO / "docker-compose.deploy.yml").read_text("utf-8")
    assert "ANONYMOUS_READ: ${ANONYMOUS_READ:-true}" in compose


# ---- deploy files -----------------------------------------------------------------------------


def test_vercel_layout_frontend_spa_and_backend_container():
    # frontend/vercel.json is the standalone-project SPA rewrite; in the Services layout
    # Vercel routes through the ROOT vercel.json, so the SPA fallback is repeated there.
    cfg = json.loads((REPO / "frontend" / "vercel.json").read_text("utf-8"))
    assert cfg == {"rewrites": [{"source": "/(.*)", "destination": "/index.html"}]}

    root = json.loads((REPO / "vercel.json").read_text("utf-8"))
    assert root["services"]["backend"] == {
        "root": "backend_v2/",
        "runtime": "container",
        "entrypoint": "Dockerfile.vercel",
    }
    frontend = root["services"]["frontend"]
    assert frontend["root"] == "frontend/" and frontend["framework"] == "vite"
    # Top-level order: /api/* reaches the backend first, everything else the frontend.
    assert root["rewrites"] == [
        {"source": "/api/(.*)", "destination": {"service": "backend"}},
        {"source": "/(.*)", "destination": {"service": "frontend"}},
    ]
    # SPA fallback inside the frontend service: every path whose last segment has no dot
    # becomes index.html; a path with a file extension (assets, missing files) does not.
    (spa,) = frontend["rewrites"]
    assert spa["destination"] == "/index.html"
    rx = re.compile("^/" + spa["source"].removeprefix("/") + "$")
    for path in ("/map", "/queue", "/record/1301-RS", "/mp-performance", "/dashboard", "/login"):
        assert rx.match(path), path
    for path in ("/assets/index-abc123.js", "/favicon.png", "/missing.js", "/a/b.c/d.txt", "/robots.txt"):
        assert not rx.match(path), path


def test_the_only_frontend_source_change_is_api_base():
    src = (REPO / "frontend" / "src" / "services" / "api.js").read_text("utf-8")
    lines = [ln for ln in src.splitlines() if "API_BASE =" in ln]
    assert lines == ["const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';"]
    example = (REPO / "frontend" / ".env.example").read_text("utf-8")
    assert "VITE_API_BASE_URL" in example and "public" in example.lower()
    # only VITE_API_BASE_URL is read anywhere in the frontend
    used = set()
    for p in (REPO / "frontend" / "src").rglob("*.js*"):
        used |= set(re.findall(r"import\.meta\.env\.(\w+)", p.read_text("utf-8", errors="ignore")))
    assert used <= {"VITE_API_BASE_URL"}, used


def test_deploy_stack_one_worker_no_published_api_port_and_trusted_proxy_pinned():
    compose = (REPO / "docker-compose.deploy.yml").read_text("utf-8")
    assert "--workers 1" in compose and "--no-proxy-headers" in compose
    assert "TRUSTED_PROXIES: 172.30.14.10" in compose and "ipv4_address: 172.30.14.10" in compose
    api_block = compose.split("  api:")[1].split("\n  worker:")[0]
    assert "ports:" not in api_block, "the API must only be reachable through the tunnel"
    assert 'CORS_ORIGIN_REGEX: ""' in compose and "*" not in compose.split("CORS_ORIGINS:")[1].split("\n")[0]


def test_deploy_secrets_are_git_ignored():
    """The ignore rules that keep the deploy secrets out of git (verified with
    `git check-ignore` when added; the test images have no git binary)."""
    rules = set((REPO / ".gitignore").read_text("utf-8").splitlines())
    assert {
        "*.env",
        "!*.env.example",
        "ops/deploy/cloudflared/*.json",
        "**/.env.*",
        "!**/.env.example",
    } <= rules
    assert (REPO / "ops" / "deploy" / "deploy.env.example").exists()
