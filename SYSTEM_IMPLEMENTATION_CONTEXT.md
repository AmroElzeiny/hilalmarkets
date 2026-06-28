# TraceEdge - System Implementation Context

Last updated: 2026-06-23

## Current System Rating

- Stability: 7.2/10
- Growth potential: 8.7/10
- Robustness: 7.2/10
- Workability: 7.7/10
- Product uniqueness: 8.3/10
- MVP readiness: 7/10

These ratings are confirmed after inspection. The repository has real foundations: a structured
strategy schema, nested AND/OR logic, a deterministic rule engine, on-demand scans, Telegram and
Discord services, billing/trials, dashboard APIs, setup replay jobs, Near-Miss persistence, proof
receipts, entitlements/plans, and CCXT-based market data. The main weaknesses remain production
hardening, broader interpretation coverage, more complete Discord end-to-end command testing,
provider reliability/backoff, deeper dashboard UX, and deployment/migration discipline.

## Current Architecture Summary

- FastAPI application with versioned API routers in `src/ai_market_monitor/api/routers`.
- SQLAlchemy ORM models in `src/ai_market_monitor/db/models` with Alembic migrations.
- Pydantic strategy/onboarding/on-demand schemas in `src/ai_market_monitor/schemas`.
- Deterministic engine layers in `src/ai_market_monitor/engine`:
  - indicators
  - market filters
  - rule evaluation
  - Near-Miss scoring
  - risk calculations
  - proofs
  - lifecycle/forensics support
- Service layer in `src/ai_market_monitor/services`:
  - onboarding and strategy approval
  - OpenAI/rules interpretation
  - market preview and CCXT providers
  - on-demand scans
  - dashboard jobs/replay/backtests/exports
  - billing/trials/entitlements/referrals
  - admin/reliability/support
- Telegram service in `src/ai_market_monitor/telegram`.
- Discord connection, alerts, moderation, slash command support in `src/ai_market_monitor/discord`.
- Dashboard HTML/CSS/JS in `src/ai_market_monitor/templates` and `src/ai_market_monitor/static`.
- Docker, Celery worker, Redis/Postgres-ready local orchestration files exist.

## Existing Implemented Features

- Natural-language setup interpretation with OpenAI adapter and conservative rule fallback.
- Structured strategy schema with condition groups and rule metadata.
- Approval gate before saved monitor activation.
- Deterministic rule evaluation, risk validation, Near-Miss scoring, proof receipts.
- On-demand scan API for approved inline/saved strategies.
- Quick dashboard scan prompt interpretation.
- Setup Replay and historical replay/backtest jobs with chart payloads.
- Telegram onboarding, menus, strategy creation, approval, billing/trial/support paths.
- Discord OAuth/delivery primitives, slash command service, role sync jobs, moderation checks.
- Configurable plan catalog, trial lifecycle, NOWPayments/Stripe/static billing abstraction.
- Usage records, audit events, support tickets, admin routes, reliability status.
- Central capability registry with 97 recognized strategy/indicator/tactic capabilities, including
  87 deterministic executable rules, 10 recognized-not-executable ideas, 26 synonyms, and 20 built-in
  strategy templates.
- Dashboard capability API for builder dropdowns and parameter metadata.
- Expanded deterministic interpreter coverage for moving averages, RSI, MACD, stochastic, Bollinger,
  VWAP, volume, percent moves, all-time/six-month highs, sessions, BOS/CHOCH, sweeps,
  support/resistance, and candle behavior.

## Known Bugs

- Dashboard UX is still coarse in places and some result panes show raw JSON instead of polished cards.
- Discord Quick Scan currently opens a secure dashboard link rather than executing scans inside Discord.
- Telegram Quick Scan can execute only when the Telegram runtime is constructed with a market-data provider;
  otherwise it safely hands off to dashboard Quick Scan.
- Market provider errors are collected per symbol, but systemic provider failures need better detection,
  retry, and backoff.
