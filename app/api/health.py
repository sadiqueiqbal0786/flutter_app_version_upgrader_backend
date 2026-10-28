from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    settings = request.app.state.settings
    db_ok = True
    try:
        async with request.app.state.db.sessions() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    cache = request.app.state.cache
    cache_ok = await cache.ping()
    return HealthOut(
        status="ok" if db_ok and cache_ok else "degraded",
        database=db_ok,
        cache=f"{cache.kind}:{'ok' if cache_ok else 'down'}",
        google_play=settings.google_play_configured,
        app_store_connect=settings.app_store_connect_configured,
    )


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str]:
    try:
        async with request.app.state.db.sessions() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        response.status_code = 503
        return {"status": "not ready"}
    return {"status": "ready"}
