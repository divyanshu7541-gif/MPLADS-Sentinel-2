"""
Query parameters and helpers shared across routers.

`house` (Phase 3) is an optional filter on which works an endpoint
reports: "LS" = 18th Lok Sabha, "RS" = sitting Rajya Sabha. Omitted means
both houses. Any other value is rejected with 422. It filters the rows
shown and never changes a score or how a peer baseline is built
(BLUEPRINT.md §6: peer groups mix houses). Live since the Phase 12 cutover.
"""

from typing import Callable, Literal, Optional

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

House = Optional[Literal["LS", "RS"]]


def guarded(fn: Callable, what: str = "data"):
    """Run a DB read: bad input -> 422, database failure -> 503 (never a
    half-built 200)."""
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"{what} temporarily unavailable") from e


def not_found(kind: str, key: str):
    raise HTTPException(status_code=404, detail=f"{kind} {key} not found.")