- Some recognized concepts intentionally remain non-executable until supporting data/evaluation exists,
  including cross-market BTC/ETH filters, market-cap filters, meme-token tags, Fibonacci zones, and
  previous-session segmentation.

## Blockers

- Production secrets must be rotated if any real credentials were ever exposed during local testing.
- Real deployment still needs verified production `.env`, HTTPS public base URL, real Telegram/Discord
  adapters, billing webhook signatures, database migrations, worker scheduling, and monitoring.
- Exchange rate limits and provider failover are not fully implemented.
- Some advanced Discord flows still need end-to-end live integration testing.

## Technical Gaps

- No full symbol normalization service across exchanges yet.
- No provider failover/cache/backoff layer beyond current CCXT abstraction.
- Charting still uses a simple canvas fallback plus optional CDN chart library.
- Admin HTML tooling remains summary-oriented.
- User deletion/privacy export flows need a deeper audit.
- Strategy dictionary coverage is now much wider, but should keep expanding from real user prompts and
  support tickets.

## Product Gaps

- Light/Quick Scan now exists, but Telegram/Discord UX should become richer and more visual.
- Dashboard scan results should become user-friendly cards instead of primarily JSON.
- Free-plan value is improved but should be measured against real infrastructure cost.
- Optional-condition explanations should be made more human-friendly in all alert channels.

## Security Notes

- `.env` is gitignored and `.env.example` exists.
- The active `.env` contains real-looking exchange/API credentials. Values are not copied here. Rotate
  any key/token/secret that may have been exposed in chat logs, terminals, screenshots, or commits.
- Redaction was strengthened for secret-like keys and values.
- Structlog now applies a redaction processor before JSON rendering.
- Header-only `X-User-ID` principals are now disabled outside development/test.
- Do not commit `.env`, database files, export artifacts, logs, or local tunnel output.
- Unsupported recognized strategy ideas are returned as structured issues with a `blocking` flag so
  optional preferences can be explained without preventing execution.

## What Was Changed In This Run

- Added `SYSTEM_IMPLEMENTATION_CONTEXT.md`.
- Added plan feature `light_prompt_scan`.
- Added plan limits:
  - `light_prompt_scans_per_day`
  - `light_prompt_symbols`
- Added `light_scan` mode to on-demand scan requests.
- Added `LightScanRequest`.
- Added dashboard API route `POST /api/v1/dashboard/light-scan`.
- Quick Scan interprets free text, requires at least one executable deterministic condition, runs without
  saved strategy approval, records usage as `light_prompt_scans`, and returns top results plus warnings.
- Optional failed/error/pending conditions now appear as non-blocking in proof receipts.
- Condition proof rows now include `blocking`.
- On-demand scan proof now includes `scan_mode` and `light_scan`.
- Dashboard prompt-mode scan now calls `/light-scan` instead of the stricter saved-strategy scan route.
- Dashboard JS gained:
  - `safeNumber`
  - `safeArray`
  - `safeJson`
  - `renderEmptyState`
  - `renderErrorState`
  - `renderLoadingState`
  - safer chart coercion and fallback rendering
  - loading/success/error states for scan/replay/backtest/export/support paths
- Dashboard support form now has a result output pane.
- Telegram gained a `Quick Scan` entry point and can run light scans when constructed with a market-data provider.
- Discord gained a `/quick_scan` dashboard handoff command.
- Security redaction was strengthened in `SecurityReviewService`.
- Structured logging now redacts sensitive fields before rendering.
- Production/staging header-principal authentication was disabled for admin/user dashboard APIs.
- Added `src/ai_market_monitor/engine/capabilities.py` as the central strategy capability registry.
- Added/expanded deterministic indicator adapters for:
  - average volume
  - EMA/SMA slope
  - moving-average distance percent
  - ATR percent
  - relative volume slope
  - MACD histogram delta
  - Bollinger bandwidth percent/delta
  - VWAP deviation percent
  - range ratio
  - pullback depth percent
