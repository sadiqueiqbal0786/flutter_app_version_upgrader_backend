"""GET /v1/version/check — the one endpoint the Flutter plugin calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.auth import require_client
from app.cache import cache_key
from app.models import App, Channel, Platform, Policy, Release
from app.policy.engine import NoPublishedRelease, evaluate
from app.ratelimit import enforce_rate_limit
from app.releases.install_stats import record_install_background
from app.schemas import AppRef, PolicyOut, UpdateOut, VersionCheckResponse, VersionPair
from app.signing import sign

router = APIRouter(prefix="/v1/version", tags=["version"])


async def build_response(session: AsyncSession, settings, app_id: str, platform: Platform, channel: Channel) -> VersionCheckResponse:
    app = (await session.execute(select(App).where(App.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown app {app_id!r}")
    releases = (
        await session.execute(select(Release).where(Release.app_pk == app.id, Release.platform == platform, Release.channel == channel))
    ).scalars().all()
    policy = (
        await session.execute(select(Policy).where(Policy.app_pk == app.id, Policy.platform == platform, Policy.channel == channel))
    ).scalar_one_or_none() or Policy.default(app.id, platform, channel)
    try:
        decision = evaluate(list(releases), policy, include_rollouts=settings.include_in_progress_rollouts)
    except NoPublishedRelease:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no published release for {app_id} {platform.value}/{channel.value}") from None

    url = policy.update_url or decision.latest_release.store_url or app.default_update_url(platform, channel)
    return VersionCheckResponse(
        app=AppRef(id=app.app_id, platform=platform, channel=channel),
        latest=VersionPair(version=decision.latest.version, build=decision.latest.build),
        minimum=VersionPair(version=decision.minimum.version, build=decision.minimum.build),
        recommended=VersionPair(version=decision.recommended.version, build=decision.recommended.build) if decision.recommended else None,
        policy=PolicyOut(mode=policy.mode, force_update=policy.force_update, enabled=policy.enabled, update_message=policy.update_message),
        update=UpdateOut(url=url, release_notes=decision.latest_release.release_notes),
        server_time=datetime.now(timezone.utc),
    )


@router.get("/check", response_model=VersionCheckResponse, dependencies=[Depends(require_client), Depends(enforce_rate_limit)])
async def check(
    request: Request,
    background: BackgroundTasks,
    app_id: str = Query(min_length=1),
    platform: Platform = Query(),
    channel: Channel = Query(default=Channel.production),
    version: str | None = Query(default=None, description="Installed version; recorded for the installed-versions view"),
    build: int | None = Query(default=None, description="Installed build; recorded with the version"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = request.app.state.settings
    cache = request.app.state.cache
    key = cache_key(app_id, platform, channel)
    if version:
        background.add_task(record_install_background, request.app, app_id, platform, channel, version, build)

    body = await cache.get(key)
    if body is None:
        payload = await build_response(session, settings, app_id, platform, channel)
        body = payload.model_dump_json()
        await cache.set(key, body)
    else:
        # Keep server_time honest even when the rest is cached.
        data = json.loads(body)
        data["server_time"] = datetime.now(timezone.utc).isoformat()
        body = json.dumps(data, separators=(",", ":"))

    raw = body.encode()
    headers = {"Cache-Control": "no-store"}
    if settings.response_signing_secret:
        ts, sig = sign(settings.response_signing_secret, raw)
        headers["X-Signature-Timestamp"] = ts
        headers["X-Signature"] = sig
    return Response(content=raw, media_type="application/json", headers=headers)
