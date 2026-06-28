# Test Systems Setup

Use dedicated test credentials only. Never put real tokens in Git.

## Telegram Test Bot

1. Create a dedicated bot in BotFather.
2. Store the token locally or in staging secrets as `TELEGRAM_BOT_TOKEN`.
3. Set `TELEGRAM_BOT_USERNAME`, `TELEGRAM_ENABLED=true`, and `TELEGRAM_ADAPTER=http`.
4. For local live testing, use polling:
   `TELEGRAM_POLLING_ENABLED=true`.
5. For staging webhooks, use HTTPS `PUBLIC_BASE_URL` and set `TELEGRAM_WEBHOOK_SECRET`.
6. Send `/start` from a test Telegram user.
7. Send one test alert and verify `AlertDelivery` status.
8. Confirm logs do not print the bot token.

## Discord Test Server

1. Create a private Discord test server.
2. Create a Discord application and bot.
3. Store `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, and `DISCORD_WEBHOOK_PUBLIC_KEY`.
4. Set `DISCORD_ENABLED=true` and `DISCORD_ADAPTER=http`.
5. Install the bot with slash command, message, embed, thread, and role permissions required by the selected mode.
6. Connect a test user/server and send one deterministic proof alert.
7. Verify delivery retry and failure visibility.

## Payments

1. Use provider sandbox/test mode only.
2. Current launch path is NOWPayments:
   `BILLING_PROVIDER=nowpayments`.
3. Set `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_BASE_URL`, and `BILLING_WEBHOOK_SECRET`.
4. Test checkout, webhook replay, downgrade/cancel, failed payment, and refund/dispute events if the sandbox supports them.
5. Treat verified provider webhooks as source of truth.

## Email

1. Use a sandbox SMTP provider.
2. Set `EMAIL_ADAPTER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM_EMAIL`.
3. Test login one-time code, password reset, and support notifications.
4. Do not email real users from local/dev.

## Market Data

1. Local/CI uses fixture mode:
   `TRACEDGE_MARKET_DATA_MODE=fixture`,
   `TRACEDGE_FIXTURE_MARKET_DATA_ENABLED=true`.
2. Staging/production must use `TRACEDGE_MARKET_DATA_MODE=ccxt`.
3. Production refuses fixture/mock providers.
4. Provider-required concepts remain hidden unless their adapter and proof tests exist.

## Smoke Commands

```powershell
.venv\Scripts\python.exe scripts\smoke_worker.py
.venv\Scripts\python.exe -m pytest tests\unit\test_fixture_market_data.py -q
```

Expected result: smoke script prints `PASS` and writes `WORKER_SMOKE_REPORT.md`.
