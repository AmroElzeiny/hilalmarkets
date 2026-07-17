# Launch-Readiness Correction Report

Date: 2026-07-17  
Branch inspected: `main`  
Baseline commit inspected: `a7a52964`

## 1. Executive Status

This correction closes the highest-risk repository gaps found after the Passport, governance,
checkout, notification, and historical-evidence work. The release is **not yet approved for an
external beta**. The repository now has stricter governance, API ownership, provider-accurate
billing, fail-closed production presentation, owner grants, terminology boundaries, and a CI release
gate. A post-change green test run and all staging/external operations remain outstanding.

The deterministic authority remains unchanged:

- `ShariaUniverseResolver` is the only screening execution boundary.
- AI produces factual research support only; it cannot decide Sharia status or publish.
- approval and publication are separate audited human actions;
- approved versions, Passport publications, alert proofs, and universe snapshots remain immutable;
- one-owner operation remains supported with `REQUIRE_SECOND_REVIEWER=false`.

## 2. Blocker-By-Blocker Resolution

### Methodology criteria and evidence gates

Resolved in the repository:

- Removed approval-grade fallback criterion decisions.
- Added strict, versioned methodology rule and evidence schemas.
- Required an explicit decision for every required criterion and use scope.
- Required written reasons for qualifications, failures, not-applicable decisions, and evidence gaps.
- Blocked missing criteria, blocking outcomes, stale/missing evidence, incomplete evidence,
  unresolved contradictions, and dossier/source-version mismatches.
- Persisted methodology version, criteria version, criteria hash, criterion decisions, and use-case
  decisions in each immutable review decision.
- Added `effective_to` and fail-closed expired-methodology behavior.
- Added contract validation both when an active methodology is created and whenever it is selected
  for execution. Direct database insertion cannot bypass this check.

SC Malaysia remains an official asset-level external reference. The SC adapter verifies the exact
source wording, authority, identity, and scope. HilalMarkets use-specific factual review is stored
and displayed separately; no unpublished SC reasoning is implied.

### Methodology authority

Resolved:

- Publication resolves `ReviewCase.methodology_id`; it no longer substitutes a hard-coded method.
- Active/effective/executable/non-development status and source-family compatibility are checked.
- SC parsing/import wording remains adapter-specific while approval/publication is methodology-neutral.
- Customer comparison UI remains unavailable until two real approved results exist for the asset.

### User-scoped API security

Resolved in code:

- Billing and other customer endpoints derive ownership from the authenticated principal.
- User-supplied identifiers do not authorize entitlement, checkout, usage, receipts, portals,
  integrations, investigations, scans, or strategy data.
- Suspended users are rejected.
- Cookie mutations are protected by deployed same-origin checks; sensitive form/API routes use CSRF.
- Public and signed-webhook endpoints must be explicitly annotated.
- A repository-wide API route audit fails unannotated, unauthenticated `/api/v1` routes.
- Rate limits cover authentication, AI chat, market checks, checkout, portal, support, Passport
  reports, Telegram tests, and both general and Sharia administration mutations.
- The primary support-ticket API and both API and server-rendered checkout entry points are covered;
  their route-pattern tests prevent either customer flow from falling outside its limit.
- Deployed rate limiting fails closed if Redis is unavailable.

### Billing semantics

Resolved:

- Providers declare recurring, portal, cancellation, refund, and receipt capabilities.
- Stripe may present monthly automatic renewal and a customer portal.
- The implemented NOWPayments invoice adapter presents one-time 30-day access, manual renewal,
  no automatic charge, and no portal. NOWPayments' separate recurring API is not integrated.
- One-time access is stored with end-of-period cancellation semantics and expires without another
  verified payment.
- Activation validates checkout attempt, provider, plan, amount, currency, and configured payment
  variance policy.
- Duplicate provider events remain idempotent; refunds revoke active access.
- Public Pricing, checkout, Billing, success content, and payment email use the same capability-aware
  wording and Plan Catalog limits.
- Payment preview is development/staging ADMIN-only and returns `404` in production.

### Release gate and repository hygiene

Resolved in repository configuration:

- Added GitHub Actions jobs for Python 3.12, exact direct dependency pins, Alembic single-head and
  previous-to-head migration, Ruff, MyPy, route/release checks, Jinja, JavaScript, full backend
  tests, Chromium desktop/mobile browser tests, pip-audit, Gitleaks, Trivy, and artifact upload.
