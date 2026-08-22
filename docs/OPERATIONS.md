# Hilal Markets operations guide

Date: 2026-08-12

> Renamed on 12 August 2026. This file was titled "TraceEdge Operations Guide", the
> name of an earlier product. Nothing in the running system is called TraceEdge, and an
> operations guide that opens with the wrong product name is the first thing a new
> person on call reads.

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
| `API_WORKER_PROCESSES` | How many API worker processes serve at once. Default `2`. **One is a single point of failure** — when it is killed or replaced, the website is down until it returns. Measured: with one worker, 94 of 240 requests failed during a recycle; with two, none did. |
| `API_WORKER_MAX_REQUESTS` | A worker retires after this many requests and a fresh one takes over. Default `20000`. This bounds a slow leak that nobody has found yet: the process never lives long enough to reach the container's memory ceiling. It was `800`, and that was too eager — Caddy holds pooled connections to each worker, so every retirement produced a handful of 502s. Retiring is now rare, and `deploy/Caddyfile` retries a dropped upstream instead of showing an error. |
| `API_WORKER_MAX_REQUESTS_JITTER` | Random extra requests added per worker before it retires. Default `5000`. **Never set this to 0.** Workers start together, so without jitter they all retire at the same moment — which is the outage again, just on a schedule. |
| `CELERY_WORKER_CONCURRENCY` | How many worker children run at once. Default `1`. Two was tried and the server killed a child at 890 MB — two CPUs do not mean two children are affordable, memory decides that. Left unset entirely, Celery uses one per CPU and peak memory depends on which machine it lands on. |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | Replace a worker child after this many tasks. Default `50`. Bounds a slow leak that no single task causes. Recycling costs a few seconds of process start; memory on this server costs more. |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB` | Kilobytes. A child above this is replaced once its current task finishes. Default `350000` (350 MB). The sum must fit the worker container's 1024 MB ceiling **including the parent process**: 200 MB parent + 1 × 350 MB = 550 MB. If it does not fit, Docker kills the container before Celery can recycle, and this setting does nothing at all. Note the words *once its current task finishes*: this cannot stop a single task that grows, which is why the container ceiling is sized from the measured peak of a scan instead. |
| `SHARIA_LIVE_QUOTE_CACHE_SECONDS` | How long one provider price snapshot is reused — and, through the same number, how often the Market page asks for a new one. Default `5.0`. It was `0.75`, which is below the browser's own 2-second floor, so the cache never served anybody and one open Market tab meant thirty full round trips to the exchange every minute. Do not lower it below `2.0`. |
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
| `AI_AGENT_CONTROL_ENABLED` | **Not a Setup Chat switch.** Bounded Agent Control is a retired general coordinator with no authority over authenticated Setup Chat. Must stay `false`. See "Stopping Setup Chat" below. |
| `AI_AGENT_SHADOW_MODE` | Belongs to the same retired coordinator. Default: `false`. |
| `AI_AGENT_ROLLOUT_PERCENT` | Belongs to the same retired coordinator. Default and production value: `0`. |
| `SETUP_CHAT_EMERGENCY_DISABLED` | **The Setup Chat kill switch.** `true` stops every new turn behind the AI-unavailable banner. Default: `false`. |
| `SETUP_FREE_TEXT_ENABLED` | Free-text messages in Setup Chat. `false` closes the composer and leaves the guided Builder. Default: `true`. |
| `SETUP_PLANNER_ENABLED` | The model call that reads a sentence into operations. Default: `true`. |
| `SETUP_COMPOSER_ENABLED` | The model call that writes the reply. `false` builds replies from the deterministic summary of what changed. Default: `true`. |
| `SETUP_BUILDER_ENABLED` | The guided Builder. Turning this off *and* the AI off leaves no way to author a Watchlist. Default: `true`. |
| `SETUP_SCANNER_ENABLED` / `SETUP_MONITOR_ENABLED` | Running a Scanner sweep, and creating/running Monitors, from a reviewed draft. Default: `true`. |
| `SETUP_CHAT_PRIVATE_BETA_USER_IDS` | Non-empty limits Setup Chat to exactly those user UUIDs. Empty keeps normal entitlement-controlled availability. |
| `SETUP_CHAT_RECOVERY_DISABLED` | Stops the crash-recovery worker. Left `false`: without it, a crashed turn holds its session and the user cannot send anything. |
| `SETUP_TURN_DEADLINE_SECONDS` | Whole-turn budget from the authenticated request boundary. Default: `45`. |
| `AI_BUDGET_PER_TURN_MAX_USD` etc. | Spending ceilings. A call whose cost cannot be estimated is refused rather than guessed at. |
| `AUTH_TEST_FIXED_CODE` | Test-only. Deployed startup **refuses to boot** if it is set: with a value, every sign-in code and every System Brain second factor becomes it. |
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
| `FASSET_SHARIAH_REPORTS_URL` | Published Fasset Shariah Reports page imported by the bounded authority worker. |
| `FASSET_MINIMUM_PROFILE_COUNT` | Fail-closed source-shape floor for complete Fasset profiles. Default: `100`. |
| `SHARIA_AI_MODEL` | Model used for bounded factual dossier/change analysis. Default: `gpt-5.4-nano`. |
| `SHARIA_AI_REASONING_EFFORT` | Factual research reasoning effort. Default: `low`. |
| `SHARIA_AI_SERVICE_TIER` | Must be `flex` for the deployed governance workflow. |
| `SHARIA_AI_TIMEOUT_SECONDS` | Whole factual-analysis request timeout. Default: `900`. |
| `SHARIA_AI_MAX_RETRIES` | Retry limit for retryable Flex failures. Default: `5`. |
| `SHARIA_AI_ALLOW_STANDARD_FALLBACK` | Allows an explicit standard-tier fallback; default is fail-closed `false`. |
| `SHARIA_REVIEW_REMINDER_HOURS` | Reminder window for open review cases. Default: `6`. |
| `SHARIA_REVIEW_SLA_HOURS` | Initial due-date window for review cases. Default: `48`. |
| `REQUIRE_SECOND_REVIEWER` | When true, approving does not publish: a different reviewer must publish. Default: `false`, so approving publishes in the same step. |
| `SHARIA_PACK_EVIDENCE_MAX_AGE_DAYS` | How old retained evidence may be, in days, when deciding an import-pack case. Default: `90`. A Shariah-governance number — change it with the governance owner. |
| `SHARIA_SOURCE_SCAN_INTERVAL_HOURS` | Authority import and approved-source monitoring interval. Default: `24` hours. Sets each case's re-check reminder; it never refuses a decision. |
| `SHARIA_SCRAPER_CONCURRENCY` | Must be `1`; official sources are fetched sequentially. |
| `SHARIA_SCRAPER_OBEY_ROBOTS` | Must remain `true` in staging and production. |
| `SHARIA_SCRAPER_DOWNLOAD_DELAY_SECONDS` | Delay between official-source requests; deployed minimum is one second. |
| `SHARIA_PILOT_SYMBOLS` | Pilot allowlist, default `BTC,ETH,SOL`. |
| `SHARIA_PROCESS_REMAINING_IMPORTS` | Sends every explicit imported authority record through exact identity, factual research, and human review gates; default `true`. |
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
| `PUBLIC_WAITLIST_MODE` | Pre-launch mode for the public site. `true` replaces the pricing section and plan comparison with the waitlist form, turns every "Sign in" / "Start free" call to action into "Join the waitlist", removes Pricing and Halal Assets from the public menus and the sitemap, redirects `/pricing` to `/#waitlist`, and stops the support assistant from sending an anonymous visitor to an account page. `/signin`, `/signup` and the dashboard keep working for invited users; they are only unadvertised. Set `false` to restore the pricing site unchanged. |
| `PUBLIC_CHAT_ENABLED` | Enables the separate public product-information assistant. |
| `PUBLIC_CHAT_AI_ENABLED` | Enables grounded multi-turn AI support. Deployed public chat requires `true`. |
| `PUBLIC_CHAT_AI_MODEL` / `PUBLIC_CHAT_AI_REASONING_EFFORT` | Configurable support model and effort; defaults to the main model and `low`. |
| `PUBLIC_CHAT_AI_TIMEOUT_SECONDS` / `PUBLIC_CHAT_AI_PROVIDER_ATTEMPTS` | Bounded provider timeout and retry count. |
| `PUBLIC_CHAT_AI_MAX_OUTPUT_TOKENS` / `PUBLIC_CHAT_AI_MAX_ESTIMATED_COST_USD_PER_TURN` | Output and cost ceilings enforced before and after provider calls. |
| `PUBLIC_CHAT_AI_MIN_CONFIDENCE` | Below this threshold, the bot clarifies or reports that the fact is unverified. It never opens the Support form. |
| `PUBLIC_CHAT_AI_MAX_HISTORY_MESSAGES` / `PUBLIC_CHAT_SESSION_MAX_TURNS` | Bounded server-side conversation memory and per-session abuse limit. |
| `PUBLIC_CHAT_NOTION_ENABLED` / `PUBLIC_CHAT_NOTION_ROOT` | Enables bounded read-only retrieval from the project Notion export. Retrieved files are context-only, never current-product authority. |
| `PUBLIC_CHAT_NOTION_MAX_DOCUMENTS` / `PUBLIC_CHAT_NOTION_MAX_CHARACTERS` / `PUBLIC_CHAT_NOTION_MAX_FILE_BYTES` | Per-turn and per-file limits for the Notion context index. |
| `PUBLIC_CHAT_INQUIRY_EMAIL` | Office recipient for consented public inquiries. |
| `PUBLIC_CHAT_PROFILE_VERSION` | Invalidates stale local-only visitor profile consent when changed. |
| `PUBLIC_CHAT_MESSAGE_MAX_LENGTH` / `PUBLIC_CHAT_INQUIRY_MAX_LENGTH` | Server-enforced public input bounds. |
| `PUBLIC_CHAT_ANSWER_AUDIT_RETENTION_DAYS` / `PUBLIC_CHAT_INQUIRY_RETENTION_DAYS` | Retention for hashed answer telemetry and consented inquiries. |
| `PUBLIC_CHAT_EMAIL_MAX_ATTEMPTS` / `PUBLIC_CHAT_EMAIL_RETRY_MINUTES` / `PUBLIC_CHAT_EMAIL_CLAIM_TIMEOUT_MINUTES` | Bounded inquiry-email outbox retry and abandoned-claim recovery. |

