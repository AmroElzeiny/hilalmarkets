# Sharia Passport, Governance, and Billing Implementation Report

Date: 2026-07-16

## 1. Current Versus Final Verification Flow

The prior flow retained evidence and an administrator decision, but customer Passport presentation,
historical event binding, reviewer assignments, publication separation, and operational billing
records were incomplete.

The implemented flow is now:

1. An approved source adapter imports a versioned source snapshot and content hash.
2. Canonical identity is checked using name, network/native-token identity, contract or official
   project references, provider IDs, and an exact exchange-market mapping. Ticker-only matching
   fails closed.
3. The factual dossier is bound to retained source snapshot IDs. AI organizes factual evidence only;
   it cannot decide, approve, reject, publish, or change a public status.
4. The completeness gate sends the case to review, identity review, evidence collection, or a
   recorded research failure.
5. A human reviewer records criterion outcomes, reasoning, qualifications, acknowledged gaps,
   evidence references, role, application version, timestamp, and integrity hash.
6. Approval and publication are separate state transitions, forms, CSRF checks, and audit events.
   The same authorized person may perform both while `REQUIRE_SECOND_REVIEWER=false`.
7. Publication creates immutable assessment and Passport versions, supersedes rather than
   overwrites a previous version, refreshes the screened universe, and records affected users and
   Watch Plans.
8. Source changes create a scoped review only when material. A safety hold is reversible only by a
   fresh reviewed and separately published version; removing a hold never silently reactivates the
   old record.

## 2. Passport Quick View

The shared Quick View is included once by
`src/ai_market_monitor/templates/hilal/base_dashboard.html` and opened through one controller,
`src/ai_market_monitor/static/passport-quick-view.js`. Entry points include Screened Market rows,
saved Watchlists, opportunity cards, compliance changes, Watch Plan opportunity results, and alert
proof views.

The first view shows asset identity, symbol, network/native-token state, current or historical
status, evidence freshness, methodology/version, reviewer, decision/publication/review dates, main
reasons, qualification, and important use coverage. It provides Full Passport, official-source,
copy-reference, saved-asset, and policy-allowed Watch Plan actions. Restricted states explain why a
Watch Plan cannot be created.

Historical event context is requested with exact Passport version and event time. The modal labels
the version used at event time and resolves current status separately. Loading, error, unavailable,
keyboard focus trap, Escape close, focus restoration, desktop modal, and mobile sheet behavior are
implemented.

Visual evidence:

- `reports/visual-qa/sharia-first/passport-quick-view-desktop.png`
- `reports/visual-qa/sharia-first/passport-quick-view-mobile-390.png`
- `reports/visual-qa/sharia-first/screened-market-live-table-desktop.png`
- `reports/visual-qa/sharia-first/screened-market-mobile-390.png`

## 3. Full Sharia Evidence Passport

`ShariaPassportReadService` is the single read model for current, historical, and compact Passport
views. `hilal/dashboard/passport.html` renders one reusable page with:

- sticky identity/status/methodology/review summary;
- plain-language meaning and limits;
- separate use-case coverage matrix;
- canonical identity and exact provider/exchange mappings;
- criterion outcomes and retained evidence sources;
- the explicit label "AI-organized factual research - not a religious decision";
- human decision record and chronological audit history;
- methodology comparison without averaging;
- immutable historical alert/opportunity references;
- authenticated factual-problem reporting.

Technical hashes and source snapshot details remain behind progressive disclosure. The permanent
notice states that the result is methodology- and evidence-date-specific and is not a universal
ruling or approval of every use.

Visual evidence:

- `reports/visual-qa/sharia-first/sharia-evidence-passport-desktop.png`

## 4. Models, Migration, Routes, Services, and UI Files

Migration:

- `alembic/versions/e7f8a9b0c1d2_add_passport_governance_checkout_email.py`

New or extended persistence:

- `BillingCheckoutAttempt` and `PaymentEmailDelivery` in `db/models/commercial.py`.
- Exact Passport/universe/policy references on `ScanResult`, `SetupInstance`, and `Alert` in
  `db/models/monitoring.py`.
- Review assignment/SLA fields, criterion decisions, qualifications, acknowledged gaps, AI snapshot
  reference, actor role, integrity hash, and superseding publication linkage in
  `db/models/sharia_governance.py`.
