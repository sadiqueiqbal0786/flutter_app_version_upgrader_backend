"""Turns discovered releases plus an admin policy into latest / minimum /
recommended versions. Pure functions: no I/O, easy to test."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import Policy, PolicyMode, Release, ReleaseStatus
from app.versioning import SemVer, compare_release, release_sort_key


@dataclass(frozen=True)
class VersionRef:
    version: str
    build: int | None

    @classmethod
    def of(cls, r: Release) -> "VersionRef":
        return cls(r.version, r.build)

    def __lt__(self, other: "VersionRef") -> bool:
        return compare_release(self.version, self.build, other.version, other.build) < 0


@dataclass(frozen=True)
class Decision:
    latest: VersionRef
    minimum: VersionRef
    recommended: VersionRef | None
    latest_release: Release


class NoPublishedRelease(Exception):
    pass


def published_releases(releases: list[Release], include_rollouts: bool) -> list[Release]:
    allowed = {ReleaseStatus.published}
    if include_rollouts:
        allowed.add(ReleaseStatus.rollout)
    rows = [r for r in releases if r.status in allowed and SemVer.try_parse(r.version) is not None]
    rows.sort(key=lambda r: release_sort_key(r.version, r.build))
    return rows


def evaluate(releases: list[Release], policy: Policy, *, include_rollouts: bool = False, now: datetime | None = None) -> Decision:
    """Computes the decision for one (app, platform, channel).

    * ``auto_latest``: minimum = latest.
    * ``custom``: minimum = the configured version, clamped to latest so the
      backend never demands a version nobody can install.
    * ``age_based``: minimum = oldest published release younger than
      ``max_age_days``; if every release is older, minimum = latest.
    """
    now = now or datetime.now(timezone.utc)
    rows = published_releases(releases, include_rollouts)
    if not rows:
        raise NoPublishedRelease
    latest_release = rows[-1]
    latest = VersionRef.of(latest_release)

    if policy.mode is PolicyMode.custom and policy.custom_minimum_version:
        minimum = VersionRef(policy.custom_minimum_version, policy.custom_minimum_build)
        if latest < minimum:
            minimum = latest
    elif policy.mode is PolicyMode.age_based and policy.max_age_days:
        cutoff = now - timedelta(days=policy.max_age_days)
        young = [r for r in rows if _aware(r.published_at) >= cutoff]
        minimum = VersionRef.of(young[0]) if young else latest
    else:
        minimum = latest

    recommended: VersionRef | None = None
    if policy.recommended_version:
        recommended = VersionRef(policy.recommended_version, policy.recommended_build)
        if latest < recommended:
            recommended = latest

    return Decision(latest=latest, minimum=minimum, recommended=recommended, latest_release=latest_release)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
