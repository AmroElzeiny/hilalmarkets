# Sharia-First Product Layer Implementation Report

Date: 14 July 2026

## Readiness Statement

The software foundation described here is implemented and technically verified. It is **not a
claim that TraceEdge or any asset is Sharia-compliant**, and it is not production-ready as a
religious screening service until a qualified governing body approves a real methodology,
qualified reviewers are appointed, evidence sources and review SLAs are operational, and the
production database contains reviewed, dated evidence-backed assessments.

The migration seeds only `TRACEDGE_DEV_TEST_V1`, a draft and non-executable development record.
It contains no asset conclusions and is deliberately hidden from normal user execution.

## 1. Architecture Summary

The implementation adds one screening boundary rather than a parallel product:

1. `ShariaScreeningService` owns methodology, effective-assessment, evidence, history, passport,
   comparison, and fail-closed status logic.
2. `ShariaUniverseResolver` intersects the approved status set with exchange symbols, quote
   assets, explicit symbols, approved watchlists, and plan limits. Scanner, one-time Scanner mode,
   strategy validation, and workers call this same resolver.
3. `ComplianceWatchService` ingests deduplicated structured changes, creates provisional safety
   holds when configured, and requires an authenticated human review before replacing an approved
   assessment.
4. `ActivityReadService` builds a tenant-scoped read model over opportunity journeys, alerts,
   compliance drift, and investigations without destructively merging source tables.
5. Alert proof stores the methodology/version, assessment, status, universe snapshot, policy
   decision, and review evidence used at evaluation time. Presentation reads the frozen proof, not
   the asset's later status.

AI can explain stored data and clarify a user's desired screened market. AI cannot create a
methodology conclusion, set an asset status, approve a review, bypass the resolver, or turn missing
evidence into eligibility.

## 2. Files Added and Changed

### New source and migration files

- `alembic/versions/c5d6e7f8a9b0_add_sharia_first_product_layer.py`
- `src/ai_market_monitor/db/models/sharia.py`
- `src/ai_market_monitor/schemas/sharia.py`
- `src/ai_market_monitor/services/sharia_screening.py`
- `src/ai_market_monitor/services/sharia_universe.py`
- `src/ai_market_monitor/services/compliance_watch.py`
- `src/ai_market_monitor/services/activity.py`
- `src/ai_market_monitor/services/product_language.py`
- `src/ai_market_monitor/api/routers/sharia.py`
- `src/ai_market_monitor/api/routers/activity.py`
- `src/ai_market_monitor/static/sharia-product.css`
- `src/ai_market_monitor/static/system-brain-compliance.css`

### Existing integration files changed

- `.env.example`, `.env.production.example`
- `src/ai_market_monitor/core/config.py`, `core/startup.py`, `main.py`, `worker.py`
- `src/ai_market_monitor/db/models/__init__.py`, `enums.py`, `strategy.py`, `monitoring.py`
- `src/ai_market_monitor/schemas/strategy.py`, `schemas/on_demand.py`
- `src/ai_market_monitor/services/strategy.py`, `scanner.py`, `on_demand_scans.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`, `lifecycle_dashboard.py`
- `src/ai_market_monitor/services/notifications.py`, `notification_preferences.py`,
  `alert_presentation.py`
- `src/ai_market_monitor/telegram/service.py`, `discord/service.py`
- `src/ai_market_monitor/api/routers/__init__.py`, `dashboard.py`, `dashboard_api.py`,
  `on_demand.py`, `system_brain.py`
- `src/ai_market_monitor/templates/index.html`, `dashboard.html`, `system_brain.html`
- `src/ai_market_monitor/static/dashboard.js`, `system-brain.js`
- `tests/services/test_sharia_screening.py`
- `tests/integration/test_sharia_api.py`, `test_sharia_migration.py`
- `tests/browser/conftest.py`, `test_dashboard_e2e.py`
- terminology/safety compatibility assertions in landing, lifecycle, and runtime tests

## 3. Database Migration

Migration head: `c5d6e7f8a9b0`.

New tables:

- `sharia_methodologies`
- `asset_sharia_assessments`
- `sharia_evidence_sources`
- `asset_sharia_status_history`
- `approved_watchlists` and `approved_watchlist_assets`
- `sharia_universe_snapshots`
- `monitor_sharia_asset_states`
- `compliance_changes` and `compliance_reviews`
- `compliance_drift_notifications`
- `sharia_monitor_migration_records`

`strategy_universes`, `scan_results`, and `setup_instances` receive immutable screening references
and policy evidence. The migration pauses every previously active monitor, marks its universe as not
policy-ready, and records the prior status and reason. An operator must assign a real approved
methodology, resolve and preview the universe, and explicitly resume each monitor.

