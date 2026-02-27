#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLS_ROOT="$REPO_ROOT/tools"
TS="$(date +"%Y%m%d-%H%M%S")"

# Artifacts root (logs/reports)
ARTIFACTS_ROOT="${DJTOOLS_ARTIFACTS_ROOT:-$REPO_ROOT}"
mkdir -p "$ARTIFACTS_ROOT/logs"

FINAL="${1:-$ARTIFACTS_ROOT/logs/soulseek_report_${TS}.json}"

cd "$TOOLS_ROOT"
# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

python3 -m soulseek_sync.run \
  --config "$TOOLS_ROOT/soulseek_sync/config.json" \
  --wanted "$TOOLS_ROOT/soulseek_sync/wanted.txt" \
  --dry-run \
  --max-results 300 \
  --report-path "$FINAL"

echo
echo "Dry-run done."
echo "  report: $FINAL"


