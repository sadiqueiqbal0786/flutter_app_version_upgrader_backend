from datetime import datetime, timezone

import pytest

from app.models import App, Channel, Platform, ReleaseSource, ReleaseStatus
from app.releases.service import sync_app, upsert_releases
from app.stores.app_store import parse_app_store_versions, parse_testflight_builds
from app.stores.base import DiscoveredRelease, StoreError
from app.stores.google_play import parse_track, version_from_release_name


def test_parse_play_track():
    track = {
        "track": "production",
        "releases": [
            {"name": "2.8.0", "status": "inProgress", "versionCodes": ["280"], "userFraction": 0.1},
            {"name": "2.7.0", "status": "completed", "versionCodes": ["270", "271"], "releaseNotes": [{"language": "en-US", "text": "Fixes"}]},
            {"name": "2.6.0", "status": "halted", "versionCodes": ["260"]},
            {"name": "draft", "status": "draft"},
        ],
    }
    out = parse_track(track, "https://play.google.com/store/apps/details?id=x")
    assert [(r.version, r.build, r.status) for r in out] == [
        ("2.8.0", 280, ReleaseStatus.rollout),
        ("2.7.0", 271, ReleaseStatus.published),
    ]
    assert out[1].release_notes == "Fixes" and out[1].source is ReleaseSource.google_play


@pytest.mark.parametrize(
    "name,expected",
    [
        ("1.0.37", "1.0.37"),
        ("54 (1.0.37)", "1.0.37"),  # Play's default release name
        ("Spring release 2.3.0", "2.3.0"),
        ("v2.4.0-rc.1 hotfix", "2.4.0-rc.1"),
        ("Big launch", None),
        ("", None),
        (None, None),
    ],
)
def test_version_from_play_release_name(name, expected):
    assert version_from_release_name(name) == expected


def test_parse_play_track_default_names_and_unparsable():
    track = {"releases": [
        {"name": "54 (1.0.37)", "status": "completed", "versionCodes": ["54"]},
        {"name": "Mystery", "status": "completed", "versionCodes": ["55"]},
    ]}
    out = parse_track(track, None)
    assert [(r.version, r.build) for r in out] == [("1.0.37", 54)]


def test_parse_app_store_versions():
    payload = {
        "data": [
            {
                "type": "appStoreVersions",
                "id": "v1",
                "attributes": {"versionString": "2.7.0", "createdDate": "2026-10-20T10:00:00Z"},
                "relationships": {"build": {"data": {"type": "builds", "id": "b1"}}},
            },
            {"type": "appStoreVersions", "id": "v2", "attributes": {"versionString": "2.6.0"}, "relationships": {"build": {"data": None}}},
        ],
        "included": [{"type": "builds", "id": "b1", "attributes": {"version": "270"}}],
    }
    out = parse_app_store_versions(payload, "https://apps.apple.com/app/id1")
    assert [(r.version, r.build) for r in out] == [("2.7.0", 270), ("2.6.0", None)]
    assert out[0].published_at == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    assert out[0].source is ReleaseSource.app_store


def test_parse_testflight_builds():
    payload = {
        "data": [
            {"type": "builds", "id": "b1", "attributes": {"version": "281", "uploadedDate": "2026-10-25T08:00:00Z", "expired": False},
             "relationships": {"preReleaseVersion": {"data": {"type": "preReleaseVersions", "id": "p1"}}}},
            {"type": "builds", "id": "b2", "attributes": {"version": "280", "expired": True},
             "relationships": {"preReleaseVersion": {"data": {"type": "preReleaseVersions", "id": "p1"}}}},
        ],
        "included": [{"type": "preReleaseVersions", "id": "p1", "attributes": {"version": "2.8.0"}}],
    }
    out = parse_testflight_builds(payload, None)
    assert [(r.version, r.build) for r in out] == [("2.8.0", 281)]
    assert out[0].source is ReleaseSource.testflight


