"""
Phase 13 randomised audit sample API (BLUEPRINT.md §12; app/audit/sampling.py).

  POST /api/audit/samples                       supervisor/admin: draw a sample
  GET  /api/audit/samples                       supervisor/admin: list samples
  GET  /api/audit/samples/{id}/report           supervisor/admin: precision report
  GET  /api/audit/samples/{id}/items            auditor: the BLIND review list
  POST /api/audit/items/{blind_code}/reviews    auditor: record an outcome

Reviewers (role `auditor`) are blind to tier: their items carry no tier,
score, confidence, signal or peer figure, and the auditor role is refused
by every risk-bearing endpoint (app/auth/deps.py `reader`). The reviewer
recorded on a review is the token's account, never the request body.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..audit import sampling
from ..auth.deps import AUDIT_MANAGERS, AUDIT_REVIEWERS, Principal, require_roles
from ..db.session import get_db
from ..models.security import AuditSample

router = APIRouter()


def _db(fn, what: str):
    try:
        return fn()
    except IntegrityError:
        raise  # a constraint conflict is the caller's 409, not an outage
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"{what} temporarily unavailable") from e


class DrawBody(BaseModel):
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


@router.post("/api/audit/samples")
def draw_sample(
    body: DrawBody, db: Session = Depends(get_db), p: Principal = Depends(require_roles(*AUDIT_MANAGERS))
):
    def run():
        s = sampling.draw(db, created_by=p.actor, seed=body.seed)
        db.commit()
        return {
            "sample_id": s.id,
            "run_id": s.run_id,
            "config": s.config_name,
            "seed": s.seed,
            "design": s.design,
        }

    try:
        return _db(run, "audit sample")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/audit/samples")
def list_samples(db: Session = Depends(get_db), p: Principal = Depends(require_roles(*AUDIT_MANAGERS))):
    def run():
        rows = db.execute(select(AuditSample).order_by(AuditSample.id)).scalars().all()
        return [
            {
                "sample_id": s.id,
                "run_id": s.run_id,
                "seed": s.seed,
                "created_by": s.created_by,
                "created_at": s.created_at.isoformat(),
                "total_drawn": s.design.get("total_drawn"),
                "archived_at": s.archived_at.isoformat() if s.archived_at else None,
                "archive_reason": s.archive_reason,
            }
            for s in rows
        ]

    return _db(run, "audit samples")


@router.get("/api/audit/samples/{sample_id}/report")
def sample_report(
    sample_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_roles(*AUDIT_MANAGERS))
):
    try:
        return _db(lambda: sampling.report(db, sample_id), "audit report")
    except LookupError as e:
        raise HTTPException(status_code=404, detail="unknown sample") from e


@router.get("/api/audit/samples/{sample_id}/items")
def sample_items(
    sample_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    p: Principal = Depends(require_roles(*AUDIT_REVIEWERS)),
):
    def run():
        if db.get(AuditSample, sample_id) is None:
            return None
        return sampling.blind_items(db, sample_id, p.user_id, page, page_size)

    out = _db(run, "audit items")
    if out is None:
        raise HTTPException(status_code=404, detail="unknown sample")
    return out


class ReviewBody(BaseModel):
    outcome: Literal["follow_up_needed", "no_follow_up", "data_issue"]
    note: Optional[str] = Field(None, max_length=4000)
    # A reviewer name in the body is refused by the schema (extra="forbid"):
    # the reviewer is always the token's account.
    model_config = {"extra": "forbid"}


@router.post("/api/audit/items/{blind_code}/reviews")
def add_review(
    blind_code: str,
    body: ReviewBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_roles(*AUDIT_REVIEWERS)),
):
    def run():
        r = sampling.add_review(db, blind_code=blind_code, principal=p, outcome=body.outcome, note=body.note)
        db.commit()
        return {
            "blind_code": blind_code,
            "outcome": r.outcome,
            "reviewer": r.reviewer,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    try:
        return _db(run, "audit review")
    except LookupError as e:
        raise HTTPException(status_code=404, detail="unknown item") from e
    except sampling.ArchivedSampleError as e:
        raise HTTPException(status_code=409, detail="this audit sample is archived") from e
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="you have already reviewed this item") from e
