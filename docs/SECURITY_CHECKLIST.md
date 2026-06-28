# Security Checklist

- Reject default secrets, SQLite, HTTP origins and mock providers in deployed environments.
- Keep Telegram, Discord, Stripe and application secrets in a managed secret store.
- Require Telegram secret, Discord Ed25519 and Stripe timestamped webhook verification.
- Apply API-wide authentication, ownership authorization, rate limits and request-size limits.
- Restrict CORS and secure cookies to the production origin.
- Run Ruff, tests, dependency audit, container scan and secret scan in CI.
- Back up PostgreSQL and test restoration.
- Never request exchange private keys, withdrawal access, wallet keys or seed phrases.
- Review audit events, failed deliveries, billing failures and incidents daily during beta.

The repository implements provider signature checks and admin RBAC. API-wide customer
authentication/rate limiting and CI vulnerability scanning remain required before public launch.