- Expanded deterministic evaluator support for new price-action and candle-pattern operands.
- Reworked the rule-based interpreter to map many more free-text prompts into measurable rules while
  keeping unsupported requirements explicit.
- Updated OpenAI interpretation instructions/schema so the model must choose from executable registry
  capabilities and flag recognized-not-executable items instead of guessing.
- Added 20 built-in strategy templates shared by Telegram and dashboard flows.
- Added `GET /api/v1/dashboard/capabilities` and wired dashboard builder dropdowns/parameter metadata
  to the registry.
- Enhanced dashboard light-scan interpretation responses with required rules, optional rules, warnings,
  ignored optional ideas, blocking unsupported rules, safety level, and light-mode compatibility.
- Updated Telegram template menus to use the shared template catalog.
- Added Discord `/templates` and `/strategy-templates` command handling.
- Fixed prompt parsing so higher-timeframe EMA phrases like "four-hour 200 EMA" do not override the
  primary scan timeframe.
- Part 3 added deterministic quality scoring:
  - `alert_trust_score_from_proof` for explainable alert quality factors.
  - `market_coverage_score` for persisted scan coverage, data failures, timeframe coverage and staleness.
- Proof receipts now include `alert_trust_score`.
- Telegram alert rendering now shows Alert Trust Score and routes proof/feedback actions to functional
  dashboard proof or replay destinations.
- Telegram main menu was polished around Quick Scan, Create Monitor, My Monitors, Near-Miss Radar,
  Setup Replay, Latest Alerts, Trial, Subscription, Settings, Support and About.
- Setup Replay replaces the old "Why No Alert?" label in user-facing Telegram and Discord paths while
  keeping old aliases working.
- Dashboard overview now highlights alerts today, near-misses today, latest setup, latest alert,
  Telegram/Discord connection state and Market Coverage Score.
- Dashboard navigation and pages now expose Setup Replay, Near-Miss Radar, Alerts & Proof,
  Latest Setups, Analytics, Historical Replay and Exports as first-class sections.
- Alerts & Proof includes alert rows, proof detail, delivery rows and trust-score factors.
- Near-Miss Radar UI now displays one-condition-remaining badges and clearer passed/missing/closest
  condition evidence.
- Discord slash command aliases were added for `/create_monitor`, `/monitors`, `/setup_replay` and
  `/scan_now`, with Setup Replay links replacing old Why No Alert dashboard targets.
- Added launch-facing docs for positioning, Light Scan, strategy language, supported capabilities,
  free-plan behavior, Telegram UX, Discord UX, dashboard UX, alert proof and Setup Replay.

## Remaining Work

- Add richer Quick Scan cards in Discord and dashboard.
- Add a dedicated symbol normalization service.
- Implement provider backoff, caching, and failover.
- Add full live Discord slash-command integration tests against a real Discord test server.
- Add deeper dashboard accessibility/mobile testing.
- Add more interpretation patterns and strategy synonym coverage.
- Add full privacy/deletion/account export flows.
- Add production deployment smoke tests with real webhook signature validation.
- Add deterministic support for currently recognized external-data capabilities after adding the needed
  providers, especially market cap, token tags, cross-market benchmark filters, and prior-session levels.

## Test Results

- `python -m ruff check src tests` passed.
- `python -m pytest -q` passed.
- Added/updated tests cover Quick Scan, free limits, optional vs mandatory condition behavior,
  dashboard JS safety helpers, redaction, and production header-principal rejection.
- Added/updated tests cover capability registry breadth, dashboard capabilities API, expanded interpreter
  prompt mechanics, optional-vs-mandatory unsupported ideas, higher-timeframe parsing, and expanded
  indicator registry support.
- Added/updated tests cover deterministic Alert Trust Score, Market Coverage Score, proof receipt
  trust-score inclusion, dashboard coverage API, dashboard navigation visibility, and Discord command
  aliases for creator/monitor/replay flows.