## 4. Models and Enums

Methodology versions are append-only by unique `(code, version)`. Assessments are effective-dated,
supersede rather than overwrite prior records, and retain evidence/source hashes. Status history is
separate from the current-effective query.

Implemented enums cover methodology status, asset screening status, universe mode, compliance
change behavior/severity/status, review decision, policy decision, and per-monitor asset state.
Default included statuses are `eligible` and `eligible_with_qualifications`; missing assessments,
`under_review`, `disputed`, `excluded`, and `insufficient_information` fail closed.

## 5. Routes and API Endpoints

User pages:

- `/dashboard/market`
- `/dashboard/market/{asset}`
- `/dashboard/methodology`
- `/dashboard/activity` (`/dashboard/lifecycles` remains a compatible route)

Authenticated API:

- `GET /api/v1/sharia/assets`
- `GET /api/v1/sharia/assets/{asset}`
- `GET /api/v1/sharia/assets/{asset}/passport`
- `GET /api/v1/sharia/assets/{asset}/history`
- `GET /api/v1/sharia/assets/{asset}/methodology-comparison`
- `GET /api/v1/sharia/methodologies[/{id}]`
- `GET|POST /api/v1/sharia/watchlists`
- `DELETE /api/v1/sharia/watchlists/{id}/assets/{asset}`
- `PUT /api/v1/sharia/preferences`
- `GET /api/v1/activity`

Reviewer/admin API:

- `POST /api/v1/sharia/admin/methodologies`
- `POST /api/v1/sharia/admin/assessments` for an initial assessment only
- `POST /api/v1/sharia/admin/compliance-changes`
- `POST /api/v1/sharia/admin/compliance-changes/{id}/review`

Ordinary users cannot read draft methodology records or mutate assessments. Existing assessments
can only be superseded through Compliance Watch so cache invalidation, history, monitor impact, and
drift notification are one audited transaction.

## 6. Monitor and Scanner Integration

Watch Plans support `eligible_market`, `approved_watchlist`, and `explicit_assets`. Empty explicit
asset selection fails instead of falling back to an exchange-wide universe. Approved watchlists are
owner-scoped and intersected with the selected screening policy.

Every resolution returns included assets, excluded assets and reasons, missing-evidence count,
policy hash, immutable snapshot hash/version, methodology, and resolution time. The same service is
called by persistent monitor workers and one-time Scanner mode. Excluded assets are policy
exclusions, not ordinary technical failures.

Before evaluation, the worker re-resolves policy. `pause_asset`, `remove_asset`,
`pause_monitor_if_any_asset_changes`, and `notify_only` are persisted. `notify_only` never means
"continue scanning an ineligible asset"; the asset remains fail-closed while notification behavior
controls user impact.

## 7. Compliance Watch Workflow

Structured source changes are normalized and deduplicated with an idempotency key. Review-required
and critical changes enter the System Brain queue. A configured safety policy can operationally
overlay `under_review` immediately, pause affected assets, invalidate universe caches, and notify
users, while the last approved religious assessment remains unchanged.

A reviewer can approve, request more evidence, or dismiss. Approval creates a superseding
assessment and history record. Dismissal releases a provisional safety hold back to the current
approved assessment. Reviewer notes are audited and not exposed in public passport summaries.

## 8. Notification Behavior

Compliance drift creates an immutable `Alert`, an in-app dashboard notification, a deduplicated
`ComplianceDriftNotification`, and configured Telegram/Discord deliveries. Under-review,
disputed, excluded, and critical events are immediate. Non-critical events can be grouped into a
daily external summary at `SHARIA_COMPLIANCE_DIGEST_LOCAL_HOUR` in the user's timezone. In-app
evidence remains immediate.

Compliance channels are independent of ordinary market-alert channels. Delivery rows retain normal
idempotency and retry behavior. Telegram and Discord alert presentation includes status at
evaluation, methodology/version, review date, and an Evidence Passport action.

## 9. Admin and System Brain

`/system-brain#compliance-watch` displays queue counts, pending source changes, current status,
source links, methodology selection, affected Watch Plans/users, and reviewer actions. CSRF,
existing OTP/password security, admin authorization, required reviewer notes, audit events, and
server-side validation remain in force. There is no AI status-approval endpoint.

## 10. UX and Terminology

Primary navigation connects Screened Market, Watch Plans, Activity, Methodology, Integrations,
Billing, Settings, and Support. Portfolio remains absent rather than presented as a working module.
Screened Market has persisted filters, methodology disclosure, status summary, opportunity/all
views, evidence-backed cards, and passport details.

