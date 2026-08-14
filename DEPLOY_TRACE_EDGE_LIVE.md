# Deploy TraceEdge Live On One VPS

> **ARCHIVAL.** "TraceEdge" is an earlier name for this product; the product is called
> **Hilal Markets**. Superseded by `docs/PRODUCTION_DEPLOYMENT.md` and `docs/LAUNCH_CHECKLIST.md`.
> Do not follow this document for a deployment: the hostnames, image names and environment
> variables in it are not the ones the product ships. Kept for history; nothing below is edited.

This guide deploys the existing TraceEdge FastAPI/Celery/PostgreSQL/Redis stack to one VPS with
Docker Compose, Caddy, and Cloudflare DNS/proxy.

Target URLs:

- Public landing page: `https://hilalmarkets.com`
- Dashboard/app: `https://app.hilalmarkets.com`
- `https://www.hilalmarkets.com` redirects to `https://hilalmarkets.com`

This is not a Cloudflare Workers deployment and does not change the product into an automated
trading bot.

## 1. VPS Requirements

Install:

- Ubuntu 22.04+ or Debian 12+
- Docker Engine and Docker Compose plugin
- Git
- `curl`

Open only:

- TCP `80`
- TCP `443`
- SSH, preferably restricted to your IP

Do not expose Postgres, Redis, or the FastAPI container directly.

## 2. Cloudflare DNS

In Cloudflare DNS for `hilalmarkets.com`, add:

| Type | Name | Target | Proxy |
| --- | --- | --- | --- |
| A | `@` | VPS public IPv4 | Proxied |
| A | `www` | VPS public IPv4 | Proxied |
| A | `app` | VPS public IPv4 | Proxied |

Recommended Cloudflare settings:

- SSL/TLS mode: `Full (strict)`
- Always Use HTTPS: enabled
- Automatic HTTPS Rewrites: enabled
- Minimum TLS: TLS 1.2 or higher
- WebSockets: enabled
- Cache level: standard
- Do not create a Worker route for these domains.

Caddy still obtains and renews origin certificates. If certificate issuance is blocked by the
orange-cloud proxy, temporarily set the DNS records to DNS-only, wait for Caddy to issue the
certificates, then re-enable the proxy.

## 3. Put Code On The VPS

```bash
cd /opt
sudo mkdir -p traceedge
sudo chown "$USER:$USER" traceedge
git clone https://github.com/AmroElzeiny/Trace_Edge.git traceedge
cd /opt/traceedge
```

If the repository is already present:

```bash
cd /opt/traceedge
git pull --ff-only
```

## 4. Configure Environment

```bash
cp .env.production.example .env.production
nano .env.production
```

Fill every `REPLACE_*` value.
Production startup rejects placeholder values in critical credentials, so the app will not boot
with the template secrets unchanged.

Minimum required for the API to start in production:

- `APP_ENV=production`
- `APP_SECRET_KEY` with at least 32 random characters
- `PUBLIC_BASE_URL=https://hilalmarkets.com`
- `APP_BASE_URL=https://app.hilalmarkets.com`
- `DATABASE_URL=postgresql+asyncpg://market_monitor:<same password>@db:5432/market_monitor`
- `POSTGRES_PASSWORD=<same password>`
- `REDIS_URL=redis://redis:6379/0`
- `ALLOW_MOCK_PROVIDERS=false`
- `TRACEDGE_MARKET_DATA_MODE=ccxt`
- `TRACEDGE_FIXTURE_MARKET_DATA_ENABLED=false`
- `OPENAI_API_KEY` when `AI_INTERPRETER_PROVIDER=openai`

Enable integrations only after their credentials are present:

- Telegram: set `TELEGRAM_ENABLED=true`, `TELEGRAM_ADAPTER=http`,
  `TELEGRAM_BOT_USERNAME`, and `TELEGRAM_BOT_TOKEN`.
- Discord: set `DISCORD_ENABLED=true`, `DISCORD_ADAPTER=http`, client credentials,
  bot token, and webhook public key.
- NOWPayments: set `BILLING_ENABLED=true`, `BILLING_PROVIDER=nowpayments`,
  `NOWPAYMENTS_API_KEY`, and `BILLING_WEBHOOK_SECRET`.
- SMTP: set `EMAIL_ADAPTER=smtp` and SMTP credentials before relying on email login,
  reset, or support tickets.

The real `.env.production` file is ignored by git.

## 5. Start Production Stack

Keep one stable `COMPOSE_PROJECT_NAME` in `.env.production` and use that same
project name for every command. On an existing installation, select the project
that already owns the authoritative PostgreSQL volume; do not rename it during
deployment. The project name is part of each named volume's identity. For
example, `traceedge_postgres_data` and `hilalmarkets_postgres_data` are two
unrelated PostgreSQL databases even when both commands are run from
`/opt/hilalmarkets`.