When `PUBLIC_CHAT_ENABLED=true` in staging or production, startup requires `EMAIL_ADAPTER=smtp`
plus non-placeholder SMTP host, username, password, and sender address. This deliberately prevents
an inquiry-enabled deployment from accepting questions into an outbox that cannot be delivered.
When `PUBLIC_CHAT_NOTION_ENABLED=true`, deployed startup also requires the configured Notion root
to exist. The runtime image copies `Notion/`; no generic filesystem tool is exposed to either AI.

The browser opens the Support form only after the visitor chooses **No. Submit a support form** or
explicitly asks to contact the team. Low confidence, missing evidence, provider timeouts, schema
failures, greetings, normal conversation, refusals, and out-of-scope turns never open it. The
answer-feedback API is session-bound and accepts one idempotent choice per answer event.

## Private-Beta Locked Profile

The controlled private beta is invite-only and free. Production examples intentionally enforce BTC,
ETH and SOL on Binance spot, one approved methodology, in-app and Telegram delivery, paid checkout
off, WhatsApp off, Discord retired, and certified user-scoped OHLCV extensions on.

> Corrected on 14 August 2026. This paragraph said the Bounded Agent was "live for every
> authenticated beta user" and that startup and the release invariants "require shadow mode off and
> rollout at 100 percent". Both are false at HEAD and were the opposite of what the product ships:
> `scripts/check_release_invariants.py` requires `AI_AGENT_CONTROL_ENABLED=false`,
> `AI_AGENT_SHADOW_MODE=false` and `AI_AGENT_ROLLOUT_PERCENT=0`, and `.env.production.example`
> carries exactly those. Authenticated Setup Chat is served by the Setup Agent, whose bounds are the
> `SETUP_AGENT_*` and `SETUP_*` settings.

