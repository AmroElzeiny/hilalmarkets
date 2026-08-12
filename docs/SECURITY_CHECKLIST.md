# Security Checklist

- Reject default secrets, SQLite, HTTP origins and mock providers in deployed environments.
- Keep Telegram, payment-provider, SMTP and application secrets in a managed secret store.
- Require Telegram secrets and payment-provider timestamped webhook verification when enabled.
- Apply API-wide authentication, ownership authorization, rate limits and request-size limits.
- Restrict CORS and secure cookies to the production origin.
- Run Ruff, tests, dependency audit, container scan and secret scan in CI.
- Back up PostgreSQL and test restoration.
- Never request exchange private keys, withdrawal access, wallet keys or seed phrases.
- Review audit events, failed deliveries, billing failures and incidents daily during beta.
- Keep secrets, prompts, model output and customer plan text out of metrics, logs, operational
  issues and alerts. This is enforced in code by
  `observability/labels.assert_no_sensitive_content`, which refuses API keys, bearer and basic
  credentials, Telegram bot tokens, JSON web tokens, private-key blocks, AWS access keys, email
  addresses, seed-phrase-shaped text, and any value long enough to be prose. Metric labels are
  additionally restricted to a closed vocabulary with a per-label ceiling on distinct values, so a
  label can never carry a user id, a raw error string or free text.

The repository implements provider signature checks and admin RBAC. API-wide customer
authentication/rate limiting and CI vulnerability scanning remain required before public launch.
