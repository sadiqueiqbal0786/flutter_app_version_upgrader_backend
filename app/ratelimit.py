"""Fixed-window per-client rate limit for the public endpoint."""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def client_identity(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(request: Request) -> None:
    settings = request.app.state.settings
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    cache = request.app.state.cache
    key = f"vm:rl:{client_identity(request)}"
    count = await cache.incr_window(key, 60)
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded", headers={"Retry-After": "60"})
