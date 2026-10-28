"""Store adapter interface. Each adapter answers one question: which
releases are currently published for an app on a platform and channel."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models import App, Channel, Platform, ReleaseSource, ReleaseStatus


@dataclass(frozen=True)
class DiscoveredRelease:
    version: str
    build: int | None
    status: ReleaseStatus
    source: ReleaseSource
    published_at: datetime | None = None  # None → first-seen time is used
    store_url: str | None = None
    release_notes: str | None = None


class StoreError(Exception):
    """Raised when a store API call fails. Existing data is kept."""


class StoreProvider(Protocol):
    source: ReleaseSource

    def supports(self, platform: Platform, channel: Channel) -> bool: ...

    async def get_releases(self, app: App, platform: Platform, channel: Channel) -> list[DiscoveredRelease]: ...
