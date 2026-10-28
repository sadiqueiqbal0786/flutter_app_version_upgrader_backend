"""Admin control plane: apps, releases, policies, sync."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.auth import require_admin
from app.models import App, Channel, InstallStat, Platform, Policy, Release, ReleaseSource
from app.policy.engine import NoPublishedRelease, VersionRef, evaluate
from app.releases.service import sync_all, sync_app
from app.schemas import (
    AppCreate,
    AppOut,
    AppUpdate,
    InstallStatOut,
    PolicyIn,
    PolicyOutAdmin,
    ReleaseCreate,
    ReleaseOut,
    SyncResult,
    VersionCheckResponse,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _app_or_404(session: AsyncSession, app_id: str) -> App:
    app = (await session.execute(select(App).where(App.app_id == app_id))).scalar_one_or_none()
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown app {app_id!r}")
    return app


# --- apps -------------------------------------------------------------------


@router.get("/apps", response_model=list[AppOut])
async def list_apps(session: AsyncSession = Depends(get_session)) -> list[App]:
    return list((await session.execute(select(App).order_by(App.name))).scalars().all())


@router.post("/apps", response_model=AppOut, status_code=status.HTTP_201_CREATED)
async def create_app(body: AppCreate, session: AsyncSession = Depends(get_session)) -> App:
    app = App(**body.model_dump())
    session.add(app)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"app {body.app_id!r} already exists") from None
    await session.refresh(app)
    return app


@router.get("/apps/{app_id}", response_model=AppOut)
async def get_app(app_id: str, session: AsyncSession = Depends(get_session)) -> App:
    return await _app_or_404(session, app_id)


@router.patch("/apps/{app_id}", response_model=AppOut)
async def update_app(app_id: str, body: AppUpdate, request: Request, session: AsyncSession = Depends(get_session)) -> App:
    app = await _app_or_404(session, app_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(app, k, v)
    await session.commit()
    await session.refresh(app)
    await request.app.state.cache.invalidate_app(app_id)
    return app


@router.delete("/apps/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(app_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> None:
    app = await _app_or_404(session, app_id)
    await session.delete(app)
    await session.commit()
    await request.app.state.cache.invalidate_app(app_id)


# --- releases ---------------------------------------------------------------------


@router.get("/apps/{app_id}/releases", response_model=list[ReleaseOut])
async def list_releases(
    app_id: str,
    platform: Platform | None = Query(default=None),
    channel: Channel | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[Release]:
    app = await _app_or_404(session, app_id)
    q = select(Release).where(Release.app_pk == app.id)
    if platform:
        q = q.where(Release.platform == platform)
    if channel:
        q = q.where(Release.channel == channel)
    q = q.order_by(Release.published_at.desc(), Release.id.desc())
    return list((await session.execute(q)).scalars().all())


@router.post("/apps/{app_id}/releases", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
async def create_release(app_id: str, body: ReleaseCreate, request: Request, session: AsyncSession = Depends(get_session)) -> Release:
    """Manually record a release (apps distributed outside the stores, or
    before store credentials are configured)."""
    app = await _app_or_404(session, app_id)
    now = datetime.now(timezone.utc)
    rel = Release(
        app_pk=app.id,
        source=ReleaseSource.manual,
        published_at=body.published_at or now,
        discovered_at=now,
        last_seen_at=now,
        **body.model_dump(exclude={"published_at"}),
    )
    session.add(rel)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "release already exists") from None
    await session.refresh(rel)
    await request.app.state.cache.invalidate(app_id, body.platform, body.channel)
    return rel


@router.delete("/apps/{app_id}/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(app_id: str, release_id: int, request: Request, session: AsyncSession = Depends(get_session)) -> None:
    app = await _app_or_404(session, app_id)
    rel = (await session.execute(select(Release).where(Release.id == release_id, Release.app_pk == app.id))).scalar_one_or_none()
    if rel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown release")
    await session.execute(delete(Release).where(Release.id == release_id))
    await session.commit()
    await request.app.state.cache.invalidate(app_id, rel.platform, rel.channel)


# --- policies ------------------------------------------------------------------------


def _policy_out(p: Policy, is_default: bool) -> PolicyOutAdmin:
    return PolicyOutAdmin(
        platform=p.platform,
        channel=p.channel,
        enabled=p.enabled,
        mode=p.mode,
        custom_minimum_version=p.custom_minimum_version,
        custom_minimum_build=p.custom_minimum_build,
        max_age_days=p.max_age_days,
        force_update=p.force_update,
        update_message=p.update_message,
        update_url=p.update_url,
        recommended_version=p.recommended_version,
        recommended_build=p.recommended_build,
        updated_at=p.updated_at,
        is_default=is_default,
    )


@router.get("/apps/{app_id}/policies", response_model=list[PolicyOutAdmin])
async def list_policies(app_id: str, session: AsyncSession = Depends(get_session)) -> list[PolicyOutAdmin]:
    app = await _app_or_404(session, app_id)
    rows = (await session.execute(select(Policy).where(Policy.app_pk == app.id))).scalars().all()
    by_key = {(p.platform, p.channel): p for p in rows}
    out: list[PolicyOutAdmin] = []
    for platform in Platform:
        for channel in Channel:
            p = by_key.get((platform, channel))
            out.append(_policy_out(p, False) if p else _policy_out(Policy.default(app.id, platform, channel), True))
    return out


@router.get("/apps/{app_id}/policies/{platform}/{channel}", response_model=PolicyOutAdmin)
async def get_policy(app_id: str, platform: Platform, channel: Channel, session: AsyncSession = Depends(get_session)) -> PolicyOutAdmin:
    app = await _app_or_404(session, app_id)
    p = (
        await session.execute(select(Policy).where(Policy.app_pk == app.id, Policy.platform == platform, Policy.channel == channel))
    ).scalar_one_or_none()
    return _policy_out(p, False) if p else _policy_out(Policy.default(app.id, platform, channel), True)


@router.put("/apps/{app_id}/policies/{platform}/{channel}", response_model=PolicyOutAdmin)
async def put_policy(
    app_id: str, platform: Platform, channel: Channel, body: PolicyIn, request: Request, session: AsyncSession = Depends(get_session)
) -> PolicyOutAdmin:
    try:
        body.validate_mode()
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    app = await _app_or_404(session, app_id)
    p = (
        await session.execute(select(Policy).where(Policy.app_pk == app.id, Policy.platform == platform, Policy.channel == channel))
    ).scalar_one_or_none()
    if p is None:
        p = Policy(app_pk=app.id, platform=platform, channel=channel)
        session.add(p)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    await request.app.state.cache.invalidate(app_id, platform, channel)
    return _policy_out(p, False)


@router.delete("/apps/{app_id}/policies/{platform}/{channel}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_policy(app_id: str, platform: Platform, channel: Channel, request: Request, session: AsyncSession = Depends(get_session)) -> None:
    app = await _app_or_404(session, app_id)
    await session.execute(delete(Policy).where(Policy.app_pk == app.id, Policy.platform == platform, Policy.channel == channel))
    await session.commit()
    await request.app.state.cache.invalidate(app_id, platform, channel)


# --- installed versions ------------------------------------------------------------


@router.get("/apps/{app_id}/installs", response_model=list[InstallStatOut])
async def list_installs(
    app_id: str, platform: Platform, channel: Channel, request: Request, session: AsyncSession = Depends(get_session)
) -> list[InstallStatOut]:
    """Installed versions reported by checks, newest activity first, each
    flagged against the current latest / minimum decision."""
    app = await _app_or_404(session, app_id)
    rows = (
        await session.execute(
            select(InstallStat)
            .where(InstallStat.app_pk == app.id, InstallStat.platform == platform, InstallStat.channel == channel)
            .order_by(InstallStat.last_seen_at.desc())
        )
    ).scalars().all()
    latest: VersionRef | None = None
    minimum: VersionRef | None = None
    try:
        releases = (
            await session.execute(select(Release).where(Release.app_pk == app.id, Release.platform == platform, Release.channel == channel))
        ).scalars().all()
        policy = (
            await session.execute(select(Policy).where(Policy.app_pk == app.id, Policy.platform == platform, Policy.channel == channel))
        ).scalar_one_or_none() or Policy.default(app.id, platform, channel)
        d = evaluate(list(releases), policy, include_rollouts=request.app.state.settings.include_in_progress_rollouts)
        latest, minimum = d.latest, d.minimum
    except NoPublishedRelease:
        pass
    out: list[InstallStatOut] = []
    for r in rows:
        ref = VersionRef(r.version, r.build)
        out.append(
            InstallStatOut(
                version=r.version,
                build=r.build,
                checks=r.checks,
                first_seen_at=r.first_seen_at,
                last_seen_at=r.last_seen_at,
                is_latest=latest is not None and not (ref < latest) and not (latest < ref),
                below_minimum=minimum is not None and ref < minimum,
            )
        )
    return out


# --- preview + sync ------------------------------------------------------------------


@router.get("/apps/{app_id}/preview/{platform}/{channel}", response_model=VersionCheckResponse)
async def preview(app_id: str, platform: Platform, channel: Channel, request: Request, session: AsyncSession = Depends(get_session)) -> VersionCheckResponse:
    """What a client on this platform/channel would receive right now."""
    from app.api.v1.version import build_response

    return await build_response(session, request.app.state.settings, app_id, platform, channel)


@router.post("/releases/sync", response_model=list[SyncResult])
async def sync_releases(
    request: Request,
    background: BackgroundTasks,
    app_id: str | None = Query(default=None),
    wait: bool = Query(default=True, description="Run inline and return results, or schedule in the background"),
    session: AsyncSession = Depends(get_session),
) -> list[SyncResult]:
    providers = request.app.state.providers
    cache = request.app.state.cache
    if not providers:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no store credentials configured; add releases manually or configure VM_GOOGLE_PLAY_* / VM_APP_STORE_CONNECT_*")
    if app_id:
        app = await _app_or_404(session, app_id)
        if wait:
            return await sync_app(session, app, providers, cache)
        background.add_task(_background_sync, request.app, app_id)
        return []
    if wait:
        return await sync_all(session, providers, cache)
    background.add_task(_background_sync, request.app, None)
    return []


async def _background_sync(fastapi_app, app_id: str | None) -> None:
    async with fastapi_app.state.db.sessions() as session:
        if app_id:
            app = await _app_or_404(session, app_id)
            await sync_app(session, app, fastapi_app.state.providers, fastapi_app.state.cache)
        else:
            await sync_all(session, fastapi_app.state.providers, fastapi_app.state.cache)
