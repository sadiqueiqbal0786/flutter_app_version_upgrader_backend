"""Runtime configuration. Every value comes from the environment (prefix VM_)
or a `.env` file; nothing secret is checked in."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VM_", env_file=".env", extra="ignore")

    # --- core -------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./version_manager.db"
    redis_url: str | None = None
    log_level: str = "INFO"
    cors_origins: str = ""  # comma separated

    # --- auth ---------------------------------------------------------------
    admin_api_key: str = Field(min_length=16)
    client_api_keys: str = ""  # comma separated; empty = no client auth
    response_signing_secret: str | None = None

    # --- behaviour ------------------------------------------------------------
    sync_interval_seconds: int = 900
    response_cache_ttl_seconds: int = 60
    rate_limit_per_minute: int = 120
    include_in_progress_rollouts: bool = False
    sync_on_startup: bool = True

    # --- Google Play -----------------------------------------------------------
    google_play_service_account_file: str | None = None
    google_play_service_account_json: str | None = None

    # --- App Store Connect -----------------------------------------------------
    app_store_connect_issuer_id: str | None = None
    app_store_connect_key_id: str | None = None
    app_store_connect_private_key_file: str | None = None
    app_store_connect_private_key: str | None = None  # PEM contents

    @property
    def client_api_key_list(self) -> list[str]:
        return [k.strip() for k in self.client_api_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_play_configured(self) -> bool:
        return bool(self.google_play_service_account_file or self.google_play_service_account_json)

    @property
    def app_store_connect_configured(self) -> bool:
        return bool(
            self.app_store_connect_issuer_id
            and self.app_store_connect_key_id
            and (self.app_store_connect_private_key_file or self.app_store_connect_private_key)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
