# Private-Beta Launch Checklist

Last corrected: 2026-07-17

This checklist separates repository implementation from CI, staging, production configuration,
and owner/governance approval. A checked code path is not evidence that an external system works.

Status vocabulary:

- `IMPLEMENTED`: present in the repository with deterministic tests or static checks.
- `PARTIAL`: an important part exists, but a required implementation or proof artifact is missing.
- `CI PENDING`: the release workflow exists but has not completed successfully on GitHub.
- `STAGING PENDING`: requires the staging PostgreSQL/provider/network environment.
- `PRODUCTION PENDING`: requires production infrastructure or credentials.
- `OWNER REQUIRED`: requires an accountable human decision or recorded sign-off.

## P0 Release Gates

| Gate | Repository | CI | Staging | Production/owner |
|---|---|---|---|---|
| Explicit decision for every required methodology criterion | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| No approval-grade criterion defaults | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Evidence completeness, age, missing-field, contradiction, and cadence gates | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Methodology-neutral review/publication with SC-specific import adapter | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Active methodology contract validated at creation and execution | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Immutable approved review, publication, Passport, universe, alert, and proof records | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Every user-owned API authenticated and ownership-scoped or explicitly public/signed | IMPLEMENTED | CI PENDING | STAGING PENDING | PRODUCTION PENDING |
| Auth/AI/market/billing/support/Passport/Telegram/admin rate limits | IMPLEMENTED | CI PENDING | STAGING PENDING | PRODUCTION PENDING |
| Same-origin cookie mutation protection and route-specific CSRF | IMPLEMENTED | CI PENDING | STAGING PENDING | PRODUCTION PENDING |
| Public plan allowlist excludes internal, lifetime, trial, creator, and admin plans | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| NOWPayments text and entitlement behavior reflect one-time 30-day access | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Payment amount, currency, plan, attempt, provider, and duplicate checks | IMPLEMENTED | CI PENDING | STAGING PENDING | STAGING PENDING |
| GitHub Actions release gate | IMPLEMENTED | CI PENDING | N/A | Branch protection pending |
| Resolved transitive dependency lock | PARTIAL | CI PENDING | N/A | Generate and review a Python 3.12 lock artifact |
| Generated/runtime artifacts absent from Git index | IMPLEMENTED | CI PENDING | N/A | N/A |
| Initial beta profile is BTC/ETH/SOL, Binance spot, invite-only/free | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Bounded Agent is shadow-only with a zero-percent live cohort | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Billing, WhatsApp, and capability extensions are disabled | IMPLEMENTED | CI PENDING | STAGING PENDING | OWNER REQUIRED |
| Active Discord product/API/worker/UI surfaces are removed | IMPLEMENTED | CI PENDING | STAGING PENDING | Historical data inventory before physical DB removal |
| Public product chatbot is grounded, rate-limited, and non-executing | IMPLEMENTED | CI PENDING | STAGING PENDING | Privacy and owner content review |
| Public inquiry outbox sends one customer and one office event | IMPLEMENTED | CI PENDING | STAGING PENDING | Controlled SMTP delivery proof |
| Enabled deployed public chat refuses missing/fake SMTP configuration | IMPLEMENTED | CI PENDING | STAGING PENDING | Replace example placeholders and verify provider |

## Product Acceptance

| Capability | Repository | CI | Staging | Remaining acceptance evidence |
|---|---|---|---|---|
| Screened Market uses `ShariaUniverseResolver` execution policy | IMPLEMENTED | CI PENDING | STAGING PENDING | BTC/ETH/SOL live-provider matrix |
| Production hides development/test methodologies | IMPLEMENTED | CI PENDING | STAGING PENDING | Inspect deployed database and pages |
| Production screener requires active published Passports | IMPLEMENTED | CI PENDING | STAGING PENDING | Inspect three pilot Passports |
| Exact canonical asset and exchange market mapping | IMPLEMENTED | CI PENDING | STAGING PENDING | Delisting/migration/quote-change drill |
| Passport use coverage is asset-specific and reviewer-approved | IMPLEMENTED | CI PENDING | STAGING PENDING | Qualified reviewer inspection |
| Historical alert opens the frozen Passport version | IMPLEMENTED | CI PENDING | STAGING PENDING | Historical alert browser drill |
| Watch Plan and My Screened Watchlist terminology is distinct | IMPLEMENTED | CI PENDING | STAGING PENDING | Ten-user comprehension study |
| Saved-asset removal lists affected non-archived Watch Plans first | IMPLEMENTED | CI PENDING | STAGING PENDING | Mobile/desktop browser QA |
| Check the Market Now and worker use shared compiler/resolver | IMPLEMENTED | CI PENDING | STAGING PENDING | Frozen-candle parity test expansion |
| Opportunity cards use stored condition evidence | IMPLEMENTED | CI PENDING | STAGING PENDING | Corrected-candle and missing-evidence drill |
| Opportunity Journey state transitions are durable/idempotent | PARTIAL | CI PENDING | STAGING PENDING | Seven-day duplicate/restart soak |
| Compliance drift payload contains status, reason, action, plans, and Passport | IMPLEMENTED | CI PENDING | STAGING PENDING | Controlled Telegram delivery matrix |
| Methodology comparison hidden below two approved assessments | IMPLEMENTED | CI PENDING | STAGING PENDING | Browser check with one/two methods |
| Paid billing remains provider-accurate but inaccessible in beta | IMPLEMENTED | CI PENDING | STAGING PENDING | Billing is not a beta acceptance dependency |
| Explicit first-owner governance grants | IMPLEMENTED | CI PENDING | STAGING PENDING | Run bootstrap CLI and retain audit event |
| AI Setup Chat structured intent and clause coverage | IMPLEMENTED | CI PENDING | STAGING PENDING | Reviewed multilingual shadow corpus |
| Adaptive model routing uses configured tiers and records telemetry | IMPLEMENTED | CI PENDING | STAGING PENDING | Cost/latency/quality review before any live-agent cohort |
| Public profile consent, session fallback, inquiry redaction, and retention | IMPLEMENTED | CI PENDING | STAGING PENDING | Browser/privacy review |

