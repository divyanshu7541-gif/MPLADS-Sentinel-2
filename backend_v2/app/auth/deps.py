"""
Who is calling, and what they may do (BLUEPRINT.md §10 roles, §11 Controls).

Every data endpoint depends on `reader` (or a stricter requirement). It
resolves the caller to a Principal:
  * a valid `Authorization: Bearer <token>` -> that account, with its role
    and scope RE-READ FROM app_user (a disabled account or narrowed scope
    applies immediately);
  * an invalid, expired or disabled-account token -> 401, never a silent
    fall-back to anonymous;
  * no token -> the built-in anonymous "public" principal (national,
    read-only) when ANONYMOUS_READ is on, else 401.

Roles (BLUEPRINT.md §10):
  admin, ministry, supervisor, investigator  -- scope from their row
                                                (normally national)
  state, district, mp                        -- scope required by the row's
                                                CHECK constraints
  auditor                                    -- audit-sample reviewer: blind
                                                to tiers, so refused by every
                                                risk-bearing endpoint
  public                                     -- anonymous, never stored
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..db.session import get_db
from ..models.security import AppUser
from . import tokens
from .scope import NATIONAL, Scope

CASE_WRITERS = ("investigator", "supervisor", "admin")
CASE_READERS = ("investigator", "supervisor", "admin")
AUDIT_MANAGERS = ("supervisor", "admin")
AUDIT_REVIEWERS = ("auditor",)


@dataclass(frozen=True)
class Principal:
    user_id: int | None
    username: str
    role: str
    scope: Scope

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def actor(self) -> str:
        """What audit records store as the actor -- always from here."""
        return f"{self.username} (user {self.user_id})"


PUBLIC = Principal(None, "public", "public", NATIONAL)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def scope_of(user: AppUser) -> Scope:
    return Scope(
        house=user.scope_house,
        state=user.scope_state,
        district_authority_id=user.scope_district_authority_id,
        mp=user.scope_mp,
    )


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    header = request.headers.get("authorization")
    if not header:
        if get_settings().anonymous_read:
            return PUBLIC
        raise _unauthorized("authentication required")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("expected 'Authorization: Bearer <token>'")
    try:
        claims = tokens.decode(token.strip())
        user_id = int(claims["sub"])
    except (tokens.TokenError, ValueError, KeyError) as e:
        raise _unauthorized("invalid or expired token") from e
    try:
        user = db.get(AppUser, user_id)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail="authentication temporarily unavailable") from e
    if user is None or not user.is_active:
        raise _unauthorized("account disabled or removed")
    return Principal(user.id, user.username, user.role, scope_of(user))


def reader(principal: Principal = Depends(get_principal)) -> Principal:
    """Any role that may see risk data. Auditors are blind to tiers."""
    if principal.role == "auditor":
        raise HTTPException(
            status_code=403,
            detail="auditor accounts review the blind audit sample only (no tier, score or signal data)",
        )
    return principal


def require_roles(*roles: str):
    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.authenticated:
            raise _unauthorized("authentication required")
        if principal.role not in roles:
            raise HTTPException(status_code=403, detail=f"requires one of: {', '.join(roles)}")
        return principal

    return dep


def require_national(principal: Principal = Depends(reader)) -> Principal:
    """National-level figures that cannot be restricted to a scope."""
    if not principal.scope.national:
        raise HTTPException(status_code=403, detail="not available to a scoped account (national figures)")
    return principal
