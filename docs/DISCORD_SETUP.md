# Discord Setup

1. Create a Discord application and bot.
2. Set the OAuth callback under the HTTPS `PUBLIC_BASE_URL`.
3. Configure client id, client secret, bot token and interaction public key.
4. Set `DISCORD_ENABLED=true` and `DISCORD_ADAPTER=http`.
5. Point Discord interactions to `/api/v1/discord/interactions`.
6. Install the bot with message, embed, thread and role permissions required by the selected mode.
7. Use destination test delivery before enabling alerts.

OAuth profiles are fetched server-side. Interaction requests require Ed25519 signatures. Alert and
role failures are retried with bounded attempts.
