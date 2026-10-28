"""Standalone worker entry point: ``python -m app.workers.main``.

Use this (and set VM_SYNC_INTERVAL_SECONDS=0 on the API) when you run more
than one API replica, so only one process polls the stores.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.cache import build_cache
from app.config import get_settings
from app.db import Database
from app.stores.registry import build_providers
from app.workers.scheduler import SyncScheduler


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    db = Database(settings.database_url)
    cache = await build_cache(settings)
    scheduler = SyncScheduler(db, build_providers(settings), cache, settings.sync_interval_seconds)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    scheduler.start()
    await stop.wait()
    await scheduler.stop()
    await cache.close()
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
