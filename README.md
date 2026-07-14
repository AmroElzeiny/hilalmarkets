# HilalMarkets

HilalMarkets is a screened-market intelligence and monitoring platform for crypto spot
traders. Users describe a Watch Plan, approve its structured interpretation, preview it against
recent market data, and receive evidence-backed Telegram or Discord alerts. Version one never places trades.

The web Watch Plan Builder starts with AI Setup Chat. The server-side interviewer keeps a durable
conversation, asks for measurable definitions, compiles only into the validated strategy DSL, shows
confidence/lint/assumption evidence, and creates an immutable approved strategy version only after
explicit user approval. `OPENAI_API_KEY` is server-side only; `OPENAI_MODEL` is optional and defaults
to `gpt-5.4-nano` with low reasoning.

An optional Bounded Agent Control coordinator can select among a small server-offered tool set for
messy, multi-intent chat turns. It is off by default, has a no-execution shadow mode, and uses a
stable percentage cohort for gradual live rollout. Registry,
compiler, provider, scanner, ownership, entitlement, hash, approval, and activation authority remain
in application services; the model never receives approval or activation tools. See
[docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md).

HilalMarkets also has a fail-closed Sharia-first market layer. Screened Market, one-time Scanner runs,
persistent Watch Plans, workers, opportunity evidence, and alerts share one versioned methodology
and universe resolver. Missing or non-executable governance data excludes the asset; AI cannot set
a religious status. The included development methodology is non-executable and contains no asset
conclusions. See
[docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md](docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md)
for architecture, deployment order, tests, and the qualified-human-review requirements.

If a user confirms a candle-computable mechanic that is not in the registry, HilalMarkets can create a
user-scoped, versioned mechanic through a bounded JSON expression DSL. The worker validates it,
tests it against the configured spot provider, independently reviews it, and requires the normal
strategy approval flow before activation. It never executes AI-generated Python or fabricates
provider data. See
[docs/CAPABILITY_EXTENSION_PIPELINE.md](docs/CAPABILITY_EXTENSION_PIPELINE.md).

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
Capability creation and five-scan reviews are asynchronous, so local non-Docker operation also
requires Redis, the Celery worker and the Celery beat scheduler described in
[docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

## Safety boundary

The LLM/interpretation layer may propose structured rules and explanations. Indicator values,
condition outcomes, Near-Miss scores, and lifecycle transitions are produced by deterministic
services only. Strategy activation requires explicit user approval and a recent-market preview.

See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md),
[docs/OPERATIONS.md](docs/OPERATIONS.md), [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md),
[docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md](docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md), and
[docs/CAPABILITY_EXTENSION_PIPELINE.md](docs/CAPABILITY_EXTENSION_PIPELINE.md), and
[docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md), and
[docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md](docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md).
The production page map, exact prototype asset mapping, and UI verification commands are in
[docs/HILALMARKETS_UI_MIGRATION.md](docs/HILALMARKETS_UI_MIGRATION.md); the supplied design,
component, UX, and QA references are preserved under [docs/hilalmarkets-ui](docs/hilalmarkets-ui/README.md).

Current beta infrastructure includes idempotent scheduled scans, shared CCXT REST clients,
deterministic proof persistence, setup lifecycle records, Telegram webhook delivery, Discord HTTP
delivery/interactions, and Stripe checkout/webhook support. See
[docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) for the required fail-closed
configuration.