Live OpenAI/Binance/SMTP proof remains a separate staging gate.

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

Migration `3cedf4051627` adds Support conversation modes, advisory handoff evidence, one
session-bound answer-feedback record, server-owned inquiry metadata, and answer-to-inquiry linkage.

Migration `d6e7f8a9b0c1` adds the SC Malaysia governance workflow. It seeds only the versioned
methodology family/version and never seeds or publishes an asset.

Migration `e7f8a9b0c1d2` adds immutable Passport/event references, governance roles, reviewer
profiles and assignments, problem reports, decision/publication integrity fields, first-party
checkout attempts, and payment-email outbox state.

Migration `6f02832495ab` adds Fasset source provenance fields, archives development/test
methodologies, and adds the Fasset methodology and deduplicated `All` aggregate view. Neither
methodology seed publishes an asset.

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

### Stopping Setup Chat

> Rewritten on 14 August 2026. This section previously gave `AI_AGENT_CONTROL_ENABLED=false` as the
> Setup Chat rollback. It is not one. `AISetupChatService.handle_message` hands every authenticated
> turn to `SetupChatLaunchService` and returns
> (`src/ai_market_monitor/services/ai_setup_chat.py:1313`); the branch that reads
> `AI_AGENT_CONTROL_ENABLED` sits below that return and can only be reached through
> `SETUP_CHAT_LEGACY_TEST_COMPAT_ENABLED`, which deployed startup refuses
> (`src/ai_market_monitor/core/config.py:733`). An operator following the old instruction during an
> incident would have changed a variable, restarted, and watched Setup Chat carry on unchanged.

The release profile is:

```dotenv
AI_AGENT_CONTROL_ENABLED=false
AI_AGENT_SHADOW_MODE=false
AI_AGENT_ROLLOUT_PERCENT=0
CAPABILITY_EXTENSION_ENABLED=true
CAPABILITY_EXTENSION_PREFLIGHT_EXCHANGE=binance
PUBLIC_CHAT_ENABLED=true
PUBLIC_CHAT_AI_ENABLED=true
```

**What actually stops Setup Chat**, narrowest action first. Each takes effect on API restart.

| Symptom | Set | What keeps working |
|---|---|---|
| Replies are wrong or unsafe | `SETUP_COMPOSER_ENABLED=false` | Everything. Replies become the deterministic summary of what really changed. |
| The planner misreads sentences | `SETUP_PLANNER_ENABLED=false` | The guided Builder, Scanner, Monitors, every approved Watchlist. |
| Free text must stop entirely | `SETUP_FREE_TEXT_ENABLED=false` | The guided Builder. A person can still author and approve a complete Watchlist. |
| Turns are failing mid-way | `SETUP_CHAT_EMERGENCY_DISABLED=true` | Every approved Watchlist keeps evaluating and keeps alerting. Nothing saved is changed or lost. |
| One capability is misbehaving | `BUILDER_CAPABILITIES_DISABLED=<key>` | Everything else. The capability is still shown, with a reason. |
| Only some accounts should have it | `SETUP_CHAT_PRIVATE_BETA_USER_IDS=<uuids>` | Everyone on the list. |

There is **no** switch that removes the writable path while leaving free text on: the Setup Agent is
the only writer for free text, so stopping it means stopping free text. Rolling the deployment back
is still available and is the only way to change the agent's behaviour rather than its availability.

To restore, set the switch back and restart. No schema rollback is needed for any of them.

Before opening access, verify zero forbidden executions and unsupported-condition leakage, inspect
fallbacks and clause gaps, and complete the live staging matrix in
`docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md`. System Brain is the operational evidence view;
committed reports are not runtime proof.

The retired coordinator's catalog, policy and limits are kept for history only in
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
- Public waitlist Google Sheet and contact-office email delivery retries.
- Dormant WhatsApp webhook/retry tasks only when the separately disabled WhatsApp feature is enabled.
- Certified capability creation and five-scan repair reviews every 30 seconds.
- Database connectivity metric.
- Idempotent methodology-pack, SC Malaysia, and Fasset authority imports every
  `SHARIA_SOURCE_SCAN_INTERVAL_HOURS` (24 hours by default).
- Hourly open-review reminders and minute-level Telegram retry processing.
- Published-asset source monitoring at `SHARIA_SOURCE_SCAN_INTERVAL_HOURS`.

The live scanner currently uses shared CCXT REST clients. Jobs are claimed atomically from
`queued` to `running`, store worker id/claim/heartbeat timestamps, and are not rerun after terminal
states. WebSocket ingestion and a durable candle store remain future production-hardening work.
Capability extension jobs additionally require a configured server-side OpenAI key. The generated
artifact remains a bounded deterministic expression and must pass normal user approval. See
`docs/CAPABILITY_EXTENSION_PIPELINE.md` for the escalation and failure behavior.

## Public Landing, Contact, and Analytics

The landing/contact source is `Hilal-Markets-Website/`. The Docker image runs its locked
TypeScript and Vite build before packaging `dist/` as `static/landing/`. A failed frontend build
therefore fails the application image build.

Public forms are same-origin and CSRF-protected. `waitlist_signups` is the source of truth; Google
Sheet delivery is a retryable projection. Contact creates one idempotent delivery from
`CONTACT_FORM_SENDER_EMAIL` to `CONTACT_FORM_RECIPIENT_EMAIL`, with the visitor address only as
`Reply-To`. Verify that the configured sender is authorized by the SMTP provider before deployment.

To connect the waitlist Sheet:

1. Add `scripts/google_apps_script/waitlist_webhook.gs` to an Apps Script project.
2. Set Script Properties `WAITLIST_SPREADSHEET_ID`, `WAITLIST_WEBHOOK_SECRET`, and optionally
   `WAITLIST_SHEET_NAME`.
