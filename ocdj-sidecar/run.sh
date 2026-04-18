#!/bin/bash
# Chat sidecar for OCDJ.
#
# Runs on the HOST (not inside Docker) so the Claude Agent SDK can
# authenticate against the user's local `claude` CLI (Max subscription).
# The Docker backend is reached at http://localhost:8002 from here.
#
# Port 5179 (Vault uses 5178).

set -e
cd "$(dirname "$0")"

# Create venv if missing
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

pip install -r requirements.txt -q

echo "Starting OCDJ sidecar on http://127.0.0.1:5179"
exec uvicorn server:app --host 127.0.0.1 --port 5179 --reload
