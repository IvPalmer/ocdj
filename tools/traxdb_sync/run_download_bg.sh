#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Working library root (downloads only)
ID3_ROOT="${DJTOOLS_ID3_ROOT:-/Users/palmer/Music/Musicas/Electronic/ID3}"

# Artifacts root (logs/reports/progress)
ARTIFACTS_ROOT="${DJTOOLS_ARTIFACTS_ROOT:-$REPO_ROOT}"
mkdir -p "$ARTIFACTS_ROOT/logs"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$ARTIFACTS_ROOT/logs/traxdb_download_${STAMP}.log"
PROGRESS="$ARTIFACTS_ROOT/logs/traxdb_download_${STAMP}.progress.json"
FINAL="$ARTIFACTS_ROOT/logs/traxdb_download_${STAMP}.final.json"
REPORT="${DJTOOLS_TRAXDB_REPORT_PATH:-$ARTIFACTS_ROOT/logs/traxdb_sync_report_links.json}"

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

nohup python3 "$REPO_ROOT/tools/traxdb_sync/download_from_report.py" \
  --config "$REPO_ROOT/tools/traxdb_sync/config.json" \
  --traxdb-root "$ID3_ROOT/traxdb" \
  --report "$REPORT" \
  --progress-path "$PROGRESS" \
  --report-path "$FINAL" \
  > "$LOG" 2>&1 &

echo "Started."
echo "  log:      $LOG"
echo "  progress: $PROGRESS"
echo "  final:    $FINAL"
echo "Watch:"
echo "  tail -f \"$LOG\""

