#!/usr/bin/env bash
#
# Go and find the pages the System Brain is asking a person about.
#
# Run this on the server. It works through the coins listed under "Pages not found" and,
# for each one, looks again for the project's own news page: the curated table, the
# market-data provider's record, the identity a reviewer approved, the links the project's
# own homepage carries, an open-web search, the usual /blog and /news addresses, and - if
# it is switched on - a model. Anything it finds is fetched, checked against the site's
# own robots policy, read and dated before it counts.
#
# A coin it settles has its task closed. A coin it cannot settle has its task rewritten
# with the addresses tried this time and, for each one, why it did not work.
#
# It decides no Shariah status and publishes nothing. It only finds and proves addresses.
#
#   bash deploy/fix-missing-source-pages.sh                # every pending coin
#   bash deploy/fix-missing-source-pages.sh --limit 25     # the first 25, to try it out
#   bash deploy/fix-missing-source-pages.sh --dry-run      # fetch, change nothing
#   bash deploy/fix-missing-source-pages.sh --symbol HNT   # one coin by name
#
# Anything after the script name is passed straight to scripts/recheck_official_sources.py.
#
# It takes a long time. Every fetch is a request to somebody else's server and the
# scraper waits a second between them, so a few hundred coins is hours, not minutes.
# Start it inside tmux or screen so closing the terminal does not kill it.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=deploy/resource_guard.sh
source "$ROOT_DIR/deploy/resource_guard.sh"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${TRACEDGE_ENV_FILE:-.env.production}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="${REPORT_DIR:-reports}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. This has to run on the server, next to the real settings file." >&2
  exit 1
fi

export TRACEDGE_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

mkdir -p "$REPORT_DIR"

# Reported, not enforced — see the note on memory_report_swap in deploy/resource_guard.sh.
#
# It matters more here than in an ordinary deploy. This run may start a headless Chromium,
# which is the largest thing that ever runs on this machine, and the machine has 3.9 GB.
# With no swap the kernel has no slack at all: that is how it came to kill systemd on
# 22 August 2026 and take SSH down with the site. If this prints "no swap", the safe way
# to run is with SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES set low, or with the browser off.
memory_report_swap || true

# Run in a container of its own rather than inside the running worker. Two reasons, and
# both matter on a 3.9 GB server with no swap:
#
#  * the worker is busy. A source sweep started inside it competes with whatever scan is
#    running for the same 1024 MB ceiling, and the loser is killed by Docker;
#  * this run may start a browser, and a browser is the largest thing on the machine. In
#    its own container it has its own ceiling, and if it grows too far only this run dies.
#
# --rm so nothing is left behind, and -T because there is no terminal to attach.
#
# There is deliberately no separate pre-flight check here. The Python script prints what
# it can use - the browser, the search key, the cadence - as its first lines, and it is
# the one that has to be right. A second copy in shell would be a second owner of the
# same report, and the two would eventually disagree.
#
# `mem_limit` from the compose file applies to this container too, so the browser is
# bounded exactly as it is in the worker.
echo "Working through the coins with no found pages. This takes hours; leave it running."
echo
"${COMPOSE[@]}" run --rm -T --no-deps api \
  python scripts/recheck_official_sources.py \
  --pending-only \
  --report "exports/fix-missing-source-pages-${STAMP}.md" \
  --json "exports/fix-missing-source-pages-${STAMP}.json" \
  "$@"

# The report is written inside the container, onto the `exports` volume that every
# service shares. Copy it out so it can be read without going back into Docker.
echo
echo "Copying the report out of the container..."
CONTAINER_ID="$("${COMPOSE[@]}" ps -q api | head -n 1)"
COPIED=""
if [[ -n "$CONTAINER_ID" ]]; then
  for suffix in md json; do
    if docker cp \
      "${CONTAINER_ID}:/app/exports/fix-missing-source-pages-${STAMP}.${suffix}" \
      "${REPORT_DIR}/" 2>/dev/null; then
      COPIED="yes"
    fi
  done
fi

echo
echo "Finished."
if [[ -n "$COPIED" ]]; then
  echo "Report: ${REPORT_DIR}/fix-missing-source-pages-${STAMP}.md"
else
  # The run wrote it onto the shared `exports` volume even when the copy could not
  # happen, so say where it is rather than letting it look lost.
  echo "The report could not be copied out, but it was written. It is on the shared"
  echo "exports volume at /app/exports/fix-missing-source-pages-${STAMP}.md - read it with:"
  echo "  ${COMPOSE[*]} run --rm -T --no-deps api cat /app/exports/fix-missing-source-pages-${STAMP}.md"
fi
echo "Open the System Brain and look at 'Pages not found' - the coins this run settled"
echo "are gone from it. The ones still there say what was tried and why each address failed."
