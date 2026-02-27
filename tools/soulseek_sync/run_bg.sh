#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLS_ROOT="$REPO_ROOT/tools"
TS="$(date +"%Y%m%d-%H%M%S")"

# Working library root (downloads only)
ID3_ROOT="${DJTOOLS_ID3_ROOT:-/Users/palmer/Music/Musicas/Electronic/ID3}"

# Artifacts root (logs/reports/progress)
ARTIFACTS_ROOT="${DJTOOLS_ARTIFACTS_ROOT:-$REPO_ROOT}"
mkdir -p "$ARTIFACTS_ROOT/logs"

LOG="$ARTIFACTS_ROOT/logs/soulseek_${TS}.log"
PROGRESS="$ARTIFACTS_ROOT/logs/soulseek_progress_${TS}.json"
FINAL="$ARTIFACTS_ROOT/logs/soulseek_report_${TS}.json"

(
  cd "$TOOLS_ROOT"
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  exec python3 -m soulseek_sync.run \
    --config "$TOOLS_ROOT/soulseek_sync/config.json" \
    --wanted "$TOOLS_ROOT/soulseek_sync/wanted.txt" \
    --preclean failed \
    --max-attempts 10 \
    --max-results 300 \
    --probe-timeout-s 30 \
    --accept-queued-probe \
    --progress-path "$PROGRESS" \
    --report-path "$FINAL"
) >>"$LOG" 2>&1 &

echo "Started."
echo "  log:      $LOG"
echo "  progress: $PROGRESS"
echo "  final:    $FINAL"
echo "Watch:"
echo "  tail -f \"$LOG\""


