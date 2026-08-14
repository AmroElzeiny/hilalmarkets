# Hilal Markets Private-Beta Readiness Report

> **ARCHIVAL — 17 July 2026. Superseded by `docs/RELEASE_READINESS_REPORT.md` (14 August 2026).**
>
> Kept as the record of what was true in July. Its **verdict still stands** — the product is not
> ready for external private beta — but three things in it are no longer accurate at HEAD, and it
> was linked from README as the *current* status until 14 August 2026:
>
> | It says | True at HEAD |
> |---|---|
> | The virtual environment cannot run; only Python 3.11 is present | `.venv` runs Python 3.12.0. Ruff, MyPy, Alembic and pytest all execute. |
> | 33 migration revisions, head `1acbd2e3f405` | 57 revisions, one head `9d21c4e75f80` |
> | `AI_AGENT_CONTROL_ENABLED=true` … "remains the immediate kill switch" (section 9) | It is `false` in production and has no authority over Setup Chat at all. See `docs/OPERATIONS.md`, "Stopping Setup Chat". |
>
> Nothing below has been edited. Read it as history.

Date: 2026-07-17

Scope: repository correction and local verification only

Decision: **NOT READY FOR EXTERNAL PRIVATE BETA**

This report separates repository implementation, local/static verification, CI evidence, staging
evidence, production configuration, and accountable human approval. A code path or fake adapter is
not treated as proof that an external system works.

## 1. Initial Failures And Root Causes

The first full local pytest baseline reached:

- `1970 passed`
- `6 failed`
- `18 errors`
- `2 warnings`

The six production-contract failures were:

1. My Screened Watchlist copy had diverged from the customer-facing contract.
2. An unpublished Passport could be reported as `assessment_not_found` instead of the accurate
   `passport_not_published` state.
3. Opportunity test evidence omitted required `data_freshness_ms`.
4. A selected live methodology test did not isolate the requested methodology correctly.
5. A local screening fixture inherited a default methodology and leaked unintended authority.
6. Observability handling treated null freshness as usable retained evidence.

Those six cases were corrected and their focused rerun passed (`6 passed`). The 18 errors were
Playwright setup errors because Chromium was not installed; they were not browser assertion
failures.

After further implementation, the repository virtual environment stopped being runnable because
`.venv/pyvenv.cfg` points to a removed Python 3.12 installation. This workstation currently exposes
only Python 3.11, while `pyproject.toml` requires Python 3.12. Node is absent, and Docker Engine
access is denied from this session. Consequently, no post-patch full-suite result is claimed.

## 2. Files And Migration Changed

The implementation is grouped as follows; Appendix A contains the exact working-tree inventory.

- Beta configuration/release gate: environment examples, release workflow, release invariant
  checker, runtime configuration, plan exposure, startup, route guards, and operations docs.
- Public product chat: API router, schemas, service, database models, migration, worker tasks,
  landing partial, CSS, JavaScript, and unit/integration/browser tests.
- Internal Setup Chat: configured model router, structured intent state, clause coverage, feedback,
  shadow telemetry, System Brain reporting, language corpus, and tests.
- Product scope: active Discord routes/services/tests/docs removed; billing and WhatsApp hidden and
  disabled; Telegram/in-app retained.
- Existing beta contract corrections: Screened Market/Passport error semantics, Opportunity
  freshness, Watch Plan/Watchlist wording, channel filtering, and relevant tests.
- Operations: private-beta soak audit/runbook, deployment/security documentation, and archival
  notices on old reports that describe retired product behavior.

Migration `1acbd2e3f405_add_public_product_chat.py` follows `09bac1d2e3f4` and creates:

- `public_chat_answer_events`
- `public_inquiries`
- `public_inquiry_email_deliveries`
- `public_inquiry_ratings`

The statically reconstructed migration graph contains 33 revisions and one head:
`1acbd2e3f405`. A real Alembic/PostgreSQL upgrade was not possible in this session.

## 3. Tests And Exact Results

### Executed before the runtime broke

| Check | Result |
|---|---|
| Initial full pytest baseline | 1970 passed, 6 failed, 18 errors, 2 warnings |
| Focused correction rerun | 6 passed |

### Executed after the latest patch

