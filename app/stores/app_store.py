"""App Store Connect API adapter (App Store releases and TestFlight builds).

Authenticates with an ES256 JWT built from an API key (issuer id, key id,
.p8 private key) created in App Store Connect → Users and Access → Keys.
The key needs at least the Developer role.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings
from app.models import App, Channel, Platform, ReleaseSource, ReleaseStatus
from app.stores.base import DiscoveredRelease, StoreError

BASE_URL = "https://api.appstoreconnect.apple.com/v1"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_app_store_versions(payload: dict[str, Any], store_url: str | None) -> list[DiscoveredRelease]:
    """Pure parser for ``GET /v1/apps/{id}/appStoreVersions?include=build``."""
    builds: dict[str, dict[str, Any]] = {
        item["id"]: item.get("attributes", {}) for item in payload.get("included", []) if item.get("type") == "builds"
    }
    out: list[DiscoveredRelease] = []
    for item in payload.get("data", []):
        attrs = item.get("attributes", {})
        version = attrs.get("versionString")
        if not version:
            continue
        build_ref = (item.get("relationships", {}).get("build", {}).get("data") or {}).get("id")
        build_attrs = builds.get(build_ref, {}) if build_ref else {}
        build_str = build_attrs.get("version")
        build = int(build_str) if build_str and str(build_str).isdigit() else None
        out.append(
            DiscoveredRelease(
                version=version,
                build=build,
                status=ReleaseStatus.published,
                source=ReleaseSource.app_store,
                published_at=_parse_date(attrs.get("createdDate")),
                store_url=store_url,
            )
        )
    return out


def parse_testflight_builds(payload: dict[str, Any], store_url: str | None) -> list[DiscoveredRelease]:
    """Pure parser for ``GET /v1/builds?include=preReleaseVersion``."""
    pre: dict[str, dict[str, Any]] = {
        item["id"]: item.get("attributes", {})
        for item in payload.get("included", [])
        if item.get("type") == "preReleaseVersions"
    }
    out: list[DiscoveredRelease] = []
    for item in payload.get("data", []):
        attrs = item.get("attributes", {})
        if attrs.get("expired"):
            continue
        ref = (item.get("relationships", {}).get("preReleaseVersion", {}).get("data") or {}).get("id")
        version = pre.get(ref, {}).get("version") if ref else None
        if not version:
            continue
        build_str = attrs.get("version")
        build = int(build_str) if build_str and str(build_str).isdigit() else None
        out.append(
            DiscoveredRelease(
                version=version,
                build=build,
                status=ReleaseStatus.published,
                source=ReleaseSource.testflight,
                published_at=_parse_date(attrs.get("uploadedDate")),
                store_url=store_url,
            )
        )
    return out


class AppStoreConnectProvider:
    source = ReleaseSource.app_store

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._token: str | None = None
        self._token_exp = 0.0

    def supports(self, platform: Platform, channel: Channel) -> bool:
        # Apple has two distribution paths: the App Store (production) and
        # TestFlight (beta). There is no alpha/internal distinction, so those
        # channels are left to manual releases rather than duplicating rows.
        return platform is Platform.ios and channel in (Channel.production, Channel.beta)

    # -- auth -----------------------------------------------------------------

    def _private_key(self) -> str:
        s = self._settings
        if s.app_store_connect_private_key:
            return s.app_store_connect_private_key
        if s.app_store_connect_private_key_file:
            with open(s.app_store_connect_private_key_file, encoding="utf-8") as f:
                return f.read()
        raise StoreError("App Store Connect private key is not configured")

    def token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        s = self._settings
        if not (s.app_store_connect_issuer_id and s.app_store_connect_key_id):
            raise StoreError("App Store Connect issuer id / key id are not configured")
        try:
            import jwt
        except ImportError as e:  # pragma: no cover
            raise StoreError(f"PyJWT missing: {e}") from e
        exp = int(now) + 19 * 60
        self._token = jwt.encode(
            {"iss": s.app_store_connect_issuer_id, "iat": int(now), "exp": exp, "aud": "appstoreconnect-v1"},
            self._private_key(),
            algorithm="ES256",
            headers={"kid": s.app_store_connect_key_id, "typ": "JWT"},
        )
        self._token_exp = exp
        return self._token

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token()}"}
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            r = await client.get(f"{BASE_URL}{path}", params=params, headers=headers)
        except httpx.HTTPError as e:
            raise StoreError(f"App Store Connect: {e}") from e
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code >= 400:
            raise StoreError(f"App Store Connect returned {r.status_code}: {r.text[:300]}")
        return r.json()

    # -- queries ------------------------------------------------------------------

    async def get_releases(self, app: App, platform: Platform, channel: Channel) -> list[DiscoveredRelease]:
        if not app.apple_app_id:
            raise StoreError(f"app {app.app_id} has no apple_app_id")
        if channel is Channel.production:
            payload = await self._get(
                f"/apps/{app.apple_app_id}/appStoreVersions",
                {
                    "filter[platform]": "IOS",
                    "filter[appVersionState]": "READY_FOR_DISTRIBUTION",
                    "include": "build",
                    "limit": "5",
                },
            )
            return parse_app_store_versions(payload, app.app_store_url())
        payload = await self._get(
            "/builds",
            {
                "filter[app]": app.apple_app_id,
                "filter[processingState]": "VALID",
                "filter[expired]": "false",
                "sort": "-uploadedDate",
                "include": "preReleaseVersion",
                # Apple's maximum. Every live (non-expired) build must be in
                # the answer, otherwise a build that merely drops off the
                # first page would be marked withdrawn and shift an
                # age-based minimum.
                "limit": "200",
            },
        )
        return parse_testflight_builds(payload, app.testflight_url())
