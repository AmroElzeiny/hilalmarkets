# Implementation Roadmap

## Completed foundation

- FastAPI application, environment configuration, structured package layout, and Docker assets.
- Core relational model, lifecycle enums, indexes/constraints, and initial Alembic migration.
- Strategy DSL with nested AND/OR groups, multi-timeframe conditions, universe/risk/alert policies.
- Identity linking, attribution, disclaimer, resumable onboarding, interpretation/approval/preview
  and activation services.
- Public landing page and REST onboarding flow.
- Unit and integration test scaffolding for critical activation and resumption paths.
- Deterministic strategy engine with canonical indicators, market filters, rule evaluation,
  Near-Miss scoring, proof receipts, risk calculations, duplicate suppression, forensic
  investigation and forward-test records.
- Telegram application service with `/start`, deep-link attribution, persistent menu, disclaimer,
  create-monitor, approval, activation, Near-Miss browsing, subscription, feedback and support flows.
- Configurable commercial catalog, entitlement service, conditional 14-day trial lifecycle, billing
  provider abstraction, verified/idempotent webhook processing, usage records, referral foundations
  and audited admin commercial overrides.
- Reliability and admin foundations: market-data freshness health, integration health, operational
  metrics, incident history, support escalation context, API-first admin dashboard, audited admin
  actions, security review utilities, worker/scheduler task hooks and operational documentation.

## Completed production-beta repair

- Shared CCXT REST ingestion, timeframe-aware candle closure, health records and freshness gates.
- Persist deterministic `EvaluationResult` into `ScanResult`, `SetupInstance`,
  `SetupConditionResult`, `NearMissSnapshot` and `Alert` rows inside worker jobs.
- Plan-capped universes, per-symbol partial jobs, cooldown persistence and scan idempotency.
- Telegram Bot API and provider-accurate payment adapters.
- V2 scan-job claiming/recovery, shared alert presentation, and conditional 14-day trial cycles.

## Next: delivery and investigation

- Chart snapshots, generalized dead-letter processing and user incident
  notifications.
- Historical reconstruction API for "Why Wasn't I Alerted?" using versioned data and policy logs.

The REST scanning path and provider adapters are implemented. WebSocket ingestion and a durable
historical candle store remain.

## Next: commercial and operations

- Invoice-history UI and promotion-code management UI.
- Forward tests, replay/backtest jobs, outcome analytics and polished support console.
- Production authentication, rate limiting, observability dashboards, retention/backup policy,
  privacy/legal pages.
- Load, chaos, security, and end-to-end tests before production launch.

## Explicit production gaps

- Email magic links, API-wide authentication/rate limiting, polished admin UI and operational
  alerting remain.
- Real exchange preview uses the CCXT adapter, but automated tests use deterministic market-data
  fakes; exchange sandbox and rate-limit integration tests remain.
- The current conservative rules interpreter is a safe fallback. A production LLM adapter still
  needs schema-constrained output, prompt/version audit records and evaluation coverage.
