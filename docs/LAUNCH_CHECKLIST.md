# Launch Checklist

Date: 2026-06-15

## Product

- [x] Landing page explains monitoring, Near-Miss Radar and proof receipts.
- [x] No guaranteed-profit claims in the landing page.
- [x] Web onboarding supports interpretation, approval, preview and activation.
- [x] Telegram application service supports onboarding and monitor creation.
- [x] Discord connection and delivery foundations exist.
- [x] Trial and plan definitions are centrally configurable.
- [x] Strategy approval is mandatory before activation.
- [x] Near-Miss scoring and proof receipt builders are deterministic.
- [x] Forensic investigation service produces deterministic results.
- [x] Setup lifecycle transition validation exists.
- [x] Production Telegram Bot API webhook and delivery adapter are connected.
- [x] Production Discord HTTP delivery, OAuth and signed interaction adapters are connected.
- [x] Production REST scanner persists `ScanResult`, evidence and `SetupInstance` rows.
- [ ] Chart generation service is connected.

## Reliability

- [x] Market-data health records provider, exchange, symbol, timeframe and freshness evidence.
- [x] Stale or incomplete data blocks confirmed alerts unless explicitly allowed.
- [x] Delivery states include retryable and permanent failures.
- [x] Discord delivery retries are scheduled.
- [x] Incident records, impacts and updates are durable.
- [x] User-facing and admin status APIs exist.
- [x] Worker and scheduler entry points exist.
- [ ] WebSocket ingestion and REST fallback are production implemented.
- [ ] Queue-depth and API-latency metrics are exported to the monitoring backend.
- [ ] User incident notifications are wired to Telegram/Discord adapters.

## Business

- [x] Conditional 14-day trial-cycle lifecycle exists.
- [x] Entitlement checks run at activation level.
- [x] Billing webhooks are idempotent.
- [x] Downgrades pause excess strategies without deletion.
- [x] Referral attribution foundations exist.
- [x] Admin commercial overrides are audited.
- [x] Stripe checkout/customer portal provider and signature verification are integrated.
- [ ] Invoice history UI exists.

## Safety

- [x] Disclaimer acknowledgement is stored.
- [x] Strategy DSL is restricted; user Python is never evaluated.
- [x] Security review utility blocks SSRF-style URLs and unsafe uploads.
- [x] Discord moderation catches secret requests and guaranteed-profit claims.
- [x] No exchange trading API keys are requested.
- [ ] API-wide authentication and rate limiting are production complete.
- [x] Telegram secret, Discord Ed25519 and Stripe timestamped signature checks exist.
- [ ] CI dependency and container vulnerability scans are configured.

## Operations

- [x] Docker Compose includes API, worker, scheduler, PostgreSQL and Redis.
- [x] Alembic migrations are current.
- [x] Operational docs exist.
- [x] Admin dashboard APIs exist for health, users, support, incidents and audit events.
- [ ] Production dashboards and alerts are configured in the monitoring platform.
- [ ] Backup and restore drills are complete.
- [ ] Staging end-to-end provider sandbox tests are complete.
