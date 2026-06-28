# TraceEdge

TraceEdge is a subscription monitoring and decision-support platform for crypto spot
traders. Users describe a setup, approve its structured interpretation, preview it against recent
market data, and receive explainable Telegram or Discord alerts. Version one never places trades.

## Local development

Python 3.12+ is required.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn ai_market_monitor.main:app --reload
```

For the complete service stack, install Docker and run `docker compose up --build`.

## Safety boundary

The LLM/interpretation layer may propose structured rules and explanations. Indicator values,
condition outcomes, Near-Miss scores, and lifecycle transitions are produced by deterministic
services only. Strategy activation requires explicit user approval and a recent-market preview.

See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md),
[docs/OPERATIONS.md](docs/OPERATIONS.md), and [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md).

Current beta infrastructure includes idempotent scheduled scans, shared CCXT REST clients,
deterministic proof persistence, setup lifecycle records, Telegram webhook delivery, Discord HTTP
delivery/interactions, and Stripe checkout/webhook support. See
[docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) for the required fail-closed
configuration.