Presentation mapping uses Watch Plan, Opportunity, Forming, Getting closer, Ready for review,
Alert sent, Ended, What is still missing, Market check, Readiness, and "Why didn't this alert
happen?" while preserving internal enums for backward compatibility. `Peak readiness 100%` is
shown separately from terminal `Ended` status.

Visual QA:

- `reports/visual-qa/sharia-first/screened-market-desktop.png`
- `reports/visual-qa/sharia-first/screened-market-mobile-390.png`
- `reports/visual-qa/sharia-first/sharia-evidence-passport-desktop.png`

## 11. Tests Executed and Results

- Focused Sharia service/API/migration: **18 passed**.
- Full browser suite: **15 passed in 72.96s**, 0 failed/skipped.
- Full repository suite: **1,847 passed in 487.21s**.
- Scoped Ruff over Sharia source/tests: passed.
- Scoped mypy over 9 new Sharia source modules: passed.
- `node --check` for dashboard and System Brain scripts: passed.
- Jinja loading for dashboard, landing page, and System Brain: passed.
- `alembic heads`: one head, `c5d6e7f8a9b0`.

A repository-wide Ruff invocation still reports 19 pre-existing `E501` lines in unrelated
`concept_e2e.py`, `email_delivery.py`, and `setup_observability.py`; none is in this feature's
scoped checks and no broad unrelated formatting change was made.

## 12. Existing Functionality Regression Results

The full suite covers authentication, billing, chatbot/compiler, visual canvas, Scanner,
persistent scanning, lifecycle/observability, proof immutability, Telegram, Discord, System Brain,
and provider gates. The browser suite also exercises strategy approval/activation, screened-market
passport, Activity filters, integrations, alert proof, desktop, mobile, and reduced motion.

The Windows E2E fixture was repaired to terminate the Uvicorn child process tree, preventing locked
browser databases on later test runs.

## 13. Environment and Deployment Steps

Required production posture:

```text
SHARIA_SCREENING_ENFORCED=true
SHARIA_ALLOW_LEGACY_UNSCREENED_LOCAL=false
SHARIA_DEFAULT_METHODOLOGY_CODE=<qualified-approved-active-code>
SHARIA_UNIVERSE_CACHE_TTL_SECONDS=300
SHARIA_COMPLIANCE_SAFETY_UNDER_REVIEW=true
SHARIA_COMPLIANCE_DIGEST_LOCAL_HOUR=8
```

Deployment sequence:

1. Back up PostgreSQL and test restore.
2. Deploy with scanning stopped.
3. Run `alembic upgrade head`.
4. Inspect `sharia_monitor_migration_records`; confirm all formerly active monitors are paused.
5. Have qualified governance publish the real active methodology and evidence requirements.
6. Import/review evidence-backed asset assessments through the admin workflow.
7. Set the approved methodology code and fail-closed flags above.
8. Resolve and preview each Watch Plan's screened market, then explicitly resume it.
9. Start API, worker, and scheduler; verify Compliance Watch and a staging drift delivery.
10. Confirm no production scan can run the development methodology or a missing assessment.

## 14. Data Requiring Qualified Human Review

Before launch, humans must provide and approve:

- the methodology definitions, thresholds, scope, governing authority, and version policy;
- reviewer identities, qualifications, separation of duties, and escalation rules;
- approved evidence-source catalog, licensing, freshness, retrieval, and source-verification rules;
- evidence-backed assessment for every asset shown as eligible;
- qualification language and disputed/insufficient-information handling;
- review cadence, evidence expiry, incident response, appeal/correction, and SLA policies;
- validation of every production Compliance Watch source connector.

Automated collection can prepare a case. It cannot make the final religious determination.

## 15. Risks and Limitations

- No real methodology or asset ruling is seeded. A fresh deployment therefore has an intentionally
  empty executable screened market.
- Compliance changes currently enter through authenticated structured ingestion. Live official
  source connectors require provider-specific legal, freshness, authentication, and reliability
  work before production use.
- User execution currently selects one disclosed methodology. Strict multi-methodology
  intersection, majority agreement, and custom governance policies are not enabled. Majority mode
  must not ship without explicit governance approval.
- Evidence quality depends on qualified human review and source operations; software validation
  cannot establish religious authority.
- Daily digest delivery waits for a connected configured external channel; immediate in-app
  evidence remains available even when external delivery is disconnected.
- Advanced policy controls can display non-default statuses only after explicit acknowledgement,
  but normal scans still fail closed when evidence or executable governance is absent.

These limitations are deliberate rather than silently replaced by AI guesses or test data.
