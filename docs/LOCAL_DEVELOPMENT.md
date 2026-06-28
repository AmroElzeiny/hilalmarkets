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
python -m ruff check src tests alembic/env.py
python -m mypy src/ai_market_monitor
python -m pytest -q
python -m compileall -q src
python -m alembic check
```

Mock providers are allowed only in development and test. Do not put real secrets in `.env.example`
or commit a local `.env`.
