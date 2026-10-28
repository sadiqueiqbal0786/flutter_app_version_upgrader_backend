"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.api import health
from app.api.v1 import admin, version
from app.cache import build_cache
from app.config import Settings, get_settings
from app.db import Database
from app.stores.registry import build_providers
from app.workers.scheduler import SyncScheduler

ADMIN_UI = Path(__file__).parent / "admin_ui" / "index.html"


def create_app(settings: Settings | None = None, *, create_tables: bool = False, start_scheduler: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = Database(settings.database_url)
        if create_tables:
            await app.state.db.create_all()
        app.state.cache = await build_cache(settings)
        app.state.providers = build_providers(settings)
        app.state.scheduler = None
        if start_scheduler and settings.sync_interval_seconds > 0 and app.state.providers:
            app.state.scheduler = SyncScheduler(
                app.state.db, app.state.providers, app.state.cache, settings.sync_interval_seconds, run_immediately=settings.sync_on_startup
            )
            app.state.scheduler.start()
        try:
            yield
        finally:
            if app.state.scheduler:
                await app.state.scheduler.stop()
            await app.state.cache.close()
            await app.state.db.dispose()

    app = FastAPI(title="Version Manager", version="0.1.0", lifespan=lifespan, docs_url="/docs", redoc_url=None)
    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])

    app.include_router(health.router)
    app.include_router(version.router)
    app.include_router(admin.router)

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    async def admin_ui() -> FileResponse:
        return FileResponse(ADMIN_UI)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/admin/")

    return app

