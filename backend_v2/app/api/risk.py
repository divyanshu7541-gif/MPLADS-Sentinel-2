"""
docs/frontend_contract.md #25-#32 (risk/signals/evidence/context/
investigations/recalculate), read from served_work + signal_result for the
served run.

Path params are named `project_id` to match the literal frontend contract;
they are record ids (work_key).

Route ORDER matters: the literal `/api/risk/top` and `/api/risk/summary`
must be registered before `/api/risk/{project_id}`. The OLD router had them
in the wrong order, so /api/risk/top was swallowed as project "top" (404).

Recalculate (#32) cannot recompute one work: every v2 score is a
population-relative, leave-one-out figure produced by a full, checksummed
analysis run (SENTINEL_REBUILD_PLAN_v2.md §5: "flag for next run"). It
records a `recalculate_requested` case_event and returns the STORED
result, labelled as such. It never writes risk_result.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth.deps import CASE_WRITERS, Principal, reader, require_roles
from ..db.session import get_db
from ..serving import service
from ..serving.public_mask import public_view
from .params import guarded, not_found

router = APIRouter()


def _dossier(db: Session, project_id: str, what: str, p: Principal):
    d = guarded(lambda: service.dossier(db, service.served(db, p.scope), project_id), what)
    if d is None:
        not_found("Project", project_id)
    return public_view(d, p.authenticated)  # anonymous: beneficiary details masked


@router.get("/api/risk/top")
def get_risk_top(
    limit: int = Query(20, ge=1, le=service.MAX_TOP),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    def run():
        # Mask before the 100-character cut, so a name is never half-shown.
        rows = public_view(service.top_records(db, service.served(db, p.scope), limit), p.authenticated)
        return [
            {
                "record_id": r["record_id"],
                "mp": r["mp_name"],
                "constituency": r["constituency"],
                "state": r["state"],
                "description": r["description"][:100],
                "amount": r["amount_numeric"],
                "risk_score": None if r["risk_score"] is None else round(r["risk_score"] * 100, 1),
                "risk_level": r["risk_level"],
                "confidence": r["confidence_percent"],
                "active_signal_count": r["active_signal_count"],
            }
            for r in rows
        ]

    return guarded(run, "top risk")


@router.get("/api/risk/summary")
def get_risk_summary(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    def run():
        sm = service.summary(db, service.served(db, p.scope), None)
        return {
            k: sm[k]
            for k in (
                "total_records",
                "risk_distribution",
                "average_risk",
                "average_confidence",
                "critical_count",
                "high_count",
                "model_version",
            )
        }

    return guarded(run, "risk summary")


@router.get("/api/risk/{project_id}")
def get_risk(project_id: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    d = _dossier(db, project_id, "risk", p)
    return {
        "project": service.source_record(d["row"]),
        "risk_assessment": d["ra"],
        "context": d["ra"]["context"],
        "evidence_items": d["items"],
        "investigation_recommendation": service.recommendation(d["row"], d["items"]),
    }


@router.get("/api/signals/{project_id}")
def get_signals(project_id: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    d = _dossier(db, project_id, "signals", p)
    return {
        "project_id": project_id,
        "signals": d["items"],
        "not_evaluated": service.not_evaluated_signals(d["sig_rows"]),
    }


@router.get("/api/evidence/{project_id}")
def get_evidence(project_id: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    d = _dossier(db, project_id, "evidence", p)
    return {
        "project_id": project_id,
        "evidence_items": d["items"],
        "evidence_chain": service.evidence_chain(d["row"], d["items"], d["ra"]),
    }


@router.get("/api/context/{project_id}")
def get_context(project_id: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    d = _dossier(db, project_id, "context", p)
    return {"project_id": project_id, **d["ra"]["context"]}


@router.get("/api/investigations")
def get_investigations(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    rows = guarded(lambda: service.top_records(db, service.served(db, p.scope), 50), "investigations")
    return public_view(rows, p.authenticated)


@router.post("/api/risk/recalculate/{project_id}")
def recalculate_risk(
    project_id: str, db: Session = Depends(get_db), p: Principal = Depends(require_roles(*CASE_WRITERS))
):
    def run():
        s = service.served(db, p.scope)
        d = service.dossier(db, s, project_id)
        if d is None:
            return None
        ev = service.request_recalculation(db, s, project_id, p)
        db.commit()
        return {
            "record_id": project_id,
            "risk_result": {**d["ra"], "status": "flagged_for_next_run", "recomputed": False},
            "evidence_items": d["items"],
            "request": {
                "event_id": ev.id,
                "note": ev.note,
                "actor": ev.actor,
                "actor_is_placeholder": ev.actor_is_placeholder,
            },
        }

    out = guarded(run, "recalculation request")
    if out is None:
        not_found("Project", project_id)
    return out