3. Deploy it as a Web App executing as the owner and retain the `/exec` URL.
4. Set the same random secret in `WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET`, set the URL in
   `WAITLIST_GOOGLE_SHEETS_WEBHOOK_URL`, then enable `WAITLIST_GOOGLE_SHEETS_ENABLED`.
5. Behind Cloudflare, enable `WAITLIST_TRUST_CLOUDFLARE_COUNTRY_HEADER` only after direct-origin
   traffic is blocked. Otherwise country remains `unknown` instead of trusting a spoofable header.

The server sends one fixed request body, built only by
`src/ai_market_monitor/services/waitlist_sheet_contract.py`:

| Field | Value |
|---|---|
| `secret` | `WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET` |
| `email` | the signup email |
| `name` | always empty — the form asks for an email and nothing else |
| `source` | always `hilalmarkets_waitlist` |
| `country` | the server-side country, or the word `unknown` |
| `status` | always `waitlist` |

Change that list in one place only. A receiver that reads a different field name rejects every
signup, and the rejection looks like an ordinary delivery failure. Whatever else Hilal Markets
knows about a signup — when it happened, which page it came from, first-touch attribution — stays
in `waitlist_signups` and is not sent.

**Both halves of this contract now live in the repository and agree.** Until 14 August 2026 they
did not: `waitlist_webhook.gs` authorised on `webhook_secret` and required `event_id` and
`submitted_at`, none of which the server sends. Deploying that file would have answered
`unauthorized` to every signup, and the rejection would have looked like an ordinary delivery
failure in the retry log. The file now reads `secret` and the six fields above, and
`tests/unit/test_invariant_phase6_launch_audit.py` fails if the two sides drift apart again.

> **BLOCKING EXTERNAL DEPENDENCY — the deployed Web App must be redeployed from this file.**
> Nothing in this repository can change what is running in Google Apps Script. Until the steps
> below are carried out, the sheet is served by whatever was pasted into it previously, and this
> repository cannot tell you which version that is.
>
> 1. Open the Apps Script project bound to the waitlist spreadsheet.
> 2. Replace the entire contents of `Code.gs` with `scripts/google_apps_script/waitlist_webhook.gs`
>    from this commit. Do not merge by hand; replace the file.
> 3. Confirm Script Properties `WAITLIST_WEBHOOK_SECRET` and `WAITLIST_SPREADSHEET_ID` are set, and
>    that the secret is byte-identical to `WAITLIST_GOOGLE_SHEETS_WEBHOOK_SECRET` in the deployment.
> 4. **Deploy → Manage deployments → edit the existing deployment → New version.** Creating a new
>    deployment instead issues a new `/exec` URL and the server keeps posting to the old one.
> 5. Execute as **Me**, access **Anyone**. Keep the `/exec` URL unchanged.
> 6. Verify before trusting it: submit one signup on the public site, confirm a row appears with the
>    correct email and country, then submit the same address again and confirm **no second row**.
> 7. The first request after the upgrade rewrites the worksheet into the layout below, carrying
>    every existing row across. Take a copy of the sheet before step 6.

Duplicates are prevented by email address, not by a delivery id. The server sends no delivery id, so
a retry cannot be recognised by one; the receiver checks the email column before appending instead,
which is the right key for a waitlist — one person, one row.

`Joined At (UTC)` is the moment the script received the signup, not the moment the person submitted
it. The server does not send its own timestamp. The authoritative submission time stays in
`waitlist_signups`, which is the source of truth for everything the sheet does not carry.

The receiver serializes writes with a script lock and keeps the visible worksheet business-facing:
Email Address, Joined At (UTC), Country, Signup Source, Status, and Notes. Status is an editable
controlled list and Notes is free text for the beta team; neither is overwritten by a delivery. The
endpoint, secret, and delivery metadata never enter HTML, browser JavaScript, analytics, or public
form responses.

Analytics is off by default. Configure `VITE_GTM_ID`, keep the deprecated
`VITE_GA4_MEASUREMENT_ID` empty, then set `VITE_ANALYTICS_ENABLED=true`. GA4 must be configured
inside the published GTM container; the website does not load `gtag.js` directly. Meta additionally requires `VITE_META_PIXEL_ID`,
`VITE_META_PIXEL_ENABLED=true`, and `MARKETING_CONSENT_ENABLED=true`. GA initializes only after
Analytics consent; Meta initializes only after Marketing consent. `VITE_ANALYTICS_DEBUG=true`
enables sanitized console diagnostics; never enable it as routine production logging.

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
grant cannot perform governance mutations. With `REQUIRE_SECOND_REVIEWER=false`, approving records
the approval and publishes the Passport in the same action — two audited governed steps, one press.
Set it to `true` to keep the two apart and require a different person for the publication.

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

## Service-level objectives, alerts and the issue queue

Everything operational is defined in one place, `src/ai_market_monitor/observability/`:

| File | What it owns |
|---|---|
| `labels.py` | The only allowed metric labels, and the rule that keeps secrets out |
| `metrics.py` | Every metric the product records, and the recorder that writes them |
| `slos.py` | The objectives. Each names a metric from `metrics.py` |
| `alerts.py` | The rules that page or raise a ticket when an objective breaks |
| `alert_delivery.py` | Sends a page, at most once per problem, and falls back |
| `durable_metrics.py` | Writes the measurements down so they survive a restart |
| `issues.py` | The deduplicated queue of operational problems |
| `banners.py` | What a customer is told while something is degraded |

Two rules are enforced by the application itself and will stop a deployment:

1. **An objective must be measurable.** If an objective names a metric nothing emits,
   startup refuses to boot. An unmeasurable objective reads as "no data" forever, which
   on a dashboard looks exactly like health.
2. **An alert may not travel through what it is watching.** The alert about Telegram
   delivery cannot be a Telegram message. Startup refuses that too, because the failure
   is silent: the alert is generated correctly and never arrives.

### Where the measurements live

Recording happens in memory, because it must never slow a request down or fail one.
Every process then writes down **only what it added since its own last write**, into a
row keyed by its own writer identity. Reading adds those rows up.

