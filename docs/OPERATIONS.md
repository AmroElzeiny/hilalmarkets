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
| `WHATSAPP_ENABLED` / `WHATSAPP_ADAPTER` | Enable official Meta Cloud API delivery only when set to `true` / `http`. |
| `WHATSAPP_GRAPH_API_VERSION` | Explicit deploy-time Graph API version, for example `v23.0`; no version is embedded in code. |
| `WHATSAPP_ACCESS_TOKEN` | Server-side Meta system-user access token. Keep secret and rotate through deployment secrets. |
| `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` | POST signature secret and independent GET webhook challenge token. Keep secret. |
| `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta phone-number and WABA identifiers used to bind webhook authority. |
| `WHATSAPP_BUSINESS_PHONE_E164` | Registered HilalMarkets WhatsApp business number in normalized E.164 form. |
| `WHATSAPP_TEMPLATE_NAMES` | JSON event/locale map containing only templates actually approved in the configured WABA. |
| `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED` | Separate default-off gate for lifecycle and confirmed research-event messaging. |
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
| `AI_SETUP_SIMPLE_MODEL` / `AI_SETUP_SIMPLE_REASONING_EFFORT` | Configured low-cost Setup Chat route for greetings, option answers and clear one-condition requests. |
| `AI_SETUP_COMPLEX_MODEL` / `AI_SETUP_COMPLEX_REASONING_EFFORT` | Configured stronger Setup Chat route for complex logic, corrections, low-confidence retrieval and multilingual turns. It receives no additional authority. |
| `AI_SETUP_COMPLEX_CONDITION_THRESHOLD` | Condition-count threshold for complex routing. Default: `4`. |
| `AI_SETUP_REPEATED_CORRECTION_THRESHOLD` | Correction count that escalates interpretation capacity. Default: `2`. |
| `AI_SETUP_LOW_CAPABILITY_CONFIDENCE` | Resolver-confidence threshold for complex routing. Default: `0.72`. |
| `AI_AGENT_CONTROL_ENABLED` | Bounded Setup Chat coordinator kill switch. Default: `false`. |
| `AI_AGENT_SHADOW_MODE` | Records proposed agent tool selection but executes no agent tools. Default: `false`. |
| `AI_AGENT_ROLLOUT_PERCENT` | Live authenticated-user percentage. Application default: `0`; controlled beta requires `100`. |
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
| `CAPABILITY_EXTENSION_PREFLIGHT_EXCHANGE` | Public spot provider used for certification preflight. Controlled beta requires `binance`. |
| `SHARIA_ADMIN_TELEGRAM_CHAT_ID` | Admin-only destination for review notifications. Required when deployed screening is enforced. |
| `SC_MALAYSIA_DIGITAL_ASSETS_URL` | Authoritative SC Malaysia digital-assets page imported by the governance worker. |
| `SHARIA_AI_MODEL` | Model used for bounded factual dossier/change analysis. Default: `gpt-5.4-nano`. |
| `SHARIA_AI_REASONING_EFFORT` | Factual research reasoning effort. Default: `low`. |
| `SHARIA_AI_SERVICE_TIER` | Must be `flex` for the deployed governance workflow. |
| `SHARIA_AI_TIMEOUT_SECONDS` | Whole factual-analysis request timeout. Default: `900`. |
| `SHARIA_AI_MAX_RETRIES` | Retry limit for retryable Flex failures. Default: `5`. |
| `SHARIA_AI_ALLOW_STANDARD_FALLBACK` | Allows an explicit standard-tier fallback; default is fail-closed `false`. |
| `SHARIA_REVIEW_REMINDER_HOURS` | Reminder window for open review cases. Default: `6`. |
| `SHARIA_REVIEW_SLA_HOURS` | Initial due-date window for review cases. Default: `48`. |
| `REQUIRE_SECOND_REVIEWER` | When true, the reviewer cannot publish the same decision. Default: `false`. |
| `SHARIA_SOURCE_SCAN_INTERVAL_HOURS` | Published-source monitoring interval. Default: `24`. |
| `SHARIA_SCRAPER_CONCURRENCY` | Must be `1`; official sources are fetched sequentially. |
| `SHARIA_SCRAPER_OBEY_ROBOTS` | Must remain `true` in staging and production. |
| `SHARIA_SCRAPER_DOWNLOAD_DELAY_SECONDS` | Delay between official-source requests; deployed minimum is one second. |
| `SHARIA_PILOT_SYMBOLS` | Pilot allowlist, default `BTC,ETH,SOL`. |
| `SHARIA_PROCESS_REMAINING_IMPORTS` | Enables processing of explicit non-pilot rows after pilot approval; default `false`. |
| `TELEGRAM_ENABLED` | Enables Telegram webhooks and delivery workers. |
| `TELEGRAM_ADAPTER` | Must be `http` when Telegram is enabled in a deployed environment. |
| `BILLING_ENABLED` | Enables checkout, portal and billing webhook processing. |
| `BILLING_PROVIDER` | Configured payment provider. Use `nowpayments` for this build. |
| `BILLING_CHECKOUT_TTL_MINUTES` | Expiry for a prepared first-party checkout attempt. Default: `30`. |
| `BILLING_TERMS_VERSION` | Version captured with checkout consent. |
| `PAYMENT_EMAIL_MAX_ATTEMPTS` | Maximum outbox delivery attempts. Default: `5`. |
| `PAYMENT_EMAIL_RETRY_MINUTES` | Delay between payment-email retries. Default: `15`. |
| `NOWPAYMENTS_API_KEY` | NOWPayments server API key. |
| `NOWPAYMENTS_BASE_URL` | NOWPayments API base URL. |
| `BILLING_WEBHOOK_SECRET` | Provider webhook/IPN signature secret. |
| `STRIPE_SECRET_KEY` | Optional Stripe server API key if the provider is switched later. |
| `STRIPE_PRICE_IDS` | Optional Stripe price-id map if the provider is switched later. |
| `SYSTEM_BRAIN_CLOUDFLARE_ACCESS_REQUIRED` | Requires Access headers in addition to application ADMIN auth. Enable only after origin access is restricted. |
| `PUBLIC_CHAT_ENABLED` | Enables the separate public product-information assistant. |
| `PUBLIC_CHAT_AI_ENABLED` | Enables grounded multi-turn AI support. Deployed public chat requires `true`. |
| `PUBLIC_CHAT_AI_MODEL` / `PUBLIC_CHAT_AI_REASONING_EFFORT` | Configurable support model and effort; defaults to the main model and `low`. |
| `PUBLIC_CHAT_AI_TIMEOUT_SECONDS` / `PUBLIC_CHAT_AI_PROVIDER_ATTEMPTS` | Bounded provider timeout and retry count. |
| `PUBLIC_CHAT_AI_MAX_OUTPUT_TOKENS` / `PUBLIC_CHAT_AI_MAX_ESTIMATED_COST_USD_PER_TURN` | Output and cost ceilings enforced before and after provider calls. |
| `PUBLIC_CHAT_AI_MIN_CONFIDENCE` | Below this threshold, the bot clarifies or offers a human inquiry instead of guessing. |
| `PUBLIC_CHAT_AI_MAX_HISTORY_MESSAGES` / `PUBLIC_CHAT_SESSION_MAX_TURNS` | Bounded server-side conversation memory and per-session abuse limit. |
| `PUBLIC_CHAT_INQUIRY_EMAIL` | Office recipient for consented public inquiries. |
| `PUBLIC_CHAT_PROFILE_VERSION` | Invalidates stale local-only visitor profile consent when changed. |
| `PUBLIC_CHAT_MESSAGE_MAX_LENGTH` / `PUBLIC_CHAT_INQUIRY_MAX_LENGTH` | Server-enforced public input bounds. |
| `PUBLIC_CHAT_ANSWER_AUDIT_RETENTION_DAYS` / `PUBLIC_CHAT_INQUIRY_RETENTION_DAYS` | Retention for hashed answer telemetry and consented inquiries. |
| `PUBLIC_CHAT_EMAIL_MAX_ATTEMPTS` / `PUBLIC_CHAT_EMAIL_RETRY_MINUTES` / `PUBLIC_CHAT_EMAIL_CLAIM_TIMEOUT_MINUTES` | Bounded inquiry-email outbox retry and abandoned-claim recovery. |

When `PUBLIC_CHAT_ENABLED=true` in staging or production, startup requires `EMAIL_ADAPTER=smtp`
plus non-placeholder SMTP host, username, password, and sender address. This deliberately prevents
an inquiry-enabled deployment from accepting questions into an outbox that cannot be delivered.

## Private-Beta Locked Profile

The controlled private beta is invite-only and free. Production examples intentionally enforce BTC,
ETH and SOL on Binance spot, one approved methodology, in-app and Telegram delivery, paid checkout
off, WhatsApp off, Discord retired, certified user-scoped OHLCV extensions on, and the Bounded Agent
live for every authenticated beta user. Deployed startup and release invariants require shadow mode
off and rollout at 100 percent. Live OpenAI/Binance/SMTP proof remains a separate staging gate.

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
the live coordinator.

Migration `2bdce3f40516` adds bounded public-support conversations, idempotent turns, authenticated
ownership references, model usage, latency, grounding, and validation audit fields.

Migration `d6e7f8a9b0c1` adds the SC Malaysia governance workflow. It seeds only the versioned
methodology family/version and never seeds or publishes an asset.

Migration `e7f8a9b0c1d2` adds immutable Passport/event references, governance roles, reviewer
profiles and assignments, problem reports, decision/publication integrity fields, first-party
checkout attempts, and payment-email outbox state.

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

The controlled-beta release profile is:

```dotenv
AI_AGENT_CONTROL_ENABLED=true
AI_AGENT_SHADOW_MODE=false
AI_AGENT_ROLLOUT_PERCENT=100
CAPABILITY_EXTENSION_ENABLED=true
CAPABILITY_EXTENSION_PREFLIGHT_EXCHANGE=binance
PUBLIC_CHAT_ENABLED=true
PUBLIC_CHAT_AI_ENABLED=true
```

Before opening access, verify zero forbidden executions and unsupported-condition leakage, inspect
fallbacks and clause gaps, and complete the live staging matrix in
`docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md`. System Brain is the operational evidence view;
committed reports are not runtime proof.

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
- Public-inquiry email retries and bounded public-chat retention cleanup.
- Dormant WhatsApp webhook/retry tasks only when the separately disabled WhatsApp feature is enabled.
- Certified capability creation and five-scan repair reviews every 30 seconds.
- Database connectivity metric.
- Daily idempotent SC Malaysia import and pilot processing.
- Hourly open-review reminders and minute-level Telegram retry processing.
- Published-asset source monitoring at `SHARIA_SOURCE_SCAN_INTERVAL_HOURS`.

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

## WhatsApp Cloud API Setup

WhatsApp is not a private-beta channel. Keep `WHATSAPP_ENABLED=false` and do not mount or advertise
it. A later approved rollout uses signed webhooks only; do not add polling. Configure the callback as
`https://<public-host>/api/v1/whatsapp/webhook`, subscribe the WABA to message events, and keep
`WHATSAPP_ENABLED=false` until the registered phone, token permissions, callback verification, and
required templates have been validated in staging. Complete setup, rotation, smoke tests, event
categories, template variables, and troubleshooting are in
[`WHATSAPP_CLOUD_API_RUNBOOK.md`](WHATSAPP_CLOUD_API_RUNBOOK.md).