- Expanded `.gitignore`; generated reports and runtime artifacts are rejected by release invariants.
- Removed the tracked Playwright Markdown report from the index; CI artifacts replace committed runs.
- Added checks for hidden plans, production test/fixture flags, unsafe provider settings, protected
  routes, and deprecated customer terminology.

## 3. Files And Migration

Primary new files:

- `.github/workflows/release-gate.yml`
- `alembic/versions/f8a9b0c1d2e3_enforce_methodology_review_contract.py`
- `scripts/bootstrap_governance_owner.py`
- `scripts/check_api_route_security.py`
- `scripts/check_dependency_lock.py`
- `scripts/check_javascript.py`
- `scripts/check_jinja_templates.py`
- `scripts/check_release_invariants.py`
- `src/ai_market_monitor/api/request_guards.py`
- `src/ai_market_monitor/api/route_security.py`
- `src/ai_market_monitor/schemas/sharia_methodology.py`
- `src/ai_market_monitor/services/governance_bootstrap.py`
- `src/ai_market_monitor/services/opportunity_cards.py`
- `tests/integration/test_api_route_ownership.py`
- `tests/services/test_governance_bootstrap.py`
- `tests/unit/test_api_route_security.py`
- `tests/unit/test_request_guards.py`
- `docs/LAUNCH_READINESS_CORRECTION_REPORT.md`

Primary modified backend files:

- `src/ai_market_monitor/core/config.py`, `core/plans.py`, `core/site_content.py`, `core/startup.py`
- `src/ai_market_monitor/db/models/sharia.py`, `db/models/sharia_governance.py`
- `src/ai_market_monitor/schemas/sharia.py`
- `src/ai_market_monitor/api/dependencies.py`
- `src/ai_market_monitor/api/routers/billing.py`, `dashboard.py`, `dashboard_api.py`, `discord.py`,
  `investigations.py`, `onboarding.py`, `public.py`, `sharia.py`, `status.py`, `system_brain.py`,
  `telegram.py`
- `src/ai_market_monitor/services/billing.py`, `compliance_watch.py`, `payment_emails.py`,
  `product_language.py`, `scanner.py`, `setup_observability.py`, `sharia_admin_dashboard.py`,
  `sharia_governance.py`, `sharia_passports.py`, `sharia_screening.py`, `sharia_universe.py`
- `src/ai_market_monitor/worker.py`

Primary presentation files:

