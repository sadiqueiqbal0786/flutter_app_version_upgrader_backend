"""Periodic store sync. Runs inside the API process by default (simple
deployments) or as its own process via ``python -m app.workers.main``."""

from __future__ import annotations

import asyncio
import logging

from app.cache import ResponseCache
from app.db import Database
from app.releases.service import sync_all
from app.stores.base import StoreProvider

log = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, db: Database, providers: list[StoreProvider], cache: ResponseCache, interval_seconds: int, run_immediately: bool = True) -> None:
        self._db = db
        self._providers = providers
        self._cache = cache
        self._interval = max(60, interval_seconds)
        self._run_immediately = run_immediately
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run_once(self) -> None:
        if not self._providers:
            log.info("no store providers configured; skipping sync")
            return
        async with self._db.sessions() as session:
            results = await sync_all(session, self._providers, self._cache)
        log.info("sync finished: %d results, %d errors", len(results), sum(1 for r in results if r.error))

    async def _loop(self) -> None:
        if not self._run_immediately:
            await self._sleep()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("sync run failed")
            await self._sleep()

    async def _sleep(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
        except asyncio.TimeoutError:
            pass

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="store-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
