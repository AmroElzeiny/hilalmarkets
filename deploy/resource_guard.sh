#!/usr/bin/env bash
# One owner for "does this server have what it needs" — room on the disk, and swap.
#
# This file is sourced, never run:
#
#   source "$(dirname "${BASH_SOURCE[0]}")/resource_guard.sh"
#
# Every deploy script and the emergency cleanup read the machine through these functions,
# so they all report the same number, in the same unit, and all refuse for the same
# reason. A second `df | awk` written somewhere else is how two scripts start disagreeing
# about whether there is room to work.
#
# Reading the disk is a diagnostic, and a diagnostic must never become the failure. When
# `df` cannot answer, these functions print nothing and the caller treats the answer as
# unknown. They never answer zero: zero would refuse a deploy that was perfectly fine.

# python_bin
#
# The Python interpreter to run **on this server**. Prints its path and returns 0, or
# prints nothing and returns 1 when the server has none.
#
# Debian and Ubuntu ship `python3` and **no `python` at all** — the bare name has not been
# a command on a default install since 2020. A deploy script that types `python` therefore
# dies on a clean server, and under `set -u` the real message is buried: the next line
# fails with an unrelated "unbound variable" because the array the command should have
# filled is empty. That is exactly how `deploy/deploy.sh` failed on 2 September 2026:
#
#     deploy.sh: line 23: python: command not found
#     deploy.sh: line 27: COMPOSE_IDENTITY[0]: unbound variable
#
# `python3` is tried first on purpose. Where both exist they are usually the same binary,
# and where they differ `python` is the one more likely to be an old Python 2.
#
# This is only for Python that runs on the **host**. Python *inside* the application
# container is always plain `python`: the image is built on `python:3.12-slim`, where that
# is the real name.
python_bin() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

# Free space in gigabytes, one decimal place, on the filesystem holding $1.
# Prints nothing when the path cannot be read.
disk_free_gb() {
  df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%.1f", $4 / 1048576}'
}

# The mount point of the filesystem holding $1. Used so that several paths on one
# filesystem are reported once instead of counted as separate room.
disk_mount_of() {
  df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $6}'
}

# disk_require_free <minimum_gb> <path> [path...]
#
# Prints one line per distinct filesystem behind the given paths, and returns non-zero if
# any of them has less than <minimum_gb> free. A path that cannot be read is reported and
# skipped, never counted as full.
disk_require_free() {
  local minimum_gb="$1"
  shift
  local short=0 seen="" target mount free_gb
  for target in "$@"; do
    [[ -z "$target" ]] && continue
    mount="$(disk_mount_of "$target")"
    if [[ -z "$mount" ]]; then
      printf '  %-28s could not be read - not checked\n' "$target"
      continue
    fi
    # Already reported through another path on the same filesystem.
    case $'\n'"$seen" in
      *$'\n'"$mount"$'\n'*) continue ;;
    esac
    seen="$seen$mount"$'\n'
    free_gb="$(disk_free_gb "$target")"
    if [[ -z "$free_gb" ]]; then
      printf '  %-28s could not be read - not checked\n' "$mount"
      continue
    fi
    printf '  %-28s %s GB free\n' "$mount" "$free_gb"
    # awk compares, not bash: bash cannot compare "4.7" with "5" at all.
    if awk -v have="$free_gb" -v need="$minimum_gb" \
         'BEGIN { exit !(have + 0 < need + 0) }'; then
      short=1
    fi
  done
  return "$short"
}