- `src/ai_market_monitor/templates/system_brain.html`
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html`, `checkout.html`, `market.html`,
  `passport.html`, `settings.html`, `watch_plans.html`, `watchlist.html`
- `src/ai_market_monitor/templates/hilal/macros/opportunity_card.html`, `watch_plan_card.html`
- `src/ai_market_monitor/templates/hilal/public/partials/pricing_cards.html`
- `src/ai_market_monitor/static/hilalmarkets.css`, `hilalmarkets.js`, `passport-quick-view.js`
- payment-success HTML/text templates and customer terminology templates under `templates/hilal/`

Primary test files updated:

- `tests/services/test_sc_malaysia_governance.py`
- `tests/services/test_sharia_screening.py`
- `tests/integration/test_checkout_and_payment_email.py`
- `tests/integration/test_hilal_public_site.py`
- `tests/integration/test_investigation_api.py`
- `tests/integration/test_scanner_pipeline.py`
- `tests/integration/test_sharia_api.py`
- `tests/integration/test_telegram_service.py`
- `tests/unit/test_billing_entitlements.py`
- `tests/unit/test_reliability_security.py`
- `tests/unit/test_setup_observability.py`
- `tests/browser/test_sharia_governance_admin.py`

The migration adds methodology expiry and immutable methodology/use-decision fields, and upgrades the
SC methodology seed to the explicit criteria/evidence contract. Existing approved assessment,
publication, Passport, alert, and universe records are not rewritten.

## 4. Route Authentication Matrix

| Route family | Exposure | Ownership/authority |
|---|---|---|
| `/api/v1/billing/plans` | Explicit public | Public allowlist only; internal plans excluded |
| Billing checkout, portal, entitlement, usage, history | Authenticated | Principal user only; same-origin/CSRF where applicable |
| Billing webhooks | Signed webhook | Provider signature, idempotency, amount/currency/attempt validation |
| Dashboard strategies, versions, scans, alerts, investigations | Authenticated | Server-side user/strategy/monitor ownership |
| Sharia assets, Passports, watchlists, preferences | Authenticated | Principal user; saved collections owner-scoped |
| Sharia admin methodologies | Authenticated admin | Explicit `SYSTEM_ADMIN` grant plus CSRF |
| Legacy initial assessment creation | Local/test admin only | Explicit `PUBLISHER`; blocked in staging/production |
| Compliance ingest/review | Authenticated admin | Explicit `RESEARCHER`/`REVIEWER` grant plus CSRF |
| System Brain | Separate admin session | Application ADMIN, explicit grants, OTP/session/CSRF, optional Access outer gate |
| Telegram/Discord customer connection APIs | Authenticated | Principal connection ownership |
| Telegram/Discord provider callbacks | Signed webhook | Telegram secret or Discord signature |
| Public health/content routes | Explicit public | Bounded non-user data only |

## 5. Passport And Use-Coverage Corrections

- Public use coverage is reconstructed only from immutable reviewer use decisions.
- Every use decision carries reason, criterion/source references, reviewer, verification time, and scope.
- `native_staking` may be `NOT_APPLICABLE`; it is not universal.
- Spot monitoring/ownership is not covered unless explicitly reviewed.
- Source scan cadence, evidence expiry, and governance review date are distinct.
- Public reasons and qualifications come from the reviewer record; AI factual summaries are labelled.
- Production customer lists require an active published Passport.
- `can_create_watch_plan` requires allowed current status, user policy, no safety hold, and an exact
  active spot market mapping.
- Historical alerts retain their exact Passport version and display current status separately.

## 6. Fail-Closed And Market Corrections

- Added duration, included/excluded count, per-reason fail-closed, and abnormal exclusion metrics.
- Dependency/provider failures normalize to exclusions or an unavailable response; unknown never
  becomes eligible.
- Expired/inactive/development/invalid-contract methodologies cannot execute or appear as normal
  executable choices.
- Deployed resolution requires a published Passport, verified canonical identity, and exact active
  exchange-market mapping.
- Shared opportunity cards use retained condition evidence, prior readiness, blocker values, data
  freshness, and Passport actions; missing evidence remains missing. Mixed known/unknown freshness
  is labelled partial rather than collapsed into a misleading single value.

## 7. Terminology And UX

- `Watch Plan` means executable market behavior and rules.
- `My Screened Watchlist` means user-saved assets and Sharia-status following.
- `Screened Market` means assets currently allowed by policy.
- Deprecated strategy-as-watchlist customer copy is rejected by the release invariant check.
- Removing a saved asset first loads the exact non-archived Watch Plans whose current version selects that
  collection. The API returns `409` until explicit confirmation and requires CSRF.
- A branded responsive dialog shows affected plans; immutable old versions/history are untouched.
- Methodology comparison is hidden when fewer than two approved asset assessments exist.

## 8. Notification Coverage

Compliance drift evidence now carries previous and current/pending status, methodology/version,
reason, review state, automatic Watch Plan action, affected plans, Passport path, and next action.
Durable delivery records and retry behavior remain idempotent. Fake/no-send tests do not count as a
live Telegram result. The complete multi-user, no-channel, rate-limit, permanent-failure, and digest
matrix still requires final CI and controlled staging delivery.

## 9. CI And Verification Results

Authoritative results available in this local run:

- Pre-edit baseline full suite: **1918 passed, 2 warnings in 972.07 seconds**.
- Post-change repository-wide Ruff: **PASS** (`ruff check .`).
- Current dependency pin check: **PASS, 27 direct runtime/test dependencies exact-pinned**.
- Host-Python syntax compilation for `src`, `tests`, `scripts`, and `alembic`: **PASS**.
- `git diff --check`: **PASS**; line-ending notices are informational and no whitespace errors exist.
- Static Alembic graph: **31 revisions, one root, one head (`f8a9b0c1d2e3`), no missing parent**.
- Git-index scan for runtime databases/reports/logs/Playwright report: **no matches**.
- Deprecated customer terminology scan over release customer surfaces: **no matches**.
- Filename-only scans found no tracked OpenAI, Telegram, or Resend key signatures. This is not a
  substitute for the pending full-history Gitleaks job.

Intermediate focused runs earlier in the correction passed governance, billing/security/public,
owner bootstrap, screening, observability, and public-site groups. They are not treated as final
proof because later code changed.

Final verification is incomplete:

- The repository-local virtual environment points to a removed Python installation.
- The host Python lacks FastAPI and other project dependencies.
- `node` is not installed on the host.
- The isolated Docker verifier became unavailable due to the execution environment's approval-usage
  limit before the post-change full suite, MyPy, runtime Alembic, Jinja, JavaScript, and browser
  reruns.
- A focused MyPy retry also could not start because `.venv` still points at the removed Python 3.12
  executable. A pre-repair full MyPy run reported 106 errors in 18 files; those repairs remain
  unverified by a working MyPy runtime.
- There is no resolved, hash-locked transitive dependency artifact. Exact direct pins and `pip check`
  in CI reduce drift but do not yet satisfy a fully reproducible dependency-lock requirement.
- The GitHub Actions workflow has been added but has not run; it is **not green evidence yet**.

## 10. Staging And Production Evidence

Actually performed: none. No staging database, provider sandbox, Cloudflare account, DNS/firewall,
live test chat, SMTP control panel, or ten-person participant group was available to this local run.

Still required:

- PostgreSQL backup migration and isolated restore drill;
- BTC/ETH/SOL official imports, factual dossiers, explicit reviews, and separate publications;
- Passport inspection and exact live Binance/Bybit mapping tests;
- source unchanged/change/withdrawn/unavailable/stale/restore sequence;
- live Telegram, NOWPayments sandbox, and SMTP SPF/DKIM/DMARC delivery;
- Cloudflare Access, Tunnel/firewall, spoofed-header, alternate-host, and origin-IP tests;
- operational dashboards and alerts;
- seven-day worker/scheduler duplicate soak;
- ten-user terminology/Passport/activation/Telegram/alert-comprehension study.

## 11. Visual QA

Requested screenshots at 1440, 1024, 768, and 390 pixels were **not generated**. Chromium was not
available locally and the isolated browser verifier was blocked before capture. There are no paths
to report, and no fabricated visual evidence is substituted. CI browser artifacts must be reviewed
after the workflow runs, followed by authenticated staging captures.

## 12. Updated Readiness Matrix

| Area | Code readiness | Release evidence |
|---|---|---|
| Methodology contract and explicit review | Strong | CI and owner pilot pending |
| Publication/Passport immutability | Strong | Staging pilot pending |
| Resolver and fail-closed policy | Strong | Destructive staging matrix pending |
| API authentication/ownership | Strong | Final route audit/CI pending |
| Billing semantics/security | Strong | Sandbox catalog/event matrix pending |
| Opportunity cards/journeys | Mixed | Cards improved; full journey soak pending |
| Compliance drift delivery | Strong payload foundation | Full fake matrix and live controlled send pending |
| System Brain grants/actions | Strong service foundation | Full browser action matrix pending |
| Customer terminology/flow | Improved | Responsive QA and ten-user study pending |
| CI/repository hygiene | Workflow implemented | First green protected-branch run pending |
| Operations/infrastructure | Documented | Not executed/configured in this run |

## 13. Unresolved Dependencies

- Qualified religious-governance authority and accountable reviewer decisions.
- Rights and permitted retention/redistribution terms for official source material.
- Legal review of product claims, Passport wording, privacy, refunds, incidents, and jurisdiction.
- Production PostgreSQL/Redis/worker/scheduler sizing, backup, restore, and monitoring.
- Cloudflare Access and direct-origin network controls.
- NOWPayments sandbox and production catalog/webhook configuration.
- SMTP sender-domain authentication and deliverability.
- Telegram/Discord controlled test systems and support process.
- Post-change CI, browser screenshots, staging pilot, soak, and user research.

## 14. Release Decision

Do not open the external beta yet. The next release decision requires: a fully green Release Gate,
successful staging backup/upgrade/restore, explicit owner grants, three real published pilot
Passports, one deterministic scan/alert/change-hold/republication journey, controlled delivery and
payment evidence, edge bypass tests, seven-day soak, responsive visual review, and recorded owner,
legal, source-rights, and governance sign-off.
