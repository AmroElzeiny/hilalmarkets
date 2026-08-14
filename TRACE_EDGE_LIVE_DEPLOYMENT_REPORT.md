# TraceEdge Live Deployment Preparation Report

> **ARCHIVAL — 6 July 2026.** "TraceEdge" is an earlier name for this product; the product is
> called **Hilal Markets**. Superseded by `docs/PRODUCTION_DEPLOYMENT.md` and
> `docs/LAUNCH_CHECKLIST.md`. Do not follow this document for a deployment. Kept for history;
> nothing below is edited.

Date: 2026-07-06

## Scope

Prepared the repository for a single-VPS live deployment using Docker Compose, Caddy, PostgreSQL,
Redis, Celery worker, Celery beat scheduler, and Cloudflare DNS/proxy.

Domains:

- `trace-edge.com`
- `www.trace-edge.com`
- `app.trace-edge.com`

## Files Added

- `docker-compose.prod.yml`
- `deploy/Caddyfile`
- `.env.production.example`
- `deploy/deploy.sh`
- `scripts/deployment_smoke.py`
- `DEPLOY_TRACE_EDGE_LIVE.md`
- `TRACE_EDGE_LIVE_DEPLOYMENT_REPORT.md`
- `tests/integration/test_public_health.py`

## Files Updated

- `.gitignore`
- `src/ai_market_monitor/api/routers/public.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`
- `IMPLEMENTATION_SUMMARY.md`

## Deployment Architecture

The production Compose stack defines:

- Caddy as the only public service on ports `80` and `443`.
- FastAPI API container, internal-only on port `8000`.
- Celery worker for scans, deliveries, exports, trial cycles, and background jobs.
- Celery beat scheduler.
- PostgreSQL 16.
- Redis 7.
- Persistent volumes for Postgres, Redis, Caddy certificates/config, and dashboard exports.
- App secrets are scoped to API, worker, and scheduler containers. Postgres receives only
  `POSTGRES_*` values, and Caddy does not receive application secrets.

## Health Checks

Implemented:

- `/health`: shallow service health with service and environment metadata.
- `/health/deep`: database and Redis reachability checks.

The deep health endpoint returns status only and does not expose exception details.

## Environment

Added `.env.production.example` with placeholders for:

- App URLs and production secrets.
- PostgreSQL and Redis.
- Binance/Bybit market data.
- OpenAI interpreter.
- Telegram.
- Discord.
- NOWPayments.
- SMTP.
- Optional provider-backed concepts.
- Export/chart settings.

`.gitignore` now allows tracking `.env.production.example` while continuing to ignore real
`.env.*` files.

## Cloudflare/Caddy

Added `deploy/Caddyfile` for:

- `trace-edge.com` reverse proxy to API.
- `app.trace-edge.com` reverse proxy to API.
- `www.trace-edge.com` permanent redirect to `trace-edge.com`.

Cloudflare should use proxied A records and Full (strict) SSL/TLS.

## Validation Added

Production/staging runtime config now rejects non-HTTPS `APP_BASE_URL` when it is set.
It also rejects placeholder values such as `REPLACE_*` in critical production credentials.
Existing checks still reject default secrets, SQLite, mock providers, fixture data, unsafe
integration configuration, and missing provider credentials.

## Tests Added

Added integration tests for public health endpoints:

- `test_public_health_returns_service_metadata`
- `test_public_deep_health_checks_database_and_redis`
- `test_production_runtime_rejects_placeholder_credentials`

## Validation Run

Commands:

- `.venv\Scripts\python.exe -m pytest tests\integration\test_public_health.py tests\unit\test_reliability_security.py -q`
- `$env:TRACEDGE_ENV_FILE='.env.production.example'; docker compose --env-file .env.production.example -f docker-compose.prod.yml config`
- Production runtime validation with `.env.production.example` plus safe dummy overrides replacing
  `APP_SECRET_KEY`, `DATABASE_URL`, and `OPENAI_API_KEY`
- `.venv\Scripts\python.exe scripts\deployment_smoke.py --help`

Results:

- Health/runtime security tests passed: `10 passed`.
- Production Compose rendered successfully.
- Production config passed runtime validation once placeholders were replaced.
- Deployment smoke CLI loaded successfully.

Not run:

- Live smoke against `https://trace-edge.com` or `https://app.trace-edge.com`, because DNS/VPS
  deployment credentials and live server state are outside this local workspace.

## Manual VPS Steps Remaining

Before live launch:

1. Create the VPS and point Cloudflare DNS records to it.
2. Copy `.env.production.example` to `.env.production`.
3. Fill real secrets and provider credentials.
4. Run `docker compose --env-file .env.production -f docker-compose.prod.yml build`.
5. Run migrations.
6. Start the production stack.
7. Run smoke checks against both public domains.
8. Verify Telegram, Discord, billing, and email in real test mode.
9. Configure backups and log retention.

## Known Notes

- The same FastAPI app serves both public and app hostnames.
- Telegram polling remains the simplest live test path. Webhook mode is documented but requires
  setting the Telegram webhook manually.
- TradingView Charting Library licensed assets are not bundled by this deployment work.
