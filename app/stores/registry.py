"""Builds the list of configured store providers."""

from __future__ import annotations

from app.config import Settings
from app.stores.app_store import AppStoreConnectProvider
from app.stores.base import StoreProvider
from app.stores.google_play import GooglePlayProvider


def build_providers(settings: Settings) -> list[StoreProvider]:
    providers: list[StoreProvider] = []
    if settings.google_play_configured:
        providers.append(GooglePlayProvider(settings))
    if settings.app_store_connect_configured:
        providers.append(AppStoreConnectProvider(settings))
    return providers
