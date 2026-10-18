#!/usr/bin/env bash
# Portable container start — Docker Compose, plain Docker, Railway, etc.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

export DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"
DATA_DIR="$(cd "$DATA_DIR" && pwd)"
export DATA_DIR

load_env

# A fresh volume has no F-Droid config yet. Initialization is mandatory there:
# continuing would report a healthy service backed by an unusable repository.
# Existing volumes can keep serving their last known-good repo if a later env
# sync fails. init.sh only rewrites generated config; it never clears APKs or
# replaces an existing keystore.
if [ ! -f "$DATA_DIR/config.yml" ]; then
  echo "Fresh volume detected — running init (DATA_DIR=$DATA_DIR)..."
  "$SCRIPT_DIR/init.sh"
else
  echo "Syncing config from env (DATA_DIR=$DATA_DIR)..."
  if ! "$SCRIPT_DIR/init.sh"; then
    echo "WARNING: init.sh failed — continuing with existing config."
  fi
fi

# A config can survive an interrupted first boot, so use the signed v1 index
# as the durable completion marker. Retrying is safe: build.sh preserves APKs
# and an existing keystore.
INITIAL_BUILD_COMPLETED=false
if [ ! -s "$DATA_DIR/repo/index-v1.jar" ]; then
  echo "No signed repository index found — running initial build..."
  ALLOW_EMPTY_REPO=true "$SCRIPT_DIR/build.sh"
  "$SCRIPT_DIR/deploy.sh"
  INITIAL_BUILD_COMPLETED=true
fi

# Rebuild the repo index when env-driven settings that affect the index change.
# Rebuild runs in the background so a failed fdroid update cannot crash the service.
FINGERPRINT_FILE="$DATA_DIR/.env_fingerprint"
env_fingerprint() {
  local payload
  payload="$(printf '%s\0' \
    "${REPO_NAME:-}" \
    "${REPO_URL:-}" \
    "${REPO_DESCRIPTION:-}" \
    "${REPO_WEB_BASE_URL:-}" \
    "${SELF_HOST:-}" \
    "${KEYDNAME:-}" \
    "${REPO_KEYALIAS:-}" \
    "${KEYSTORE_FILE:-}" \
    "${S3_REMOTE_NAME:-}" \
    "${S3_PROVIDER:-}" \
    "${S3_BUCKET:-}" \
    "${S3_ENDPOINT:-}" \
    "${S3_REGION:-}" \
    "${S3_ACCESS_KEY_ID:-}" \
    "${S3_SECRET_ACCESS_KEY:-}")"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$payload" | sha256sum | awk '{print $1}'
  else
    printf '%s' "$payload" | openssl dgst -sha256 | awk '{print $NF}'
  fi
}

NEW_FP="$(env_fingerprint)"
OLD_FP=""
if [ -f "$FINGERPRINT_FILE" ]; then
  OLD_FP="$(tr -d '[:space:]' < "$FINGERPRINT_FILE")"
fi

# The initial build already used the current environment, so record it before
# the background-rebuild decision to avoid immediately building the same APKs
# a second time.
if [ "$INITIAL_BUILD_COMPLETED" = "true" ]; then
  echo "$NEW_FP" > "$FINGERPRINT_FILE"
  OLD_FP="$NEW_FP"
fi

APK_COUNT=$(find "$DATA_DIR/apks" -maxdepth 1 -name '*.apk' 2>/dev/null | wc -l | tr -d ' ')

maybe_rebuild() {
  if [ "$APK_COUNT" -eq 0 ]; then
    echo "No APKs yet — skipping rebuild."
    echo "$NEW_FP" > "$FINGERPRINT_FILE"
    return 0
  fi
  if [ "$NEW_FP" = "$OLD_FP" ]; then
    echo "Env fingerprint unchanged — serving existing repo."
    return 0
  fi
  echo "Env/config fingerprint changed — rebuilding repo index in background..."
  if "$SCRIPT_DIR/update.sh"; then
    echo "$NEW_FP" > "$FINGERPRINT_FILE"
    echo "Background rebuild complete."
  else
    echo "WARNING: background rebuild failed — serving previous repo if any."
  fi
}

# Kick off rebuild without blocking uvicorn (Railway healthchecks need the port up).
maybe_rebuild &

PORT="${PORT:-8000}"
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