| Check | Result |
|---|---|
| Syntax compile of all 69 changed/new Python files using the available Python 3.11 parser | PASS |
| `git diff --check` | PASS; line-ending warnings only |
| Static migration graph reconstruction | PASS; one head `1acbd2e3f405` |
| Production example locked-profile audit | PASS; 19/19 values match |
| `.env.example` / `.env.production.example` key parity | PASS; 250/250 keys, no omissions |
| Active Discord scan across API/templates/static/Telegram/worker/main | PASS; zero references |
| Deprecated Watchlist-as-strategy phrase scan | PASS; zero references |
| Tracked generated/runtime artifact scan | PASS; zero forbidden paths among 4030 tracked files |
| High-confidence tracked OpenAI/Telegram/Resend/private-key pattern scan | PASS; zero matches |
| Language corpus JSONL parse | PASS; 18 cases across 6 language labels |

### Required but not executable after the latest patch

- Full pytest
- Full Playwright/Chromium
- Ruff
- MyPy over `src/ai_market_monitor`
- JavaScript validation
- Jinja loading
- Imported API route-security audit
- Imported release invariant audit
- Alembic CLI one-head/check/upgrade
- Clean PostgreSQL upgrade and previous-revision upgrade
- Dependency audit
- Gitleaks
- Container build and Trivy scan

Exact machine error: `.venv\Scripts\python.exe --version` returns
`No Python at 'C:\Users\amroe\AppData\Local\Programs\Python\Python312\python.exe'`.
Docker reports denied access to `npipe:////./pipe/docker_engine`; Node is not installed.

## 4. GitHub Release Gate Status

`.github/workflows/release-gate.yml` is configured with four jobs:

1. `backend-quality`
2. `browser`
3. `dependency-and-secret-scan`
4. `container-scan`

It uses Python 3.12, PostgreSQL 16, Redis 7, Node 22, full backend pytest, full browser pytest,
Ruff, MyPy, dependency lock/pip checks, route security, release invariants, Jinja/JavaScript checks,
pip-audit, Gitleaks, a runtime image build, and Trivy. The migration step upgrades first to the
actual previous revision `09bac1d2e3f4`, then to head.

No GitHub Actions run was inspected or executed. CI is **configured, not verified green**. Branch
protection must require all four jobs above before merge.

## 5. Feature Readiness Matrix

| Feature | Implemented | CI verified | Staging verified | Production configured | Pending external action |
|---|---:|---:|---:|---:|---|
| BTC/ETH/SOL Binance-spot beta scope | Yes | No | No | Example only | Publish inspected pilot Passports |
| Single screened-universe execution boundary | Preserved | No | No | Example only | Live parity drill |
| Human criterion review and separate publication | Preserved | No | No | No | Owner governance run |
| Immutable Passport/history behavior | Preserved | No | No | No | Historical alert drill |
| Watch Plans and Screened Watchlist terminology | Yes | No | No | N/A | Ten-user study |
| Opportunity evidence/readiness | Corrected | No | No | N/A | Provider/restart soak |
| Telegram and in-app beta delivery | Yes | No | No | Credentials pending | Controlled delivery matrix |
| Bounded Agent shadow-only operation | Yes | No | No | Example locked | Review shadow metrics |
| Adaptive Setup Chat model routing | Yes | No | No | Example configured | Quality/cost review |
| Structured intent and clause coverage | Yes | No | No | N/A | Multilingual review |
| Public grounded product assistant | Yes | No | No | Example enabled | Browser/privacy/content review |
| Public inquiry and two-recipient outbox | Yes | No | No | SMTP pending | Controlled SMTP retry test |
| Discord retirement | Active surface removed | No | No | Example has no keys | Historical DB inventory |
| Billing beta disable | Yes | No | No | Example disabled | None for beta |
| WhatsApp beta disable | Yes | No | No | Example disabled | Meta/legal work after beta |
| Seven-day duplicate audit | Tooling only | No | No | No | Execute day-zero/day-seven runbook |
| Cloudflare/origin controls | Docs only | N/A | No | No | Configure and attack-test |

## 6. Screened Market And Passport Corrections

- `ShariaUniverseResolver` remains the screened-universe execution boundary.
- Production examples disable mocks, fixture market data, and test-market exposure.
- Pilot symbols are explicitly `BTC,ETH,SOL`; exchange and provider are Binance/CCXT spot.
- Unpublished evidence now has a distinct `passport_not_published` outcome.
- Local test fixtures no longer inherit a methodology implicitly where that would grant authority.
- Opportunity evidence requires an explicit freshness value rather than treating null as fresh.
- Eligible assets without exact active spot mapping remain non-executable.
- Existing immutable historical Passport references were not replaced or rewritten.

