#!/usr/bin/env bash
# Registers an app and applies one policy to all eight platform/channel slots
# on a running backend. Safe to re-run: existing rows are updated.
#
#   VM_URL=https://versions.example.com VM_ADMIN_API_KEY=... \
#   scripts/seed_app.sh com.mtd.app "Manage The Day" com.mtd.app com.mtd.app 6746727961 90
#
# args: app_id name android_package_name ios_bundle_id apple_app_id [max_age_days]
set -euo pipefail

: "${VM_URL:?set VM_URL to the backend base URL}"
: "${VM_ADMIN_API_KEY:?set VM_ADMIN_API_KEY}"
APP_ID=${1:?app_id}; NAME=${2:?name}; ANDROID=${3:-}; BUNDLE=${4:-}; APPLE=${5:-}; DAYS=${6:-90}
MESSAGE=${VM_UPDATE_MESSAGE:-"This version is no longer supported. Please update to continue."}
H=(-H "X-Admin-Key: $VM_ADMIN_API_KEY" -H "Content-Type: application/json")

body=$(printf '{"app_id":"%s","name":"%s","android_package_name":%s,"ios_bundle_id":%s,"apple_app_id":%s}' \
  "$APP_ID" "$NAME" \
  "$( [ -n "$ANDROID" ] && printf '"%s"' "$ANDROID" || echo null)" \
  "$( [ -n "$BUNDLE" ] && printf '"%s"' "$BUNDLE" || echo null)" \
  "$( [ -n "$APPLE" ] && printf '"%s"' "$APPLE" || echo null)")

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$VM_URL/v1/admin/apps" "${H[@]}" -d "$body")
if [ "$code" = "409" ]; then
  curl -sf -X PATCH "$VM_URL/v1/admin/apps/$APP_ID" "${H[@]}" -d "$body" >/dev/null && echo "app $APP_ID updated"
elif [ "$code" = "201" ]; then
  echo "app $APP_ID created"
else
  echo "creating app failed with HTTP $code" >&2; exit 1
fi

policy=$(printf '{"mode":"age_based","max_age_days":%s,"force_update":true,"enabled":true,"update_message":"%s"}' "$DAYS" "$MESSAGE")
for platform in android ios; do
  for channel in production beta alpha internal; do
    curl -sf -X PUT "$VM_URL/v1/admin/apps/$APP_ID/policies/$platform/$channel" "${H[@]}" -d "$policy" >/dev/null
    echo "policy $platform/$channel -> age_based ${DAYS}d"
  done
done

echo "syncing releases from the stores..."
curl -s -X POST "$VM_URL/v1/admin/releases/sync?app_id=$APP_ID" "${H[@]}" | python3 -c '
import sys, json
for r in json.load(sys.stdin):
    print(f"  {r[\"platform\"]:8}{r[\"channel\"]:11} new={r[\"discovered\"]:<3} {(\"ERROR: \" + r[\"error\"]) if r[\"error\"] else \"ok\"}")'
