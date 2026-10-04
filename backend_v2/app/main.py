"""
MPLADS Sentinel v2 -- FastAPI application.

Since the Phase 12 cutover every route under app/api is DB-backed: it reads
the published run's serving tables (served_work, map_build/map_work,
graph_*, entity_metric) and keeps the shapes in docs/frontend_contract.md.
No endpoint computes a score; scores come only from a full analysis run.

Phase 13 (BLUEPRINT.md §11, docs/security.md): token login and role/scope
checks (app/auth), scope applied inside every data query, rate limits on
login/copilot/search, an explicit CORS allowlist, and a hash-chained case
log with the actor always taken from the token.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .api.router import router
from .core.config import get_settings

log = logging.getLogger(__name__)


def _warm() -> None:
    from .db.session import get_session_factory
    from .serving import service

    try:
        with get_session_factory()() as db:
            s = service.served(db)
            for house in (None, "LS", "RS"):
                service.summary(db, s, house)
                service.analytics(db, s, house)
            service.data_health(db, s)
    except Exception:  # a cold cache is only slower, never wrong
        log.exception("cache warm-up failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .auth import tokens

    get_settings().check_configuration()  # no DATABASE_URL outside development = refuse to start
    tokens.check_configuration()  # no signing key outside development = refuse to start
    if get_settings().warm_cache:
        threading.Thread(target=_warm, name="warm-cache", daemon=True).start()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="MPLADS Sentinel v2",
    description="Published-run, DB-backed API (docs/frontend_contract.md).",
    version="2.0.0-phase13",
)

# BLUEPRINT.md §11 "Explicit origin allowlist; no wildcard combined with
# credentials". Origins come from CORS_ORIGINS (+ optional CORS_ORIGIN_REGEX
# for preview deployments, Phase 14). Tokens travel in the Authorization
# header, not cookies, so credentials are off.
_settings = get_settings()
if "*" in _settings.cors_origin_list:
    raise RuntimeError("CORS_ORIGINS must list explicit origins, never '*'")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_origin_regex=_settings.cors_origin_regex or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match"],
    # Phase 9: map run/date/precision labels travel in headers on array responses.
    expose_headers=[
        "X-Map-Run-Id",
        "X-Map-Data-As-Of",
        "X-Map-Is-Latest",
        "X-Map-Status",
        "X-Location-Precision",
        "X-Location-Precision-Note",
        "X-Total-Count",
        "X-Returned-Count",
        "X-Map-Works-Cap",
        "X-Sampled",
        "X-Min-Works-For-Rate",
        "X-Geo-Version",
        "ETag",
    ],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # Phase 9: the ~2 MB versioned GeoJSON

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
