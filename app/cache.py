"""Response cache for /v1/version/check. Redis when configured, otherwise a
process-local TTL dict (fine for a single replica and for tests)."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.models import Channel, Platform


def cache_key(app_id: str, platform: Platform, channel: Channel) -> str:
    return f"vm:check:{app_id}:{platform.value}:{channel.value}"


class ResponseCache:
    kind = "memory"

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._data: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        hit = self._data.get(key)
        if not hit:
            return None
        expires, value = hit
        if time.monotonic() >= expires:
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str) -> None:
        if self._ttl <= 0:
            return
        self._data[key] = (time.monotonic() + self._ttl, value)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def invalidate(self, app_id: str, platform: Platform, channel: Channel) -> None:
        await self.delete(cache_key(app_id, platform, channel))

    async def invalidate_app(self, app_id: str) -> None:
        for p in Platform:
            for c in Channel:
                await self.invalidate(app_id, p, c)

    async def incr_window(self, key: str, window_seconds: int) -> int:
        """Fixed-window counter used by the rate limiter."""
        now = time.monotonic()
        hit = self._data.get(key)
        if hit and now < hit[0]:
            count = int(hit[1]) + 1
            self._data[key] = (hit[0], str(count))
            return count
        self._data[key] = (now + window_seconds, "1")
        return 1

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()


class RedisCache(ResponseCache):
    kind = "redis"

    def __init__(self, ttl: int, client: Any) -> None:
        super().__init__(ttl)
        self._redis = client

    async def get(self, key: str) -> str | None:
        v = await self._redis.get(key)
        return v.decode() if isinstance(v, bytes) else v

    async def set(self, key: str, value: str) -> None:
        if self._ttl > 0:
            await self._redis.set(key, value, ex=self._ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def incr_window(self, key: str, window_seconds: int) -> int:
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, window_seconds)
        return int(count)

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        await self._redis.aclose()


async def build_cache(settings: Settings) -> ResponseCache:
    if settings.redis_url:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url)
        return RedisCache(settings.response_cache_ttl_seconds, client)
    return ResponseCache(settings.response_cache_ttl_seconds)
