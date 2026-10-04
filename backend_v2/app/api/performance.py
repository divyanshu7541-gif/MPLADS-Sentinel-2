"""
docs/frontend_contract.md #4, #16-#19 (/api/analytics, mp-performance,
constituency-performance, mp-comparison, constituency-comparison), read
from served_work for the served run.

MP performance is current-tenure only: served_work holds the run's
snapshot (Snapshot A -- sitting 18th Lok Sabha and sitting Rajya Sabha
members). Prior-cycle works (prior_cycle_work) are not in it and are never
merged into a profile. Stage/amount figures cover all of the MP's works;
risk figures cover the scored ones (recommended-only works are not scored).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.deps import Principal, reader
from ..db.session import get_db
from ..serving import service
from ..serving.public_mask import public_view
from .params import House, guarded

router = APIRouter()


@router.get("/api/analytics")
def get_analytics(house: House = None, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return guarded(lambda: service.analytics(db, service.served(db, p.scope), house), "analytics")


@router.get("/api/mp-performance/{mp_name}")
def get_mp_performance(mp_name: str, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    out = guarded(lambda: service.mp_profile(db, service.served(db, p.scope), mp_name), "MP performance")
    if out is None:
        raise HTTPException(status_code=404, detail=f"No records found for MP: {mp_name}")
    return public_view(out, p.authenticated)


@router.get("/api/constituency-performance/{constituency_name}")
def get_constituency_performance(
    constituency_name: str, db: Session = Depends(get_db), p: Principal = Depends(reader)
):
    out = guarded(
        lambda: service.constituency_profile(db, service.served(db, p.scope), constituency_name),
        "constituency performance",
    )
    if out is None:
        raise HTTPException(status_code=404, detail=f"No records found for constituency: {constituency_name}")
    return public_view(out, p.authenticated)


def _names(raw: str, kind: str, plural: str) -> list[str]:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if len(names) < 2:
        raise HTTPException(status_code=400, detail=f"Provide at least 2 {kind} names separated by commas.")
    if len(names) > 4:
        raise HTTPException(status_code=400, detail=f"Maximum 4 {plural} for comparison.")
    return names


@router.get("/api/mp-comparison")
def get_mp_comparison(
    mps: str = Query(..., description="Comma-separated MP names"),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    names = _names(mps, "MP", "MPs")
    return public_view(
        guarded(lambda: service.comparison(db, service.served(db, p.scope), names, "MP"), "MP comparison"),
        p.authenticated,
    )


@router.get("/api/constituency-comparison")
def get_constituency_comparison(
    constituencies: str = Query(..., description="Comma-separated constituency names"),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    names = _names(constituencies, "constituency", "constituencies")
    return public_view(
        guarded(
            lambda: service.comparison(db, service.served(db, p.scope), names, "Constituency"),
            "constituency comparison",
        ),
        p.authenticated,
    )
