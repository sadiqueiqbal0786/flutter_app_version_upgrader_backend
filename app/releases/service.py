"""Release synchronisation: asks each store provider for the live releases
and upserts them. A failing store call never discards existing data; it is
recorded on the app as ``last_sync_error`` instead."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import ResponseCache
from app.models import App, Channel, Platform, Release, ReleaseStatus
from app.schemas import SyncResult
from app.stores.base import DiscoveredRelease, StoreError, StoreProvider

log = logging.getLogger(__name__)


async def upsert_releases(session: AsyncSession, app: App, platform: Platform, channel: Channel, found: list[DiscoveredRelease]) -> int:
    now = datetime.now(timezone.utc)
    existing = (
        await session.execute(select(Release).where(Release.app_pk == app.id, Release.platform == platform, Release.channel == channel))
    ).scalars().all()
    by_key = {(r.version, r.build): r for r in existing}
    new = 0
    seen: set[tuple[str, int | None]] = set()
    for d in found:
        key = (d.version, d.build)
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            session.add(
                Release(
                    app_pk=app.id,
                    platform=platform,
                    channel=channel,
                    version=d.version,
                    build=d.build,
                    status=d.status,
                    source=d.source,
                    published_at=d.published_at or now,
                    discovered_at=now,
                    last_seen_at=now,
                    store_url=d.store_url,
                    release_notes=d.release_notes,
                )
            )
            new += 1
        else:
            row.status = d.status
            row.last_seen_at = now
            if d.store_url:
                row.store_url = d.store_url
            if d.release_notes:
                row.release_notes = d.release_notes
    # A release that the store no longer lists on this track was replaced or
    # withdrawn. Keep the row (history) but stop treating it as live, unless
    # it was entered manually.
    for row in existing:
        if (row.version, row.build) not in seen and row.source.value != "manual" and row.status is not ReleaseStatus.withdrawn:
            row.status = ReleaseStatus.withdrawn
    return new


async def sync_app(
    session: AsyncSession,
    app: App,
    providers: list[StoreProvider],
    cache: ResponseCache | None = None,
    *,
    platforms: list[Platform] | None = None,
    channels: list[Channel] | None = None,
) -> list[SyncResult]:
    results: list[SyncResult] = []
    errors: list[str] = []
    for platform in platforms or list(Platform):
        if platform is Platform.android and not app.android_package_name:
            continue
        if platform is Platform.ios and not app.apple_app_id:
            continue
        for channel in channels or list(Channel):
            provider = next((p for p in providers if p.supports(platform, channel)), None)
            if provider is None:
                continue
            try:
                found = await provider.get_releases(app, platform, channel)
                new = await upsert_releases(session, app, platform, channel, found)
                results.append(SyncResult(app_id=app.app_id, platform=platform, channel=channel, discovered=new))
                if cache is not None:
                    await cache.invalidate(app.app_id, platform, channel)
            except StoreError as e:
                log.warning("sync %s %s/%s failed: %s", app.app_id, platform.value, channel.value, e)
                errors.append(f"{platform.value}/{channel.value}: {e}")
                results.append(SyncResult(app_id=app.app_id, platform=platform, channel=channel, discovered=0, error=str(e)))
    app.last_sync_at = datetime.now(timezone.utc)
    app.last_sync_error = "; ".join(errors) if errors else None
    await session.commit()
    return results


async def sync_all(session: AsyncSession, providers: list[StoreProvider], cache: ResponseCache | None = None) -> list[SyncResult]:
    apps = (await session.execute(select(App).where(App.sync_enabled.is_(True)))).scalars().all()
    out: list[SyncResult] = []
    for app in apps:
        out.extend(await sync_app(session, app, providers, cache))
    return out
