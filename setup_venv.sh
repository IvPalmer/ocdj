#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

python -m pip install --upgrade pip

# Install dependencies for all Python tools.
pip install -r "tools/traxdb_sync/requirements.txt"
pip install -r "tools/soulseek_sync/requirements.txt"

echo "Done."
echo "Activate with: source \"$REPO_ROOT/.venv/bin/activate\""


