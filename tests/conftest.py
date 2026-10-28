from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "test-admin-key-0123456789"


def make_settings(**overrides) -> Settings:
    base = dict(
        admin_api_key=ADMIN_KEY,
        database_url="sqlite+aiosqlite://",
        response_cache_ttl_seconds=0,
        rate_limit_per_minute=0,
        sync_on_startup=False,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def client():
    app = create_app(make_settings(), create_tables=True, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


@pytest.fixture
def admin() -> dict[str, str]:
    return {"X-Admin-Key": ADMIN_KEY}


async def create_app_fixture(client: AsyncClient, admin: dict[str, str], app_id: str = "com.example.app") -> dict:
    r = await client.post(
        "/v1/admin/apps",
        json={"app_id": app_id, "name": "Example", "android_package_name": app_id, "ios_bundle_id": app_id, "apple_app_id": "123"},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def add_release(client: AsyncClient, admin: dict[str, str], app_id: str, version: str, build: int | None, platform: str = "android", channel: str = "production", **extra) -> dict:
    r = await client.post(
        f"/v1/admin/apps/{app_id}/releases",
        json={"platform": platform, "channel": channel, "version": version, "build": build, **extra},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()
