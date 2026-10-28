# flutter_app_version_upgrader backend

[![CI](https://github.com/sadiqueiqbal0786/flutter_app_version_upgrader_backend/actions/workflows/ci.yml/badge.svg)](https://github.com/sadiqueiqbal0786/flutter_app_version_upgrader_backend/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

The optional server half of the
[`flutter_app_version_upgrader`](https://pub.dev/packages/flutter_app_version_upgrader)
Flutter plugin. The plugin works with any endpoint that returns its JSON
contract; this service is a ready-made one. It discovers what is
published on Google Play and the App Store, applies the minimum-version
policy an administrator sets, and serves `GET /v1/version/check` to the
Flutter plugin.

```text
Google Play API ─┐                          ┌─ auto_latest
App Store API  ──┼─▶ Release DB ─▶ Policy ──┼─ custom      ─▶ /v1/version/check ─▶ plugin
TestFlight API ──┘                 engine   └─ age_based
                                     ▲
                              Admin API + dashboard
```

## What you need to provide

The code is complete. It needs credentials and identifiers that only you
can supply. Nothing works end to end until the **required** rows are filled.

| Item | Required for | Where to get it | Env / field |
|---|---|---|---|
| Admin API key | Everything | Generate: `openssl rand -hex 32` | `VM_ADMIN_API_KEY` |
| Database | Production | PostgreSQL 14+ connection string | `VM_DATABASE_URL` |
| App id, name | Each app | The id your plugin sends (defaults to the package name / bundle id) | Admin API or dashboard |
| Android package name | Android discovery | Play Console | App field `android_package_name` |
| Play service account JSON | Android discovery | Google Cloud → IAM → Service account → key. Then in Play Console → Users and permissions, invite that service account's email with **View app information** on the app | `VM_GOOGLE_PLAY_SERVICE_ACCOUNT_FILE` or `_JSON` |
| Apple app id (numeric) | iOS discovery | App Store Connect → App Information → Apple ID | App field `apple_app_id` |
| App Store Connect API key | iOS + TestFlight discovery | App Store Connect → Users and Access → Integrations → App Store Connect API → generate key with **Developer** role. Note the Issuer ID, Key ID, download the `.p8` once | `VM_APP_STORE_CONNECT_ISSUER_ID`, `_KEY_ID`, `_PRIVATE_KEY_FILE` |
| Redis URL | Optional, multi-replica | Any Redis 6+ | `VM_REDIS_URL` |
| Client API key(s) | Optional | Generate; put the same key in `VersionManagerConfig.apiKey` | `VM_CLIENT_API_KEYS` |
| Response signing secret | Optional | Generate; verify in the plugin's `responseValidator` | `VM_RESPONSE_SIGNING_SECRET` |
| TestFlight public link | Optional | TestFlight → public link. Without it the `itms-beta://` deep link is used | Policy `update_url` for beta channels |

Without store credentials the backend still works: add releases manually
through the dashboard or `POST /v1/admin/apps/{id}/releases`.

## Run locally

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set VM_ADMIN_API_KEY at least
uvicorn app.asgi:app --reload   # SQLite, tables auto-created
```

Open <http://localhost:8000/admin/>, paste the admin key, create an app,
add a release, and read the "Client preview" card. Point the Flutter
example at `http://localhost:8000` (iOS simulator) or `http://10.0.2.2:8000`
(Android emulator).

API docs: <http://localhost:8000/docs>.

## Run with Docker

```sh
cp .env.example .env            # fill in keys; put .p8 / service-account files in ./secrets
docker compose up --build
```

This starts PostgreSQL, Redis, runs migrations, the API on port 8000 and a
separate worker that polls the stores every `VM_SYNC_INTERVAL_SECONDS`.

## Deploy on your own server

1. Clone this repository on the server. Put the Play service-account JSON
   and the App Store Connect `.p8` into `secrets/` and fill `.env` from
   `.env.example`. Never commit either.
2. `docker compose up -d --build` starts PostgreSQL, Redis, runs the
   migrations, and launches the API on port 8000 plus the store-sync
   worker. Put a reverse proxy with TLS in front (Caddy, nginx, a cloud
   load balancer) so the plugin can reach it over HTTPS.
3. Register the app and its policy, then trigger the first sync:

   ```sh
   VM_URL=https://versions.example.com VM_ADMIN_API_KEY=... \
     scripts/seed_app.sh com.mtd.app "Manage The Day" com.mtd.app com.mtd.app 6746727961 90
   ```

4. Open `https://versions.example.com/admin/` and check the "Latest
   published" card shows what the stores show.
5. Put the base URL in the app: `--dart-define=VERSION_API_URL=https://versions.example.com`
   and `--dart-define=VERSION_CHANNEL=production` (or `beta` for
   TestFlight / Play beta builds).

Health endpoints for your uptime monitor: `/health` and `/ready`.

## How a check is answered

1. Look up the app by `app_id`; 404 if unknown (the plugin then falls back
   to its cache and fails open).
2. Load the releases for that platform and channel. Only `published` rows
   count; staged Play rollouts (`rollout`) are excluded unless
   `VM_INCLUDE_IN_PROGRESS_ROLLOUTS=true`, so a 10 % rollout never forces
   the other 90 % onto a build they cannot download.
3. `latest` = highest semantic version, build number breaking ties.
4. `minimum` from the policy:
   - `auto_latest` (default): `latest`.
   - `custom`: the configured version, clamped to `latest`.
   - `age_based`: the lowest version published within `max_age_days`;
     if none, `latest`.
5. `update.url` = policy override, else the discovered store URL, else a
   default from the app's identifiers: the Play Store page on Android, the
   App Store page for iOS production, and the TestFlight deep link
   (`itms-beta://beta.itunes.apple.com/v1/app/<apple id>`) for other iOS
   channels. Set a policy `update_url` to a TestFlight public link if you
   prefer that.
6. The JSON is cached per app/platform/channel for
   `VM_RESPONSE_CACHE_TTL_SECONDS` and invalidated on every policy or
   release change.

## Installed versions

Every check carries the installed `version` and `build`. The backend keeps
one counter per app / platform / channel / version / build (no device or
user identifiers), and the dashboard's "Installed versions" card shows each
one flagged `latest`, `outdated` or `blocked` against the current decision.
`GET /v1/admin/apps/{app_id}/installs?platform=&channel=` returns the same
data.

Note for `age_based` on Android: Google Play does not expose release dates,
so a release's age counts from the moment this backend first saw it. On the
very first sync every existing track release looks brand new.

## Store sync behaviour

- Google Play: one throw-away edit per track read; tracks map 1:1 to
  channels (`production`, `beta`, `alpha`, `internal`). Play does not
  expose a release date, so `published_at` is when the backend first saw
  the release.
- App Store: `appStoreVersions` in state `READY_FOR_DISTRIBUTION` with the
  attached build number. `published_at` is Apple's `createdDate`.
- TestFlight (iOS `beta` channel): newest valid, unexpired builds with
  their pre-release version string. iOS `alpha` and `internal` have no
  Apple equivalent and are only fed by manual releases.
- A release that disappears from a track is marked `withdrawn`, not
  deleted. Manual releases are never withdrawn automatically.
- A failing store call keeps the existing rows and records the error on
  the app (`last_sync_error`, visible in the dashboard).

## Admin API

All under `/v1/admin`, authenticated with `X-Admin-Key: …` or
`Authorization: Bearer …`.

```text
GET    /apps                                   list
POST   /apps                                   create
GET    /apps/{app_id}
PATCH  /apps/{app_id}
DELETE /apps/{app_id}
GET    /apps/{app_id}/releases?platform=&channel=
POST   /apps/{app_id}/releases                 manual release
DELETE /apps/{app_id}/releases/{id}
GET    /apps/{app_id}/policies                 all 8 platform/channel slots
GET    /apps/{app_id}/policies/{platform}/{channel}
PUT    /apps/{app_id}/policies/{platform}/{channel}
DELETE /apps/{app_id}/policies/{platform}/{channel}   back to default
GET    /apps/{app_id}/preview/{platform}/{channel}    what a client gets
GET    /apps/{app_id}/installs?platform=&channel=     installed versions seen
POST   /releases/sync?app_id=&wait=            trigger store sync
```

Emergency example: a vulnerability in 2.5.x, latest is 2.7.0:

```sh
curl -X PUT http://localhost:8000/v1/admin/apps/com.example.app/policies/android/production \
  -H "X-Admin-Key: $VM_ADMIN_API_KEY" -H 'Content-Type: application/json' \
  -d '{"mode":"custom","custom_minimum_version":"2.7.0","custom_minimum_build":270,"force_update":true,"update_message":"A security update is required."}'
```

Clients pick it up on their next check without a new app release.

## Response signing

With `VM_RESPONSE_SIGNING_SECRET` set, every check response carries:

```text
X-Signature-Timestamp: 1761541800
X-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
```

Verify in Flutter with the `crypto` package (the plugin's
`responseValidator` option):

```dart
responseValidator: (body, headers) {
  final ts = headers['x-signature-timestamp'];
  final sig = headers['x-signature'];
  if (ts == null || sig == null) return false;
  final mac = Hmac(sha256, utf8.encode(secret)).convert(utf8.encode('$ts.$body'));
  return sig == 'sha256=$mac';
}
```

## Layout

```text
app/
├── api/           health, /v1/version/check, /v1/admin/*
├── policy/        pure decision engine
├── stores/        GooglePlayProvider, AppStoreConnectProvider (+ TestFlight)
├── releases/      sync + upsert
├── workers/       scheduler (in-process or standalone)
├── admin_ui/      single-page dashboard
├── cache.py       memory / Redis response cache + rate-limit counters
├── signing.py     HMAC response signatures
├── auth.py        admin / client keys
└── versioning.py  semver, mirrors the Dart implementation
alembic/           migrations (PostgreSQL)
tests/             pytest, SQLite in memory, no network
```

Tests: `pytest`.

## Author

**Sadique Iqbal** · [GitHub](https://github.com/sadiqueiqbal0786) · sadiqueiqbal.si@gmail.com

## License

MIT © 2026 Sadique Iqbal. See [LICENSE](LICENSE).