Real BTC/ETH/SOL source import, human review, publication, Passport inspection, mapping inspection,
provider outage, delisting, quote migration, and historical alert drills remain staging work.

## 7. Watch Plans, Watchlist, Scanner, Cards, And Journeys

- Customer wording distinguishes Watch Plans from My Screened Watchlist.
- Saved-asset removal uses server-authoritative affected-plan discovery and confirmation behavior.
- Check the Market Now and persistent monitoring retain deterministic compilation and resolver
  boundaries; no LLM was added to scheduled evaluation.
- Opportunity cards use retained condition evidence and explicit freshness.
- Journey and alert identity/idempotency mechanisms are preserved.
- `scripts/audit_private_beta_soak.py` provides a read-only duplicate audit for scheduler slots,
  scan results, journeys, alerts, payment-email events, and public-inquiry email events.

Frozen-candle scanner/worker parity, corrected candles, restart behavior, and the seven-day soak
must still be executed in staging.

## 8. Compliance Watch And Telegram

- Compliance-change calculations, historical Passport links, admin/customer paths, retries, and
  Telegram delivery infrastructure remain in place.
- Private-beta UI and product content expose in-app and Telegram only.
- No fake adapter is reported as live proof.

Still pending: controlled material-source change, safety hold, affected-plan calculation, customer
notification, review, superseding publication, restoration, rate-limit/retry/permanent-failure
matrix, and one real staging Telegram delivery.

## 9. Internal AI Setup Chat And Live Bounded Agent

- `AI_AGENT_CONTROL_ENABLED=true`, `AI_AGENT_SHADOW_MODE=false`,
  `AI_AGENT_ROLLOUT_PERCENT=100`, and `CAPABILITY_EXTENSION_ENABLED=true` are the controlled-beta
  release profile. `AI_AGENT_CONTROL_ENABLED=false` remains the immediate kill switch.
- Agent proposals remain non-authoritative; deterministic compiler, registry, provider gates,
  canonical hash, approval, activation, and evaluation remain authoritative.
- `AISetupModelRoute` selects only configured simple/complex models and efforts.
- Complex routing reasons include condition count, mixed logic, multiple timeframes,
  contradictions, repeated corrections, clarification friction, low capability confidence,
  custom terminology, and multilingual input.
- Routing metadata is persisted with usage and surfaced in System Brain.
- Structured intent records confirmed/rejected requirements, required/optional rules, timeframe,
  universe, timing, invalidation, delivery, unresolved conflicts, corrections, and message refs.
- Clause coverage labels covered, clarification-required, provider-unsupported, intentionally
  optional, and non-executable clauses; blocking losses prevent approval.
- Interpretation feedback is auditable and never auto-promotes a production capability.
- The reviewed corpus contains 18 cases across English, Arabic, Egyptian Arabic, Arabizi,
  mixed Arabic/English, and common misspellings. Three clear English cases are deterministic;
  the multilingual/typo cases still require live-provider staging evidence. Local fake-provider
  tests are not presented as semantic-accuracy proof.

## 10. Public Landing-Page Chatbot Architecture And UX

The public assistant is separate from authenticated Setup Chat and exposes no strategy, account,
scan, approval, activation, market, filesystem, or arbitrary network tool.

Implemented UX:

- fixed branded launcher with reduced-motion behavior;
- desktop panel and full-screen mobile layout;
- X, Escape, focus trap, focus return, sticky composer, new conversation, retry, offline state,
  loading, error, and success states;
- first-use name/email validation;
- Functional-consent device memory with version, consent version, and timestamp;
- session-only profile persistence when Functional consent is absent;
- explicit Forget action clearing both session and device storage;
- grounded answer links, one answer-feedback bar, explicit user-controlled Support form,
  masked-email success, rating, Cancel, and another-question flow.

No transcript, inquiry, token, or authentication state is written to browser profile storage.

## 11. Public Knowledge Sources And Grounding

`PublicKnowledgeService` builds its catalog from server-owned public content:

- `PUBLIC_PAGES`
- `HELP_CATEGORIES`
- `PURCHASE_FAQS`
- `PLAN_DEFINITIONS` constrained by public plan allowlists
- versioned product-boundary and private-beta scope entries
- bounded context-only retrieval from project-owned Notion exports

Answers record source IDs and coverage; links are resolved from server-owned route IDs. Advice,
religious rulings, secret requests, prompt injection, and private account lookup fail closed.
Unsupported or unavailable facts remain transparent chat responses. Only the visitor's explicit
No choice or contact request can start the inquiry flow. Notion snippets cannot prove current
product state. There is no external browsing and no model-created URL or authoritative market value.

