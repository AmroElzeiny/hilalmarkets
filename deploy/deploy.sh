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

echo "Compose project and persistent volumes:"
mapfile -t COMPOSE_IDENTITY < <(
  "${COMPOSE[@]}" config --format json | python -c \
    'import json,sys; data=json.load(sys.stdin); print(data["name"]); print(data["volumes"]["postgres_data"]["name"])'
)
PROJECT_NAME="${COMPOSE_IDENTITY[0]}"
POSTGRES_VOLUME="${COMPOSE_IDENTITY[1]}"
echo "project=$PROJECT_NAME"
echo "selected_postgres_volume=$POSTGRES_VOLUME"
docker volume ls \
  --filter label=com.docker.compose.volume=postgres_data \
  --format 'postgres_volume={{.Name}}'
if ! docker volume inspect "$POSTGRES_VOLUME" >/dev/null 2>&1; then
  EXISTING_POSTGRES_VOLUMES="$(
    docker volume ls \
      --filter label=com.docker.compose.volume=postgres_data \
      --format '{{.Name}}'
  )"
  if [[ -n "$EXISTING_POSTGRES_VOLUMES" ]]; then
    echo "Refusing to create a new PostgreSQL volume while another Compose PostgreSQL volume exists." >&2
    echo "Select the authoritative existing COMPOSE_PROJECT_NAME after comparing database backups and record counts." >&2
    exit 1
  fi
fi

echo "Pulling latest source..."
git pull --ff-only

echo "Building production images..."
"${COMPOSE[@]}" build

echo "Stopping application processes before the schema upgrade..."
"${COMPOSE[@]}" stop api worker scheduler || true

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
echo "Public site: https://hilalmarkets.com"
echo "Dashboard:   https://app.hilalmarkets.com/dashboard"
