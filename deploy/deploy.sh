#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${TRACEDGE_ENV_FILE:-.env.production}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.production.example to $ENV_FILE and fill the placeholders first." >&2
  exit 1
fi

export TRACEDGE_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "Pulling latest source..."
git pull --ff-only

echo "Building production images..."
"${COMPOSE[@]}" build

echo "Starting database and Redis..."
"${COMPOSE[@]}" up -d db redis

echo "Running database migrations..."
"${COMPOSE[@]}" run --rm api alembic upgrade head

echo "Starting TraceEdge services..."
"${COMPOSE[@]}" up -d

echo "Container status:"
"${COMPOSE[@]}" ps

echo "Checking API health from inside the API container..."
"${COMPOSE[@]}" exec -T api python - <<'PY'
import json
import urllib.request

for path in ("/health", "/health/deep"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
        payload = response.read().decode("utf-8")
    print(path, json.loads(payload))
PY

echo "Deployment finished."
echo "Public site: https://trace-edge.com"
echo "Dashboard:   https://app.trace-edge.com/dashboard"