## 12. Inquiry, Email, Rating, Consent, And Security

- Public profile is validated/normalized without authentication or server persistence.
- Deployed startup rejects an enabled public chat unless a real, non-placeholder SMTP adapter,
  host, username, password, sender, and office destination are configured.
- Mutations require an anonymous same-site CSRF boundary and reject foreign origins.
- Public chat has a separate rate-limit bucket and bounded lengths.
- Inquiry honeypot, Pydantic extra-field rejection, text sanitization, stable reference, timestamp,
  category, source page, bounded attribution, and server-owned answer metadata are implemented.
- One session-bound feedback record is allowed per answer event. The inquiry endpoint requires a
  negative feedback choice and binds the resulting inquiry back to that exact answer.
- Referrer and UTM attribution require an explicit analytics-consent flag and are cleared again by
  server validation when consent is absent; client code alone cannot override that rule.
- Raw visitor HTML is escaped in email; raw model prompts, stack traces, and secrets are excluded.
- Exactly two durable logical email rows are created: customer confirmation and
  `office@hilalmarkets.com`; unique event keys prevent duplicate logical work and recorded-success
  rows are never resent.
- Retry state, bounded attempts, provider message ID, redacted error, and abandoned-claim recovery
  are persisted.
- One token-bound rating is retained per inquiry; retries return the original rating.
- Token-bound immediate redaction and scheduled retention cleanup are implemented.
- Answer telemetry stores hashes/source IDs/outcome rather than raw questions.

SMTP deliverability, SPF/DKIM/DMARC, provider retry, and real office/customer receipt remain
staging/production checks. SMTP has an unavoidable ambiguity if a process dies after the provider
accepts a message but before the database records success. The stable Message-ID helps provider
deduplication, but strict provider-level exactly-once delivery requires a provider API with an
idempotency contract and remains an acceptance dependency.

## 13. Discord Removal Inventory

Removed active surfaces:

- Discord API router, callback/interaction routes, HTTP gateway, service, types, and package init;
- router registration and startup validation;
- dashboard/public cards, settings, onboarding and delivery choices;
- active worker/provider behavior and active product documentation;
- Discord interaction/service tests and dedicated setup/UX docs.

Retained only for backward compatibility:

- historical database models/tables and enum values;
- legacy fields needed to load immutable strategy/delivery/audit history;
- dated implementation reports, now marked archival;
- schema compatibility for old snapshots, with current entitlement validation rejecting new
  Discord activation as `delivery_channel_retired`.

The active executable/UI scan currently returns zero Discord references. Physical removal of
historical tables/enums requires a production-data inventory and separate migration.

## 14. Billing And WhatsApp Beta Disable

- Public Pricing exposes only free invite-only beta access while billing is disabled.
- Checkout/portal mutations fail closed and provider webhooks are not exposed in disabled mode.
- Provider-accurate billing code/tests are retained for a later separately approved launch.
- WhatsApp routes/cards/opportunity delivery remain inaccessible while both WhatsApp flags are
  false; credentials/templates remain empty in examples.
- No billing or WhatsApp availability is promised to beta users.

Payment sandbox, catalog, refunds, Meta Business verification, WABA, phone registration, signed
webhook, approved templates, consent/legal review, and live delivery are post-beta dependencies.

## 15. Infrastructure Controls: Completed Versus Pending

Repository-complete:

- release workflow and static release invariants;
- secure production-example defaults;
- worker tasks for public email retries/retention;
- soak audit and runbook;
- deployment, rollback, backup/restore, Cloudflare, queue/health, and incident instructions.

Not executed:

- Cloudflare Access/Tunnel/firewall configuration;
- spoofed-header, alternate-hostname, and origin-IP bypass tests;
- encrypted PostgreSQL backup and separate restore drill;
- worker/scheduler/Redis/provider fault drills;
- external metrics/alerts;
- seven-day soak;
- rollback exercise.

## 16. Desktop And Mobile Screenshots

Playwright is configured to generate landing evidence at:

- `reports/playwright/visual-qa/hilalmarkets-landing-1440.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-1024.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-768.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-390.png`

Public-chat evidence is configured at:

- `reports/playwright/visual-qa/public-chat/public-chat-desktop-1440.png`
- `reports/playwright/visual-qa/public-chat/public-chat-mobile-390.png`

These post-patch screenshots were **not generated** because Playwright could not run. Existing
older images are not accepted as proof of this implementation.

