#!/usr/bin/env bash
# Behaviour tests for deploy/resource_guard.sh.
#
#   bash tests/shell/resource_guard_cases.sh
#
# `tests/unit/test_invariant_deploy_disk_safety.py` runs this file through pytest wherever
# a working bash exists, and skips it where one does not.
#
# These assert the rule, not one case: every way of writing a bad "keep" is refused, not
# just the obvious zero, and the surviving dumps are checked by name rather than by count.
# A count alone passes even when the code keeps the OLDEST files, which is the mistake that
# would actually destroy a recovery.
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# shellcheck source=deploy/resource_guard.sh
source "$REPO_ROOT/deploy/resource_guard.sh"

passed=0
failed=0
check() {
  if [[ "$2" == "$3" ]]; then
    echo "  ok   $1"
    passed=$((passed + 1))
  else
    echo "  FAIL $1"
    echo "         expected: [$3]"
    echo "         got:      [$2]"
    failed=$((failed + 1))
  fi
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

make_dumps() { for stamp in "$@"; do : > "$work/predeploy-$stamp.sql.gz"; done; }
dump_names() {
  find "$work" -maxdepth 1 -type f -name 'predeploy-*.sql.gz' -exec basename {} \; \
    | sort | tr '\n' ' '
}
file_names() {
  find "$work" -maxdepth 1 -type f -exec basename {} \; | sort | tr '\n' ' '
}

echo "== reading the disk =="
free_here="$(disk_free_gb "$REPO_ROOT")"
if [[ "$free_here" =~ ^[0-9]+\.[0-9]$ ]]; then
  echo "  ok   free space is a number ($free_here GB on $(disk_mount_of "$REPO_ROOT"))"
  passed=$((passed + 1))
else
  echo "  FAIL free space is not a number: [$free_here]"
  failed=$((failed + 1))
fi
check "a path that cannot be read gives no number" "$(disk_free_gb /no/such/path)" ""
check "a path that cannot be read gives no mount" "$(disk_mount_of /no/such/path)" ""

echo "== disk_require_free =="
if disk_require_free 0 "$REPO_ROOT" >/dev/null; then result=0; else result=1; fi
check "needing 0 GB is allowed" "$result" "0"
if disk_require_free 999999 "$REPO_ROOT" >/dev/null; then result=0; else result=1; fi
check "needing 999999 GB is refused" "$result" "1"
# A check that cannot read the disk must not invent a reason to refuse a good deploy.
if disk_require_free 999999 /no/such/path >/dev/null; then result=0; else result=1; fi
check "an unreadable path is not treated as full" "$result" "0"
# Two paths on one filesystem are one lot of room, so they are reported once.
check "one filesystem is reported once" \
  "$(disk_require_free 0 "$REPO_ROOT" "$REPO_ROOT/deploy" | grep -c 'GB free')" "1"

echo "== disk_prune_backups: which dumps survive =="
make_dumps 20260801-010000 20260802-010000 20260803-010000 20260804-010000 \
           20260805-010000 20260806-010000 20260807-010000 20260808-010000
disk_prune_backups "$work" 5 >/dev/null
check "keeping 5 of 8 leaves the NEWEST 5" "$(dump_names)" \
  "predeploy-20260804-010000.sql.gz predeploy-20260805-010000.sql.gz predeploy-20260806-010000.sql.gz predeploy-20260807-010000.sql.gz predeploy-20260808-010000.sql.gz "

# The deploy calls this twice in one run, before and after writing its dump.
disk_prune_backups "$work" 5 >/dev/null
check "running it twice changes nothing" "$(dump_names)" \
  "predeploy-20260804-010000.sql.gz predeploy-20260805-010000.sql.gz predeploy-20260806-010000.sql.gz predeploy-20260807-010000.sql.gz predeploy-20260808-010000.sql.gz "

disk_prune_backups "$work" 1 >/dev/null
check "keeping 1 leaves the newest one" "$(dump_names)" "predeploy-20260808-010000.sql.gz "

disk_prune_backups "$work" 5 >/dev/null
check "fewer dumps than the limit deletes nothing" "$(dump_names)" \
  "predeploy-20260808-010000.sql.gz "

echo "== disk_prune_backups: a bad limit is refused, never guessed =="
# Every one of these must be refused with exit 2 and delete nothing. A limit of 0, or a
# limit that is not a number at all, must never be read as "delete them all".
for bad_keep in 0 -1 "" abc 2.5 " 3" 01x; do
  if disk_prune_backups "$work" "$bad_keep" >/dev/null; then result=0; else result=$?; fi
  check "keep '$bad_keep' is refused with exit 2" "$result" "2"
  check "keep '$bad_keep' deleted nothing" "$(dump_names)" "predeploy-20260808-010000.sql.gz "
done

echo "== disk_prune_backups: nothing else is ever touched =="
: > "$work/.env.production"
: > "$work/notes.txt"
: > "$work/predeploy-20260809-010000.sql.gz.partial"
: > "$work/backup.sql.gz"
: > "$work/predeploy-20260809-010000.sql.gz"
disk_prune_backups "$work" 1 >/dev/null
check "only pre-deploy dumps are deleted" "$(file_names)" \
  ".env.production backup.sql.gz notes.txt predeploy-20260809-010000.sql.gz predeploy-20260809-010000.sql.gz.partial "

echo "== disk_prune_backups: a missing folder is not a failure =="
if disk_prune_backups "$work/does-not-exist" 3 >/dev/null; then result=0; else result=1; fi
check "a missing folder is fine" "$result" "0"

echo "== memory_report_swap =="
# All three branches are driven through MEMINFO_PATH. The branch that matters is "no
# swap" — it is the one that fires on the real server — and it can never be reached on a
# machine that has swap, which every machine running these tests does. Reading the real
# /proc/meminfo would therefore test only the branch that never mattered.
contains() { case "$2" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

printf 'MemTotal:        4046696 kB\nSwapTotal:             0 kB\n' > "$work/meminfo-none"
printf 'MemTotal:        4046696 kB\nSwapTotal:       4194304 kB\n' > "$work/meminfo-4g"

# 1. No swap: must return 1, say NONE, and give the exact commands that fix it. A return
#    code alone is not enough — nobody running a deploy sees a return code.
if out="$(MEMINFO_PATH=$work/meminfo-none memory_report_swap)"; then rc=0; else rc=$?; fi
check "no swap returns 1" "$rc" "1"
if contains "NONE" "$out" && contains "fallocate -l 4G /swapfile" "$out" \
   && contains "/etc/fstab" "$out"; then
  echo "  ok   no swap: says NONE and gives the fix, including making it permanent"
  passed=$((passed + 1))
else
  echo "  FAIL no swap: the message does not explain the fix"
  echo "         got: [$out]"
  failed=$((failed + 1))
fi

# 2. Swap present: must return 0 and print the size in GB, not in kilobytes.
if out="$(MEMINFO_PATH=$work/meminfo-4g memory_report_swap)"; then rc=0; else rc=$?; fi
check "4 GB of swap returns 0" "$rc" "0"
check "4 GB of swap is printed in GB" "$(echo "$out" | tr -s ' ')" " swap: 4.0 GB"

# 3. Unreadable: a diagnostic must never become the failure. It must not refuse, and it
#    must not claim the machine has no swap when it simply could not look.
if out="$(MEMINFO_PATH=$work/no-such-file memory_report_swap)"; then rc=0; else rc=$?; fi
check "an unreadable /proc/meminfo does not refuse" "$rc" "0"
if contains "not checked" "$out" && ! contains "NONE" "$out"; then
  echo "  ok   unreadable: says it could not check, does not claim there is no swap"
  passed=$((passed + 1))
else
  echo "  FAIL unreadable: [$out]"
  failed=$((failed + 1))
fi

echo
echo "passed=$passed failed=$failed"
[[ "$failed" == "0" ]]