- `ShariaGovernanceRoleGrant`, `ShariaReviewerProfile`, `ShariaReviewAssignmentEvent`, and
  `ShariaPassportProblemReport` in the same governance model module.

Primary services:

- `services/sharia_passports.py`: current, historical, Quick View, report-problem, timeline,
  evidence, identity, use coverage, and decision read models.
- `services/sharia_governance.py`: RBAC, assignments, criterion decisions, separate publication,
  four-eyes enforcement, holds, fresh-review restoration, false-positive dismissal, retries, and
  audit export.
- `services/sharia_universe.py` and `services/scanner.py`: exact publication and canonical market
  references at deterministic scan time.
- `services/billing.py`: server-owned checkout preparation, provider session creation,
  idempotency, subscription/entitlement transition, and payment-email enqueue.
- `services/payment_emails.py`: rendering, durable outbox, retry schedule, and redacted result state.
- `core/csrf.py`: scoped signed CSRF token generation and constant-time verification.

Routes and pages:

- `/api/v1/sharia/assets/{asset}/passport/quick-view`
- `/api/v1/sharia/passports/{asset_id}/versions/{passport_version_id}`
- `/api/v1/sharia/assets/{asset}/passport/report-problem`
- `/passports/{asset_id}/versions/{passport_version_id}`
- `/dashboard/billing/checkout`
- `/dashboard/admin/payment-email-preview`
- `/system-brain/reviews/{case_id}/decision`
- `/system-brain/reviews/{case_id}/assignment`
- `/system-brain/notifications/{attempt_id}/retry`
- `/system-brain/audit-export`

Reusable UI additions include `passport_quick_view.html`, `passport-quick-view.js`,
`passport-page.js`, `checkout.html`, HTML/plain-text payment email templates, and the extended
HilalMarkets/System Brain token styles.

## 5. Decision and Publication Audit Records

Approval records criterion decisions, source snapshot IDs, dossier snapshot, reason,
qualifications, acknowledged gaps, actor/role, exact UTC time, application version, and integrity
hash. Publication is a later explicit action with its own reason, actor, timestamp, audit event, and
confirmation summary.

The publication dialog displays asset, proposed status, methodology/version, qualifications,
source count, previous public state, and affected Watch Plans/users. Invalid transitions are
rejected. Repeated state-changing submissions are fail-closed and do not create duplicate audit
events.

When `REQUIRE_SECOND_REVIEWER=true`, approval enters `awaiting_second_approval` and the reviewer
cannot publish their own decision. The default remains `false` for the current one-person operation.

## 6. Historical Passport and Event Binding

Deterministic scans persist assessment ID, Passport publication ID, methodology ID/version,
universe snapshot ID, and policy decision. Setup lifecycles and alerts copy those exact references.
Alert proof rendering constructs a historical Passport URL when the immutable publication exists.

The historical service loads that exact publication and stored Passport snapshot. It does not
redirect to or replace it with the latest record. The page states "Passport used at alert time" and
shows current status independently. Legacy rows remain honest: absent historical references are
reported as unavailable rather than backfilled with invented IDs.

## 7. Canonical Asset and Exchange-Symbol Consistency

Provider ticker payloads cannot create or override identity. A market is eligible for scanning only
when an active `ExchangeMarket` foreign-key mapping points to the reviewed `CanonicalAsset` for the
selected exchange, market type, base, and quote. Unknown, ambiguous, contradictory, or delisted
mappings fail closed.

Sharia eligibility and market availability remain separate. A reviewed asset without an exact
supported spot mapping displays "Eligible, market unavailable". A delisting stops market
monitoring without rewriting the religious assessment.

## 8. Admin RBAC and Security

Application ADMIN authentication remains mandatory for every System Brain page/action. Governance
role grants provide `SYSTEM_ADMIN`, `RESEARCHER`, `REVIEWER`, and `PUBLISHER`; the current owner can
hold all roles. Optional reviewer profiles do not block the owner when an external qualification
record is unavailable.

Every mutation verifies a scoped CSRF token and server-side state/permission policy. System Brain is
absent from customer navigation and public routes, and responses carry no-index protections. A
Cloudflare Access check can be required as an outer gate, but it never replaces application auth.

