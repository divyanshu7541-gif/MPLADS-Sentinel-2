"""
The case event log (BLUEPRINT.md §11 "Identity in audit" and "Audit trail").

  * The actor is ALWAYS the authenticated principal passed in by the route
    (app/auth/deps.py), never anything from the request body.
  * Append-only: nothing in the codebase updates or deletes case_event rows.
  * Server timestamps: created_at is set here, from the server clock.
  * Tamper evidence: every event is hash-chained to the one before it --
        event_hash = sha256(prev_hash + canonical JSON of the event)
    Appends take a transaction-scoped advisory lock so two concurrent
    writers can't fork the chain. verify_chain() recomputes it end to end;
    any edited, deleted or reordered row breaks it from that point on.
  * Exportable per case: export_case() returns a work's events with their
    hashes, plus whether the whole chain currently verifies.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..auth.scope import Scope
from ..models.serving import CaseEvent
from ..serving.redact import mask_personal

CHAIN_LOCK = 13_000_001  # pg_advisory_xact_lock key for case_event appends


def _canonical(ev: CaseEvent) -> str:
    return json.dumps(
        {
            "work_key": ev.work_key,
            "event_type": ev.event_type,
            "previous_status": ev.previous_status,
            "decision": ev.decision,
            "note": ev.note,
            "actor": ev.actor,
            "actor_user_id": ev.actor_user_id,
            "run_id": ev.run_id,
            "created_at": ev.created_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_hash(prev_hash: str | None, ev: CaseEvent) -> str:
    return hashlib.sha256(((prev_hash or "") + _canonical(ev)).encode("utf-8")).hexdigest()


def _last_decision(session: Session, work_key: str) -> str:
    d = session.execute(
        text(
            "SELECT decision FROM case_event WHERE work_key = :wk AND event_type = 'investigation_decision' "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"wk": work_key},
    ).scalar()
    return d or "Unreviewed"


def append(
    session: Session, *, work_key: str, event_type: str, decision, note, principal, run_id
) -> CaseEvent:
    if not principal.authenticated:
        raise PermissionError("case events need an authenticated actor")
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK})
    prev = session.execute(text("SELECT event_hash FROM case_event ORDER BY id DESC LIMIT 1")).scalar()
    ev = CaseEvent(
        work_key=work_key,
        event_type=event_type,
        previous_status=_last_decision(session, work_key) if event_type == "investigation_decision" else None,
        decision=decision,
        note=note,
        actor=principal.actor,
        actor_is_placeholder=False,
        actor_user_id=principal.user_id,
        run_id=run_id,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    ev.prev_hash = prev
    ev.event_hash = compute_hash(prev, ev)
    session.add(ev)
    session.flush()
    return ev


def entry(ev: CaseEvent) -> dict:
    return {
        "record_id": ev.work_key,
        "previous_status": ev.previous_status,
        # Output only: the stored note (and the hash chain over it) is unchanged.
        "decision": mask_personal(ev.decision),
        "reviewer": ev.actor,
        "note": mask_personal(ev.note),
        "timestamp": ev.created_at.isoformat() if ev.created_at else None,
        # additive (Phase 12/13)
        "event_id": ev.id,
        "event_type": ev.event_type,
        "actor_is_placeholder": ev.actor_is_placeholder,
        "prev_hash": ev.prev_hash,
        "event_hash": ev.event_hash,
    }


def _in_scope_clause(scope: Scope, served_run: int | None) -> tuple[str, dict]:
    """Events are scoped by the work they concern, judged against the served run."""
    if scope.national:
        return "", {}
    conds, p = scope._conds("_sc.")
    return (
        " AND EXISTS (SELECT 1 FROM served_work _sc WHERE _sc.run_id = :srun AND _sc.work_key = ce.work_key "
        f"AND {' AND '.join(conds)})",
        {**p, "srun": served_run},
    )


def trail(session: Session, scope: Scope, served_run: int | None, limit: int) -> list[dict]:
    clause, p = _in_scope_clause(scope, served_run)
    ids = (
        session.execute(
            text(
                "SELECT ce.id FROM case_event ce WHERE ce.event_type = 'investigation_decision'"
                f"{clause} ORDER BY ce.id DESC LIMIT :lim"
            ),
            {**p, "lim": limit},
        )
        .scalars()
        .all()
    )
    evs = (
        session.execute(select(CaseEvent).where(CaseEvent.id.in_(ids)).order_by(CaseEvent.id)).scalars().all()
    )
    return [entry(e) for e in evs]


def verify_chain(session: Session) -> dict:
    prev, n, first_break = None, 0, None
    for ev in session.execute(
        select(CaseEvent).order_by(CaseEvent.id).execution_options(populate_existing=True)
    ).scalars():
        n += 1
        if ev.event_hash is None:
            continue  # pre-Phase-13 row (none exist); not part of the chain
        if ev.prev_hash != prev or compute_hash(prev, ev) != ev.event_hash:
            first_break = first_break or ev.id
        prev = ev.event_hash
    return {"events": n, "valid": first_break is None, "first_break_event_id": first_break, "head": prev}


def export_case(session: Session, work_key: str) -> dict:
    evs = (
        session.execute(select(CaseEvent).where(CaseEvent.work_key == work_key).order_by(CaseEvent.id))
        .scalars()
        .all()
    )
    return {
        "record_id": work_key,
        "events": [entry(e) for e in evs],
        "chain": verify_chain(session),
        "hash_rule": "event_hash = sha256(prev_hash + canonical JSON of the event; "
        "see app/audit/case_log.py)",
    }
