"""
docs/frontend_contract.md #5, #6, #7, #8, #15
(/api/data-health, /api/stages, /api/constituencies, /api/states, /api/mps),
read from served_work / provenance / compliance_result for the served run.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth.deps import Principal, reader
from ..db.session import get_db
from ..serving import service
from .params import guarded

router = APIRouter()


@router.get("/api/data-health")
def get_data_health(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return guarded(lambda: service.data_health(db, service.served(db, p.scope)), "data health")


@router.get("/api/stages")
def get_stages(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return {"stages": guarded(lambda: service.distinct(db, service.served(db, p.scope), "stage"), "stages")}


@router.get("/api/constituencies")
def get_constituencies(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return {
        "constituencies": guarded(
            lambda: service.constituencies(db, service.served(db, p.scope)), "constituencies"
        )
    }


@router.get("/api/states")
def get_states(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return {"states": guarded(lambda: service.distinct(db, service.served(db, p.scope), "state"), "states")}


@router.get("/api/mps")
def get_mps(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return {"mps": guarded(lambda: service.distinct(db, service.served(db, p.scope), "mp"), "MPs")}
