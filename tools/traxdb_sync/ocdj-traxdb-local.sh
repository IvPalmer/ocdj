#!/usr/bin/env bash
# ocdj-traxdb-local.sh — LaunchAgent wrapper for the TraxDB local-download daemon.
#
# Installed to ~/bin/ and invoked by dev.grooveops.ocdj-traxdb-local.plist.
# Keeps the same shape as ocdj-drain.sh: repo venv, single log, non-zero exit on
# preflight failure so launchd surfaces it.
set -euo pipefail

REPO_ROOT="${OCDJ_REPO_ROOT:-/Users/palmer/Work/Dev/ocdj}"
LOG_FILE="${HOME}/Library/Logs/ocdj-traxdb-local.log"
LIMIT="${TRAXDB_LOCAL_LIMIT:-3}"

mkdir -p "$(dirname "$LOG_FILE")"

if [[ ! -f "$REPO_ROOT/.venv/bin/activate" ]]; then
  echo "$(date -u +%FT%TZ) [traxdb-local] FATAL: venv missing at $REPO_ROOT/.venv" | tee -a "$LOG_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

# A single cycle per invocation — launchd handles the interval, so a hung run
# can't stack up behind the next one.
exec python3 "$REPO_ROOT/tools/traxdb_sync/local_daemon.py" --limit "$LIMIT" 2>&1 \
  | tee -a "$LOG_FILE"
