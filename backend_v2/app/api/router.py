from fastapi import APIRouter

from . import audit, auth, chat, dashboard, entities, health, maps, performance, queue, reference, risk

router = APIRouter()
for sub in (health, auth, dashboard, queue, reference, maps, risk, performance, chat, entities, audit):
    router.include_router(sub.router)
