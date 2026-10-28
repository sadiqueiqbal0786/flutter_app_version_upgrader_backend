import json

from app.signing import verify
from tests.conftest import ADMIN_KEY, add_release, create_app_fixture, make_settings


async def test_check_returns_contract(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "2.7.0", 270, release_notes="Bug fixes.")
    await add_release(client, admin, "com.example.app", "2.5.0", 250)
    await client.put(
        "/v1/admin/apps/com.example.app/policies/android/production",
        json={"mode": "custom", "custom_minimum_version": "2.5.0", "custom_minimum_build": 250, "force_update": True, "update_message": "Please update."},
        headers=admin,
    )

    r = await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android", "channel": "production", "version": "2.4.1", "build": 241})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema_version"] == 1
    assert body["app"] == {"id": "com.example.app", "platform": "android", "channel": "production"}
    assert body["latest"] == {"version": "2.7.0", "build": 270}
    assert body["minimum"] == {"version": "2.5.0", "build": 250}
    assert body["recommended"] is None
    assert body["policy"] == {"mode": "custom", "force_update": True, "enabled": True, "update_message": "Please update."}
    assert body["update"]["url"] == "https://play.google.com/store/apps/details?id=com.example.app"
    assert body["update"]["release_notes"] == "Bug fixes."
    assert "server_time" in body
    assert r.headers["cache-control"] == "no-store"


async def test_default_policy_is_auto_latest(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "2.9.0", 290)
    await add_release(client, admin, "com.example.app", "2.10.0", 2100)
    r = await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android"})
    body = r.json()
    assert body["latest"]["version"] == "2.10.0"
    assert body["minimum"] == body["latest"]
    assert body["policy"]["mode"] == "auto_latest"


async def test_channels_are_isolated(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "2.7.0", 270)
    await add_release(client, admin, "com.example.app", "2.8.0-beta.2", 280, channel="beta")
    prod = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android", "channel": "production"})).json()
    beta = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android", "channel": "beta"})).json()
    assert prod["latest"]["version"] == "2.7.0"
    assert beta["latest"]["version"] == "2.8.0-beta.2"


async def test_ios_uses_app_store_url(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "1.0.0", 7, platform="ios")
    body = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "ios"})).json()
    assert body["update"]["url"] == "https://apps.apple.com/app/id123"


async def test_ios_beta_uses_testflight_deep_link(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "1.1.0", 9, platform="ios", channel="beta")
    body = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "ios", "channel": "beta"})).json()
    assert body["update"]["url"] == "itms-beta://beta.itunes.apple.com/v1/app/123"
    # A policy override (e.g. a TestFlight public link) wins.
    await client.put(
        "/v1/admin/apps/com.example.app/policies/ios/beta",
        json={"mode": "auto_latest", "update_url": "https://testflight.apple.com/join/abc123"},
        headers=admin,
    )
    body = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "ios", "channel": "beta"})).json()
    assert body["update"]["url"] == "https://testflight.apple.com/join/abc123"


async def test_unknown_app_and_no_release_are_404(client, admin):
    r = await client.get("/v1/version/check", params={"app_id": "nope", "platform": "android"})
    assert r.status_code == 404
    await create_app_fixture(client, admin)
    r = await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android"})
    assert r.status_code == 404


async def test_invalid_platform_is_422(client):
    r = await client.get("/v1/version/check", params={"app_id": "x", "platform": "windows"})
    assert r.status_code == 422


async def test_policy_change_invalidates_cache():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app(make_settings(response_cache_ttl_seconds=300), create_tables=True, start_scheduler=False)
    admin = {"X-Admin-Key": ADMIN_KEY}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await create_app_fixture(client, admin)
            await add_release(client, admin, "com.example.app", "2.7.0", 270)
            await add_release(client, admin, "com.example.app", "2.5.0", 250)
            q = {"app_id": "com.example.app", "platform": "android"}
            assert (await client.get("/v1/version/check", params=q)).json()["minimum"]["version"] == "2.7.0"
            await client.put("/v1/admin/apps/com.example.app/policies/android/production", json={"mode": "custom", "custom_minimum_version": "2.5.0"}, headers=admin)
            assert (await client.get("/v1/version/check", params=q)).json()["minimum"]["version"] == "2.5.0"
            # cached path still serves a fresh server_time
            a = (await client.get("/v1/version/check", params=q)).json()
            assert a["server_time"]


async def test_client_key_enforced_when_configured():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app(make_settings(client_api_keys="k1, k2"), create_tables=True, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = {"X-Admin-Key": ADMIN_KEY}
            await create_app_fixture(client, admin)
            await add_release(client, admin, "com.example.app", "1.0.0", 1)
            q = {"app_id": "com.example.app", "platform": "android"}
            assert (await client.get("/v1/version/check", params=q)).status_code == 401
            assert (await client.get("/v1/version/check", params=q, headers={"X-Api-Key": "bad"})).status_code == 401
            assert (await client.get("/v1/version/check", params=q, headers={"X-Api-Key": "k2"})).status_code == 200


async def test_signed_responses():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app(make_settings(response_signing_secret="s3cret"), create_tables=True, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = {"X-Admin-Key": ADMIN_KEY}
            await create_app_fixture(client, admin)
            await add_release(client, admin, "com.example.app", "1.0.0", 1)
            r = await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android"})
            assert verify("s3cret", r.content, r.headers["x-signature-timestamp"], r.headers["x-signature"])
            assert not verify("wrong", r.content, r.headers["x-signature-timestamp"], r.headers["x-signature"])
            json.loads(r.content)  # body is the exact signed bytes


async def test_rate_limit():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app(make_settings(rate_limit_per_minute=2), create_tables=True, start_scheduler=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin = {"X-Admin-Key": ADMIN_KEY}
            await create_app_fixture(client, admin)
            await add_release(client, admin, "com.example.app", "1.0.0", 1)
            q = {"app_id": "com.example.app", "platform": "android"}
            assert (await client.get("/v1/version/check", params=q)).status_code == 200
            assert (await client.get("/v1/version/check", params=q)).status_code == 200
            assert (await client.get("/v1/version/check", params=q)).status_code == 429


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] is True and body["cache"].startswith("memory")
    assert (await client.get("/ready")).status_code == 200
    assert (await client.get("/admin/")).status_code == 200