That is what makes the numbers true for the whole product rather than for one web
process, and what makes them survive a restart. Because no two processes ever write the
same row, two of them writing at the same moment cannot lose each other's counts, and a
retried write cannot count anything twice.

| Setting | What it does | Default |
|---|---|---|
| `OBSERVABILITY_WINDOW_SECONDS` | Width of one stored window | 300 |
| `OBSERVABILITY_FLUSH_INTERVAL_SECONDS` | How often a process writes down | 60 |
| `OBSERVABILITY_ROLLUP_AFTER_HOURS` | When per-process rows become one row | 6 |
| `OBSERVABILITY_RETENTION_HOURS` | When stored measurements are deleted | 72 |

Retention must be longer than the rollup age. Startup refuses to boot if it is not,
because rows would be deleted before they were ever folded together and the history
would quietly stop going back as far as the page says it does.

Two scheduled tasks keep this working. Both are in the beat schedule in `worker.py`:

| Task | Every | What it does |
|---|---|---|
| `ai_market_monitor.flush_operational_metrics` | 60s | The scheduler writes its own measurements down |
| `ai_market_monitor.compact_operational_metrics` | 1h | Folds old rows into one, then deletes past retention |

The API writes its own on a timer inside the process. Every worker writes its own after
a task it runs, throttled to the flush interval. A scheduled task only ever runs in one
process, so it can never write the others down for them.

**`compact_operational_metrics` is the only thing bounding the size of the table.** If
it stops running, nothing fails until the health page is too slow to open. The release
gate checks that both tasks are still defined and still scheduled.

### Where a page actually goes

There is no external paging service in this product, so every route depends on part of
this product. A page-worthy alert therefore names **two** routes whose dependencies do
not overlap, and the second is used when the first refuses.

| Setting | What it does |
|---|---|
| `OPERATIONAL_ALERT_TELEGRAM_CHAT_ID` | The operations Telegram chat |
| `OPERATIONAL_ALERT_EMAIL` | The operations mailbox |
| `OPERATIONAL_ALERT_REPEAT_MINUTES` | How long a firing rule stays quiet after paging once |
| `OPERATIONAL_ALERT_MAX_ATTEMPTS` | Attempts before a delivery is marked failed |

Deliberately **not** the Sharia review chat. Two different audiences; a page dropped
into a review queue buries both.

While these are unset, a page is still recorded in the operational issue queue and the
delivery row says plainly that it could not be sent. Nothing is lost. Nobody is woken.

Two more scheduled tasks carry this:

| Task | Every | What it does |
|---|---|---|
| `ai_market_monitor.deliver_operational_alerts` | 60s | Evaluates the rules and claims what must be sent |
| `ai_market_monitor.retry_operational_alert_deliveries` | 60s | Sends what is claimed, falling back if needed |

**A ticket-worthy alert is never delivered.** It goes into the issue queue and waits
for somebody looking at the queue. Waking a person for a slow page is how they learn to
ignore the next message, which may be the outage.

`operational_alert_deliveries` holds one row per page. `used_fallback` says whether the
first route refused; if it is true, find out why before trusting the first route again.

Where to look:

- `/api/v1/admin/health` — every objective with its current reading, every firing
  alert, and the issue-queue counts. This is the operator view; there is no second
  console.
- `/api/v1/admin/activity` — scan, alert, delivery and billing activity.

An objective reading `no_data` has **not** passed. It has not been tested.

### How to read an issue

One row per problem, not one per occurrence. `occurrence_count` and `first_seen_at`
together answer the only question that matters at the start: how long has this been
happening, and is it getting worse.

States move `open → acknowledged → mitigated → resolved`. A resolved problem that
happens again **reopens the same row** rather than starting a new one, so a recurring
fault keeps its history. Suppression always has an end time; a suppression with no
expiry is how a known problem stops being reported and then stops being known.

Issues never hold customer content. No strategy text, no religious status, no secrets.
This is enforced in code, not by convention.

## Incident runbooks

One section per alert. Each is written to be followed by somebody who was asleep ten
minutes ago.

**Before any of them:** none of these procedures involves turning off Shariah
screening, widening the screened universe, or relaxing the market-data staleness
check. Those are fail-closed gates. If one of them is blocking, it is doing its job,
and forcing it open converts a visible outage into a wrong answer shown to a customer.

### The server runs out of memory
*No alert watches this. Nothing in the product measures the server's memory, so the first
sign is that the whole site stops working and SSH stops answering.*

**This is what actually happened on 22 August 2026.** It looked like a full disk. It was
not. Always check memory before you start deleting files.

- **Detection.** The site does not answer. SSH is refused or hangs. The Hetzner console
  shows the machine up but unusable. The disk looks fine.
- **Proof.** From the rescue system, read the machine's own log:

  ```bash
  strings /mnt/var/log/journal/*/system.journal \
    | grep -iE "out of memory|oom-kill|no space left" | tail -40
  ```

  On the day, that printed:

  ```
  Out of memory: Killed process 564376 (celery) anon-rss:1428256kB   15:48:53
  Out of memory: Killed process 597555 (celery) anon-rss:1428884kB   16:50:20
  systemd invoked oom-killer                                        16:50:19
  Total swap = 0kB, 1023866 pages RAM (about 3.9 GB)
  ```

- **What it means in plain words.** A background worker grew to 1.4 GB. The server has
  3.9 GB and **no swap**, so the kernel had to kill something. It killed `systemd`, which
  is the program that runs everything else. That is why SSH died too.
- **Mitigation.** Reboot. Then make sure all three protections below are in place.

**The three things that stop it happening again.** Any one of them alone keeps the site
up. All three are now in place except the first, which is a server setting and cannot
live in this repository:

| # | Protection | Where it lives |
|---|---|---|
| 1 | **Swap.** Gives the kernel slack instead of killing at once | The server — see below. **Check this first after any rebuild** |
| 2 | **Celery recycles a worker child that grows** | `celery_worker_*` in `core/config.py`, applied in `worker.py` |
| 3 | **Each container has a memory ceiling** | `mem_limit` per service in `docker-compose.prod.yml` |

