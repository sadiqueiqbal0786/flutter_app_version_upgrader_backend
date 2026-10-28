"""Records the installed version each check reports, for the dashboard's
"installed versions" view. Best effort: a failure here never affects the
response the client already received."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, Channel, InstallStat, Platform
from app.versioning import SemVer

log = logging.getLogger(__name__)


async def record_install(session: AsyncSession, app_id: str, platform: Platform, channel: Channel, version: str, build: int | None) -> None:
    if SemVer.try_parse(version) is None:
        return
    app_pk = (await session.execute(select(App.id).where(App.app_id == app_id))).scalar_one_or_none()
    if app_pk is None:
        return
    now = datetime.now(timezone.utc)
    where = (
        InstallStat.app_pk == app_pk,
        InstallStat.platform == platform,
        InstallStat.channel == channel,
        InstallStat.version == version,
        InstallStat.build == build,
    )
    row = (await session.execute(select(InstallStat).where(*where))).scalar_one_or_none()
    if row is None:
        session.add(InstallStat(app_pk=app_pk, platform=platform, channel=channel, version=version, build=build, checks=1, first_seen_at=now, last_seen_at=now))
        try:
            await session.commit()
            return
        except IntegrityError:
            await session.rollback()
            row = (await session.execute(select(InstallStat).where(*where))).scalar_one_or_none()
            if row is None:
                return
    row.checks += 1
    row.last_seen_at = now
    await session.commit()


async def record_install_background(fastapi_app, app_id: str, platform: Platform, channel: Channel, version: str, build: int | None) -> None:
    try:
        async with fastapi_app.state.db.sessions() as session:
            await record_install(session, app_id, platform, channel, version, build)
    except Exception:  # noqa: BLE001
        log.exception("could not record install stat")
