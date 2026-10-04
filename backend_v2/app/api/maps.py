"""
docs/frontend_contract.md #9, #10, #11, #22, #23, #24.

Phase 9: the six map endpoints are DB-backed (app/geo/service.py) and read
only the per-run map tables built by scripts/run_geo.py for the published
run. graph-data (#12) moved to app/api/entities.py in Phase 10 -- it reads
the offline graph tables, not the map tables, so it no longer belongs here.

Response shapes are the contract's; every Phase 9 field is additive. Where
the contract shape is a bare array (map-data, map-works), run/date/precision
labels travel in response headers (X-Map-Run-Id, X-Map-Data-As-Of,
X-Map-Is-Latest, X-Location-Precision-Note). A database failure is a 503,
never an empty 200.
"""

from collections.abc import Callable
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth import ratelimit
from ..auth.deps import Principal, reader
from ..db.session import get_db
from ..geo import service
from ..serving.public_mask import public_view
from .params import House

router = APIRouter()

Tier = Optional[Literal["CRITICAL", "HIGH", "MODERATE", "LOW", "NOT_EVALUATED"]]


def _guard(fn: Callable):
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail="map data temporarily unavailable") from e


def _json(body, served: service.Served, extra: dict | None = None) -> JSONResponse:
    return JSONResponse(body, headers=served.headers() | (extra or {}))


def _search_limit(request: Request, search: Optional[str], p: Principal) -> None:
    if search and search.strip():
        ratelimit.limit("search", request, f"user:{p.user_id}" if p.authenticated else "")


@router.get("/api/map-data")
def get_map_data(
    request: Request,
    state: Optional[str] = None,
    priority: Tier = None,
    risk_level: Tier = None,
    stage: Optional[str] = None,
    search: Optional[str] = None,
    house: House = None,
    level: Optional[Literal["constituency", "state"]] = Query(
        None,
        description="Optional (additive). 'state': one row per state where the works are located, "
        "for the Rajya Sabha map. Omitted or 'constituency': the constituency rows, unchanged.",
    ),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    _search_limit(request, search, p)

    def run():
        q = service.clean_search(search)
        s = service.served(db, p.scope)
        if level == "state":
            rows = service.map_state_data(
                db,
                s,
                state=state or None,
                tier=priority or risk_level,
                stage=stage or None,
                search=q,
                house=house,
                public=not p.authenticated,
            )
            return _json(rows, s, {"X-Min-Works-For-Rate": str(service.MIN_WORKS_FOR_RATE)})
        rows = service.map_data(
            db,
            s,
            state=state or None,
            tier=priority or risk_level,
            stage=stage or None,
            search=q,
            house=house,
            public=not p.authenticated,
        )
        return _json(rows, s, {"X-Min-Works-For-Rate": str(service.MIN_WORKS_FOR_RATE)})

    return _guard(run)


@router.get("/api/map-works")
def get_map_works(
    request: Request,
    state: Optional[str] = None,
    constituency: Optional[str] = None,
    priority: Tier = None,
    risk_level: Tier = None,
    stage: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(service.MAP_WORKS_CAP, ge=1, description=f"capped at {service.MAP_WORKS_CAP}"),
    house: House = None,
    redelimited: bool = Query(
        False,
        description="additive: the state's re-delimited seats' works (no stored point; needs state)",
    ),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    _search_limit(request, search, p)
    if redelimited and not state:
        raise HTTPException(status_code=422, detail="redelimited=true needs a state")

    def run():
        q = service.clean_search(search)
        s = service.served(db, p.scope)
        rows, info = service.map_works(
            db,
            s,
            state=state or None,
            constituency=constituency or None,
            tier=priority or risk_level,
            stage=stage or None,
            search=q,
            house=house,
            limit=limit,
            public=not p.authenticated,
            redelimited=redelimited,
        )
        headers = {
            "X-Location-Precision": service.LOCATION_PRECISION,
            "X-Location-Precision-Note": service.PRECISION_NOTE,
            "X-Total-Count": str(info["total"]),
            "X-Returned-Count": str(info["returned"]),
            "X-Map-Works-Cap": str(info["cap"]),
            "X-Sampled": "true" if info["sampled"] else "false",
        }
        if redelimited:
            headers["X-Placement"] = service.REDELIMITED_PLACEMENT
        return _json(public_view(rows, p.authenticated), s, headers)

    return _guard(run)


@router.get("/api/geo/areas/{area_key}/works")
def get_area_works(
    request: Request,
    area_key: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    priority: Tier = None,
    stage: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    """Phase 9 drill-down (additive, not in the frontend contract): every work
    in one constituency ('pc:..') or district ('dist:..') area, paginated,
    including works without a marker."""

    _search_limit(request, search, p)

    def run():
        q = service.clean_search(search)
        s = service.served(db, p.scope)
        body = service.area_works(
            db,
            s,
            area_key,
            page=page,
            page_size=page_size,
            tier=priority,
            stage=stage or None,
            search=q,
            public=not p.authenticated,
        )
        if body is None:
            raise HTTPException(status_code=404, detail="unknown area or map not built")
        return _json(public_view(body, p.authenticated), s)

    return _guard(run)


@router.get("/api/map-filters")
def get_map_filters(house: House = None, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    def run():
        s = service.served(db, p.scope)
        return _json(service.map_filters(db, s, house), s)

    return _guard(run)


@router.get("/api/geojson")
def get_geojson(request: Request, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    def run():
        version, body = service.geojson_bytes(db)
        etag = f'"{version}"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=3600", "X-Geo-Version": version}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    return _guard(run)


@router.get("/api/boundary-outlines")
def get_boundary_outlines(request: Request, db: Session = Depends(get_db), p: Principal = Depends(reader)):
    """Additive (2026-10-03): display-only outlines, versioned like /api/geojson.
    Fails soft: the Map loads without them and says so, so a failed build is
    a 503, never a 500."""

    def run():
        try:
            version, body = service.outlines_bytes(db)
        except SQLAlchemyError:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail="boundary outlines temporarily unavailable") from e
        etag = f'"{version}"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=3600", "X-Outlines-Version": version}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    return _guard(run)


@router.get("/api/geographic-coverage")
def get_geographic_coverage(db: Session = Depends(get_db), p: Principal = Depends(reader)):
    def run():
        s = service.served(db, p.scope)
        return _json(service.geographic_coverage(db, s), s)

    return _guard(run)


@router.get("/api/constituency-intelligence")
def get_constituency_intelligence(
    state: str = Query(..., description="State name"),
    constituency: str = Query(..., description="Constituency name"),
    db: Session = Depends(get_db),
    p: Principal = Depends(reader),
):
    def run():
        s = service.served(db, p.scope)
        body = service.constituency_intelligence(db, s, state, constituency)
        return _json(public_view(body, p.authenticated), s)

    return _guard(run)
