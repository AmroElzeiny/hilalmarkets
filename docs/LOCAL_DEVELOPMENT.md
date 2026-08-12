# Local Development

Python 3.12+ is required. PostgreSQL and Redis are recommended for integration work.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn ai_market_monitor.main:app --reload
```

The checked-in `.env.example` is host-run friendly: SQLite plus localhost Redis. Docker Compose
overrides `DATABASE_URL` and `REDIS_URL` inside containers so they can use the Compose service names
`db` and `redis`.

Run the validation suite:

```powershell
python -m ruff check .
python -m mypy src/ai_market_monitor src/hm_chatbot_eval
python -m pytest --ignore=tests/browser
python -m alembic heads
```

These match the release gate exactly. The earlier version of this list checked narrower
paths than the gate does — `ruff check src tests alembic/env.py` passes locally while
`ruff check .` fails in CI — so it is now kept in step with it.

**`.agents/commands.json` is the authoritative list of every engineering command**, with
each one marked as safe, test-only, paid, staging or production.
`scripts/check_oi_command_catalog.py` fails if the release gate grows a step the list does
not know about. Browse it with:

```powershell
python -m hm_oi commands
python -m hm_oi plan engine        # which tests to run for a change in engine/
```

Mock providers are allowed only in development and test. Do not put real secrets in `.env.example`
or commit a local `.env`.
