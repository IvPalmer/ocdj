#!/bin/bash
# ─── DJ Tools Dev Environment ─────────────────────────────────
# Starts all services needed for development:
#   1. Docker (PostgreSQL + slskd + Django backend)
#   2. Vite dev server (frontend)
#
# Usage:
#   ./dev.sh          Start everything
#   ./dev.sh stop     Stop all services
#   ./dev.sh docker   Start only Docker services
#   ./dev.sh logs     Tail backend logs
# ──────────────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"

stop_services() {
    echo "Stopping services..."
    cd "$DIR" && docker compose down
    lsof -ti:5174 2>/dev/null | xargs kill 2>/dev/null
    echo "Done."
}

if [ "$1" = "stop" ]; then
    stop_services
    exit 0
fi

if [ "$1" = "logs" ]; then
    cd "$DIR" && docker compose logs -f backend
    exit 0
fi

# ── Ensure node_modules ──
if [ ! -d "$DIR/node_modules" ]; then
    echo "Installing npm dependencies..."
    cd "$DIR" && npm install
fi

# ── Start Docker services ──
echo "Starting Docker services (db + slskd + backend)..."
cd "$DIR" && docker compose up -d db slskd backend

# Wait for backend to be ready
echo "Waiting for backend..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8002/api/core/health/ > /dev/null 2>&1; then
        echo "  Backend ready!"
        break
    fi
    sleep 2
done

if [ "$1" = "docker" ]; then
    echo "Docker services running. Frontend not started."
    echo "  Backend:  http://localhost:8002"
    echo "  slskd:    http://localhost:5030"
    echo "  Admin:    http://localhost:8002/admin/"
    exit 0
fi

# ── Start Vite (foreground so Ctrl+C kills everything) ──
echo "Starting Vite dev server on :5174..."
echo "──────────────────────────────────────"
echo "  Frontend: http://localhost:5174"
echo "  Backend:  http://localhost:8002"
echo "  slskd:    http://localhost:5030"
echo "  Admin:    http://localhost:8002/admin/"
echo "──────────────────────────────────────"
trap "stop_services" EXIT

cd "$DIR" && npx vite --host --port 5174
