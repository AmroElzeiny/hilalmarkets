# TraceEdge Operations Guide

Date: 2026-06-15

## Environment Variables

Do not commit real values. Generate secrets with a password manager or cloud secret manager.

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development`, `test`, `staging` or `production`. |
| `APP_SECRET_KEY` | Signing key for continuation and identity assertion tokens. Minimum 32 characters. |
| `DATABASE_URL` | SQLAlchemy async database URL. Production should use PostgreSQL. |
| `REDIS_URL` | Redis URL for Celery broker/result backend and future locks/cooldowns. |
| `PUBLIC_BASE_URL` | Public HTTPS origin used for OAuth, web links and callbacks. |
| `TELEGRAM_BOT_USERNAME` | Telegram bot username for landing-page deep links. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token. Keep secret. |
| `TELEGRAM_WEBHOOK_SECRET` | Secret used to protect Telegram webhook endpoints. |
| `DISCORD_CLIENT_ID` | Discord application client id. |
| `DISCORD_CLIENT_SECRET` | Discord OAuth client secret. Keep secret. |
| `DISCORD_WEBHOOK_PUBLIC_KEY` | Discord interaction public key. Keep secret. |
| `BILLING_WEBHOOK_SECRET` | Billing provider webhook signing secret. Keep secret. |
| `TRIAL_DAYS` | Trial monitoring-cycle length. Default is `14`. |
| `DELIVERY_SETTLEMENT_GRACE_MINUTES` | Time to wait after a trial cycle ends before renewal evaluation. |
| `SCAN_JOB_CLAIM_TIMEOUT_SECONDS` | Running scan heartbeat age after which a job can be recovered. |
| `SCAN_JOB_MAX_ATTEMPTS` | Maximum retry attempts for retryable provider-wide scan failures. |
| `DISCLAIMER_VERSION` | Current disclaimer version stored with acknowledgements. |
| `CONTINUATION_TOKEN_TTL_MINUTES` | Telegram-to-web continuation token TTL. |
| `PREVIEW_CANDLE_LIMIT` | Candle limit for recent-market preview. |
| `DEFAULT_NEAR_MISS_THRESHOLD` | Default Near-Miss alert threshold. |
| `DEFAULT_ALERT_COOLDOWN_SECONDS` | Default duplicate/cooldown window. |
| `LOG_LEVEL` | Application log level. |
| `ALLOW_MOCK_PROVIDERS` | Must be `false` in staging and production. |
| `SCANNING_ENABLED` | Enables scheduled market scans. |
| `MARKET_DATA_PROVIDER` | `ccxt` for deployed scanning; `memory` is test-only. |
| `MARKET_DATA_EXCHANGE` | Default market-data exchange; use `binance` for this build. |
| `BINANCE_API_KEY` | Optional Binance key placeholder. Public spot data works without it. |
| `BINANCE_API_SECRET` | Optional Binance secret placeholder. Do not use withdrawal/trading permissions for v1. |
| `BINANCE_REST_BASE_URL` | Binance REST base URL, default `https://api.binance.com`. |
| `BINANCE_WS_BASE_URL` | Binance WebSocket base URL, default `wss://stream.binance.com:9443`. |
| `AI_INTERPRETER_PROVIDER` | `openai` for AI strategy interpretation or `rules` for deterministic fallback only. |
| `OPENAI_API_KEY` | OpenAI API key used only to convert user text into structured draft rules. |
| `OPENAI_MODEL` | Default interpretation model. Default: `gpt-5.4-nano`. |
| `OPENAI_REASONING_EFFORT` | Default interpretation reasoning effort. Default: `low`. |
| `OPENAI_BASE_URL` | OpenAI API base URL, default `https://api.openai.com/v1`. |
| `AI_AGENT_CONTROL_ENABLED` | Bounded Setup Chat coordinator kill switch. Default: `false`. |
| `AI_AGENT_SHADOW_MODE` | Records proposed agent tool selection but executes no agent tools. Default: `false`. |
| `AI_AGENT_ROLLOUT_PERCENT` | Stable authenticated-user cohort allowed to execute agent tools outside shadow mode. Default: `0`. |
| `AI_AGENT_MAX_STEPS` | Maximum Responses loop steps per turn. Default: `4`. |
| `AI_AGENT_MAX_TOOL_CALLS_PER_TURN` | Maximum validated function calls per turn. Default: `4`. |
| `AI_AGENT_MAX_REPEATED_CALLS` | Retry allowance for retryable unavailable/validation results. Default: `1`. |
| `AI_AGENT_TIMEOUT_SECONDS` | Whole bounded-turn timeout. Default: `45`. |
| `AI_AGENT_TOOL_TIMEOUT_SECONDS` | Per-tool timeout. Default: `30`. |
| `AI_AGENT_MAX_OUTPUT_TOKENS` | Cumulative model output-token limit per turn. Default: `1800`. |
| `AI_AGENT_MAX_ESTIMATED_COST_USD_PER_TURN` | Estimated per-turn cost stop. Default: `0.02`. |
| `AI_AGENT_PARALLEL_TOOL_CALLS` | Must remain `false` for the bounded coordinator. |
| `CAPABILITY_EXTENSION_ENABLED` | Enables user-approved certification of missing OHLCV mechanics. |
| `CAPABILITY_EXTENSION_DRAFT_MODEL` | Initial mechanic draft/review model. Default: `gpt-5.4-nano`. |
| `CAPABILITY_EXTENSION_IMPLEMENTATION_MODEL` | Implementation-only repair model. Default: `gpt-5.4-nano`. |
| `CAPABILITY_EXTENSION_REVIEW_MODEL` | Independent escalation model. Default: `gpt-5.4-mini`. |
| `CAPABILITY_EXTENSION_REPAIR_SERVICE_TIER` | `flex` for review/repair work or `default`. |
| `CAPABILITY_EXTENSION_PREFLIGHT_EXCHANGE` | Public spot provider used for certification preflight. Default: `bybit`. |
| `TELEGRAM_ENABLED` | Enables Telegram webhooks and delivery workers. |
| `TELEGRAM_ADAPTER` | Must be `http` when Telegram is enabled in a deployed environment. |
| `DISCORD_ENABLED` | Enables Discord OAuth, interactions, delivery and role sync. |
| `DISCORD_ADAPTER` | Must be `http` when Discord is enabled in a deployed environment. |
| `DISCORD_BOT_TOKEN` | Discord bot token used for API delivery and role operations. |
| `BILLING_ENABLED` | Enables checkout, portal and billing webhook processing. |
| `BILLING_PROVIDER` | Configured payment provider. Use `nowpayments` for this build. |
| `NOWPAYMENTS_API_KEY` | NOWPayments server API key. |
| `NOWPAYMENTS_BASE_URL` | NOWPayments API base URL. |
| `BILLING_WEBHOOK_SECRET` | Provider webhook/IPN signature secret. |
| `STRIPE_SECRET_KEY` | Optional Stripe server API key if the provider is switched later. |
| `STRIPE_PRICE_IDS` | Optional Stripe price-id map if the provider is switched later. |

