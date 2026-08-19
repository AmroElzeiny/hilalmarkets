#!/usr/bin/env bash
# Full clean redeploy: new source, no build cache, new containers, new images.
#
# Use this when an edit is on GitHub but the live site still shows the old thing.
# It removes every layer that can hold an old copy:
#   git working tree -> Docker build cache -> BuildKit pip/pnpm caches ->
#   running containers -> old images
#
# What it NEVER deletes:
#   - the PostgreSQL data, the Redis data, the exported files, the TLS certificates
#     (they live in Docker volumes, which this script does not remove)
#   - .env.production, or any other file that is not in GitHub
#
#   bash deploy/redeploy-clean.sh
#   FLUSH_REDIS=1 bash deploy/redeploy-clean.sh   # also wipe the Redis cache (see warning)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${TRACEDGE_ENV_FILE:-.env.production}"
FLUSH_REDIS="${FLUSH_REDIS:-0}"
PUBLIC_URL="${PUBLIC_URL:-https://hilalmarkets.com}"
APP_URL="${APP_URL:-https://app.hilalmarkets.com}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.production.example to $ENV_FILE and fill it first." >&2
  exit 1
fi

export TRACEDGE_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }

step "0/9  Checking no other container owns the web ports"
# A container from an older Compose project can keep holding ports 80 and 443 forever.
# When that happens this project's caddy silently stays in "Created" state, the old
# container keeps serving the site with its own old configuration and its own old
# certificates, and every change to deploy/Caddyfile is ignored. It looks like the site
# works, so nobody notices until the old container stops. This is exactly what happened
# on 19 Aug 2026: a caddy left over from the "tracedge" project name held both ports.
# Compose itself is asked which containers are ours, so no second copy of Compose's
# project-naming rules is written here.
OUR_CONTAINERS="$("${COMPOSE[@]}" ps -aq 2>/dev/null || true)"
OUR_PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' \
  $(echo "$OUR_CONTAINERS" | head -1) 2>/dev/null || true)"
for port in 80 443; do
  while read -r intruder_id; do
    [[ -z "$intruder_id" ]] && continue
    if [[ -n "$OUR_CONTAINERS" ]] && grep -Fxq "$intruder_id" <<<"$OUR_CONTAINERS"; then
      continue  # our own caddy, still running from the previous deploy - expected
    fi
    intruder_name="$(docker inspect -f '{{.Name}}' "$intruder_id" | sed 's|^/||')"
    intruder_project="$(docker inspect \
      -f '{{index .Config.Labels "com.docker.compose.project"}}' "$intruder_id" 2>/dev/null || true)"
    echo "Container '$intruder_name' (compose project '${intruder_project:-none}') holds port $port." >&2
    echo "It does not belong to this deployment, so this project's caddy can never start." >&2
    cat <<HINT >&2

Nothing was changed. Fix this once, keeping the existing certificates so that
Let's Encrypt is not asked for new ones:

  OLDVOL=\$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' $intruder_name)
  docker volume create ${OUR_PROJECT:-<project>}_caddy_data
  docker run --rm -v "\$OLDVOL":/from:ro -v ${OUR_PROJECT:-<project>}_caddy_data:/to alpine cp -a /from/. /to/
  docker update --restart=no $intruder_name && docker stop $intruder_name && docker rm $intruder_name

HINT
    fail "another container owns port $port"
  done < <(docker ps --filter "publish=$port" --no-trunc --format '{{.ID}}')
done
echo "ports 80 and 443 belong to this deployment only"

step "0b/9  Checking no other container is running PostgreSQL"
# This is the check that matters most. On 19 Aug 2026 .env.production said
# COMPOSE_PROJECT_NAME=hilalmarkets while POSTGRES_VOLUME_NAME still pointed at the live
# volume of the older "traceedge" project, whose stack was still running. Deploying
# started a SECOND PostgreSQL server on the SAME data folder. Two servers writing one
# folder destroyed it: a data page ended up stamped ahead of the end of the write-ahead
# log, PostgreSQL could not complete a checkpoint, and the database would not start at
# all. Recovery needed a full restore from a dump, and one duplicated row had to be
# removed by hand before the primary key could be rebuilt.
#
# Docker will not stop this happening: an external volume can be mounted by any number of
# containers. Nothing except this check stands between a rename and a destroyed database.
while read -r other_id; do
  [[ -z "$other_id" ]] && continue
  if [[ -n "$OUR_CONTAINERS" ]] && grep -Fxq "$other_id" <<<"$OUR_CONTAINERS"; then
    continue  # our own db from the previous deploy - it is stopped before ours starts
  fi
  other_vol="$(docker inspect \
    -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' \
    "$other_id" 2>/dev/null || true)"
  [[ -z "$other_vol" ]] && continue
  other_name="$(docker inspect -f '{{.Name}}' "$other_id" | sed 's|^/||')"
  echo "Container '$other_name' is running PostgreSQL on volume '$other_vol'." >&2
  echo "It is not part of this deployment." >&2
  cat <<HINT >&2

Nothing was changed. Before deploying, check whether that volume is the same one this
project uses:

  grep POSTGRES_VOLUME_NAME $ENV_FILE

If it is the SAME name, two PostgreSQL servers would run on one data folder and destroy
it. Stop the other stack first:

  docker update --restart=no $other_name && docker stop $other_name

If it is a DIFFERENT name, the other stack is a leftover that still serves nothing here -
retire it the same way, so it cannot take the web ports back.

