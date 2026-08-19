#!/usr/bin/env bash
# Full clean redeploy: new source, no build cache, new containers, new images.
#
# Use this when an edit is on GitHub but the live site still shows the old thing.
# It removes every layer that can hold an old copy:
#   git working tree -> Docker build cache -> BuildKit pip/pnpm caches ->
#   running containers -> old images -> the browser copy (the ?v= key)
#
# What it never deletes: the PostgreSQL data, the Redis data, the exported files,
# and the Caddy TLS certificates. Those live in named volumes and are kept.
#
#   bash deploy/redeploy-clean.sh              # normal clean redeploy
#   FLUSH_REDIS=1 bash deploy/redeploy-clean.sh  # also wipe the Redis cache (see warning)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${TRACEDGE_ENV_FILE:-.env.production}"
FLUSH_REDIS="${FLUSH_REDIS:-0}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.production.example to $ENV_FILE and fill it first." >&2
  exit 1
fi

export TRACEDGE_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "1/9  Backing up the database before anything is torn down"
# The backup is written OUTSIDE the repository on purpose. Step 2 runs `git clean`,
# which deletes untracked files inside the repository - a backup stored in-tree would
# be destroyed a few seconds after it was made.
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/../hilalmarkets-backups}"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/predeploy-$(date -u +%Y%m%d-%H%M%S).sql.gz"
# shellcheck disable=SC2016
if "${COMPOSE[@]}" exec -T db sh -c \
     'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null | gzip > "$BACKUP_FILE"; then
  echo "database backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
else
  rm -f "$BACKUP_FILE"
  echo "no running database to back up - continuing (first deploy, or stack already down)"
fi

step "2/9  Replacing the source with an exact copy of origin/$BRANCH"
git fetch origin "$BRANCH" --prune
git reset --hard "origin/$BRANCH"
# No -x: the .gitignore rule `.env.*` keeps .env.production, and ignored data stays.
git clean -fd
echo "now at: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"

step "3/9  Stopping and removing the old containers"
# --remove-orphans clears services that no longer exist in the compose file.
# No -v on purpose: that flag would delete the Redis data, the exported files and
# the Caddy certificates, and re-issuing certificates can hit the Let's Encrypt limit.
"${COMPOSE[@]}" down --remove-orphans

step "4/9  Emptying the Docker build cache"
# --no-cache alone does NOT clear the pip and pnpm caches the Dockerfile mounts,
# so an old wheel or an old node package can still be reused. This clears them.
docker builder prune --all --force

step "5/9  Building the images from zero"
"${COMPOSE[@]}" build --no-cache --pull

step "6/9  Starting the database and Redis"
"${COMPOSE[@]}" up -d db redis

if [[ "$FLUSH_REDIS" == "1" ]]; then
  step "6b/9  Emptying Redis"
  # WARNING: this also drops queued background jobs, the AI budget counters, and any
  # setup chat a user has half finished. Only use it when a stale cached value is the
  # actual problem.
  "${COMPOSE[@]}" exec -T redis redis-cli FLUSHALL
fi

step "7/9  Upgrading the database schema"
"${COMPOSE[@]}" run --rm api alembic upgrade head

step "8/9  Starting everything with new containers"
# --force-recreate rebuilds every container even when Compose thinks nothing changed.
"${COMPOSE[@]}" up -d --force-recreate --remove-orphans
docker image prune --force
"${COMPOSE[@]}" ps

step "9/9  Checking that the new code is really the code that is running"
"${COMPOSE[@]}" exec -T api python - <<'PY'
import json
import urllib.request

for path in ("/health", "/health/deep"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=15) as response:
        print(path, json.loads(response.read().decode("utf-8")))
PY

echo
echo "Asset version the live pages ask browsers to load:"
curl -fsS https://hilalmarkets.com/ | grep -o '?v=[A-Za-z0-9._-]*' | sort -u || true
echo "Asset version in the source you just deployed:"
grep -rho '?v=[A-Za-z0-9._-]*' src/ai_market_monitor/templates | sort -u

cat <<'DONE'

Deployment finished.
  Public site: https://hilalmarkets.com
  Dashboard:   https://app.hilalmarkets.com/dashboard

If the two asset versions above do not match, the browser or the CDN is still
holding the old files - purge the CDN cache, then reload with Ctrl+Shift+R.
DONE