```bash
export TRACEDGE_ENV_FILE=.env.production
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d db redis
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Do not alternate between unqualified `docker compose` commands and
`docker compose -p <another-name>` commands. Determine the existing
authoritative project before setting `COMPOSE_PROJECT_NAME`. The repository does
not force a project name because doing so could attach an existing installation
to the wrong volume.

Before deleting any apparently old volume, inspect all Compose projects and
volumes:

```bash
docker compose ls
docker volume ls \
  --filter label=com.docker.compose.volume=postgres_data \
  --format 'postgres_volume={{.Name}}'
```

Back up the authoritative database before changing project or volume names.
Removing database files from `.gitignore` does not export a PostgreSQL Docker
volume, and application SQLite files are not used by this Compose stack.

After deployment, confirm the migration and governance record counts against
the selected project:

```bash
docker compose exec -T api alembic current
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT code, version, status FROM sharia_methodologies ORDER BY code, version;
SELECT source_family, mapping_state, COUNT(*) FROM external_assessments
GROUP BY source_family, mapping_state ORDER BY source_family, mapping_state;
SELECT state, publication_state, COUNT(*) FROM sharia_review_cases
GROUP BY state, publication_state ORDER BY state, publication_state;
SELECT COUNT(*) AS published_passports FROM published_asset_assessments;
"'
```

To fetch the current SC Malaysia and Fasset source records immediately instead
of waiting for the configured ten-day schedule:

```bash
docker compose exec -T worker celery -A ai_market_monitor.worker.app call \
  ai_market_monitor.process_sharia_authority_imports
docker compose logs --since=10m worker
```

The import is deliberately not an approval action. It retains official-source
evidence, maps identities where authoritative mappings exist, and creates review
cases. An authorized human must complete the required criteria and use-scope
decisions and publish each approved assessment in System Brain before it becomes
a customer-visible Passport. An empty screener with imported but unpublished
cases is therefore fail-closed behavior, not a cache problem.

The production stack contains:

- `caddy`: public reverse proxy and HTTPS
- `api`: FastAPI app at `ai_market_monitor.main:app`
- `worker`: Celery worker
- `scheduler`: Celery beat
- `db`: PostgreSQL
- `redis`: Redis broker/cache

Only Caddy binds host ports `80` and `443`.

## 6. Smoke Test

From the VPS:

```bash
curl -fsS http://127.0.0.1/health
curl -fsS https://hilalmarkets.com/health
curl -fsS https://app.hilalmarkets.com/health
```

From a local Python environment with project dependencies installed:

```bash
.venv/Scripts/python.exe scripts/deployment_smoke.py --base-url https://hilalmarkets.com
.venv/Scripts/python.exe scripts/deployment_smoke.py --base-url https://app.hilalmarkets.com
```

On Linux:

```bash
python scripts/deployment_smoke.py --base-url https://hilalmarkets.com
python scripts/deployment_smoke.py --base-url https://app.hilalmarkets.com
```

Expected:

- `/health` returns `status=ok`
- `/health/deep` returns database and Redis status
- Landing page renders `Hilal Markets`
- `/dashboard` redirects to sign-in or returns the dashboard for an authenticated user
- `/static/styles.css` and `/static/app.js` are reachable

## 7. Telegram Notes

The current production template uses polling by default:

```text
TELEGRAM_POLLING_ENABLED=true
TELEGRAM_POLLING_CLEAR_WEBHOOK=true
```

That lets the worker receive bot updates without exposing a Telegram webhook. If webhook mode is
used instead, configure:

```text
TELEGRAM_POLLING_ENABLED=false
TELEGRAM_WEBHOOK_SECRET=<random secret>
```

Then set the webhook with Telegram to:

```text
https://app.hilalmarkets.com/api/v1/telegram/webhook
```

and send header:

```text
X-Telegram-Bot-Api-Secret-Token: <TELEGRAM_WEBHOOK_SECRET>
```

## 8. Operations

View logs:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f api
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f worker
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f scheduler
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f caddy
```

Restart one service:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml restart api
```

Run migrations:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm api alembic upgrade head
```

Deploy from the repository root:

```bash
bash deploy/deploy.sh
```

Backups to configure before inviting real users:

- PostgreSQL volume backup
- Caddy data volume backup
- Export volume backup if exports are business-critical

## 9. Security Checklist

- Real `.env.production` is not committed.
- `APP_SECRET_KEY` is unique and long.
- Cloudflare proxy is enabled for all three DNS records.
- VPS firewall exposes only SSH, 80, and 443.
- Postgres and Redis are not publicly reachable.
- Fixture/mock providers are disabled in production.
- Exchange API keys, if ever used, have no trading or withdrawal permissions.
- Billing webhook secrets are configured before enabling billing.
- Telegram/Discord secrets never appear in logs.

## 10. Current Architecture Note

The same FastAPI app serves both `hilalmarkets.com` and `app.hilalmarkets.com`. Caddy routes both
hostnames to the API container. The public landing page is at `/`; the dashboard is at
`/dashboard` and sign-in/sign-up are under `/signin` and `/signup`.