class FakeProvider:
    source = ReleaseSource.google_play

    def __init__(self, releases: list[DiscoveredRelease] | None = None, fail: bool = False) -> None:
        self.releases = releases or []
        self.fail = fail
        self.calls: list[tuple[Platform, Channel]] = []

    def supports(self, platform: Platform, channel: Channel) -> bool:
        return platform is Platform.android

    async def get_releases(self, app: App, platform: Platform, channel: Channel) -> list[DiscoveredRelease]:
        self.calls.append((platform, channel))
        if self.fail:
            raise StoreError("store down")
        return self.releases


def d(version: str, build: int, status: ReleaseStatus = ReleaseStatus.published) -> DiscoveredRelease:
    return DiscoveredRelease(version=version, build=build, status=status, source=ReleaseSource.google_play)


@pytest.fixture
async def session_and_app(client, admin):
    r = await client.post("/v1/admin/apps", json={"app_id": "a", "name": "A", "android_package_name": "a"}, headers=admin)
    assert r.status_code == 201
    db = client.app.state.db
    async with db.sessions() as s:
        from sqlalchemy import select

        app = (await s.execute(select(App).where(App.app_id == "a"))).scalar_one()
        yield s, app


async def test_sync_upserts_and_withdraws(session_and_app):
    s, app = session_and_app
    p = FakeProvider([d("2.7.0", 270), d("2.6.0", 260)])
    results = await sync_app(s, app, [p])
    assert {(r.channel, r.discovered) for r in results if r.platform is Platform.android} == {(Channel.production, 2), (Channel.beta, 2), (Channel.alpha, 2), (Channel.internal, 2)}
    assert app.last_sync_error is None and app.last_sync_at is not None

    # Next sync: 2.6.0 vanished from the track, 2.8.0 appeared.
    p.releases = [d("2.7.0", 270), d("2.8.0", 280)]
    new = await upsert_releases(s, app, Platform.android, Channel.production, p.releases)
    await s.commit()
    assert new == 1
    from sqlalchemy import select

    from app.models import Release

    rows = (await s.execute(select(Release).where(Release.app_pk == app.id, Release.channel == Channel.production))).scalars().all()
    by_version = {r.version: r.status for r in rows}
    assert by_version == {"2.7.0": ReleaseStatus.published, "2.8.0": ReleaseStatus.published, "2.6.0": ReleaseStatus.withdrawn}


async def test_sync_failure_keeps_existing_data(session_and_app):
    s, app = session_and_app
    await sync_app(s, app, [FakeProvider([d("2.7.0", 270)])])
    results = await sync_app(s, app, [FakeProvider(fail=True)])
    assert all(r.error for r in results)
    assert "store down" in (app.last_sync_error or "")
    from sqlalchemy import select

    from app.models import Release

    rows = (await s.execute(select(Release).where(Release.app_pk == app.id, Release.channel == Channel.production))).scalars().all()
    assert [(r.version, r.status) for r in rows] == [("2.7.0", ReleaseStatus.published)]


async def test_sync_skips_platforms_without_identifiers(session_and_app):
    s, app = session_and_app
    p = FakeProvider([d("1.0.0", 1)])
    await sync_app(s, app, [p])
    assert all(platform is Platform.android for platform, _ in p.calls)  # no apple_app_id → iOS skipped


def test_app_store_provider_supports_only_production_and_beta():
    from app.config import Settings
    from app.stores.app_store import AppStoreConnectProvider

    p = AppStoreConnectProvider(Settings(_env_file=None, admin_api_key="x" * 16))  # type: ignore[arg-type]
    assert p.supports(Platform.ios, Channel.production)
    assert p.supports(Platform.ios, Channel.beta)
    assert not p.supports(Platform.ios, Channel.alpha)
    assert not p.supports(Platform.ios, Channel.internal)
    assert not p.supports(Platform.android, Channel.production)
