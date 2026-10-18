#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

load_env
ensure_dirs
cd "$DATA_DIR"

# -- Generate configs if missing -----------------------------------------------

if [ ! -f config.yml ] || [ ! -f rclone.conf ]; then
  echo "Configs missing -- running init..."
  "$SCRIPT_DIR/init.sh"
fi

# -- Copy APKs -----------------------------------------------------------------

APK_COUNT=$(find apks/ -maxdepth 1 -name '*.apk' 2>/dev/null | wc -l | tr -d ' ')
ALLOW_EMPTY_REPO="$(echo "${ALLOW_EMPTY_REPO:-false}" | tr '[:upper:]' '[:lower:]')"

if [ "$APK_COUNT" -eq 0 ] && [[ ! "$ALLOW_EMPTY_REPO" =~ ^(1|true|yes|on)$ ]]; then
  echo "No APKs found in $DATA_DIR/apks/ -- nothing to build."
  exit 1
fi

if [ "$APK_COUNT" -eq 0 ]; then
  echo "No APKs found — generating an initial empty repository."
else
  echo "Copying $APK_COUNT APK(s) to repo/..."
  cp apks/*.apk repo/
fi

# -- Repo icon -----------------------------------------------------------------

mkdir -p repo/icons
ICON="$(icon_source)"
if [ -n "$ICON" ]; then
  cp "$ICON" repo/icons/icon.png
  echo "Copied $ICON -> repo/icons/icon.png"
fi

# -- Verify APK signatures ----------------------------------------------------

if [ "$APK_COUNT" -gt 0 ] && command -v apksigner &>/dev/null; then
  "$SCRIPT_DIR/verify.sh"
fi

# -- Update index ---------------------------------------------------------------

KS_PATH="$(keystore_path)"
UPDATE_FLAGS=(--create-metadata --pretty)
if [ ! -f "$KS_PATH" ]; then
  UPDATE_FLAGS+=(--create-key)
fi

echo "Running fdroid update..."
fdroid update "${UPDATE_FLAGS[@]}"

echo "Build complete (DATA_DIR=$DATA_DIR)."
