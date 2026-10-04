"""
Rate limits (BLUEPRINT.md §11: "Applied to login, copilot and search
endpoints").

A sliding one-minute window per (bucket, key), in process memory. Keys are
the client address and, where a caller is authenticated or names an
account, that identity too -- so one client can't exhaust a user's login
attempts from many addresses without also hitting the per-username limit.
A request over the limit gets 429 with Retry-After.

LIMITATION -- the counters live in this process's memory. That is correct
for a single API process (the compose deployment). On Vercel every warm
instance keeps its own counters, so the effective limit is the configured
one times the number of instances, and a cold start begins at zero. A hard
global limit needs a shared store (for example Redis) behind the same
interface; none is used here. Documented in docs/security.md and
DEPLOY_NOTES.md.
"""

from __future__ import annotations

import ipaddress
import math
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from ..core.config import get_settings

WINDOW = 60.0


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def hit(self, bucket: str, key: str, limit: int, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and q[0] <= now - WINDOW:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, math.ceil(q[0] + WINDOW - now))
                raise HTTPException(
                    status_code=429,
                    detail=f"rate limit exceeded for {bucket}: {limit} requests per minute",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)


LIMITER = RateLimiter()


def _in(addr: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in networks)


def _ip(value: str) -> str | None:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def client_key(request: Request) -> str:
    """The client address the limits count against (Phase 14).

    When the TCP peer is a configured proxy (TRUSTED_PROXIES, the cloudflared
    tunnel), forwarded headers are used: Cloudflare's CF-Connecting-IP (set by
    its edge, overwriting anything the client sent), else the right-most
    X-Forwarded-For hop that is not itself a trusted proxy.

    Behind a platform proxy with no fixed address (Vercel: VERCEL set, or
    TRUST_PROXY_HEADERS=1), the right-most X-Forwarded-For entry is used --
    the one the platform's proxy appended -- else X-Real-IP.

    Entries the client wrote (to the left) are never preferred over the one
    a proxy appended. Vercel is documented to overwrite X-Forwarded-For with
    the single client address; verify on a preview (DEPLOY_NOTES.md) that no
    internal hop is appended after it. In every other case the peer is counted by its own address, whatever
    headers it sends."""
    peer = request.client.host if request.client else "unknown"
    s = get_settings()
    networks = s.trusted_proxy_networks
    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    if networks and _in(peer, networks):
        cf = _ip(request.headers.get("cf-connecting-ip") or "")
        if cf:
            return cf
        for hop in reversed(hops):
            if not _in(hop, networks):
                return hop
        return peer
    if s.behind_platform_proxy:
        appended = _ip(hops[-1]) if hops else None
        if appended:
            return appended
        real = _ip(request.headers.get("x-real-ip") or "")
        if real:
            return real
    return peer


def limit(bucket: str, request: Request, *extra_keys: str) -> None:
    s = get_settings()
    per_minute = {
        "login": s.rate_limit_login_per_minute,
        "chat": s.rate_limit_chat_per_minute,
        "search": s.rate_limit_search_per_minute,
    }[bucket]
    LIMITER.hit(bucket, f"ip:{client_key(request)}", per_minute)
    for k in extra_keys:
        if k:
            LIMITER.hit(bucket, k, per_minute)
