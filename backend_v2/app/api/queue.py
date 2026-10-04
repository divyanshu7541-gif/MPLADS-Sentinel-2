"""
docs/frontend_contract.md #2, #3, #13, #14
(/api/queue, /api/record/{record_id}, /api/investigate/{record_id}, /api/audit-trail),
plus the Phase 13 per-case export and chain check.

Queue: real server-side pagination over served_work (scored works only),
restricted to the caller's scope in SQL (app/auth/scope.py). Parameter
names are the ones frontend/src/services/api.js actually sends: the Queue
page sends `mp` and `risk_level`, which the old router silently ignored;
both spellings are accepted here. Text filters are literal substring
matches; a search is rate-limited.

Investigate (Phase 13): requires an authenticated investigator, supervisor
or admin. The actor is the token's account (app/audit/case_log.py) --
NEVER the request body. The body's `reviewer` is accepted for contract
compatibility and ignored. Every event is hash-chained.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..audit import case_log
from ..auth import ratelimit
from ..auth.deps import CASE_READERS, CASE_WRITERS, Principal, reader, require_roles
from ..db.session import get_db
from ..serving import service
from ..serving.public_mask import public_view
from .params import House, guarded, not_found

router = APIRouter()


@router.get("/api/queue")
def get_investigation_queue(
    request: Request,
    priority: Optional[str] = Query(None, max_length=20),
    risk_level: Optional[str] = Query(None, max_length=20),
    confidence: Optional[str] = Query(None, max_length=20),
    category: Optional[str] = Query(None, max_length=300),
    state: Optional[str] = Query(None, max_length=200),
    constituency: Optional[str] = Query(None, max_length=200),
    mp_name: Optional[str] = Query(None, max_length=200),
    mp: Optional[str] = Query(None, max_length=200),
    stage: Optional[str] = Query(None, max_length=20),
    search: Optional[str] = Query(None, max_length=200),
    sort_by: str = Query("risk_score", max_length=40),
    sort_order: str = Query("desc", max_length=4),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=service.MAX_PAGE_SIZE),
    house: House = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    search = search.strip() if search and search.strip() else None
    if search:
        ratelimit.limit("search", request, f"user:{p.user_id}" if p.authenticated else "")
    filters = {
        "priority": priority,
        "risk_level": risk_level,
        "confidence": confidence,
        "category": category,
        "state": state,
        "constituency": constituency,
        "mp_name": mp_name or mp,
        "stage": stage,
        "search": search,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "page": page,
        "page_size": page_size,
        "house": house,
        "public": not p.authenticated,
    }
    return public_view(
        guarded(lambda: service.queue(db, service.served(db, p.scope), filters), "queue"), p.authenticated
    )


@router.get("/api/record/{record_id}")
def get_record_detail(record_id: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    detail = guarded(lambda: service.record_detail(db, service.served(db, p.scope), record_id), "record")
    if detail is None:
        not_found("Record", record_id)
    return public_view(detail, p.authenticated)


class InvestigateBody(BaseModel):
    decision: Optional[str] = Field("", max_length=60)  # case_event.decision is String(60)
    # Accepted for contract compatibility, never used (see module docstring).
    reviewer: Optional[str] = Field(None, max_length=200)
    note: Optional[str] = Field("", max_length=4000)


@router.post("/api/investigate/{record_id}")
def update_investigation(
    record_id: str,
    body: InvestigateBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_roles(*CASE_WRITERS)),
):
    def run():
        s = service.served(db, p.scope)
        if service._row(db, s, record_id) is None:  # missing OR outside the caller's scope
            return None
        entry = service.investigate(db, s, record_id, body.decision or "", body.note or "", p)
        db.commit()
        return entry

    entry = guarded(run, "investigation log")
    if entry is None:
        not_found("Record", record_id)
    return {"status": "saved", "entry": entry}


@router.get("/api/audit-trail")
def get_audit_trail(db: Session = Depends(get_db), p: Principal = Depends(require_roles(*CASE_READERS))):
    return guarded(lambda: service.audit_trail(db, service.served(db, p.scope)), "audit trail")


@router.get("/api/cases/{record_id}/events")
def export_case_events(
    record_id: str, db: Session = Depends(get_db), p: Principal = Depends(require_roles(*CASE_READERS))
):
    """Per-case export (BLUEPRINT.md §11 "exportable per case"), with hashes."""

    def run():
        if service._row(db, service.served(db, p.scope), record_id) is None:
            return None
        return case_log.export_case(db, record_id)

    out = guarded(run, "case export")
    if out is None:
        not_found("Record", record_id)
    return out


@router.get("/api/audit-trail/verify")
def verify_audit_chain(
    db: Session = Depends(get_db), p: Principal = Depends(require_roles("supervisor", "admin"))
):
    out = guarded(lambda: case_log.verify_chain(db), "audit chain")
    if not out["valid"]:
        raise HTTPException(status_code=409, detail=out)
    return out
