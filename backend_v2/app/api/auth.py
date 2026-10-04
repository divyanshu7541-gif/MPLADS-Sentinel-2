"""
Phase 13 login (BLUEPRINT.md §11): POST /api/auth/login exchanges a
username and password for a short-lived bearer token; GET /api/auth/me
returns who the token belongs to and the scope every query will apply.

Not in the frontend contract: the protected frontend's login page is a
client-side demo that never calls the API (docs/security.md). Accounts are
created only by an administrator on the server (scripts/create_user.py);
there is no self-registration and no password in any request other than
this one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth import passwords, ratelimit, tokens
from ..auth.deps import Principal, get_principal
from ..db.session import get_db
from ..models.security import AppUser

router = APIRouter()


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class MeOut(BaseModel):
    username: str
    role: str
    authenticated: bool
    scope: dict


@router.post("/api/auth/login", response_model=TokenOut)
def login(request: Request, body: LoginBody, db: Session = Depends(get_db)):
    username = body.username.strip().lower()
    # per address AND per account, so neither can be brute-forced from many of the other
    ratelimit.limit("login", request, f"login-user:{username}")
    try:
        user = db.execute(select(AppUser).where(AppUser.username == username)).scalar_one_or_none()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail="authentication temporarily unavailable") from e
    # Always run one hash verification, so response time doesn't reveal whether the account exists.
    ok = passwords.verify_password(body.password, user.password_hash if user else passwords.DUMMY_HASH)
    if user is None or not ok or not user.is_active:
        raise HTTPException(
            status_code=401, detail="invalid username or password", headers={"WWW-Authenticate": "Bearer"}
        )
    token, ttl = tokens.issue(user.id, user.username, user.role)
    return TokenOut(access_token=token, expires_in=ttl, role=user.role)


@router.get("/api/auth/me", response_model=MeOut)
def me(p: Principal = Depends(get_principal)):
    sc = p.scope
    return MeOut(
        username=p.username,
        role=p.role,
        authenticated=p.authenticated,
        scope={
            "national": sc.national,
            "house": sc.house,
            "state": sc.state,
            "district_authority_id": sc.district_authority_id,
            "mp": sc.mp,
        },
    )
