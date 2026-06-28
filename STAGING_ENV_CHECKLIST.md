# Staging Environment Checklist

## Required Core Env

- `APP_ENV=staging`
- `APP_SECRET_KEY` set to a non-default 32+ character secret
- `DATABASE_URL` uses PostgreSQL
- `REDIS_URL` points to staging Redis
- `PUBLIC_BASE_URL` uses HTTPS
- `ALLOW_MOCK_PROVIDERS=false`
- `SCANNING_ENABLED=true`
- `TRACEDGE_MARKET_DATA_MODE=ccxt`
- `TRACEDGE_FIXTURE_MARKET_DATA_ENABLED=false`
- `MARKET_DATA_PROVIDER=ccxt`
- `MARKET_DATA_EXCHANGE=binance`

## Provider Flags

- `BINANCE_MARKET_DATA_ENABLED=true`
- `BINANCE_ORDER_BOOK_ENABLED=true` only if order-book proof tests are enabled
- `BINANCE_DERIVATIVES_ENABLED=false` unless a derivatives adapter is configured
- `COINGECKO_ENABLED=false` unless a tested adapter is configured
- `ALTERNATIVE_ME_ENABLED=false` unless a tested adapter is configured
- `FRED_ENABLED=false` unless a tested adapter and key are configured

## Workers

- API starts.
- Worker starts:
  `celery -A ai_market_monitor.worker.app worker --loglevel=INFO`
- Scheduler starts:
  `celery -A ai_market_monitor.worker.app beat --loglevel=INFO`
- Scan jobs can be claimed, processed, retried, and marked failed without duplication.

## Integrations

- Telegram test bot configured or `TELEGRAM_ENABLED=false`.
- Discord test application configured or `DISCORD_ENABLED=false`.
- Billing sandbox configured or `BILLING_ENABLED=false`.
- SMTP sandbox configured before enabling email login/reset.

## Safety Checks

- No fixture/mock providers in staging.
- No default secrets.
- No SQLite database.
- No HTTP public base URL.
- No provider-required concepts visible in normal builder unless adapter and tests exist.
- Proof receipts show data source, timestamps, latency, and condition-level status.
