# Deploy TraceEdge Live On One VPS

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

```bash
export TRACEDGE_ENV_FILE=.env.production
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d db redis
docker compose --env-file .env.production -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

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
