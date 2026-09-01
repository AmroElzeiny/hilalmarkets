#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=deploy/resource_guard.sh
source "$ROOT_DIR/deploy/resource_guard.sh"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${TRACEDGE_ENV_FILE:-.env.production}"
MIN_FREE_GB="${MIN_FREE_GB:-5}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.production.example to $ENV_FILE and fill the placeholders first." >&2
  exit 1
fi

export TRACEDGE_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

# Resolved once, and never typed as a bare `python`. See python_bin in resource_guard.sh:
# a default Ubuntu has `python3` only, and the missing command shows up two lines later as
# an unrelated "unbound variable".
if ! PYTHON_BIN="$(python_bin)"; then
  cat <<'HINT' >&2

No Python on this server, so the Compose project and its database volume cannot be read.
Nothing was changed.

Install it:

  apt-get update && apt-get install -y python3

HINT
  exit 1
fi

echo "Compose project and persistent volumes:"
mapfile -t COMPOSE_IDENTITY < <(
  "${COMPOSE[@]}" config --format json | "$PYTHON_BIN" -c \
    'import json,sys; data=json.load(sys.stdin); print(data["name"]); print(data["volumes"]["postgres_data"]["name"])'
)
# Checked before it is used. Without this the failure arrives as "COMPOSE_IDENTITY[0]:
# unbound variable", which names the array rather than the thing that actually went
# wrong — a Compose file that would not parse, or a volume this project does not declare.
if (( ${#COMPOSE_IDENTITY[@]} < 2 )); then
  echo "Could not read the Compose project name and the postgres_data volume name." >&2
  echo "Check that $COMPOSE_FILE parses: ${COMPOSE[*]} config" >&2
  exit 1
fi
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

# Building images needs room, and a build that runs out of disk part way leaves its
# half-written layers behind - so the next attempt starts with even less. The same check
# and the same reading of the disk as deploy/redeploy-clean.sh, from deploy/disk_guard.sh.
echo "Checking the server has room to build..."
DOCKER_ROOT="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || true)"
if ! disk_require_free "$MIN_FREE_GB" "$ROOT_DIR" ${DOCKER_ROOT:+"$DOCKER_ROOT"}; then
  cat <<HINT >&2

Less than ${MIN_FREE_GB} GB free. Nothing was changed.

Free the room first. That script only removes things the server can make again, and it
cannot reach a Docker volume, so the database, Redis, the exports and the certificates
are all safe:

  bash deploy/free-disk.sh

Never free room with \`docker system prune -a --volumes\` or \`docker volume prune\`.
With the stack stopped, both of them delete the database.
HINT
  exit 1
fi
# Reported, not enforced - see the note on memory_report_swap in deploy/resource_guard.sh.
memory_report_swap || true

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
