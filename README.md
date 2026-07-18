# HilalMarkets

HilalMarkets is a screened-market intelligence and monitoring platform for crypto spot
traders. Users describe a Watch Plan, approve its structured interpretation, preview it against
recent market data, and receive evidence-backed in-app or Telegram alerts during private beta.
Version one never places trades.

The guided Watch Plan builder starts with AI Setup Chat. The server-side interviewer keeps a durable
conversation, asks for measurable definitions, compiles only into the validated strategy DSL, shows
confidence/lint/assumption evidence, and creates an immutable approved strategy version only after
explicit user approval. `OPENAI_API_KEY` is server-side only; `OPENAI_MODEL` is optional and defaults
to `gpt-5.4-nano` with low reasoning.

Bounded Agent Control selects among a small server-offered tool set for messy, multi-intent chat
turns. The controlled-beta deployment profile serves the live coordinator to all authenticated
beta users (`AI_AGENT_SHADOW_MODE=false`, `AI_AGENT_ROLLOUT_PERCENT=100`). Registry, compiler,
provider, scanner, ownership, entitlement, hash, approval, and activation authority remain in
application services; the model never receives approval or activation tools. Setting
`AI_AGENT_CONTROL_ENABLED=false` returns users to the durable guided flow without a database
rollback. See
[docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md).

HilalMarkets also has a fail-closed Sharia-first market layer. Screened Market, one-time Scanner runs,
persistent Watch Plans, workers, opportunity evidence, and alerts share one versioned methodology
and universe resolver. The SC Malaysia workflow imports only explicit asset-level source rows,
verifies canonical identity, builds a factual evidence dossier, and creates an administrator review
case. No asset is customer-visible until an application `ADMIN` approves publication. AI cannot set
a religious status or publish an asset. See
[docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md](docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md)
for the architecture, source boundaries, deployment order, tests, and manual review requirements.
Current and historical Passport views now share one read model, alerts retain the exact Passport
version used at evaluation, and System Brain separates review from publication with optional
four-eyes enforcement. The first-party checkout uses the server Plan Catalog and a durable
successful-payment email outbox. See
[docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md](docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md).

The public product surface uses shared HilalMarkets Jinja shells, emerald/ivory/gold design
tokens, and server-owned content sources. Dedicated routes cover Features, How It Works, How We
Screen, Pricing, Help, Contact, About, Trust & Safety, Risk Disclosure, Privacy, Terms, and Cookies.
Public Pricing and authenticated Billing read the same plan catalog. The private beta exposes only
free invite access and rejects paid checkout while billing is disabled. Optional analytics is disabled
by default; Consent Mode v2 denied defaults execute before the optional GTM loader, and users can
withdraw consent through Cookie Settings. See
[docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md](docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md).
The landing-page product assistant is a separate public, non-executing boundary. It generates
multi-turn answers only from server-owned product knowledge and bounded read-only tools. Anonymous
visitors cannot inspect accounts; a signed-in user may read only their own account, Telegram,
Watch Plan, alert, entitlement, usage, Screened Watchlist, and published Passport state. Unknown
questions enter a CSRF-protected, rate-limited, idempotent inquiry flow. The inquiry commits exactly
one customer and one office email outbox row before returning, supports token-bound
feedback/deletion, and never exposes Setup Chat mutation tools.
The current private-beta correction status, including verification blockers and required staging
evidence, is recorded in
[docs/PRIVATE_BETA_READINESS_REPORT.md](docs/PRIVATE_BETA_READINESS_REPORT.md).

During the controlled beta, an explicitly confirmed candle-computable mechanic that is not in the
registry can be prepared as a user-scoped, versioned mechanic through a bounded JSON expression
DSL. The worker validates it, tests it against Binance spot, independently reviews it, and requires
the normal strategy approval flow before activation. Provider-only requests are rejected before
queueing. Certified artifacts remain fail-closed during approval and scheduled scans, and owner
quarantine/restore/repair-discard controls never replace an active version silently. The pipeline
never executes AI-generated Python or fabricates provider data. See
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
[docs/PRIVATE_BETA_SOAK_RUNBOOK.md](docs/PRIVATE_BETA_SOAK_RUNBOOK.md),
[docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md](docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md), and
[docs/CAPABILITY_EXTENSION_PIPELINE.md](docs/CAPABILITY_EXTENSION_PIPELINE.md), and
[docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md), and
[docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md](docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md), and
[docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md](docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md), and
[docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md](docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md), and
[docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md](docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md).
The production page map, exact prototype asset mapping, and UI verification commands are in
[docs/HILALMARKETS_UI_MIGRATION.md](docs/HILALMARKETS_UI_MIGRATION.md); the supplied design,
component, UX, and QA references are preserved under [docs/hilalmarkets-ui](docs/hilalmarkets-ui/README.md).

Current beta infrastructure includes idempotent scheduled scans, shared CCXT REST clients,
deterministic proof persistence, setup lifecycle records, and Telegram webhook delivery. WhatsApp
code remains dormant pending Meta onboarding and controlled tests; it is not mounted or shown when
disabled. Certified user-scoped OHLCV capability creation is enabled; paid billing remains disabled.
See
[docs/WHATSAPP_CLOUD_API_RUNBOOK.md](docs/WHATSAPP_CLOUD_API_RUNBOOK.md). Retained payment providers
remain covered by tests for a later explicit rollout. See
[docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) for the required fail-closed
configuration.

The current live-AI code and verification status is recorded in
[docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md](docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md).

The `.github/workflows/release-gate.yml` workflow is the release authority for automated checks.
Committed Markdown reports are explanatory records, not test proof. Require every workflow job in
branch protection before merging a release candidate.
