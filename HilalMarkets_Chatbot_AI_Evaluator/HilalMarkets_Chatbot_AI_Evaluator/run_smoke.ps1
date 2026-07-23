$ErrorActionPreference = "Stop"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\playwright install chromium
.\.venv\Scripts\hm-chatbot-eval doctor
.\.venv\Scripts\hm-chatbot-eval run --mode smoke --target both