## Database Migration

Local or production migration:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

Check for model/migration drift:

```powershell
.venv\Scripts\python.exe -m alembic check
```

Never run destructive schema changes without a tested backup and rollback plan.

Migration `b4c5d6e7f8a9` adds redacted bounded-agent run and tool-call traces. Apply it before enabling
shadow mode.

## Local Development

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn ai_market_monitor.main:app --reload
```

Run tests without live exchanges:

```powershell
python -m ruff check src tests
python -m mypy src/ai_market_monitor
python -m pytest
```

### Bounded-agent rollout

Deploy with `AI_AGENT_CONTROL_ENABLED=false`. For staging comparison, set:

```dotenv
AI_AGENT_CONTROL_ENABLED=true
AI_AGENT_SHADOW_MODE=true
AI_AGENT_ROLLOUT_PERCENT=0
```

Shadow mode does not execute agent-selected tools. The existing legacy chat flow still answers the
user, and `/system-brain#agent-control` compares the proposed first tool with the resulting legacy
action. Review tool-selection agreement, invalid/forbidden attempts, contained ungrounded claims,
fallback rate, latency, calls, tokens, cost, compiler success, and clarification evidence. Do not
enable execution until forbidden executions and unsupported-condition leakage remain zero and the
messy-request corpus shows no safety regression.

For live rollout, turn shadow mode off and begin with `AI_AGENT_ROLLOUT_PERCENT=1`. Cohort assignment
is deterministic by authenticated user ID, so the same users remain in the cohort across restarts.
Increase gradually while monitoring System Brain; set the percentage back to `0` to halt live
execution without disabling ongoing shadow evaluation.

To roll back, set `AI_AGENT_CONTROL_ENABLED=false` and restart the API. No schema rollback is needed.
The full catalog, policy, limits, and safe tool-addition checklist are documented in
`docs/BOUNDED_AGENT_CONTROL.md`.

## Production Deployment

Recommended first deployment topology:

- FastAPI API container.
- Celery worker container.
- Celery beat scheduler container.
- PostgreSQL 16+.
- Redis 7+.
- HTTPS reverse proxy or managed load balancer.
- Managed secret store.
- Central logs and metrics.

Docker Compose development stack:

```powershell
docker compose up --build
```

Production must provide real secrets through the deployment platform, not `.env` committed files.
The local `.env.example` intentionally uses SQLite and localhost Redis so host-run commands such as
`alembic upgrade head` do not try to resolve Docker-only hostnames.

## Worker And Scheduler

Worker:

