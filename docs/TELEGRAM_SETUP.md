# Telegram Setup

1. Create the bot with BotFather.
2. Set `TELEGRAM_ENABLED=true`, `TELEGRAM_ADAPTER=http`, username, token and a random webhook secret.
3. Register `https://YOUR_HOST/api/v1/telegram/webhook` with Telegram and pass the same secret as
   `secret_token`.
4. Confirm `/start` creates one `telegram_update_receipts` row and a replay does not resend.
5. Run the worker and beat processes for pending alert delivery retries.

The adapter sends messages, edits where possible, answers callbacks, sends chart URLs, handles
Telegram rate limits, and records retryable/permanent delivery status.
