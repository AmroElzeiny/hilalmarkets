#!/usr/bin/env bash
# Install Open Interpreter into its own environment (Linux, including the VPS).
#
#   tools/oi/bootstrap.sh
#
# Creates .oi-venv using Python 3.11 and installs the pinned set in requirements.txt.
# The project's own .venv is never touched: Open Interpreter needs Python < 3.12 and
# pulls in litellm, selenium and matplotlib, and putting any of that beside the product's
# pinned dependencies would change what the release gate's `pip check` and
# scripts/check_dependency_lock.py see.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${ROOT}/.oi-venv"
REQUIREMENTS="${ROOT}/tools/oi/requirements.txt"
PYTHON_EXE="${1:-python3.11}"

if ! command -v "${PYTHON_EXE}" >/dev/null 2>&1; then
  echo "Python 3.11 was not found. Open Interpreter 0.4.3 requires >=3.9,<3.12." >&2
  echo "Install it, or pass the path as the first argument." >&2
  exit 2
fi

echo "Creating ${VENV} with ${PYTHON_EXE}"
"${PYTHON_EXE}" -m venv "${VENV}"
VENV_PYTHON="${VENV}/bin/python"

"${VENV_PYTHON}" -m pip install --upgrade pip --quiet

# Everything except wget, from wheels only. Left to build from source, litellm demands a
# Rust toolchain that is neither needed nor present.
echo "Installing pinned dependencies (wheels only)"
grep -vE '^\s*(#|$)' "${REQUIREMENTS}" | grep -v '^wget' | \
  xargs "${VENV_PYTHON}" -m pip install --only-binary=:all:

# wget ships as a source archive only. It is pure Python and needs no compiler, but
# interpreter/terminal_interface/local_setup.py imports it at start-up, so it cannot be
# left out even though local models are never used.
echo "Installing wget (source archive, no compiler needed)"
"${VENV_PYTHON}" -m pip install "wget==3.2"

echo
"${VENV_PYTHON}" -c "import interpreter; print('Open Interpreter installed')"

cat <<'EOF'

Done. Check the configuration with:
    .venv/bin/python -m hm_oi doctor
Then start a session with:
    tools/oi/hm-oi.sh

Set HM_OI_API_KEY before the first session. It is separate from OPENAI_API_KEY on
purpose, so engineering spend is never billed as customer spend.
EOF
