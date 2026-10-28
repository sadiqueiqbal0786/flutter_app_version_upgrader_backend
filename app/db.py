"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def create_all(self) -> None:
        """Dev/test convenience. Production uses Alembic migrations."""
        from app import models  # noqa: F401  (register tables)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as s:
            yield s

    async def dispose(self) -> None:
        await self.engine.dispose()