## 17. Private-Beta Acceptance Decision

**Do not invite external beta users yet.** The code correction is substantial and the intended
scope is fail-closed, but the definition of done explicitly requires a green full repository suite,
green browser suite, green MyPy/Ruff/security/migration/container checks, staging pilot evidence,
controlled external delivery, and seven-day soak evidence. None may be inferred from syntax checks.

Minimum next gate:

1. Recreate a Python 3.12 virtual environment and install locked dev dependencies.
2. Run the four Release Gate jobs locally where practical and then on GitHub.
3. Require all four checks in branch protection.
4. Complete the disposable-staging workflow in `docs/LAUNCH_CHECKLIST.md`.
5. Run the seven-day soak and retain day-zero/day-seven JSON evidence.
6. Obtain owner, legal, privacy, source-rights, and religious-governance approvals.

## 18. Remaining Dependencies

- **Legal/privacy:** public inquiry retention, consent language, privacy notice, risk disclosure,
  incident handling, and cross-border processor review.
- **Source rights:** authority, license, snapshot retention, attribution, and change-monitoring rights
  for every official source.
- **Governance:** owner grants, explicit criterion/use decisions, separate approval/publication,
  correction policy, and pilot Passport sign-off.
- **OpenAI:** configured model availability, pricing confirmation, data-processing settings, shadow
  quality/cost review, and no-live-agent approval until evidence is accepted.
- **Email:** production sender/domain, SPF, DKIM, DMARC, suppression/bounce handling, customer and
  office delivery proof.
- **Telegram:** webhook/polling mode decision, secret, test account, timeout/rate-limit/retry proof.
- **Cloudflare:** Access, Tunnel/firewall, direct-origin denial, host/header bypass testing.
- **Market provider:** Binance spot availability, canonical mapping, stale/outage behavior, and
  controlled BTC/ETH/SOL provider tests.
- **Operations:** PostgreSQL backup/restore, Redis/worker/scheduler monitoring, queue and latency
  alerts, fail-closed exclusion alerts, incident drill, rollback drill, and seven-day soak.
- **Deferred commercial channels:** payment-provider sandbox acceptance and all Meta/WhatsApp work.

## Appendix A: Exact Working-Tree Inventory

Status codes: `M` modified, `D` removed, `A` added (currently untracked before commit).

