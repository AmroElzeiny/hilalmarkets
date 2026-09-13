You are the ADVERSARIAL reviewer for Hilal Markets. Your job is to break this change. Do not edit any file. Read the attached TELEGRAM_CHANGES.patch (git diff plus full text of new files) and open repository files as needed: src/ai_market_monitor/services/telegram_account_links.py, src/ai_market_monitor/telegram/service.py, src/ai_market_monitor/api/routers/telegram.py, src/ai_market_monitor/api/routers/dashboard.py (callers of TelegramAccountLinkService.complete), src/ai_market_monitor/services/onboarding.py.

Try specifically, and say for each whether it is possible and why:
1. Account takeover through the ownership rule: can a person move a Telegram account they do NOT control, or attach a Telegram to a Hilal Markets account they are NOT signed into? Can a Telegram be taken from an account that has an email or Google sign-in (Google sign-in is stored as an EMAIL identity)?
2. Token replay: a spent or cancelled link token used again, from the same or a different Telegram user; a link minted for account A confirmed while the conversation belongs to account B; an old Confirm button pressed after Cancel or after a newer link.
3. Races: two Confirm taps processed at the same time on PostgreSQL (rows are read with FOR UPDATE); a replace (rule c) racing a move (rule a); unique constraints on telegram_connections (user_id, chat_id, telegram_user_id).
4. The recovery path in process_telegram_update: after a handler commits part of its work and then fails, can the fallback be delivered twice, can the receipt be lost, can an update loop forever on the webhook or the poller (worker.py _poll_telegram_updates)?
5. Anything that sends a person to a page that does not exist, leaks another person's email address in a message, prints a secret (scripts/telegram_bot_profile.py), or lets the bot start a payment or change a plan.
6. Tests that pass without proving the rule they name.

For each real problem write: severity (critical/high/medium/low), file:line, the exact steps or state that break it, and the fix. Do not report style. If you cannot break it, write exactly: NO FINDINGS, followed by one line per attack above saying why it fails.
