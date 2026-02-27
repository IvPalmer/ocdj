#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "[1/4] Setting up repo venv + deps..."
bash "$REPO_ROOT/setup_venv.sh"

echo "[2/4] Creating shared config (if missing)..."
if [[ ! -f "$REPO_ROOT/djtools_config.json" ]]; then
  cp "$REPO_ROOT/djtools_config.example.json" "$REPO_ROOT/djtools_config.json"
  echo "Created: djtools_config.json (edit it with your real values)"
else
  echo "Found: djtools_config.json"
fi

echo "[3/4] Ensuring repo logs dir exists..."
mkdir -p "$REPO_ROOT/logs"

echo "[4/4] Sanity check: can import both tool modules..."
# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"
python3 - <<'PY'
import importlib
for m in ["tools.traxdb_sync.config", "tools.soulseek_sync.config"]:
    importlib.import_module(m)
print("OK: imports")
PY

echo
echo "Bootstrap complete."
echo "Next:"
echo "  - Edit: $REPO_ROOT/djtools_config.json"
echo "  - Run TraxDB report: bash tools/traxdb_sync/run_sync.sh --max-pages 50"
echo "  - Run Soulseek:      bash tools/soulseek_sync/run_bg.sh"