**Adding swap** (do this once, on the server, as root):

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
free -h        # confirm Swap shows 4.0Gi
```

Swap is slower than memory. That is the point: a slow server is a server you can still
log in to and fix. A server with no swap goes from working to unreachable with no warning.

**The memory budget** for the Hetzner CX22 (2 CPUs, 3.9 GB, 40 GB disk):

| Service | Measured use | Ceiling |
|---|---|---|
| api | 273 MB per worker | 1280 MB |
| worker | 465 MB idle, **700 MB peak during a scan** | 1024 MB |
| db | 113 MB | 512 MB |
| scheduler | 57 MB | 192 MB |
| redis | 27 MB | 160 MB |
| caddy | 43 MB | 128 MB |
| **Total** | | **3296 MB**, leaving about 600 MB for the operating system |

The worker's ceiling was 768 MB and that was too tight: it was killed at about 700 MB on
22 August 2026, part way through a scan. **Celery's own per-child memory limit cannot
prevent that** — it is checked *between* tasks, so one scan that allocates a lot in a
single run walks straight past it and Docker kills the container instead. The killed scan
is then retried and grows the same way. A worker ceiling is therefore sized from the
largest amount one task has been *measured* using, not from the recycle threshold;
`test_the_worker_container_holds_what_one_task_has_been_seen_to_use` enforces that.

The api's share is the largest because it runs **two** worker processes plus a small
parent — about 630 MB before any growth.

The shares come from what each service actually uses, not from guessing. The first version
of this budget had it backwards — the api had 768 MB and the worker 1024 MB, and it was the
**api** that kept being killed. The api is the customer-facing process: when it dies, the
website is down. It now has the largest share, and the total came down at the same time,
because `db` and `scheduler` were holding room they never touch.

`tests/unit/test_invariant_container_memory_limits.py` fails if a service has no ceiling,
if the ceilings add up to more than the server has, or if the worker container is too
small to hold its own Celery children — in that last case Docker would kill the container
before Celery could recycle a grown child, and protection 2 would never run. **If you move
to a bigger server, change `SERVER_RAM_MB` in that test.**

- **Never.** Do not remove a `mem_limit` to make an out-of-memory error go away. Without
  it the kernel kills something else instead, and last time it chose the program that runs
  the whole machine.

### How the API is served, and why the site survives a leak

The API starts with `python -m ai_market_monitor.serve`, not a long `uvicorn` command. That
file is the only reader of the three `API_WORKER_*` settings, so the numbers live in one
place instead of being repeated in `docker-compose.prod.yml`.

Two things happen there, and together they mean **a leak can no longer take the site down,
even one nobody has found**:

| | What it does |
|---|---|
| Several workers | One is replaced while the others keep serving |
| Retire on a request count | No process lives long enough to reach the memory ceiling |
| Jitter on that count | They never retire at the same moment |

Nothing extra is installed for this. Gunicorn was the obvious answer and turned out to be
unnecessary: the pinned `uvicorn 0.49.0` already provides `--workers`,
`--limit-max-requests`, `--limit-max-requests-jitter`, and a parent process that starts a
replacement whenever a worker goes away. Using gunicorn would have meant adding **two**
dependencies — gunicorn itself and `uvicorn-worker`, because `uvicorn.workers` was removed
in 0.49 — to obtain behaviour already present.

**Measured, on Linux, with the pinned uvicorn**, sending 240 requests at a server told to
retire a worker every 5–7 requests:

| Workers | Requests | Failed | Distinct processes that answered |
|---|---|---|---|
| **2** | 240 | **0** | 4 |
| **1** | 240 | **94** | 1 |

The second row is the setup that was running until 22 August 2026. Four different
processes answering in the two-worker run is the proof that workers really were retired
and replaced during it — and no request was dropped while that happened.

`tests/unit/test_invariant_api_serving.py` checks that every setting actually reaches
uvicorn and that each name is one uvicorn accepts — a misspelled option is ignored in
silence, and the protection would look present while doing nothing.

**Retiring a worker is not free, and the proxy has to know.** Caddy keeps pooled
connections open to the API. When a worker retires, every pooled connection to *that*
worker dies, and a request in flight on one of them is answered 502. This happened in
production: the 502 timestamps matched worker start times to the second. Two things fix it
together, and neither is enough alone:

| Where | What |
|---|---|
| `deploy/Caddyfile` | `lb_try_duration 5s` — retry against the replacement instead of answering 502 |
| `API_WORKER_MAX_REQUESTS` | 20000, not 800 — retiring is rare rather than routine |

The first version used 800, which made a worker retire every few minutes on a busy page
and turned a safety net into the most common cause of errors.

### One page must not read the whole database

The defect that made the dashboard unusable on 22 August 2026 was not memory management.
It was one line: every list of screened coins asked for **all** assessments under a
methodology, then kept a page of them.

`AssetShariaAssessment` carries three JSON columns — `evidence_snapshot` holds a whole
factual profile per asset — so reading the table is not a long list, it is hundreds of
megabytes. Measured: the Home page took about **1.6 GB and thirty-six seconds** to draw a
strip of **twelve** coins.

It was never only one page. Five places went through the same call: Home, the Market tab,
the Halal Assets list, the coin search inside the monitor builder, and the setup chat. The
Market tab repeated it every two seconds and the coin search on every keystroke, so a
single open Market tab kept a worker permanently busy — which is why pages that touch no
Shariah code at all, such as Subscriptions, were also slow. They were queued behind it.

The shape of the fix, and the rule to keep:

- `ShariaScreeningService._winning_assessments` is the **single owner** of "which
  assessment governs this asset". It reads six small columns and never a JSON blob.
- Whole rows are loaded only for the rows that reach the answer.
- A caller that only needs a count uses `eligible_assets`; a caller that needs a page uses
  `list_screened_assets`; a caller that needs named assets passes `assets=`.
- **`effective_assessments` without an `assets=` scope reads everything.**
  `test_no_caller_reads_every_assessment` fails the build if any module does that.

Because this decides which coins are shown as Halal and with which status, the change is
guarded by a differential test: the old algorithm is kept in full inside
`tests/services/test_invariant_screened_list_reads_only_its_page.py` and every request
shape the product makes is compared against it. A faster list that gives a different
answer must fail.

### The server disk is full
*No alert watches this. Nothing in the product measures the server's disk, so the first
sign is that the whole site stops working.*

> **Check memory first.** On 22 August 2026 this looked exactly like a full disk and was
> not — see the section above. Run `df -h /` and `df -i /` before deleting anything. If
> they show free space, the fault is somewhere else and deleting files wastes the outage.

- **Detection.** The site does not answer. Logging in over SSH is slow, or fails, or drops
  you straight back out. `df -h /` says `100%`. PostgreSQL cannot write, so everything
  stops at once — the site, the scans and the alerts.
- **Order.** Get in **first**. You cannot delete anything without a shell, and a full disk
  can stop you getting one. Do not start a deploy to fix it: a deploy needs *more* room
  than it frees.

**Step 1 — get a shell, in this order. Stop at the first one that works.**

| Try | When it works | What it is |
|---|---|---|
| `ssh root@<server>` | Usually still works on a full disk | The normal way in |
| The hosting panel's web console (VNC / serial) | SSH refuses or disconnects at once | A screen attached to the server. It does not use the network the way SSH does, so a broken `sshd` cannot block it |
| The provider's rescue system | Even the console cannot log in | Boots a small separate system, mounts the server's disk as a folder, and lets you delete files. Reboot back to normal afterwards |

If SSH connects and then closes immediately, that is the full disk, not a wrong password.
Login needs to write a few small files and cannot.

**Step 2 — free room. One command:**

```bash
cd <the repository folder on the server>
bash deploy/free-disk.sh
```

It deletes only things the server can make again: old pre-deploy database dumps, the
container log files, the systemd journal, the apt cache, the Docker build cache, and
Docker images nothing is using. It cannot reach a Docker volume, so the database, Redis,
the exported files and the TLS certificates are all safe. If it does not free enough, run
`KEEP=1 AGGRESSIVE=1 bash deploy/free-disk.sh`.

> **Never** run `docker system prune -a --volumes` or `docker volume prune`. With the
> stack stopped, Docker counts the PostgreSQL volume as unused and both commands delete
> it. That is the entire database. It happened on this project on 19 August 2026 and
> recovery needed a full dump plus repair by hand.

**Step 3 — start the stack again.** Not a deploy — just start what is already built:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=50 db
```

