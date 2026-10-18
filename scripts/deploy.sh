#!/usr/bin/env bash
# Deploy to S3 if configured. When self-hosting is enabled, a failed mirror
# must not turn a valid local repository publish into a failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

load_env
cd "$DATA_DIR"

if ! s3_ready; then
  MODE="$(resolve_mode)"
  echo "S3 not configured -- skipping remote deploy (mode=$MODE)."
  echo "Repo is available under $DATA_DIR/repo/ for self-host."
  exit 0
fi

echo "Running fdroid deploy..."
if fdroid deploy; then
  echo "Deploy complete."
  exit 0
else
  DEPLOY_EXIT=$?
fi

if is_self_host; then
  echo "WARNING: S3 deploy failed (exit=$DEPLOY_EXIT) -- keeping self-hosted repo available."
  echo "Repo is available under $DATA_DIR/repo/."
  exit 0
fi

echo "ERROR: S3 deploy failed (exit=$DEPLOY_EXIT) and SELF_HOST is not enabled."
exit "$DEPLOY_EXIT"
