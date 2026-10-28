"""Google Play Developer API (androidpublisher v3) adapter.

Reads the releases on a track through a throw-away edit. Needs a service
account that has been granted access to the app in Play Console (the
"View app information and download bulk reports" permission is enough).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.config import Settings
from app.models import App, Channel, Platform, ReleaseSource, ReleaseStatus
from app.stores.base import DiscoveredRelease, StoreError
from app.versioning import SemVer

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/androidpublisher"

TRACKS: dict[Channel, str] = {
    Channel.production: "production",
    Channel.beta: "beta",
    Channel.alpha: "alpha",
    Channel.internal: "internal",
}


_SEMVER_TOKEN = re.compile(r"\d+(?:\.\d+){1,2}(?:-[0-9A-Za-z.-]+)?")


def version_from_release_name(name: str | None) -> str | None:
    """Extracts the marketing version from a Play release name.

    The track API exposes only the release *name*, which Play fills in as
    ``"<versionCode> (<versionName>)"`` unless someone renamed it. Accept, in
    order: a plain version ("1.0.37"), the parenthesised default
    ("54 (1.0.37)"), and finally any version-looking token in a custom name
    ("Spring release 1.0.37"). ``None`` when nothing parsable is found.
    """
    if not name:
        return None
    text = name.strip()
    if SemVer.try_parse(text):
        return text
    m = re.search(r"\(([^)]+)\)", text)
    if m and SemVer.try_parse(m.group(1).strip()):
        return m.group(1).strip()
    for token in _SEMVER_TOKEN.findall(text):
        if SemVer.try_parse(token):
            return token
    return None


def parse_track(track: dict[str, Any], store_url: str | None) -> list[DiscoveredRelease]:
    """Pure parser for a ``edits.tracks.get`` payload, shared with tests."""
    out: list[DiscoveredRelease] = []
    for rel in track.get("releases", []):
        status = rel.get("status")
        if status == "completed":
            mapped = ReleaseStatus.published
        elif status == "inProgress":
            mapped = ReleaseStatus.rollout
        else:
            continue  # draft / halted are not live
        version = version_from_release_name(rel.get("name"))
        codes = [int(c) for c in rel.get("versionCodes", []) if str(c).isdigit()]
        if not version or not codes:
            log.warning("skipping Play release %r: no parsable version", rel.get("name"))
            continue
        notes = None
        for n in rel.get("releaseNotes", []) or []:
            if n.get("language", "").startswith("en"):
                notes = n.get("text")
                break
        out.append(
            DiscoveredRelease(
                version=version,
                build=max(codes),
                status=mapped,
                source=ReleaseSource.google_play,
                store_url=store_url,
                release_notes=notes,
            )
        )
    return out


class GooglePlayProvider:
    source = ReleaseSource.google_play

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any = None

    def supports(self, platform: Platform, channel: Channel) -> bool:
        return platform is Platform.android and channel in TRACKS

    def _build_service(self) -> Any:
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as e:  # pragma: no cover
            raise StoreError(f"google client libraries missing: {e}") from e
        s = self._settings
        if s.google_play_service_account_json:
            info = json.loads(s.google_play_service_account_json)
            creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        elif s.google_play_service_account_file:
            creds = service_account.Credentials.from_service_account_file(s.google_play_service_account_file, scopes=[SCOPE])
        else:
            raise StoreError("Google Play credentials are not configured")
        self._service = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _fetch_track_sync(self, package_name: str, track: str) -> dict[str, Any]:
        service = self._build_service()
        edits = service.edits()
        edit = edits.insert(packageName=package_name, body={}).execute()
        edit_id = edit["id"]
        try:
            return edits.tracks().get(packageName=package_name, editId=edit_id, track=track).execute()
        finally:
            try:
                edits.delete(packageName=package_name, editId=edit_id).execute()
            except Exception:  # noqa: BLE001  (best effort cleanup)
                pass

    async def get_releases(self, app: App, platform: Platform, channel: Channel) -> list[DiscoveredRelease]:
        if not app.android_package_name:
            raise StoreError(f"app {app.app_id} has no android_package_name")
        try:
            track = await asyncio.to_thread(self._fetch_track_sync, app.android_package_name, TRACKS[channel])
        except StoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise StoreError(f"Google Play: {e}") from e
        return parse_track(track, app.play_store_url())