- **Verification.** `curl -sS -o /dev/null -w '%{http_code}\n' https://hilalmarkets.com/`
  answers `200`, and every service in `ps` says `running`.
- **If PostgreSQL will not start.** A disk that filled while it was writing can leave it
  refusing to open. Free the room first, then restart it once. Only if it still refuses,
  restore the newest dump from `../hilalmarkets-backups/` following
  [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md).
- **Never.** Do not delete a Docker volume, `.env.production`, or the newest database
  dump, to make room.

**Two ways it could fill up.** Both were found and fixed on 22 August 2026 while looking
into the memory outage above. **Neither of them caused that outage** — the disk was only
25% full — but both are real, and left alone either one would have filled the disk in
time:

| Cause | Fix |
|---|---|
| Every deploy wrote a full database dump into `../hilalmarkets-backups/` and nothing ever deleted them | `deploy/resource_guard.sh` keeps the newest `BACKUP_KEEP` dumps (5 by default) and deletes the rest, before and after each dump is written |
| Nothing checked for free space, so a deploy that ran out mid-build left half-written image layers behind and never reached the step that clears them — each failed try left *less* room | Both deploy scripts refuse to start below `MIN_FREE_GB` (5 by default) and tell you to run `deploy/free-disk.sh` |

Free space is read in one place only, `deploy/resource_guard.sh`, so no two scripts can
disagree about whether there is room. `tests/unit/test_invariant_deploy_disk_safety.py`
fails if a deploy script writes dumps without deleting old ones, builds without checking
for room, or contains a command that can delete a Docker volume.

**Still unsolved:** nothing takes a scheduled backup off this server. The only dumps are
the pre-deploy ones, and they sit on the same disk as the database — if the server is
lost, they are lost with it. Choosing where off-server backups go is a decision for the
owner, so it is not done.

### API availability
*Alert: `api_unavailable` — pages.*

- **Detection.** `api_availability` below 99.5% over one hour.
- **Triage.** Open `/api/v1/admin/health`. Check `dependency_health` for the database
  and Redis first. Most 5xx bursts are one of those two, not the application.
- **Mitigation.** If both dependencies are healthy, roll back to the previous release.
- **Rollback.** Redeploy the previous image tag; no schema rollback is needed for this
  phase's migration.
- **Verification.** `curl -fsS https://<host>/health` and watch `api_availability`
  return above objective for fifteen minutes.

### API latency
*Alert: `api_slow` — ticket.*

- **Detection.** `api_latency_p95` above 1000 ms over one hour.
- **Triage.** Compare with `provider_call_duration_ms` and `queue_depth`. A slow
  upstream shows here before it shows anywhere else.
- **Mitigation.** None needed urgently; nothing is wrong, only slow.
- **Verification.** p95 back under 1000 ms.

### Setup Chat turn failures
*Alert: `setup_chat_failing` — pages.*

- **Detection.** `setup_chat_turn_success` below 98% over one hour.
- **Triage.** Check `ai_provider_success` first. If the provider is also breached, this
  is a provider incident; follow that section instead.
- **Mitigation.** Set `SETUP_CHAT_EMERGENCY_DISABLED=true` and restart the API. Turns
  then stop cleanly behind the AI-unavailable banner instead of failing mid-turn.
- **What keeps working.** Every approved Watchlist keeps evaluating and keeps alerting.
  Nothing saved is changed or lost. Say this to customers.
- **Rollback.** Set the switch back to `false` and restart.
- **Verification.** Send one authenticated turn and confirm it completes.

### Setup Chat latency
*Alert: `setup_chat_slow` — ticket.*

