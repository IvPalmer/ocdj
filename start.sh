#!/bin/bash
# DJ Tools — start everything
set -e

# Ensure Docker is running
if ! docker info &>/dev/null; then
  echo "Starting Docker..."
  open -a Docker
  while ! docker info &>/dev/null; do sleep 1; done
  echo "Docker ready."
fi

# Start all services
docker compose up -d --build

echo ""
echo "DJ Tools is running:"
echo "  Frontend:  http://localhost:5174"
echo "  Backend:   http://localhost:8002"
echo "  slskd:     http://localhost:5030"
echo ""
echo "Logs: docker compose logs -f"
