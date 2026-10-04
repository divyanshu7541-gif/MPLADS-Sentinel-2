"""
Short-lived access tokens (BLUEPRINT.md §11 "Token-based login (JWT or
OIDC-compatible) ... short-lived tokens").

HS256 JWTs with the registered claims an OIDC relying party expects: iss,
aud, sub (the account id), iat, exp, jti -- plus `username` and `role` for
display. The token only IDENTIFIES the account: role and scope are re-read
from app_user on every request (app/auth/deps.py), so disabling an account
or narrowing its scope takes effect at once, not when the token expires.
There is no refresh token; expiry is `access_token_minutes` (default 15).

The signing key comes only from the environment (JWT_SECRET). In
development an unset key is replaced by a random per-process key; in any
other APP_ENV an unset or short key is a startup error.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
import uuid

import jwt

from ..core.config import get_settings

log = logging.getLogger("sentinel.auth")
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
_EPHEMERAL = secrets.token_urlsafe(48)


class TokenError(Exception):
    pass


def signing_key() -> str:
    s = get_settings()
    if s.jwt_secret:
        if len(s.jwt_secret) < MIN_SECRET_LENGTH:
            raise RuntimeError(f"JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters")
        return s.jwt_secret
    if s.app_env == "development":
        return _EPHEMERAL
    raise RuntimeError("JWT_SECRET is not set (required outside APP_ENV=development)")


def check_configuration() -> None:
    """Called at startup: fail fast rather than at the first login."""
    signing_key()
    if not get_settings().jwt_secret:
        log.warning("JWT_SECRET unset: using a random per-process key (development only)")


def issue(user_id: int, username: str, role: str, *, now: dt.datetime | None = None) -> tuple[str, int]:
    s = get_settings()
    now = now or dt.datetime.now(dt.timezone.utc)
    ttl = s.access_token_minutes * 60
    claims = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, signing_key(), algorithm=ALGORITHM), ttl


def decode(token: str) -> dict:
    s = get_settings()
    try:
        return jwt.decode(
            token,
            signing_key(),
            algorithms=[ALGORITHM],  # pinned: "none" and algorithm-confusion tokens are rejected
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e
