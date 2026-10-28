"""Database schema: applications, discovered releases and admin policies."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, enum.Enum):
    android = "android"
    ios = "ios"


class Channel(str, enum.Enum):
    production = "production"
    beta = "beta"
    alpha = "alpha"
    internal = "internal"


class PolicyMode(str, enum.Enum):
    auto_latest = "auto_latest"
    custom = "custom"
    age_based = "age_based"


class ReleaseStatus(str, enum.Enum):
    published = "published"
    rollout = "rollout"  # staged rollout in progress
    withdrawn = "withdrawn"


class ReleaseSource(str, enum.Enum):
    google_play = "google_play"
    app_store = "app_store"
    testflight = "testflight"
    manual = "manual"


class App(Base):
    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    android_package_name: Mapped[str | None] = mapped_column(String(200))
    ios_bundle_id: Mapped[str | None] = mapped_column(String(200))
    apple_app_id: Mapped[str | None] = mapped_column(String(50))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    releases: Mapped[list["Release"]] = relationship(back_populates="app", cascade="all, delete-orphan")
    policies: Mapped[list["Policy"]] = relationship(back_populates="app", cascade="all, delete-orphan")
    install_stats: Mapped[list["InstallStat"]] = relationship(back_populates="app", cascade="all, delete-orphan")

    def play_store_url(self) -> str | None:
        if not self.android_package_name:
            return None
        return f"https://play.google.com/store/apps/details?id={self.android_package_name}"

    def app_store_url(self) -> str | None:
        if not self.apple_app_id:
            return None
        return f"https://apps.apple.com/app/id{self.apple_app_id}"

    def testflight_url(self) -> str | None:
        """Deep link that opens the TestFlight app on this app's page, where
        beta testers install the newest build. Used for every non-production
        iOS channel unless a policy overrides it (e.g. with a public link)."""
        if not self.apple_app_id:
            return None
        return f"itms-beta://beta.itunes.apple.com/v1/app/{self.apple_app_id}"

    def default_update_url(self, platform: "Platform", channel: "Channel") -> str | None:
        if platform is Platform.android:
            return self.play_store_url()
        return self.app_store_url() if channel is Channel.production else self.testflight_url()


class Release(Base):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("app_pk", "platform", "channel", "version", "build", name="uq_release"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_pk: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False, length=20))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=20))
    version: Mapped[str] = mapped_column(String(50))
    build: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ReleaseStatus] = mapped_column(Enum(ReleaseStatus, native_enum=False, length=20), default=ReleaseStatus.published)
    source: Mapped[ReleaseSource] = mapped_column(Enum(ReleaseSource, native_enum=False, length=20))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    store_url: Mapped[str | None] = mapped_column(String(500))
    release_notes: Mapped[str | None] = mapped_column(Text)

    app: Mapped[App] = relationship(back_populates="releases")


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("app_pk", "platform", "channel", name="uq_policy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_pk: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False, length=20))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[PolicyMode] = mapped_column(Enum(PolicyMode, native_enum=False, length=20), default=PolicyMode.auto_latest)
    custom_minimum_version: Mapped[str | None] = mapped_column(String(50))
    custom_minimum_build: Mapped[int | None] = mapped_column(Integer)
    max_age_days: Mapped[int | None] = mapped_column(Integer)
    force_update: Mapped[bool] = mapped_column(Boolean, default=True)
    update_message: Mapped[str | None] = mapped_column(Text)
    update_url: Mapped[str | None] = mapped_column(String(500))
    recommended_version: Mapped[str | None] = mapped_column(String(50))
    recommended_build: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    app: Mapped[App] = relationship(back_populates="policies")

    @staticmethod
    def default(app_pk: int, platform: Platform, channel: Channel) -> "Policy":
        return Policy(app_pk=app_pk, platform=platform, channel=channel, enabled=True, mode=PolicyMode.auto_latest, force_update=True)


class InstallStat(Base):
    """Which versions are actually installed, as reported by version checks.

    One row per (app, platform, channel, version, build). Only version
    strings are stored: no device or user identifiers."""

    __tablename__ = "install_stats"
    __table_args__ = (UniqueConstraint("app_pk", "platform", "channel", "version", "build", name="uq_install_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_pk: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False, length=20))
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=20))
    version: Mapped[str] = mapped_column(String(50))
    build: Mapped[int | None] = mapped_column(Integer)
    checks: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    app: Mapped[App] = relationship(back_populates="install_stats")