```text
M .env.example
M .env.production.example
M .github/workflows/release-gate.yml
M README.md
M docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md
M docs/ARCHITECTURE.md
D docs/DISCORD_SETUP.md
M docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md
M docs/HILALMARKETS_UI_MIGRATION.md
M docs/LAUNCH_CHECKLIST.md
M docs/LAUNCH_READINESS_CORRECTION_REPORT.md
M docs/LIFECYCLE_INVESTIGATION_AND_MONITOR_NAMING_REPORT.md
M docs/OPERATIONS.md
M docs/PLATFORM_RESPONSIBILITIES.md
M docs/PLAYWRIGHT_E2E.md
M docs/PRODUCTION_DEPLOYMENT.md
M docs/ROADMAP.md
M docs/SECURITY_CHECKLIST.md
M docs/SETUP_OBSERVABILITY_IMPLEMENTATION_REPORT.md
M docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md
M docs/VERIFIED_STRATEGY_MONITORING_IMPLEMENTATION_REPORT.md
M docs/WORKERS.md
M docs/condition-capability-registry.md
M docs/dashboard-ux.md
D docs/discord-ux.md
M docs/implementation-reports/20260717T045512Z_WHATSAPP_CLOUD_API_IMPLEMENTATION_REPORT.md
M docs/setup-replay.md
M scripts/check_release_invariants.py
M src/ai_market_monitor/api/request_guards.py
M src/ai_market_monitor/api/routers/__init__.py
M src/ai_market_monitor/api/routers/billing.py
M src/ai_market_monitor/api/routers/dashboard.py
M src/ai_market_monitor/api/routers/dashboard_api.py
D src/ai_market_monitor/api/routers/discord.py
M src/ai_market_monitor/api/routers/public.py
M src/ai_market_monitor/cockpit_api.py
M src/ai_market_monitor/core/config.py
M src/ai_market_monitor/core/plans.py
M src/ai_market_monitor/core/platforms.py
M src/ai_market_monitor/core/site_content.py
M src/ai_market_monitor/core/startup.py
M src/ai_market_monitor/db/models/__init__.py
D src/ai_market_monitor/discord/__init__.py
D src/ai_market_monitor/discord/http_gateway.py
D src/ai_market_monitor/discord/service.py
D src/ai_market_monitor/discord/types.py
M src/ai_market_monitor/engine/concept_e2e.py
M src/ai_market_monitor/main.py
M src/ai_market_monitor/schemas/onboarding.py
M src/ai_market_monitor/schemas/sharia.py
M src/ai_market_monitor/services/admin_dashboard.py
M src/ai_market_monitor/services/agent_control.py
M src/ai_market_monitor/services/ai_setup_chat.py
M src/ai_market_monitor/services/billing.py
M src/ai_market_monitor/services/compliance_watch.py
M src/ai_market_monitor/services/entitlements.py
M src/ai_market_monitor/services/notification_preferences.py
M src/ai_market_monitor/services/notifications.py
M src/ai_market_monitor/services/onboarding.py
M src/ai_market_monitor/services/openai_interpreter.py
M src/ai_market_monitor/services/security_review.py
M src/ai_market_monitor/services/setup_observability.py
M src/ai_market_monitor/services/sharia_passports.py
M src/ai_market_monitor/services/support.py
M src/ai_market_monitor/services/system_brain.py
M src/ai_market_monitor/services/verified_strategy.py
M src/ai_market_monitor/static/ai-setup-chat.css
M src/ai_market_monitor/static/ai-setup-chat.js
M src/ai_market_monitor/static/dashboard.js
M src/ai_market_monitor/static/styles.css
M src/ai_market_monitor/strategy_cockpit.py
M src/ai_market_monitor/telegram/service.py
M src/ai_market_monitor/templates/hilal/base_public.html
M src/ai_market_monitor/templates/hilal/dashboard/billing.html
M src/ai_market_monitor/templates/hilal/dashboard/home.html
M src/ai_market_monitor/templates/hilal/dashboard/integrations.html
M src/ai_market_monitor/templates/hilal/dashboard/partials/strategy_detail_content.html
M src/ai_market_monitor/templates/hilal/dashboard/settings.html
M src/ai_market_monitor/templates/hilal/dashboard/watchlist.html
M src/ai_market_monitor/templates/hilal/public/index.html
M src/ai_market_monitor/templates/hilal/public/partials/pricing_cards.html
M src/ai_market_monitor/templates/hilal/public/pricing.html
M src/ai_market_monitor/templates/hilal/public/privacy.html
M src/ai_market_monitor/worker.py
M tests/browser/conftest.py
M tests/browser/test_dashboard_e2e.py
M tests/conftest.py
M tests/integration/test_admin_status_api.py
M tests/integration/test_api_route_ownership.py
M tests/integration/test_checkout_and_payment_email.py
M tests/integration/test_dashboard_api.py
M tests/integration/test_dashboard_web.py
D tests/integration/test_discord_interactions.py
D tests/integration/test_discord_service.py
M tests/integration/test_hilal_public_site.py
M tests/integration/test_platform_handoffs.py
M tests/integration/test_whatsapp_integration.py
M tests/services/test_sharia_screening.py
M tests/unit/test_ai_setup_chat.py
M tests/unit/test_billing_entitlements.py
M tests/unit/test_dashboard_static_assets.py
M tests/unit/test_identity.py
M tests/unit/test_reliability_security.py
M tests/unit/test_request_guards.py
M tests/unit/test_setup_observability.py
M tests/unit/test_support_escalation.py
M tests/unit/test_system_brain.py
A alembic/versions/1acbd2e3f405_add_public_product_chat.py
A docs/PRIVATE_BETA_READINESS_REPORT.md
A docs/PRIVATE_BETA_SOAK_RUNBOOK.md
A docs/RETIRED_DISCORD_COMPATIBILITY.md
A scripts/audit_private_beta_soak.py
A src/ai_market_monitor/api/routers/public_chat.py
A src/ai_market_monitor/db/models/public_chat.py
A src/ai_market_monitor/schemas/public_chat.py
A src/ai_market_monitor/services/ai_model_routing.py
A src/ai_market_monitor/services/public_chat.py
A src/ai_market_monitor/static/hilalmarkets-public-chat.css
A src/ai_market_monitor/static/hilalmarkets-public-chat.js
A src/ai_market_monitor/templates/hilal/partials/public_chat.html
A tests/fixtures/setup_chat_language_quality_corpus.jsonl
A tests/integration/test_public_chat_api.py
A tests/unit/test_ai_model_routing.py
A tests/unit/test_public_chat.py
A tests/unit/test_setup_chat_language_quality.py
```
