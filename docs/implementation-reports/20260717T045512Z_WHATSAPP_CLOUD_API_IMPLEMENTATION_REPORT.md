# Official WhatsApp Cloud API Implementation Report

Generated: 2026-07-17T04:55:12Z

Scope: production-oriented, first-party Meta WhatsApp Cloud API integration for
HilalMarkets/TraceEdge. This report covers code and local verification only. It
does not claim Meta Business verification, WABA approval, production phone
registration, template approval, or a live delivery test.

## 1. Executive summary

The repository now has a first-class WhatsApp notification channel built on the
official Meta WhatsApp Cloud API. It reuses the existing authenticated account,
alert, monitor, dashboard-link, audit, entitlement, lifecycle, and worker
boundaries rather than introducing a second notification system.

The implementation includes:

- explicit dashboard consent, category selection, locale selection, link,
  test, pause, resume, and disconnect controls;
- a signed webhook with Meta verification-token and
  `X-Hub-Signature-256` validation;
- strict inbound and outbound Pydantic payloads;
- official Graph API message delivery through `httpx`;
- the Meta 24-hour customer-service-window rule;
- approved-template delivery outside that window;
- delivery status reconciliation for accepted, sent, delivered, read, and
  failed events;
- idempotent webhook receipts and bounded retry processing;
- STOP-family opt-out and fresh-consent requirements;
- native WhatsApp navigation and safe Watch Plan pause/resume controls;
- authenticated, short-lived dashboard handoff for strategy approval,
  scanning, evidence, billing, support, and other complex workflows;
- fail-closed configuration and opportunity-alert policy flags;
- a reversible PostgreSQL-tested migration;
- focused unit, integration, browser, migration, security, and static checks.

The channel is disabled by default. No secret was added to tracked example
configuration, client JavaScript, templates, logs, or this report.

## 2. Repository areas inspected

- Application/bootstrap: `main.py`, settings, startup validation, platform and
  channel enums.
- API/security: dashboard principal, CSRF, request guards, route exposure audit,
  Telegram/Discord routes, dashboard APIs, and notification status APIs.
- Persistence: users, identities, link tokens, integration health/tests,
  alerts, deliveries, strategies, lifecycle/readiness state, and migrations.
- Delivery: shared notification dispatcher, alert presentation, candidate
  observability updates, retry behavior, and trial settlement.
- Telegram reference behavior: linking, menus, account association, Watch Plan
  actions, dashboard handoffs, settings, billing, support, lifecycle/proof, and
  webhook/polling behavior.
- Dashboard: integrations, settings, home status, channel selectors, icons,
  responsive CSS, polling, and CSRF-aware JavaScript.
- Runtime: Celery worker, beat schedule, webhook queue, retention cleanup, and
  integration health.
- Tests/docs: existing channel tests, dashboard browser smoke, environment
  examples, architecture, operations, privacy, and deployment instructions.

## 3. Telegram-to-WhatsApp parity matrix

`Native` means the action is performed directly through bounded WhatsApp
messages. `Secure handoff` means the business capability remains available, but
the authenticated dashboard owns the sensitive or visually complex action.

