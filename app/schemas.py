"""Pydantic models for the public and admin APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Channel, Platform, PolicyMode, ReleaseSource, ReleaseStatus
from app.versioning import SemVer


def _validate_version(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    SemVer.parse(v)  # raises on failure
    return v.strip()


# --- public /v1/version/check ------------------------------------------------


class VersionPair(BaseModel):
    version: str
    build: int | None = None


class PolicyOut(BaseModel):
    mode: PolicyMode
    force_update: bool
    enabled: bool
    update_message: str | None = None


class UpdateOut(BaseModel):
    url: str | None = None
    release_notes: str | None = None


class AppRef(BaseModel):
    id: str
    platform: Platform
    channel: Channel


class VersionCheckResponse(BaseModel):
    schema_version: int = 1
    app: AppRef
    latest: VersionPair
    minimum: VersionPair
    recommended: VersionPair | None = None
    policy: PolicyOut
    update: UpdateOut
    server_time: datetime


# --- admin ----------------------------------------------------------------------


class AppCreate(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    android_package_name: str | None = None
    ios_bundle_id: str | None = None
    apple_app_id: str | None = None
    sync_enabled: bool = True


class AppUpdate(BaseModel):
    name: str | None = None
    android_package_name: str | None = None
    ios_bundle_id: str | None = None
    apple_app_id: str | None = None
    sync_enabled: bool | None = None


class AppOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    name: str
    android_package_name: str | None
    ios_bundle_id: str | None
    apple_app_id: str | None
    sync_enabled: bool
    last_sync_at: datetime | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime


class ReleaseCreate(BaseModel):
    platform: Platform
    channel: Channel = Channel.production
    version: str
    build: int | None = None
    status: ReleaseStatus = ReleaseStatus.published
    published_at: datetime | None = None
    store_url: str | None = None
    release_notes: str | None = None

    _v = field_validator("version")(_validate_version)


class ReleaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: Platform
    channel: Channel
    version: str
    build: int | None
    status: ReleaseStatus
    source: ReleaseSource
    published_at: datetime
    discovered_at: datetime
    last_seen_at: datetime
    store_url: str | None
    release_notes: str | None


class PolicyIn(BaseModel):
    enabled: bool = True
    mode: PolicyMode = PolicyMode.auto_latest
    custom_minimum_version: str | None = None
    custom_minimum_build: int | None = None
    max_age_days: int | None = Field(default=None, ge=1)
    force_update: bool = True
    update_message: str | None = None
    update_url: str | None = None
    recommended_version: str | None = None
    recommended_build: int | None = None

    _v1 = field_validator("custom_minimum_version")(_validate_version)
    _v2 = field_validator("recommended_version")(_validate_version)

    def validate_mode(self) -> None:
        if self.mode is PolicyMode.custom and not self.custom_minimum_version:
            raise ValueError("custom mode requires custom_minimum_version")
        if self.mode is PolicyMode.age_based and not self.max_age_days:
            raise ValueError("age_based mode requires max_age_days")


class PolicyOutAdmin(PolicyIn):
    model_config = ConfigDict(from_attributes=True)

    platform: Platform
    channel: Channel
    updated_at: datetime | None = None
    is_default: bool = False


class InstallStatOut(BaseModel):
    version: str
    build: int | None
    checks: int
    first_seen_at: datetime
    last_seen_at: datetime
    is_latest: bool
    below_minimum: bool


class SyncResult(BaseModel):
    app_id: str
    platform: Platform
    channel: Channel
    discovered: int
    error: str | None = None


class HealthOut(BaseModel):
    status: str
    database: bool
    cache: str
    google_play: bool
    app_store_connect: bool
