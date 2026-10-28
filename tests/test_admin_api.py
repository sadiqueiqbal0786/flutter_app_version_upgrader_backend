from tests.conftest import add_release, create_app_fixture


async def test_admin_requires_key(client):
    assert (await client.get("/v1/admin/apps")).status_code == 401
    assert (await client.get("/v1/admin/apps", headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_bearer_auth_works(client):
    from tests.conftest import ADMIN_KEY

    assert (await client.get("/v1/admin/apps", headers={"Authorization": f"Bearer {ADMIN_KEY}"})).status_code == 200


async def test_app_crud(client, admin):
    created = await create_app_fixture(client, admin)
    assert created["app_id"] == "com.example.app"
    assert (await client.post("/v1/admin/apps", json={"app_id": "com.example.app", "name": "dup"}, headers=admin)).status_code == 409
    r = await client.patch("/v1/admin/apps/com.example.app", json={"name": "Renamed"}, headers=admin)
    assert r.json()["name"] == "Renamed"
    assert [a["app_id"] for a in (await client.get("/v1/admin/apps", headers=admin)).json()] == ["com.example.app"]
    assert (await client.delete("/v1/admin/apps/com.example.app", headers=admin)).status_code == 204
    assert (await client.get("/v1/admin/apps/com.example.app", headers=admin)).status_code == 404


async def test_release_crud_and_validation(client, admin):
    await create_app_fixture(client, admin)
    rel = await add_release(client, admin, "com.example.app", "2.7.0", 270)
    assert rel["source"] == "manual" and rel["status"] == "published"
    bad = await client.post("/v1/admin/apps/com.example.app/releases", json={"platform": "android", "version": "not-a-version"}, headers=admin)
    assert bad.status_code == 422
    dup = await client.post("/v1/admin/apps/com.example.app/releases", json={"platform": "android", "version": "2.7.0", "build": 270}, headers=admin)
    assert dup.status_code == 409
    rows = (await client.get("/v1/admin/apps/com.example.app/releases", params={"platform": "android"}, headers=admin)).json()
    assert len(rows) == 1
    assert (await client.delete(f"/v1/admin/apps/com.example.app/releases/{rel['id']}", headers=admin)).status_code == 204


async def test_policy_lifecycle(client, admin):
    await create_app_fixture(client, admin)
    rows = (await client.get("/v1/admin/apps/com.example.app/policies", headers=admin)).json()
    assert len(rows) == 8 and all(p["is_default"] for p in rows)

    bad = await client.put("/v1/admin/apps/com.example.app/policies/android/production", json={"mode": "custom"}, headers=admin)
    assert bad.status_code == 422
    bad = await client.put("/v1/admin/apps/com.example.app/policies/android/production", json={"mode": "age_based"}, headers=admin)
    assert bad.status_code == 422

    ok = await client.put(
        "/v1/admin/apps/com.example.app/policies/android/production",
        json={"mode": "age_based", "max_age_days": 90, "force_update": False, "update_message": "hi"},
        headers=admin,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["mode"] == "age_based" and body["is_default"] is False and body["force_update"] is False

    got = (await client.get("/v1/admin/apps/com.example.app/policies/android/production", headers=admin)).json()
    assert got["max_age_days"] == 90

    assert (await client.delete("/v1/admin/apps/com.example.app/policies/android/production", headers=admin)).status_code == 204
    got = (await client.get("/v1/admin/apps/com.example.app/policies/android/production", headers=admin)).json()
    assert got["is_default"] is True


async def test_preview_matches_public_endpoint(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "1.2.3", 5)
    pv = (await client.get("/v1/admin/apps/com.example.app/preview/android/production", headers=admin)).json()
    pub = (await client.get("/v1/version/check", params={"app_id": "com.example.app", "platform": "android"})).json()
    assert pv["latest"] == pub["latest"] and pv["minimum"] == pub["minimum"]


async def test_sync_without_providers_is_503(client, admin):
    await create_app_fixture(client, admin)
    r = await client.post("/v1/admin/releases/sync", headers=admin)
    assert r.status_code == 503


async def test_installed_versions_are_recorded_and_flagged(client, admin):
    await create_app_fixture(client, admin)
    await add_release(client, admin, "com.example.app", "2.7.0", 270)
    await add_release(client, admin, "com.example.app", "2.5.0", 250)
    await client.put(
        "/v1/admin/apps/com.example.app/policies/android/production",
        json={"mode": "custom", "custom_minimum_version": "2.5.0", "custom_minimum_build": 250},
        headers=admin,
    )
    q = {"app_id": "com.example.app", "platform": "android", "channel": "production"}
    for v, b in [("2.4.0", 240), ("2.4.0", 240), ("2.5.0", 250), ("2.7.0", 270), ("garbage", 1)]:
        assert (await client.get("/v1/version/check", params={**q, "version": v, "build": b})).status_code == 200
    assert (await client.get("/v1/version/check", params=q)).status_code == 200  # no version → not recorded

    rows = (await client.get("/v1/admin/apps/com.example.app/installs", params={"platform": "android", "channel": "production"}, headers=admin)).json()
    by = {(r["version"], r["build"]): r for r in rows}
    assert set(by) == {("2.4.0", 240), ("2.5.0", 250), ("2.7.0", 270)}
    assert by[("2.4.0", 240)]["checks"] == 2
    assert by[("2.4.0", 240)]["below_minimum"] is True and by[("2.4.0", 240)]["is_latest"] is False
    assert by[("2.5.0", 250)]["below_minimum"] is False and by[("2.5.0", 250)]["is_latest"] is False
    assert by[("2.7.0", 270)]["is_latest"] is True