| Telegram behavior family | WhatsApp disposition | Reason / implementation |
| --- | --- | --- |
| Account link and identity association | Native | Dashboard creates one-time link metadata; inbound WhatsApp message proves the Meta `wa_id` and phone before activation. |
| Consent and disclaimer | Native + dashboard | Consent is unchecked by default, versioned, categorized, audited, and confirmed through the link flow. |
| `/start`, main menu, back/main navigation | Native equivalent | `START`, `MENU`, `HELP`, greeting text, interactive lists, and main-menu buttons are supported. |
| Referral/shared-template attribution | Preserved in link metadata where supplied | Model arguments cannot choose user ownership; server-side token metadata remains authoritative. |
| Trial, pricing, billing, checkout | Secure handoff | WhatsApp opens an authenticated billing route. Payment state never changes from message prose. |
| Create/describe/import/template setup | Secure handoff | Opens the existing AI Setup Chat/Canvas workspace. No raw WhatsApp text becomes executable logic. |
| Interpretation, edit, approve, reject, activate, save | Secure handoff | Existing deterministic schema, approval hash, and explicit UI actions remain authoritative. |
| Scanner / Check Market Now | Secure handoff | Provider limits, result evidence, and idempotency stay in the existing scanner UI/service. |
| Watch Plan list and status | Native | Interactive list is scoped to the connected owner and bounded to recent non-archived plans. |
| Watch Plan pause/resume | Native | Uses the same `MonitorOperationService`; ownership is derived from the connected account. |
| Watch Plan edit/delete | Secure handoff | Destructive and logic-changing actions remain authenticated dashboard operations. |
| Lifecycle/forming/confirmed/invalidated/expired views | Template alert + secure handoff | Lifecycle template includes state and a short-lived dashboard link for full evidence. |
| Proof and sample alert | Template/test + secure handoff | Test message is native; immutable proof remains rendered by the established dashboard proof view. |
| Near-miss filters and detailed latest-state controls | Secure handoff | Complex filtering remains in the dashboard; lifecycle category can notify when enabled. |
| Mute symbol/strategy and feedback | Secure handoff | No unaudited message-side mutation was added; alert/lifecycle links preserve the existing controls. |
| Notification days/hours/timezone/frequency | Secure handoff | Existing user preference service remains the schedule authority. |
| Channel category and locale preferences | Native dashboard control | WhatsApp-specific categories and configured locales update immediately through authenticated APIs. |
| Pause/resume channel | Native | Dashboard buttons and `PAUSE`/`RESUME` text commands use the same account service. |
| Opt out | Native | `STOP`, `UNSUBSCRIBE`, `CANCEL`, `END`, and `QUIT` revoke delivery eligibility and cancel queued WhatsApp deliveries. |
| Fresh opt in after STOP | Native initiation + dashboard consent | `START` does not silently re-consent; it directs the user to explicit dashboard consent. |
| Support/about/dashboard links | Native navigation + secure handoff | About is concise; support/dashboard open short-lived authenticated routes. |
| Telegram callback answer, message edit, photo media, polling | Not applicable | These are Telegram transport primitives, not missing customer business capabilities. WhatsApp uses signed webhooks and supported interactive message types. |

No Telegram business workflow was treated as silently complete merely because a
WhatsApp button exists. Approval, activation, provider-backed scanning, billing,
and destructive actions deliberately remain under their existing server and UI
authorities.

## 4. Architecture and data flow

### Account link

1. Authenticated user opens Dashboard > Integrations.
2. User enters E.164 phone, leaves consent unchecked until intentionally
   selected, selects categories and locale, and submits a CSRF-protected link
   request.
3. Server creates a hashed, expiring identity-link token with consent metadata.
4. Browser opens the configured WhatsApp business number with the token in a
   prefilled message.
5. Meta posts the signed inbound event.
6. Webhook validates signature, WABA ID, phone-number ID, payload schema, and
   event idempotency before persisting a redacted receipt.
7. Worker validates token expiry/cancellation, user ownership, unique `wa_id`,
   unique phone ownership, and consent metadata.
8. Connection becomes active and verified; the user receives a confirmation
   before normal menu navigation.

### Alert delivery

1. Existing notification dispatcher creates an `AlertDelivery` with channel
   `whatsapp` and destination `wa:<wa_id>` only for an eligible active,
   consented connection.
2. Worker locks due deliveries with `SKIP LOCKED`.
3. Renderer maps the existing authoritative alert presentation to one registered
   WhatsApp event and category.
4. Category opt-in and opportunity policy are checked.
5. Inside the 24-hour window, the service sends a session message. Outside the
   window, it requires an explicitly configured approved template.
6. Meta response supplies the provider message ID and accepted state.
7. Signed status webhooks advance persisted state to sent, delivered, read, or
   failed and update candidate/trial/integration-health records.

### Inbound conversation

Inbound messages can link an account, opt out, show the menu, pause/resume the
channel, list owner-scoped Watch Plans, pause/resume a selected Watch Plan, or
open a short-lived authenticated dashboard route. Unknown setup prose is not
compiled inside WhatsApp; it is redirected to the established secure setup
workspace.

## 5. Exact files added, modified, and removed

This is the WhatsApp-scoped file list. The working tree contains unrelated
pre-existing launch-readiness work that was preserved.

### Added

