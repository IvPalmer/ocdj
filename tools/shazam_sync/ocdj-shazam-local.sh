#!/usr/bin/env bash
# ocdj-shazam-local.sh — LaunchAgent wrapper for the Shazam feed.
# Same shape as ocdj-traxdb-local.sh: repo venv, single log, non-zero exit on
# preflight failure so launchd surfaces it.
set -euo pipefail

REPO_ROOT="${OCDJ_REPO_ROOT:-/Users/palmer/Work/Dev/ocdj}"
LOG_FILE="${HOME}/Library/Logs/ocdj-shazam-local.log"

mkdir -p "$(dirname "$LOG_FILE")"

if [[ ! -f "$REPO_ROOT/.venv/bin/activate" ]]; then
  echo "$(date -u +%FT%TZ) [shazam-local] FATAL: venv missing at $REPO_ROOT/.venv" | tee -a "$LOG_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

exec python3 "$REPO_ROOT/tools/shazam_sync/shazam_local.py" 2>&1 | tee -a "$LOG_FILE"
