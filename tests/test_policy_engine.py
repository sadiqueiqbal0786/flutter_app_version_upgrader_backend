from datetime import datetime, timedelta, timezone

import pytest

from app.models import Channel, Platform, Policy, PolicyMode, Release, ReleaseSource, ReleaseStatus
from app.policy.engine import NoPublishedRelease, evaluate

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


def rel(version: str, build: int, days_ago: int = 0, status: ReleaseStatus = ReleaseStatus.published) -> Release:
    return Release(
        app_pk=1, platform=Platform.android, channel=Channel.production, version=version, build=build, status=status,
        source=ReleaseSource.manual, published_at=NOW - timedelta(days=days_ago),
    )


def policy(**kw) -> Policy:
    p = Policy.default(1, Platform.android, Channel.production)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


RELEASES = [rel("2.7.0", 270, days_ago=5), rel("2.5.0", 250, days_ago=60), rel("2.10.0", 2100, days_ago=1), rel("2.9.0", 290, days_ago=20), rel("2.4.0", 240, days_ago=200)]


def test_auto_latest_picks_highest_semver_not_lexical():
    d = evaluate(RELEASES, policy(), now=NOW)
    assert (d.latest.version, d.latest.build) == ("2.10.0", 2100)
    assert d.minimum == d.latest
    assert d.recommended is None


def test_custom_minimum():
    d = evaluate(RELEASES, policy(mode=PolicyMode.custom, custom_minimum_version="2.5.0", custom_minimum_build=250), now=NOW)
    assert (d.minimum.version, d.minimum.build) == ("2.5.0", 250)
    assert d.latest.version == "2.10.0"


def test_custom_minimum_is_clamped_to_latest():
    d = evaluate(RELEASES, policy(mode=PolicyMode.custom, custom_minimum_version="9.0.0"), now=NOW)
    assert d.minimum == d.latest


def test_age_based_picks_oldest_young_enough_release():
    # Within 30 days: 2.7.0 (5d), 2.9.0 (20d), 2.10.0 (1d). Lowest of those wins.
    d = evaluate(RELEASES, policy(mode=PolicyMode.age_based, max_age_days=30), now=NOW)
    assert d.minimum.version == "2.7.0"
    # Within 10 days: 2.7.0 and 2.10.0 only.
    d = evaluate(RELEASES, policy(mode=PolicyMode.age_based, max_age_days=10), now=NOW)
    assert d.minimum.version == "2.7.0"
    # Within 3 days: only 2.10.0.
    d = evaluate(RELEASES, policy(mode=PolicyMode.age_based, max_age_days=3), now=NOW)
    assert d.minimum.version == "2.10.0"
    d = evaluate(RELEASES, policy(mode=PolicyMode.age_based, max_age_days=90), now=NOW)
    assert d.minimum.version == "2.5.0"


def test_age_based_falls_back_to_latest_when_everything_is_old():
    d = evaluate(RELEASES, policy(mode=PolicyMode.age_based, max_age_days=0.5), now=NOW)
    assert d.minimum == d.latest


def test_recommended_is_clamped_and_surfaced():
    d = evaluate(RELEASES, policy(recommended_version="2.9.0", recommended_build=290), now=NOW)
    assert (d.recommended.version, d.recommended.build) == ("2.9.0", 290)
    d = evaluate(RELEASES, policy(recommended_version="99.0.0"), now=NOW)
    assert d.recommended == d.latest


def test_rollouts_excluded_unless_asked():
    rows = RELEASES + [rel("3.0.0", 300, status=ReleaseStatus.rollout)]
    assert evaluate(rows, policy(), now=NOW).latest.version == "2.10.0"
    assert evaluate(rows, policy(), include_rollouts=True, now=NOW).latest.version == "3.0.0"


def test_withdrawn_and_unparsable_are_ignored():
    rows = [rel("3.0.0", 300, status=ReleaseStatus.withdrawn), rel("garbage", 1), rel("2.0.0", 200)]
    assert evaluate(rows, policy(), now=NOW).latest.version == "2.0.0"


def test_no_published_release_raises():
    with pytest.raises(NoPublishedRelease):
        evaluate([rel("1.0.0", 1, status=ReleaseStatus.withdrawn)], policy(), now=NOW)
