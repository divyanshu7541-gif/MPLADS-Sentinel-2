"""
Infra-only health endpoint. Not part of the frontend contract (docs/frontend_contract.md)
-- it exists purely so the Docker healthcheck for the `api` service has
something to poll.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "MPLADS Sentinel v2", "version": "2.0.0-phase13"}