```powershell
celery -A ai_market_monitor.worker.app worker --loglevel=INFO
```

Scheduler:

```powershell
celery -A ai_market_monitor.worker.app beat --loglevel=INFO
```

Scheduled tasks currently wired:

- Trial expiry.
- Trial reminder eligibility.
- Due-scan scheduling and per-job deterministic scanning.
- Stale/retryable scan-job recovery.
- Setup-instance expiration.
- Telegram delivery retries.
- Discord delivery retries.
- Discord role-sync retries.
- Certified capability creation and five-scan repair reviews every 30 seconds.
- Database connectivity metric.

The live scanner currently uses shared CCXT REST clients. Jobs are claimed atomically from
`queued` to `running`, store worker id/claim/heartbeat timestamps, and are not rerun after terminal
states. WebSocket ingestion and a durable candle store remain future production-hardening work.
Capability extension jobs additionally require a configured server-side OpenAI key. The generated
artifact remains a bounded deterministic expression and must pass normal user approval. See
`docs/CAPABILITY_EXTENSION_PIPELINE.md` for the escalation and failure behavior.

## Telegram Setup

1. Create a bot with BotFather.
2. Store `TELEGRAM_BOT_USERNAME`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`.
3. Register `/api/v1/telegram/webhook` over HTTPS and configure Telegram's `secret_token`.
4. Use deep links for attribution and continuation into web onboarding.
5. Never request exchange trading keys or wallet secrets in Telegram.

The HTTP adapter validates the webhook secret, deduplicates Telegram update ids, sends and edits
messages, answers callbacks, and records bounded delivery retries.

## Discord Setup

1. Create a Discord application and bot.
2. Configure OAuth redirect URLs under `PUBLIC_BASE_URL`.
3. Store `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `DISCORD_WEBHOOK_PUBLIC_KEY`.
4. Install the bot in eligible servers.
5. Verify permissions for channel delivery and setup threads.
6. Configure role mappings from billing entitlements.

The HTTP gateway performs server-side OAuth exchange, signed interaction validation, bot delivery,
thread reuse, role synchronization and bounded retries. Register slash commands separately in the
Discord developer portal or deployment automation.

## Billing Setup

1. Configure plans from `core/plans.py` and create matching payment options in NOWPayments.
2. Send checkout and subscription events to `/api/v1/billing/webhooks/{provider}`.
3. Set `BILLING_PROVIDER=nowpayments`, `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_BASE_URL` and
   `BILLING_WEBHOOK_SECRET`.
4. Treat provider webhooks as the source of truth.
5. Use `/api/v1/admin/billing-events/{provider_event_id}/reprocess` for failed-event retries.

NOWPayments invoice links are created server-side from Dashboard billing. IPN signatures are
verified with replay-safe event processing. Telegram and Discord may show plan status and open a
signed Dashboard billing link, but they do not collect payment directly.

## Monitoring Setup

Operational APIs:

- `/health` for simple API liveness.
- `/api/v1/status/summary` for user-facing status.
- `/api/v1/status/market-data` for recent market-data status.
- `/api/v1/status/integrations` for delivery/provider integrations.
- `/api/v1/admin/health` for admin health dashboard.
- `/api/v1/admin/activity` for scan, alert, delivery and billing activity.

Track at minimum:

- Exchange connectivity.
- WebSocket connectivity and REST fallback.
- Market-data freshness, missing intervals, duplicate/out-of-order candles.
- Scan duration and failures.
- Queue depth and failed Celery jobs.
- Chart generation failures.
- Telegram and Discord delivery failures.
- Billing webhook failures.
- Database and Redis health.
- API latency and error rate.

## Known Limitations

- Exchange ingestion is REST-based; WebSocket ingestion and a durable historical candle store are
  not complete.
- Chart-generation service is not complete.
- Admin UI is API-first; a polished web console remains.
- API-wide customer authentication is not complete.
- API-wide rate limiting and WAF rules remain.
- Full replay/backtest and analytics workflows remain.
- Dependency vulnerability scanning must be performed by CI or a security platform.

## Security Risks To Close Before Launch

- Add API authentication beyond onboarding/admin header test principals.
- Add rate limiting to public and webhook endpoints.
- Add SSRF-safe chart/screenshot upload handling to any future upload endpoint.
- Run dependency and container vulnerability scans in CI.
- Add centralized redacted structured logging.
- Add end-to-end tests against staging provider sandboxes.

## Recommended Next Development Phase

Harden the implemented beta vertical slice:

1. Customer authentication and API-wide authorization.
2. Rate limiting, WAF rules and monitoring exports.
3. Chart snapshot generation and secure object storage.
4. Durable candle storage plus WebSocket ingestion with REST fallback.
5. Full forensic replay, backtesting and analytics workflows.
6. Provider sandbox end-to-end, load, security and disaster-recovery tests.
