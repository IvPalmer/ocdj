#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ID3_ROOT="${DJTOOLS_ID3_ROOT:-/Users/palmer/Music/Musicas/Electronic/ID3}"

ARTIFACTS_ROOT="${DJTOOLS_ARTIFACTS_ROOT:-$REPO_ROOT}"
mkdir -p "$ARTIFACTS_ROOT/logs"
REPORT="${DJTOOLS_TRAXDB_REPORT_PATH:-$ARTIFACTS_ROOT/logs/traxdb_sync_report_links.json}"

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

python3 "$REPO_ROOT/tools/traxdb_sync/sync.py" \
  --config "$REPO_ROOT/tools/traxdb_sync/config.json" \
  --traxdb-root "$ID3_ROOT/traxdb" \
  --report-path "$REPORT" \
  "$@"

echo
echo "Done."
echo "  report: $REPORT"