- **Detection.** `setup_chat_latency_p95` above 12 s.
- **Triage.** Check provider latency before touching routing or timeouts.
- **Mitigation.** Raising `SETUP_TURN_DEADLINE_SECONDS` is not a fix. It moves the
  failure from the server to the browser.

### AI provider degraded
*Alert: `ai_provider_degraded` — pages.*

- **Detection.** `ai_provider_success` below 97% over thirty minutes.
- **Triage.** Check `provider_circuit_state` for `openai`. An open circuit means the
  application has already stopped calling out, which is correct.
- **Mitigation.** Confirm the AI-unavailable banner is showing. Nothing else is
  required; the circuit breaker recovers on its own.
- **Never.** Do not disable screening, and do not describe this to a customer as a
  Shariah or compiler problem. It is neither.
- **Verification.** Circuit returns to `closed` and success rate recovers.

### Scans delayed
*Alert: `scans_delayed` — pages.*

- **Detection.** `scheduled_scan_completion` below 99% over three hours.
- **Triage.** Check `worker_heartbeat_age_seconds` and `queue_depth` before anything
  else. A dead scheduler shows here first.
- **Mitigation.** Restart the worker and scheduler containers. Do not re-queue jobs by
  hand; recovery is idempotent and claims are atomic.
- **Verification.** `scan_jobs_total{job_phase="run"}` rises and the objective recovers.

### Market data stale
*Alert: `market_data_stale` — pages.*

- **Detection.** `market_data_freshness` above 300 seconds.
- **Triage.** Check the exchange connection and `provider_calls_total` for the market
  data provider.
- **Mitigation.** Restore the exchange connection.
- **Never.** Do not relax the staleness check to clear the alert. Confirmed alerts are
  being blocked on purpose: the product would rather send nothing than send an alert
  computed from old prices.
- **Verification.** Freshness back under 300 seconds.

### Alert delivery failing
*Alert: `alert_delivery_failing` — pages.*

- **Detection.** `alert_delivery_success` below 99% over one hour.
- **Triage.** Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` first; an
  expired or rotated token is the usual cause.
- **Mitigation.** Restore the credential. Deliveries retry automatically, so nothing
  is lost yet.
- **Verification.** `alert_delivery_attempts_total{delivery_result="delivered"}` rises.

### Email outbox backed up
*Alert: `email_outbox_backed_up` — ticket.*

- **Detection.** `email_outbox_drain_p95` above 900 seconds.
- **Triage.** Check SMTP credentials and that the retry task is running.
- **Mitigation.** Fix the credential; the outbox drains itself. Do not re-enqueue rows
  by hand — the outbox is idempotent per logical event and manual copies break that.
- **Verification.** `email_outbox_depth` falls.

### Worker or scheduler down
*Alert: `worker_or_scheduler_down` — pages.*

- **Detection.** `worker_heartbeat_age_seconds` above 180 seconds.
- **Blast radius.** Nothing scheduled runs: no scans, no retries, no reminders.
  Customers see no error, only silence. This is the most easily missed outage in the
  product.
- **Mitigation.** Restart the scheduler container.
- **Verification.** Watch one heartbeat arrive before leaving. A restarted container
  that crashes again on boot looks identical to a fixed one for about a minute.

### Review case overdue
*Alert: `review_case_overdue` — ticket.*

- **Detection.** `review_case_sla` above 48 hours.
- **Blast radius.** None for customers. Assets stay unpublished, which is the
  fail-closed behaviour working.
- **Mitigation.** Assign the case to the on-duty reviewer.
- **Never.** Do not publish an asset to clear this alert. Publication is a governance
  decision with its own evidence requirements, not a queue-cleaning action.

### Screening refusing everything
*Alert: `screening_refusing_everything` — pages.*

- **Detection.** More than fifty `no_active_passport` refusals in the window.
- **Triage.** Check whether a methodology version was archived or a publication was
  rolled back. This alert almost always means a governance change, not a bug.
- **Blast radius.** Customers see an empty or much smaller screened market. No wrong
  religious status is shown — the layer is failing closed exactly as designed.
- **Never.** Do not widen the universe to clear the alert.
- **Verification.** Refusal rate returns to normal after the methodology or publication
  is restored.

## Using the engineering assistant during an incident

The assistant can help you work out *what* broke. It cannot fix anything, and it must not
be asked to.

**What it can do.** Read sanitized metrics, alert and delivery records, issue records,
the health and activity endpoints, provider circuit state, AI usage, worker and scanner
records, and redacted logs. Correlate them, and write a diagnosis that names the failing
layer with the evidence behind it.

**What it will refuse**, in code and not as a matter of policy:

| Refused | Rule |
|---|---|
| Restarting a service or killing a process | `ops.no_production_restart` |
| Changing a feature flag or the launch stage | `ops.no_feature_flag_change`, `ops.no_launch_stage_change` |
| Silencing, muting or resolving an alert | `ops.no_alert_suppression` |
| Writing to any database | `ops.no_production_write` |
| Connecting to production Postgres or Redis | `ops.no_live_production_connection` |
| Deploying | `production.deploy` |

Check any command before you rely on it:

```powershell
.venv\Scripts\python -m hm_oi check "systemctl restart hilalmarkets"
```

**How to read what it gives you.** Every conclusion carries the environment it applies to
and the evidence for each claim. Two things to insist on:

- **`INSUFFICIENT EVIDENCE` is a real answer.** It means the signal needed is missing.
  Treat it as a gap to close, not as a failed attempt — pushing for a conclusion anyway
  is how an incident gets the wrong fix.
- **Correlation is not cause.** A diagnosis resting only on two things moving together
  cannot be stated at high confidence, and the tool will say so. It is a starting point.

**Production evidence is a snapshot.** The assistant never connects to production. It
reads an exported, sanitized file, so its picture is as fresh as that export and no
fresher. **Whoever exports the snapshot is responsible for sanitizing it** — nothing in
the tooling verifies that, and it is the weakest link in the chain.

**When the recommendation is an action**, it comes back as the exact command for you to
run. Run it yourself, after reading it. Full description and the runbook:
`docs/OI_OPERATIONAL_INVESTIGATOR.md`.

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