- `alembic/versions/09bac1d2e3f4_add_whatsapp_cloud_api.py`
- `docs/WHATSAPP_CLOUD_API_RUNBOOK.md`
- `docs/implementation-reports/20260717T045512Z_WHATSAPP_CLOUD_API_IMPLEMENTATION_REPORT.md`
- `src/ai_market_monitor/api/routers/whatsapp.py`
- `src/ai_market_monitor/db/models/whatsapp.py`
- `src/ai_market_monitor/whatsapp/__init__.py`
- `src/ai_market_monitor/whatsapp/adapter.py`
- `src/ai_market_monitor/whatsapp/rendering.py`
- `src/ai_market_monitor/whatsapp/security.py`
- `src/ai_market_monitor/whatsapp/service.py`
- `src/ai_market_monitor/whatsapp/types.py`
- `src/ai_market_monitor/whatsapp/webhook.py`
- `tests/integration/test_whatsapp_integration.py`
- `tests/unit/test_whatsapp_adapter.py`
- `tests/unit/test_whatsapp_webhook.py`

### Modified

- `.env.example`
- `.env.production.example`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/PRODUCTION_DEPLOYMENT.md`
- `src/ai_market_monitor/api/request_guards.py`
- `src/ai_market_monitor/api/route_security.py`
- `src/ai_market_monitor/api/routers/__init__.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/cockpit_api.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/platforms.py`
- `src/ai_market_monitor/core/startup.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `src/ai_market_monitor/db/models/accounts.py`
- `src/ai_market_monitor/db/models/dashboard_extensions.py`
- `src/ai_market_monitor/db/models/enums.py`
- `src/ai_market_monitor/db/models/monitoring.py`
- `src/ai_market_monitor/main.py`
- `src/ai_market_monitor/schemas/onboarding.py`
- `src/ai_market_monitor/schemas/sharia.py`
- `src/ai_market_monitor/schemas/strategy.py`
- `src/ai_market_monitor/services/notifications.py`
- `src/ai_market_monitor/services/setup_observability.py`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/hilalmarkets-icons.js`
- `src/ai_market_monitor/static/hilalmarkets.css`
- `src/ai_market_monitor/templates/hilal/base_dashboard.html`
- `src/ai_market_monitor/templates/hilal/dashboard/home.html`
- `src/ai_market_monitor/templates/hilal/dashboard/integrations.html`
- `src/ai_market_monitor/templates/hilal/dashboard/settings.html`
- `src/ai_market_monitor/templates/hilal/public/privacy.html`
- `src/ai_market_monitor/worker.py`
- `tests/browser/conftest.py`
- `tests/browser/test_dashboard_e2e.py`
- `tests/unit/test_api_route_security.py`
- `tests/unit/test_dashboard_static_assets.py`
- `tests/unit/test_reliability_security.py`
- `tests/unit/test_request_guards.py`

### Removed

- None for this integration.

## 6. Database models and migration

Migration head: `09bac1d2e3f4`, parent `f8a9b0c1d2e3`.

New tables:

- `whatsapp_connections`: one connection per user, unique `wa_id` and E.164
  phone, connection status, explicit opt-in version/source/time/categories,
  service-window timestamps, pause/revoke/error state, and locale.
- `whatsapp_conversation_states`: owner, `wa_id`, bounded flow state,
  correlation ID, last inbound message ID, and expiry.
- `whatsapp_webhook_receipts`: unique provider event key, redacted payload,
  payload hash, processing/retry state, provider IDs/status, response metadata,
  and retention timestamp.

Extended fields:

- `identity_link_tokens`: optional onboarding session, cancellation time, and
  metadata for dashboard-originated links.
- `user_identities`: `whatsapp` provider enum value.
- `alert_deliveries`: `whatsapp` channel, provider status metadata, accepted
  timestamp, and read timestamp.
- `integration_test_results`: provider message ID for webhook reconciliation.

The migration was validated against fresh PostgreSQL 16, downgraded to its
parent, and upgraded back to head. Downgrade necessarily deletes WhatsApp rows
before narrowing enum values; this is documented operationally and must not be
used as a casual production rollback after onboarding real users.

## 7. Environment inventory

All values are empty or safe-disabled in example files:

- `WHATSAPP_ENABLED=false`
- `WHATSAPP_ADAPTER=none`
- `WHATSAPP_GRAPH_API_VERSION`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_APP_SECRET`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_BUSINESS_ACCOUNT_ID`
- `WHATSAPP_BUSINESS_PHONE_E164`
- `WHATSAPP_DEFAULT_LANGUAGE=en_US`
- `WHATSAPP_HTTP_TIMEOUT_SECONDS=15`
- `WHATSAPP_MAX_DELIVERY_ATTEMPTS=5`
- `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED=false`
- `WHATSAPP_TEMPLATE_NAMES={}`
- `WHATSAPP_OPT_IN_VERSION=2026-07`
- `WHATSAPP_MARK_INBOUND_READ=true`
- `WHATSAPP_WEBHOOK_RECEIPT_RETENTION_DAYS=30`

`API_RATE_LIMITS` includes a dedicated `whatsapp_test` bucket. Secret settings
use `SecretStr`. Startup fails closed when WhatsApp is enabled without the HTTP
adapter or required Meta identifiers/secrets. Opportunity delivery additionally
requires its policy flag and approved template mapping.

## 8. Webhook security

- GET verification is enabled only when the channel is enabled and compares
  `hub.verify_token` in constant time.
- POST requires a correctly formatted `sha256=<64 hex>`
  `X-Hub-Signature-256` HMAC over the exact raw request body.
- Missing or invalid signatures return 401 before parsing or persistence.
- Invalid signed JSON returns 400.
- Payload parser rejects unexpected object shape, WABA ID, and phone-number ID.
- Inbound and status records are validated with strict Pydantic models.
- Provider event keys are unique; database integrity handles concurrent
  duplicates.
- Persisted payloads are bounded/redacted event subsets, not unrestricted Meta
  webhook bodies.
- Authenticated account-control routes require dashboard ownership and CSRF.
- The route-security audit now traverses FastAPI lazy included-router contexts,
  so every `/api/v1` route is checked using its effective path/dependencies.

## 9. Account linking and consent

- Link initiation requires authentication, CSRF, normalized E.164 input,
  `consent=true`, at least one allowed category, and a valid configured locale.
- Consent checkbox is not preselected.
- Link tokens are hashed, expiring, single-use, cancellable, and carry only
  bounded link/consent metadata.
- Completion locks and separately checks the current user's connection, the
  Meta `wa_id` owner, and phone owner to prevent cross-account reassignment.
- The first signed inbound message proves possession of the WhatsApp identity.
- Browser status exposes masked phone/profile data only; secrets and raw `wa_id`
  are not rendered client-side.
- Disconnect revokes the connection and cancels queued deliveries.
- STOP-family messages opt out and cancel queued deliveries.
- START after opt-out does not silently restore consent; dashboard re-consent is
  mandatory.
- Consent, preference, pause/resume, disconnect, and test operations create
  audit records.

## 10. Template registry, variables, and categories

Configured event keys are strictly allowlisted. Template names and locale map
keys are validated at settings load.

| Event | Category | Required body variables |
| --- | --- | --- |
| `connection_confirmation` | account | `display_name`, `settings_url` |
| `connection_test` | account | `display_name`, `settings_url` |
| `account_notice` | account | `notice_title`, `dashboard_url` |
| `trial_update` | subscription | `trial_state`, `billing_url` |
| `subscription_update` | subscription | `subscription_state`, `billing_url` |
| `compliance_change` | compliance | `asset`, `status`, `methodology`, `passport_url` |
| `evidence_update` | evidence | `asset`, `evidence_state`, `passport_url` |
| `watchlist_paused` | watchlist_health | `watchlist_name`, `reason`, `dashboard_url` |
| `integration_failure` | operational | `channel`, `reason`, `settings_url` |
| `lifecycle_update` | lifecycle | `symbol`, `state`, `monitor_name`, `lifecycle_url` |
| `confirmed_research_event` | opportunity | `symbol`, `timeframe`, `monitor_name`, `proof_url` |

Allowed opt-in categories are `account`, `subscription`, `compliance`,
`evidence`, `watchlist_health`, `operational`, `lifecycle`, and `opportunity`.
The renderer uses existing alert evidence and URLs; it does not invent market or
Sharia facts.

## 11. Delivery and provider-status matrix

| Stage/event | Internal state | Operational effect |
| --- | --- | --- |
| Queued | pending | Eligible for a locked worker claim. |
| Graph API accepted | sent + provider `accepted` | Provider message ID and accepted timestamp are stored. |
| Meta `sent` | sent | Provider progression recorded. |
| Meta `delivered` | delivered | Delivery timestamp, integration health, candidate state, and trial settlement update once. |
| Meta `read` | delivered + provider `read` | Delivered/read timestamps recorded; never regresses to an older state. |
| Retryable HTTP/network/provider failure | failed_retryable | Redacted error metadata and bounded next retry are stored. |
| Permanent or exhausted failure | failed_permanent | No further automatic retry. |
| Missing template outside session window | failed_permanent | No free-form policy bypass. |
| Category not selected | suppressed | No send attempted. |
| Opportunity flag/template unavailable | suppressed | No send attempted. |
| Disconnected/paused/opted out | canceled | Queued delivery is not sent. |

HTTP 429, server errors, transient Meta codes, and explicit transient flags are
retryable. Invalid tokens, permissions, template errors, and recipient/window
policy errors are normalized to safe internal codes. Tokens and recipient
numbers are removed from provider error messages before persistence/logging.

## 12. Retry and idempotency controls

- Webhook event key has a database uniqueness constraint.
- Raw payload and canonical event hashes support forensic comparison without
  storing unrestricted request bodies.
- Inbound conversation state remembers the last provider message ID.
- Deliveries reuse the existing alert/channel/destination idempotency boundary.
- Worker uses row locking with `SKIP LOCKED` and a bounded batch.
- Retry maximum is configurable and validated from 1 to 20.
- Backoff is `30 * 2^(attempt-1)` seconds, capped at 3600 seconds, unless Meta
  provides a bounded `Retry-After`.
- Status events are monotonic; late `sent` cannot overwrite `read`.
- Trial usage is settled only on the first transition to delivered/read.
- Receipt cleanup uses a configurable 1-to-365-day retention window.

## 13. Policy flags and safety boundaries

- Channel kill switch: `WHATSAPP_ENABLED`, false by default.
- Adapter allowlist: only `none` or the official `http` Cloud API adapter.
- Opportunity kill switch: `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED`, false by
  default.
- Outside-window delivery requires a configured template from the strict
  registry.
- Opportunity/lifecycle events require both policy enablement and template
  availability.
- AI, raw inbound text, and templates cannot approve strategies, activate Watch
  Plans, alter billing, or create market facts.
- Scanner and scheduled evaluation remain deterministic and LLM-free.
- User ownership is derived from authenticated or linked server state, never a
  model/browser-supplied `user_id`.
- Dashboard deep links are short-lived and owner-scoped.

## 14. Tests and exact results

Commands below used the existing `trading_assistant-test:latest` image with the
workspace mounted at `/workspace`, except Ruff which used the repository-local
executable.

### Focused WhatsApp/security suite

```text
python -m pytest tests/unit/test_whatsapp_adapter.py tests/unit/test_whatsapp_webhook.py tests/integration/test_whatsapp_integration.py tests/unit/test_dashboard_static_assets.py tests/unit/test_reliability_security.py tests/unit/test_request_guards.py tests/unit/test_api_route_security.py -q
```

Result: **55 passed**.

Coverage includes strict payloads, adapter request/error behavior, webhook
verification/signature/schema/idempotency, link expiry and cross-user
protection, explicit consent, disconnect/reconnect/re-consent, service-window
and template policy, no free-form fallback, category suppression, opportunity
flag, delivery idempotency, bounded retry, status progression, dashboard masking,
startup fail-closed behavior, rate limiting, and route exposure.

### Adjacent channel/scanner regression

```text
python -m pytest tests/unit/test_telegram_imports.py tests/integration/test_telegram_webhook.py tests/integration/test_telegram_service.py tests/integration/test_compliance_telegram_notifications.py tests/integration/test_discord_service.py tests/integration/test_discord_interactions.py tests/integration/test_dashboard_api.py tests/integration/test_scanner_pipeline.py -q
```

Result: **61 passed**.

### Browser smoke

```text
python -m playwright install --with-deps chromium
python -m pytest tests/browser/test_dashboard_e2e.py::test_notification_channel_handoff_links_smoke -q
```

Result: **1 passed** in Chromium. The browser verifies the notification-channel
handoff UI, WhatsApp integration card, and absence of WhatsApp credential names
from rendered page content.

### Route/release/dependency checks

```text
python scripts/check_release_invariants.py
python scripts/check_api_route_security.py
python scripts/check_dependency_lock.py
```

Results: all **PASS**. The API checker reports every `/api/v1` route is
authenticated or explicitly annotated. Dependency checker reports 27 exact-
pinned direct runtime/test dependencies.

### Static checks

```text
.venv/Scripts/ruff.exe check .
python -m mypy src/ai_market_monitor/api/route_security.py
python scripts/check_javascript.py
python scripts/check_jinja_templates.py
```

Results: Ruff **PASS**; focused MyPy **PASS**; **16** JavaScript files pass;
**61** Jinja templates load.

The earlier focused MyPy run over the WhatsApp router/services/schemas/settings
also passed after strict webhook-type narrowing.

### Migrations

```text
alembic heads
alembic upgrade head                      # fresh PostgreSQL 16
alembic downgrade f8a9b0c1d2e3           # disposable PostgreSQL 16
alembic upgrade head                      # reapply WhatsApp migration
```

Results: one head, `09bac1d2e3f4`; full PostgreSQL upgrade **PASS**; WhatsApp
downgrade **PASS**; re-upgrade **PASS**. A prior disposable SQLite full upgrade
also passed.

## 15. Existing failures kept separate

An earlier full repository run completed with four failures plus 18 browser
setup errors. The browser errors were environmental: Chromium had not been
installed in the cached test image. Chromium was subsequently installed in a
disposable container and the updated browser smoke passed.

The fourth failure was the route-security auditor seeing zero routes under the
new lazy-router FastAPI behavior. That defect is now fixed and its three focused
tests, dedicated checker, and release-invariant checker pass.

These three unrelated failures remain and were reconfirmed directly:

1. `test_watchlists_saved_assets_and_market_scanner_are_distinct_routes`:
   expected copy `Your approved assets, kept together` is absent from the
   current Watchlist page.
2. `test_deployed_passport_requires_an_active_publication`: expected
   `passport_not_published`, current service returns `assessment_not_found`.
3. `test_opportunity_card_uses_retained_conditions_and_prior_score`: fixture
   inserts a condition result without the now-required
   `data_freshness_ms`, causing a SQLite NOT NULL failure.

No WhatsApp-focused, adjacent-channel, route-security, browser-smoke, static, or
migration check failed. The repository-wide suite is therefore **not fully
green**, and this report does not claim otherwise.

## 16. External prerequisites not verified locally

The following require the owner/Meta/staging environment and remain incomplete:

- Meta Business verification and policy acceptance.
- A production WhatsApp Business Account and registered phone number.
- System-user or otherwise appropriate long-lived access token and rotation
  procedure.
- Correct Graph API version selected for the deployment window.
- Public HTTPS webhook URL reachable by Meta.
- Webhook object/field subscription for messages and statuses.
- Approved utility/authentication/marketing template classification and exact
  template names/locales in Meta Manager.
- Legal approval of opt-in copy, privacy language, template category, retention,
  and STOP behavior for target jurisdictions.
- Production/staging secret-manager population and rotation.
- Controlled staging link, session message, outside-window template, STOP,
  reconnect, test-message, delivered, read, and failure-status run.
- Meta quality/rate-limit monitoring and production alert thresholds.

Official references used:

- Meta WhatsApp Cloud API collection:
  <https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api>
- Meta webhook payload reference:
  <https://www.postman.com/meta/whatsapp-business-platform/folder/tduohwq/webhook-payload-reference>
- Meta webhook subscriptions:
  <https://www.postman.com/meta/whatsapp-business-platform/folder/ypn8q0n/webhook-subscriptions>

## 17. Remaining risks and recommended actions

1. Fix the three unrelated repository failures and rerun the complete suite with
   Chromium preinstalled before merging.
2. Review the migration downgrade policy before onboarding real WhatsApp users;
   downgrade intentionally removes WhatsApp channel data.
3. Complete Meta and legal prerequisites, then use the runbook for a controlled
   staging smoke. Do not enable the channel before all startup settings validate.
4. Keep `WHATSAPP_OPPORTUNITY_ALERTS_ENABLED=false` until the exact opportunity
   templates are approved and their categories reviewed.
5. Confirm each configured locale/template pair in Meta and run one
   outside-service-window test per locale.
6. Configure metrics/alerts for webhook signature failures, receipt backlog,
   delivery retry exhaustion, Meta 429/5xx rates, stale integration health, and
   unusual opt-out volume.
7. Validate privacy deletion/export behavior against WhatsApp connection,
   receipt, delivery, identity-link, and audit retention obligations.
8. Run a production-like worker restart and duplicate-webhook drill to validate
   operational idempotency under concurrency.

The code path is ready for controlled staging configuration and Meta-side setup,
not an unqualified production launch.
