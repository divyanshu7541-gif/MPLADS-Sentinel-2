"""
docs/frontend_contract.md #1 (/api/summary), read from served_work for the
served run (app/serving/service.py). Risk figures cover scored works only.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth.deps import Principal, reader
from ..db.session import get_db
from ..serving import service
from ..serving.public_mask import public_view
from .params import House, guarded

router = APIRouter()


@router.get("/api/summary")
def get_summary(house: House = None, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    return public_view(
        guarded(lambda: service.summary(db, service.served(db, p.scope), house), "summary"), p.authenticated
    )