# disk_prune_backups <folder> <keep>
#
# Deletes pre-deploy database dumps from <folder> until only the newest <keep> are left.
# Prints every name it deletes, then a one-line summary.
#
# Returns 0 when it did its work, and 2 without deleting anything when <keep> is not a
# whole number of 1 or more. It refuses rather than choosing a number for the caller,
# because the two callers want different answers to a bad setting: a deploy must stop and
# have the setting fixed, while the emergency cleanup runs on a server that is already
# down and settles on 1 so that it can still free room. Deleting every dump is never one
# of the answers - a server with no backup at all is a worse problem than a full disk.
#
# There is one copy of this on purpose. The deploy writes the dumps and the emergency
# cleanup deletes them, and two separate ideas of "which file is the newest" is how the
# wrong dump gets deleted by one of them.
disk_prune_backups() {
  local folder="$1" keep="$2"
  local removed=0 old

  if ! [[ "$keep" =~ ^[0-9]+$ ]] || (( keep < 1 )); then
    printf '  keep must be a whole number of 1 or more, not "%s" - nothing deleted\n' "$keep"
    return 2
  fi
  if [[ ! -d "$folder" ]]; then
    printf '  no backup folder at %s - nothing to delete\n' "$folder"
    return 0
  fi

  # The names are predeploy-YYYYmmdd-HHMMSS.sql.gz written in UTC, so sorting the text is
  # the same as sorting by time. No file timestamp is trusted: a copied or restored dump
  # carries the wrong one, and then the wrong dump is the one deleted.
  while IFS= read -r old; do
    [[ -z "$old" ]] && continue
    if rm -f "$old"; then
      printf '  deleted %s\n' "${old##*/}"
      removed=$((removed + 1))
    fi
  done < <(
    find "$folder" -maxdepth 1 -type f -name 'predeploy-*.sql.gz' -print 2>/dev/null \
      | sort -r \
      | tail -n +$((keep + 1))
  )

  printf '  %d old backup(s) deleted, newest %d kept (%s in %s file(s))\n' \
    "$removed" "$keep" \
    "$(du -sh "$folder" 2>/dev/null | cut -f1)" \
    "$(find "$folder" -maxdepth 1 -type f -name 'predeploy-*.sql.gz' 2>/dev/null | wc -l | tr -d ' ')"
  return 0
}

# memory_report_swap
#
# Prints how much swap the server has, and returns non-zero when it has none.
#
# Swap is the one protection against the 22 August 2026 outage that cannot live in this
# repository — it is a setting on the machine. The server had none, a Celery child grew to
# 1.4 GB on a 3.9 GB machine, and the kernel killed `systemd`, which took SSH down with the
# site. The other two protections are in the repository and cannot be forgotten: Celery's
# own child limits in core/config.py, and a memory ceiling per container in
# docker-compose.prod.yml.
#
# Checking it here is what stops swap being forgotten after the next server rebuild. A
# deploy is the moment somebody is looking, so a deploy is where the reminder belongs.
#
# It reports rather than refuses. No swap does not break the deploy that is running; it
# breaks the server hours later, under load. Refusing here would block an emergency deploy
# for a fault the deploy did not cause.
#
# MEMINFO_PATH exists so the tests can prove the branch that matters. The branch that
# fires on the real server is the "no swap" one, and it cannot be reached on a machine
# that has swap — which every machine running the tests does. Without the override the
# only tested branch would be the one that never mattered.
memory_report_swap() {
  local meminfo="${MEMINFO_PATH:-/proc/meminfo}"
  local total_kb
  total_kb="$(awk '/^SwapTotal:/ {print $2}' "$meminfo" 2>/dev/null)"
  if [[ -z "$total_kb" ]]; then
    printf '  swap: could not be read - not checked\n'
    return 0
  fi
  if (( total_kb > 0 )); then
    awk -v kb="$total_kb" 'BEGIN { printf "  swap: %.1f GB\n", kb / 1048576 }'
    return 0
  fi
  cat <<'WARNING'
  swap: NONE

  This server has no swap. With no swap the kernel has no slack: when memory runs out it
  kills a process immediately, and on 22 August 2026 the process it chose was systemd -
  the site went down and SSH stopped answering. Fix it once, as root:

      fallocate -l 4G /swapfile && chmod 600 /swapfile
      mkswap /swapfile && swapon /swapfile
      echo '/swapfile none swap sw 0 0' >> /etc/fstab

  The deploy is continuing. See "The server runs out of memory" in docs/OPERATIONS.md.
WARNING
  return 1
}