The API records accepted work quickly and Celery processes inbound events. The scheduler retries
due receipts and WhatsApp `AlertDelivery` rows, then removes expired bounded receipts. There is no
free-form fallback outside Meta's customer-service window. Market-opportunity delivery remains off
unless `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED=true` and its approved template is configured.

## Billing Setup

Paid billing is not a private-beta capability. Keep `BILLING_ENABLED=false`; public Pricing exposes
only invite access and checkout/portal/webhook mutations fail closed. The retained provider flow
below is for a later provider-sandbox release decision.

1. Configure plans from `core/plans.py` and create matching payment options in NOWPayments. Public
   Pricing, checkout review, and entitlements must continue to use this same catalog.
2. Send checkout and subscription events to `/api/v1/billing/webhooks/{provider}`.
3. Set `BILLING_PROVIDER=nowpayments`, `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_BASE_URL` and
   `BILLING_WEBHOOK_SECRET`.
4. Treat provider webhooks as the source of truth.
5. Use `/api/v1/admin/billing-events/{provider_event_id}/reprocess` for failed-event retries.

The Dashboard first shows `/dashboard/billing/checkout`, where plan, cycle, price, currency, limits,
and terms are loaded from the server. The server then creates the NOWPayments invoice. IPN
signatures are verified with replay-safe event processing. Telegram may show plan status
and open a signed Dashboard billing link, but they do not collect payment directly.