## Required Staging Drill

Run these against a disposable staging environment and retain command output, timestamps, actor,
database backup identifier, evidence hashes, screenshots, provider IDs, and incident notes.

1. Stop API scanning, worker, and scheduler writes.
2. Create an encrypted PostgreSQL custom-format backup.
3. Restore it to a separate database and run read-only integrity queries.
4. Upgrade the restored previous revision to Alembic head.
5. Start API, Redis, database, worker, and scheduler; verify health and heartbeats.
6. Run `scripts/bootstrap_governance_owner.py` for the verified owner with a recorded reason.
7. Import real BTC, ETH, and SOL official rows and retain source snapshots/hashes.
8. Build factual dossiers; explicitly decide every criterion and use scope.
9. Record approval, then perform the separate publication action.
10. Inspect each current Passport and its exact canonical exchange mappings.
11. Run Check the Market Now and the worker against the same frozen candle snapshot.
12. Deliver one controlled Telegram test and verify one delivery record.
13. Open the public chatbot with and without Functional consent; verify grounded product answers,
    session/device profile behavior, an unsupported-question inquiry, and deletion/redaction.
14. Deliver one customer inquiry confirmation and one office copy through staging SMTP; force a
    retry and verify the same two logical event keys are not duplicated.
15. Trigger a material source change, safety hold, review, superseding publication, and restore.
16. Verify the old alert still opens its original Passport while current status is shown separately.
17. Exercise stale source, unavailable source, provider timeout, DB timeout, Redis loss, and restart.
18. Run desktop/mobile browser smoke and capture 1440, 1024, 768, and 390 pixel evidence.
19. Run the procedure in `docs/PRIVATE_BETA_SOAK_RUNBOOK.md` and retain day-zero/day-seven
    `scripts/audit_private_beta_soak.py` output showing no duplicate schedules, journeys, alerts,
    payment-email events, or public-inquiry email events.

No staging drill in this section has been executed by the 2026-07-17 local correction run.

## Infrastructure And External Controls

- [ ] Cloudflare Access policy protects `/system-brain*`.
- [ ] Cloudflare Tunnel or firewall prevents direct-origin access.
- [ ] Spoofed Access headers, alternate hostnames, and origin IP cannot bypass the edge.
- [ ] PostgreSQL backup retention and point-in-time recovery are configured.
- [ ] A restore drill records recovery time and recovery point.
- [ ] Redis, worker, scheduler, queue depth, API latency, and provider metrics are visible.
- [ ] Alerts exist for abnormal Sharia exclusions, stale sources, failed scans, and deliveries.
- [ ] SMTP sender passes SPF, DKIM, and DMARC checks.
- [ ] Telegram webhook secret and live test chat are configured.
- [ ] Before any post-beta billing launch, the selected provider sandbox catalog matches the public Plan Catalog.
- [ ] GitHub branch protection requires every Release Gate job.
- [ ] Legal, source-rights, religious-governance, privacy, refund, and incident policies are approved.

## Usability Study

Pending: test with at least ten target users and record whether each person can distinguish Screened
Market, My Screened Watchlist, and Watch Plan; understand a Passport; activate a Watch Plan without
help; test Telegram; and explain why an alert did or did not occur.

## Local Verification Snapshot

The first full local baseline on 2026-07-17 reached `1970 passed`, with six production-contract
failures and 18 browser setup errors. The six code failures were corrected and their focused rerun
passed. The browser errors were caused by a missing Playwright Chromium installation, not test
assertions.

### Corrected on 2026-08-12

The workstation blockers recorded above were re-measured and are **no longer true**. The
previous entry claimed the toolchain could not run; it can. Measured on 2026-08-12:

| Previous claim | Measured |
|---|---|
| `.venv` targets a removed Python 3.12 | `.venv/Scripts/python.exe` reports Python 3.12.0 |
| Only Python 3.11 is installed | 3.11, 3.12 and 3.13 are all installed |
| Node is not installed | Node v20.19.5 |
| Playwright Chromium missing | Chromium is installed; the browser suite runs |

What actually ran on 2026-08-12, at commit `afcf977f`, before any Phase 5 change:

| Command | Result |
|---|---|
| `ruff check src tests scripts` | All checks passed |
| `mypy src/ai_market_monitor` | Success, no issues in 284 source files |
| `pytest` (whole suite) | Every non-browser test passed |
| `pytest tests/browser` | **19 failures**, all in `tests/browser/` |

The 19 browser failures are a **pre-existing** condition of the repository, not a
consequence of Phase 5. They are real assertion failures, not a missing browser. This is
the first time they have been measured rather than assumed away as a setup problem.

Docker Engine access was not re-tested and remains unverified.

## Phase 5: operational truth and product boundaries

| Item | Repository | CI | Staging |
|---|---|---|---|
| One instrumentation layer; no per-router metric helpers | IMPLEMENTED | CI PENDING | STAGING PENDING |
| Metric labels are low-cardinality and refuse identifiers | IMPLEMENTED | CI PENDING | N/A |
| Secrets, prompts, model output and plan text cannot enter a record | IMPLEMENTED | CI PENDING | N/A |
| Eleven SLOs, every indicator computable from an emitted metric | IMPLEMENTED | CI PENDING | STAGING PENDING |
| Alerts bound to SLOs; page vs ticket is a field | IMPLEMENTED | CI PENDING | STAGING PENDING |
| No alert is delivered through the subsystem it watches | IMPLEMENTED | CI PENDING | Set the two destinations |
| Deduplicated operational issue queue with audit trail | IMPLEMENTED | CI PENDING | STAGING PENDING |
| One runbook section per alert, anchor-checked by a test | IMPLEMENTED | CI PENDING | N/A |
| Server-owned launch stage; PUBLIC_WAITLIST_MODE is a ceiling | IMPLEMENTED | CI PENDING | STAGING PENDING |
| Product boundary registry with explicit refusals | IMPLEMENTED | CI PENDING | Owner content review |
| Shariah spelling rule applied to customer copy | IMPLEMENTED | CI PENDING | N/A |
| Forbidden-claim copy lint in the release gate | IMPLEMENTED | CI PENDING | N/A |
| Status banners driven by the same signals as the alerts | IMPLEMENTED | CI PENDING | STAGING PENDING |
| og:image derives from PUBLIC_BASE_URL over HTTPS | IMPLEMENTED | CI PENDING | Verify with a real scraper |

## Phase 5 closeout: what the four gaps now do

| Item | Repository | CI | Staging |
|---|---|---|---|
| Measurements survive a restart and add up across processes | IMPLEMENTED | CI PENDING | STAGING PENDING |
| Concurrent writers cannot lose or double-count a measurement | IMPLEMENTED | CI PENDING | N/A |
| Stored measurements are rolled up and deleted on a schedule | IMPLEMENTED | CI PENDING | Watch the table size for a week |
| A page-worthy alert is actually sent | IMPLEMENTED | CI PENDING | Set the two destinations, then fire one |
| One firing rule pages once per repeat window, not once per tick | IMPLEMENTED | CI PENDING | Confirm over a real hour-long breach |
| A refused primary route falls back, and the row records it | IMPLEMENTED | CI PENDING | Break Telegram on purpose, confirm the email |
| Ticket-worthy alerts are queued and never delivered | IMPLEMENTED | CI PENDING | N/A |
| Narrowing the launch stage damages nothing a customer owns | IMPLEMENTED | CI PENDING | Run the drill on staging data |

**What still has to happen on staging.** Paging is not proved by a test. Two values have
to be set — `OPERATIONAL_ALERT_TELEGRAM_CHAT_ID` and `OPERATIONAL_ALERT_EMAIL` — and then
a real breach has to be caused on purpose and the message has to arrive on a phone. Until
somebody has watched that happen, the honest statement is that the code path runs, not
that the product pages.

Not delivered, and still required before external users:

- **Sending is not proved.** Every test uses a stub transport. No message has been
  delivered to a real Telegram chat or a real mailbox from this code.
- **No long-term metric store.** Three days of history in the product's own database is
  what exists. There is no dashboarding tool and no query language over it.
- **The scheduled tasks have not run under a real scheduler.** They are defined and
  scheduled and tested directly; Celery beat has not been observed running them.

## Release Decision

**Not ready for external private beta yet.** Repository blockers have been substantially corrected,
but a post-change green Release Gate run, the staging pilot, controlled SMTP and Telegram delivery,
edge tests, seven-day soak, ten-user usability study, and accountable owner/legal/governance
approvals remain mandatory. Payment-provider acceptance is deferred while beta billing is disabled.
