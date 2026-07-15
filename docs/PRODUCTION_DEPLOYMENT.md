# Production Deployment

For the current single-VPS live deployment path for `trace-edge.com` and `app.trace-edge.com`,
use [`DEPLOY_TRACE_EDGE_LIVE.md`](../DEPLOY_TRACE_EDGE_LIVE.md). The notes below remain useful
for integration-specific production settings.

Production startup is fail-closed. Set `APP_ENV=production`, `ALLOW_MOCK_PROVIDERS=false`, a
non-default 32+ character `APP_SECRET_KEY`, PostgreSQL `DATABASE_URL`, Redis, and an HTTPS
`PUBLIC_BASE_URL`.

Enable only configured integrations:

```text
SCANNING_ENABLED=true
TRACEDGE_MARKET_DATA_MODE=ccxt
TRACEDGE_FIXTURE_MARKET_DATA_ENABLED=false
MARKET_DATA_PROVIDER=ccxt
MARKET_DATA_EXCHANGE=binance
AI_INTERPRETER_PROVIDER=openai
TELEGRAM_ENABLED=true
TELEGRAM_ADAPTER=http
DISCORD_ENABLED=true
DISCORD_ADAPTER=http
BILLING_ENABLED=true
BILLING_PROVIDER=nowpayments
EMAIL_ADAPTER=smtp
```

Then provide every secret documented in `.env.example`. For OpenAI interpretation, set
`OPENAI_API_KEY` and keep `OPENAI_MODEL` configurable. For Binance public spot data, API keys are
optional; if you add `BINANCE_API_KEY` and `BINANCE_API_SECRET`, do not grant withdrawal or trade
permissions for the v1 monitoring-only product. For NOWPayments, set
`NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_BASE_URL`, and `BILLING_WEBHOOK_SECRET` for IPN signature
verification. Stripe remains behind the billing-provider abstraction, but it is not the configured
payment path for this build.

Passwordless login and password reset use short-lived email codes. Configure an SMTP account
before enabling those actions in production:

```text
EMAIL_ADAPTER=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=no-reply@example.com
SMTP_USE_TLS=true
AUTH_CODE_TTL_MINUTES=10
AUTH_CODE_MAX_ATTEMPTS=5
```

The database stores only an HMAC digest of each six-digit code. Codes are single-use, expire after
the configured TTL, allow a limited number of attempts, and are throttled to one request per
email/purpose each minute. Replace every placeholder with values from the chosen email provider;
do not commit provider credentials.

Condition-context providers are independently configurable:

```text
COINGECKO_ENABLED=false
ALTERNATIVE_ME_ENABLED=false
FRED_ENABLED=false
BINANCE_DERIVATIVES_ENABLED=false
CRYPTO_INDEX_API_URL=
CRYPTO_INDEX_API_KEY=
MACRO_MARKET_API_URL=
MACRO_MARKET_API_KEY=
EVENT_FEED_API_URL=
EVENT_FEED_API_KEY=
TOKEN_CATEGORY_API_URL=
TOKEN_CATEGORY_API_KEY=
DERIVATIVES_CONTEXT_API_URL=
DERIVATIVES_CONTEXT_API_KEY=
MARKET_METADATA_API_URL=
MARKET_METADATA_API_KEY=
CONTEXT_PROVIDER_TIMEOUT_SECONDS=15
CONTEXT_FETCH_CONCURRENCY=8
MARKET_BREADTH_MAX_SYMBOLS=100
ON_DEMAND_SCAN_CONCURRENCY=8
```

For Bybit-backed spot data, use `BYBIT_REST_BASE_URL`, `BYBIT_WS_BASE_URL`,
`BYBIT_API_KEY`, and `BYBIT_API_SECRET`. Do not put Bybit credentials into
`CRYPTO_INDEX_API_KEY` or `TOKEN_CATEGORY_API_KEY`. Bybit can supply exchange-derived
spot data such as candles, tickers, 24h volume/change, breadth, rankings, and spot
order-book context. It does not provide global crypto-index concepts such as total
market cap, BTC dominance, TOTAL2/TOTAL3, or token categories, so those provider
families remain blocked unless a real index/category provider is configured.

These feeds are optional globally but required by strategies that reference their capability
family. Each context endpoint receives the category, requested condition keys, exchange, symbol,
timeframe, quote assets, and evaluation timestamp. It must return a `values` object and an
optional `as_of` timestamp. Unreachable or incomplete providers produce condition-level
`unavailable` proof and never a guessed pass.

Binance and Bybit public spot data supply cross-market candles, breadth, rankings, and order-book
microstructure where the exchange supports them. Derivatives context is disabled for this
spot-only build and should remain blank.

Deploy:

```powershell
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d api worker scheduler
docker compose ps
```

The current migration head includes `email_auth_challenges`, which is required for one-time-code
login and password reset.

Verify `/health`, `/api/v1/status/summary`, worker ping, scheduled `ScanJob` rows, provider webhook
delivery, and one deterministic staging strategy before opening registrations.

Do not claim production readiness until chart storage, API-wide authentication/rate limiting,
monitoring exports, backup drills, and provider sandbox tests are completed.

## Sharia-first deployment gate

Deployed live scanning must use the fail-closed screened-market boundary:

```text
SHARIA_SCREENING_ENFORCED=true
SHARIA_ALLOW_LEGACY_UNSCREENED_LOCAL=false
SHARIA_DEFAULT_METHODOLOGY_CODE=<qualified-approved-active-code>
SHARIA_UNIVERSE_CACHE_TTL_SECONDS=300
SHARIA_COMPLIANCE_SAFETY_UNDER_REVIEW=true
SHARIA_COMPLIANCE_DIGEST_LOCAL_HOUR=8
```

Deploy this migration with scanning stopped. `alembic upgrade head` pauses every previously active
monitor and records it in `sharia_monitor_migration_records`; this is intentional. Do not resume a
monitor until qualified governance has published the named methodology, its assets have dated
evidence-backed assessments, the Watchlist has resolved a screened universe, and a human operator
has reviewed its exclusions and explicitly resumed it.

The `TRACEDGE_DEV_TEST_V1` migration seed is schema/test data only. It is non-executable, hidden
from ordinary methodology selection, and must never be promoted or represented as a religious
ruling. Verify the Compliance Watch review queue, cache invalidation, one provisional safety hold,
one approved status transition, and in-app plus staging Telegram/Discord drift delivery before
opening production scanning.

This release is technically fail-closed; it is not religiously production-ready until a qualified
body, reviewers, approved methodology content, evidence-source operations, review cadence, and
incident SLAs are configured. The complete checklist and known limitations are in
`docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md`.