Verified successful payment enqueues a unique `PaymentEmailDelivery`. The scheduler runs
`ai_market_monitor.retry_payment_emails` every minute and the worker processes due rows with bounded
retry state. Before live rollout, use `scripts/test_payment_email.py` for a local no-send preview and
the ADMIN/development preview route for authenticated rendering. Confirm SMTP domain authentication,
sender identity, links, and provider logs in staging.

## System Brain Edge Protection

Before the first staging or production governance action, make the owner an existing verified,
active application `ADMIN`, then provision explicit grants once:

```powershell
.venv\Scripts\python.exe scripts\bootstrap_governance_owner.py `
  --email "owner@example.com" `
  --reason "Initial accountable governance owner provisioning"
```

The command is idempotent and records one audit event for each newly activated `SYSTEM_ADMIN`,
`RESEARCHER`, `REVIEWER`, and `PUBLISHER` grant. In staging/production an ADMIN without an explicit
grant cannot perform governance mutations. `REQUIRE_SECOND_REVIEWER=false` keeps approval and
publication separate while allowing the current one-owner operation.

Application ADMIN authentication and scoped CSRF validation are always authoritative. In
production, also set `SYSTEM_BRAIN_CLOUDFLARE_ACCESS_REQUIRED=true` and place `/system-brain*`
behind a Cloudflare Access application. The application header check is not a substitute for
cryptographic Access validation at the edge and is unsafe if clients can reach the origin and spoof
headers.

Use Cloudflare Tunnel or firewall rules that accept web traffic only from the intended reverse
proxy. Remove alternate public origin ports and DNS records. From an external network verify:

1. The public System Brain route is denied before Access authentication.
2. Access success still requires an application ADMIN session.
3. A normal customer session receives `403` and has no System Brain navigation.
4. Direct origin IP/hostname requests fail at the network layer.
5. Spoofed `cf-access-*` headers sent to any reachable origin do not bypass the edge.
6. System Brain is absent from sitemap, customer navigation, analytics, and robots indexing.

Use `scripts/test_compliance_notification.py` without `--live` for safe payload/delivery-state
inspection. A live test requires the explicit live flag, confirmation phrase, and dedicated test
chat ID; never use a customer chat for deployment verification.

Before inviting external users, execute the seven-day staging procedure in
`docs/PRIVATE_BETA_SOAK_RUNBOOK.md`. Retain the daily output of
`scripts/audit_private_beta_soak.py`; never commit those environment-specific reports.

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
- Telegram delivery failures, plus WhatsApp failures only in explicitly enabled staging tests.
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