HINT
  fail "another container is running PostgreSQL"
done < <(docker ps -q)
echo "this deployment owns its database folder alone"

step "1/9  Checking the server has nothing that only exists here"
# An earlier version of this script ran `git reset --hard` and `git clean -fd`. Both
# destroy work that lives only on the server, so neither runs any more. If the working
# tree is not clean, the deploy stops and a person decides what to do.
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "The working tree is not clean. These files are changed or are not in GitHub:" >&2
  git status --short >&2
  cat <<'HINT' >&2

Nothing was changed. Decide what these are, then run this script again:
  - a real change you want to keep   -> commit and push it
  - a file that belongs only here    -> add it to .gitignore, or move it out of the repo
  - rubbish you do not need          -> delete just that file by hand
HINT
  fail "refusing to deploy over uncommitted or untracked files"
fi

step "2/9  Backing up the database before anything is torn down"
# The backup is written OUTSIDE the repository so that no git or docker command in this
# script can reach it.
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

step "3/9  Getting the new code"
git fetch origin "$BRANCH" --prune
git merge --ff-only "origin/$BRANCH"
echo "now at: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"

step "4/9  Stopping and removing the old containers"
# No -v on purpose: that flag would delete the Redis data, the exported files and the
# TLS certificates, and re-issuing certificates can hit the Let's Encrypt limit.
"${COMPOSE[@]}" down --remove-orphans

step "5/9  Emptying the Docker build cache"
# --no-cache alone does NOT clear the pip and pnpm caches the Dockerfile mounts, so an
# old wheel or an old node package can still be reused. This clears them.
docker builder prune --all --force

step "6/9  Building the images from zero"
"${COMPOSE[@]}" build --no-cache --pull

step "7/9  Starting the database, then upgrading the schema"
"${COMPOSE[@]}" up -d db redis

if [[ "$FLUSH_REDIS" == "1" ]]; then
  echo "-- emptying Redis --"
  # WARNING: this also drops queued background jobs, the AI budget counters, and any
  # setup chat a user has half finished.
  "${COMPOSE[@]}" exec -T redis redis-cli FLUSHALL
fi

"${COMPOSE[@]}" run --rm api alembic upgrade head

step "8/9  Starting everything with new containers"
"${COMPOSE[@]}" up -d --force-recreate --remove-orphans
docker image prune --force

step "9/9  Checking the site the way a visitor sees it"

# Check 1: every service in the compose file is actually running.
# The previous version of this script only asked the api container about its own health.
# That answered "ok" while caddy - the container that owns ports 80 and 443 - was not
# running at all, so the deploy looked successful while the site was offline. A service
# that is missing here is the single most likely reason a site is down after a deploy.
expected_services="$("${COMPOSE[@]}" config --services | sort)"
running_services="$("${COMPOSE[@]}" ps --services --status running | sort)"
missing_services="$(comm -23 <(echo "$expected_services") <(echo "$running_services"))"
if [[ -n "$missing_services" ]]; then
  echo "These services are NOT running:" >&2
  echo "$missing_services" | sed 's/^/  - /' >&2
  echo >&2
  "${COMPOSE[@]}" ps -a >&2
  for service in $missing_services; do
    echo "--- last 40 log lines: $service ---" >&2
    "${COMPOSE[@]}" logs --tail=40 "$service" >&2 || true
  done
  fail "$(echo "$missing_services" | tr '\n' ' ')did not start"
fi
"${COMPOSE[@]}" ps

# Check 2: the app answers from inside. Proves the code and database are working.
"${COMPOSE[@]}" exec -T api python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health/deep", timeout=15) as response:
    print("inside the container:", json.loads(response.read().decode("utf-8")))
PY

# Check 3: the real public addresses answer. This is the only check that proves TLS,
# Caddy, DNS and the CDN are all working - the path an actual visitor takes.
public_ok=1
check_url() {
  local label="$1" url="$2" code
  code="$(curl -sS -o /dev/null -w '%{http_code}' -L --max-time 25 "$url" 2>/dev/null || echo 000)"
  case "$code" in
    200|301|302|303|401) printf '  %-28s %s  OK\n' "$label" "$code" ;;
    000)                 printf '  %-28s no answer at all\n' "$label"; public_ok=0 ;;
    *)                   printf '  %-28s %s\n' "$label" "$code"; public_ok=0 ;;
  esac
}
echo "public addresses:"
check_url "$PUBLIC_URL/" "$PUBLIC_URL/"
check_url "$APP_URL/dashboard" "$APP_URL/dashboard"
if [[ "$public_ok" != "1" ]]; then
  echo >&2
  echo "--- last 60 log lines: caddy ---" >&2
  "${COMPOSE[@]}" logs --tail=60 caddy >&2 || true
  fail "the containers are running but the public site does not answer"
fi

# Check 4: the version browsers are told to load matches the version just deployed.
echo
echo "asset version served to browsers:"
curl -fsS "$PUBLIC_URL/" | grep -o '?v=[A-Za-z0-9._-]*' | sort -u | sed 's/^/  /' || true
echo "asset version in the deployed source:"
grep -rho '?v=[A-Za-z0-9._-]*' src/ai_market_monitor/templates | sort -u | sed 's/^/  /'

cat <<DONE

Deployment finished, and the public site answered.
  Public site: $PUBLIC_URL
  Dashboard:   $APP_URL/dashboard

If the two asset versions above do not match, the CDN or the browser is still holding
the old files - purge the CDN cache, then reload with Ctrl+Shift+R.
DONE