Cloudflare policy and direct-origin network blocking are deployment responsibilities; repository
code cannot prove that a VPS firewall, tunnel, DNS proxy, and Access audience are configured.

## 9. SLA, Assignment, Correction, Appeal, and Export

Cases expose age, time in stage, priority, assignee, due date, source freshness deadline, overdue
state, and affected Watch Plans/users. Assignment and reassignment create immutable assignment
events; missing an SLA never creates an automatic status.

User Passport reports create a factual-problem record, review case, audit event, and admin
notification without changing public status. Reopen, request evidence, return to research, dismiss
false positive, safety hold, and fresh-review restoration paths require reasons and preserve prior
versions. A replacement publication supersedes the previous publication instead of overwriting it.

Admin audit export supports CSV/JSON plus date, actor, asset, methodology, and action filters.

## 10. Checkout and Successful-Payment Email

Checkout renders plan name, cycle, server-owned price/currency, tax note, renewal/cancellation
terms, limits, notification access, secure-provider boundary, legal links, support, and a required
terms/risk/recurring-payment acknowledgement. Browser-supplied price and entitlement values are
ignored. Duplicate request keys and active subscriptions are handled by durable attempt records.

Verified provider events remain the authority for paid entitlements. Webhook processing is
idempotent and links provider references to the checkout attempt. The result page handles pending,
processing, success, failure, cancellation, delay, expiry, and already-subscribed states.

After a verified successful event, one `PaymentEmailDelivery` row is enqueued under a unique event
key. HTML and plain-text messages contain the plan, billing frequency, amount/currency, payment and
renewal dates, receipt link, limits, product/billing/support actions, and service boundary. They do
not contain payment credentials or authentication secrets. A Celery task retries due deliveries.

The database outbox prevents duplicate application sends for the same event key. SMTP cannot offer
mathematical exactly-once delivery after a process crash between remote acceptance and recording
`sent`; provider message IDs and attempt state make that rare ambiguity observable rather than
silently issuing another logical event.

Checkout visual evidence:

- `reports/visual-qa/checkout/checkout-desktop-1440.png`
- `reports/visual-qa/checkout/checkout-mobile-390.png`

## 11. Telegram and Admin Notification Tests

`tests/integration/test_compliance_telegram_notifications.py` uses deterministic fake adapters to
cover missing recipients, affected Watch Plan/user counts, timeout, rate limit/retry-after,
successful retry, durable deduplication, correct admin recipient, methodology/version, status
transition, and secure review link.

`scripts/test_compliance_notification.py` defaults to fake delivery and displays the payload. Live
delivery requires `--live`, a dedicated `--chat-id`, and the exact confirmation phrase. It never
prints the bot token. `scripts/test_payment_email.py` renders local HTML/text previews and sends no
email.

## 12. Admin Workflow Browser Results

The governance browser scenario confirms a normal customer has no System Brain link, then exercises
the authorized workspace, criterion UI, required reasoning, explicit approval confirmation,
separate publication modal, publication action, success state, reduced-motion behavior, and
desktop/tablet/mobile rendering.

Visual evidence:

- `reports/playwright/sharia-governance/admin-overview-desktop-1440.png`
- `reports/playwright/sharia-governance/admin-overview-tablet-900.png`
- `reports/playwright/sharia-governance/review-case-tablet-900.png`
- `reports/playwright/sharia-governance/review-case-mobile-390.png`
- `reports/playwright/sharia-governance/review-case-published-tablet-900.png`

Hold, fresh-review restoration, four-eyes publishing, false-positive dismissal, notification retry,
assignment, invalid transitions, CSRF, authorization, and audit behavior are covered at service and
HTTP integration layers. The browser suite exercises the highest-risk public transition - separate
approve and publish - without replacing those deterministic tests.

## 13. Customer-Facing Internal Notes Removed

Customer pages do not expose governance queues, reviewer assignments, migration/database language,
AI dossier failures, test methodology controls, or links into System Brain. Empty customer states
say that no screened assets are available until evidence and methodology review are complete, or
that no assets currently meet the selected policy. Internal readiness details remain in System
Brain only.

## 14. Tests and Exact Results

- Complete repository pytest suite: 1,918 passed, 0 failed, 2 upstream `lxml` deprecation
  warnings in 1,085.24 seconds.
