#!/usr/bin/env bash
set -euo pipefail
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium
.venv/bin/hm-chatbot-eval doctor
.venv/bin/hm-chatbot-eval run --mode smoke --target both
