#!/usr/bin/env bash
# Emergency: the server disk is full and the site is down.
#
# Run this FIRST, before any deploy. It only removes things the server can make again:
#
#   old pre-deploy database dumps -> container log files -> the systemd journal ->
#   the apt package cache -> the Docker build cache -> Docker images nothing is using
#
# What it can NEVER touch:
#   - any Docker volume. The PostgreSQL data, the Redis data, the exported files and the
#     TLS certificates all live in volumes, and no command below can reach one.
#   - .env.production, or any other file that is not in GitHub.
#   - the newest KEEP database dumps.
#
#   bash deploy/free-disk.sh                # safe cleanup
#   KEEP=1 bash deploy/free-disk.sh         # keep only the newest dump - more room
#   AGGRESSIVE=1 bash deploy/free-disk.sh   # also delete images no container is running
#
# ============================================================================
# NEVER run these two on this server, whatever any web page tells you:
#
#     docker system prune -a --volumes
#     docker volume prune
#
# When the stack is stopped, Docker counts the PostgreSQL volume as unused, and both
# commands delete it. That is the entire database. This project already lost its database
# once, on 19 August 2026, and the restore took a full dump plus hand repair.
# ============================================================================
set -uo pipefail

# On purpose there is no `set -e`. The disk is full, so single commands are expected to
# fail. Every step must still be tried: a step that fails frees nothing, but a step that
# is skipped is room the server never gets back.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/resource_guard.sh
source "$(dirname "${BASH_SOURCE[0]}")/resource_guard.sh"

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/../hilalmarkets-backups}"
KEEP="${KEEP:-3}"
JOURNAL_KEEP="${JOURNAL_KEEP:-200M}"
AGGRESSIVE="${AGGRESSIVE:-0}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# Never delete every dump, whatever KEEP says. A server with no backup at all is a worse
# problem than a server with a full disk.
if ! [[ "$KEEP" =~ ^[0-9]+$ ]] || (( KEEP < 1 )); then
  note "KEEP must be a whole number of 1 or more - using 1 instead of '$KEEP'"
  KEEP=1
fi

if [[ "$(id -u 2>/dev/null || echo 1)" != "0" ]]; then
  note "Not running as root. The log, journal and apt steps will probably be skipped."
  note "Run again with sudo to free everything."
fi

BEFORE_GB="$(disk_free_gb /)"

step "Where the disk stands now"
df -h / 2>/dev/null || note "df could not read the disk"
docker system df 2>/dev/null || note "Docker is not answering - its steps will be skipped"

step "1/6  Deleting old pre-deploy database dumps, keeping the newest $KEEP"
# These are written by deploy/redeploy-clean.sh, one on every deploy. Until 22 August 2026
# nothing ever deleted them, and they sit on the same disk as the database. Deleting them
# is deploy/resource_guard.sh's job, the same code the deploy itself uses, so the two can
# never disagree about which dump is the newest one to keep.
note "folder: $BACKUP_DIR"
disk_prune_backups "$BACKUP_DIR" "$KEEP"

# Somebody running this script is dealing with a server in trouble, and "the disk filled
# up" and "the memory ran out" look identical from outside: the site stops answering and
# so does SSH. On 22 August 2026 that exact confusion cost an hour. Reporting the swap
# here costs nothing.
memory_report_swap || true

step "2/6  Emptying the container log files"
# docker-compose.prod.yml caps these at 50 MB x 5 files per service, so the worst case is
# bounded - but seven services is still more than a gigabyte, and on a full disk that
# gigabyte is the difference between a database that can write and one that cannot.
# The files are emptied, not deleted: a running container keeps writing into the file it
# already opened, so deleting it frees nothing until the container restarts.
if [[ -d /var/lib/docker/containers ]]; then
  note "before: $(du -sh /var/lib/docker/containers 2>/dev/null | cut -f1)"
  find /var/lib/docker/containers -maxdepth 2 -type f -name '*-json.log' \
    -exec truncate -s 0 {} \; 2>/dev/null
  note "after:  $(du -sh /var/lib/docker/containers 2>/dev/null | cut -f1)"
else
  note "no /var/lib/docker/containers here - skipping"
fi

step "3/6  Shrinking the systemd journal to $JOURNAL_KEEP"
journalctl --vacuum-size="$JOURNAL_KEEP" 2>&1 | tail -2 \
  || note "journalctl is not available - skipping"

step "4/6  Emptying the apt package cache"
if apt-get clean 2>/dev/null; then
  note "apt cache emptied"
else
  note "apt-get is not available - skipping"
fi

step "5/6  Emptying the Docker build cache"
# Pure cache. Everything here can be built again; nothing in it is data.
docker builder prune --all --force 2>&1 | tail -2 \
  || note "Docker is not answering - skipping"

step "6/6  Deleting Docker images nothing needs"
# Dangling images only: layers left behind by a build that was replaced or interrupted.
# A failed deploy leaves these, and a deploy that fails during the build never reaches the
# `docker image prune` at its own last step - so they pile up exactly when the disk is
# already tight.
docker image prune --force 2>&1 | tail -2 || note "Docker is not answering - skipping"
if [[ "$AGGRESSIVE" == "1" ]]; then
  note "AGGRESSIVE=1: also deleting images no RUNNING container uses"
  note "the next deploy must download and build them again, which needs disk and network"
  docker image prune --all --force 2>&1 | tail -2 || true
fi

step "Result"
AFTER_GB="$(disk_free_gb /)"
if [[ -n "$BEFORE_GB" && -n "$AFTER_GB" ]]; then
  awk -v b="$BEFORE_GB" -v a="$AFTER_GB" \
    'BEGIN { printf "    freed %.1f GB   (%.1f GB free before, %.1f GB free now)\n", a - b, b, a }'
fi
df -h / 2>/dev/null || true

# Still tight? Say where the space actually went, instead of leaving the operator to guess.
if [[ -n "$AFTER_GB" ]] && awk -v a="$AFTER_GB" 'BEGIN { exit !(a + 0 < 2) }'; then
  step "Still under 2 GB free - the biggest folders on this disk"
  du -xh --max-depth=2 / 2>/dev/null | sort -h | tail -20
  cat <<'HINT'

Next things to look at, in order:
  1. KEEP=1 AGGRESSIVE=1 bash deploy/free-disk.sh
  2. The PostgreSQL volume itself. Old scan history is deleted by a nightly task; if that
     task has not been running, the table can be very large.
  3. Growing the server's disk. Nothing else on this list is safe to delete.

Do NOT run `docker system prune -a --volumes` or `docker volume prune`. Both delete the
database.
HINT
fi