- Focused Passport/governance/checkout/email/Telegram/symbol/migration/security suite: 60 passed.
- Full Playwright browser suite: 18 passed, 0 failed, 0 skipped.
- Changed-file Ruff check: passed.
- Targeted MyPy for the new/changed Passport, governance, checkout, email, and CSRF modules: passed.
- Python compileall: passed.
- JavaScript syntax checks for Passport, market, and System Brain scripts: passed.
- Safe compliance-notification fake delivery script: passed.
- Safe payment-email local preview script: passed.

Browser JUnit and generated summary:

- `reports/playwright/playwright-results.xml`
- `reports/playwright/playwright-summary.json`
- `PLAYWRIGHT_E2E_REPORT.md`

The repository-wide Ruff command still reports pre-existing findings outside the changed Passport,
governance, billing, and notification files. Those unrelated findings were not suppressed or
rewritten as part of this scoped implementation.

## 15. Desktop and Mobile Screenshot Index

- Screened Market desktop: `reports/visual-qa/sharia-first/screened-market-desktop.png`
- Live market desktop: `reports/visual-qa/sharia-first/screened-market-live-table-desktop.png`
- Screened Market mobile: `reports/visual-qa/sharia-first/screened-market-mobile-390.png`
- Passport Quick View desktop/mobile: paths in section 2.
- Full Passport desktop: path in section 3.
- Checkout desktop/mobile: paths in section 10.
- System Brain desktop/tablet/mobile/published: paths in section 12.

## 16. Staging and Deployment Steps

1. Back up the database and stop scanners/workers that can create lifecycle or billing writes.
2. Deploy the same application image to API, worker, and scheduler.
3. Run `alembic upgrade head`; confirm migration `e7f8a9b0c1d2` is applied.
4. Configure real public/app URLs, legal/support identity, SMTP sender, payment provider/webhook,
   Telegram test destination, methodology, SLA, and four-eyes policy in deployment secrets.
5. Start API, Redis, database, worker, and scheduler; confirm worker heartbeat.
6. Verify `/health`, authenticated customer routes, and authenticated System Brain through the
   intended proxy path.
7. Import one approved source, confirm no duplicate on unchanged re-import, and complete one staged
   review plus separate publication.
8. Verify exact canonical/exchange mapping and customer Passport before enabling that asset in a
   Watch Plan.
9. Run the fake scripts, then controlled live Telegram and payment-provider sandbox tests.
10. Confirm one verified sandbox payment creates one entitlement transition and one email event.
11. Test source-change hold, admin notification, fresh review, and superseding publication.
12. Open one historical alert and verify the historical Passport and current status are separate.

## 17. Manual Cloudflare and Firewall Actions

The owner/operator must still:

- put System Brain behind a Cloudflare Access application and explicit identity/group policy;
- keep application ADMIN/REVIEWER authentication enabled after Access;
- use Cloudflare Tunnel or firewall inbound rules so the origin is not directly reachable publicly;
- trust proxy headers only from the actual reverse proxy network;
- block or remove alternate origin ports and DNS records;
- test the public hostname, unauthenticated Access denial, application-auth denial, and direct-origin
  IP/host-header bypass attempts from an external network;
- verify `/system-brain` remains absent from sitemap, customer navigation, analytics, and indexing.

## 18. Owner Approvals Still Required

Code completion is not authority to launch. The owner must obtain and record:

- qualified governance approval for the production methodology, its interpretation, criteria,
  decision vocabulary, reviewer authorization, cadence, appeals, and incident policy;
- legal review of Sharia wording, limitations, privacy, payment terms, recurring-payment consent,
  risk disclosure, records retention, and regional obligations;
- permission or a lawful basis to retrieve, snapshot, transform, attribute, and retain each source;
- real canonical asset/network/contracts/exchange mappings and a delisting/mapping-change owner;
- production SMTP domain authentication and deliverability validation;
- payment-provider account, exact catalog/currencies/tax behavior, webhook signing, refund/cancel
  policy, invoice links, and sandbox-to-live approval;
- Telegram bot/test recipients, escalation ownership, retry/incident monitoring, and data-retention
  approval;
- backup, restore, audit export, vulnerability, origin-bypass, and incident-response drills.

No sample/browser-test assessment is production evidence, no AI result is a religious decision, and
no customer asset should appear until a real authorized publication exists.
