#!/usr/bin/env bash
# Start the HilalMarkets engineering assistant (Linux, including the VPS).
#
#   tools/oi/hm-oi.sh                  normal tier, you approve each command
#   HM_OI_TIER=fast tools/oi/hm-oi.sh  cheap tier for lookups
#   HM_OI_TIER=deep tools/oi/hm-oi.sh  architecture, security, hard bugs
#
# Deliberately thin. Credential scrubbing and everything else lives in
# src/hm_oi/launch.py, so Windows and Linux cannot drift apart.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${ROOT}/.oi-venv/bin/python"

if [ ! -x "${PYTHON}" ]; then
  echo "Open Interpreter is not installed. Run tools/oi/bootstrap.sh first." >&2
  exit 2
fi

export HM_OI_REPO_ROOT="${ROOT}"
export HM_OI_TIER="${HM_OI_TIER:-normal}"
export HM_OI_AUTO_RUN="${HM_OI_AUTO_RUN:-0}"

exec "${PYTHON}" "${ROOT}/tools/oi/launch.py" "$@"
