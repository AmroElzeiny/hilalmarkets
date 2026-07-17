# Closed-Beta Launch Checklist

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
| Invoice/payment history and provider-accurate checkout/email | IMPLEMENTED | CI PENDING | STAGING PENDING | Sandbox event matrix |
| Explicit first-owner governance grants | IMPLEMENTED | CI PENDING | STAGING PENDING | Run bootstrap CLI and retain audit event |

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
13. Complete one NOWPayments sandbox payment, replay its IPN, test a distinct repeated deposit, and
    verify exactly one entitlement transition and one logical payment email.
14. Trigger a material source change, safety hold, review, superseding publication, and restore.
15. Verify the old alert still opens its original Passport while current status is shown separately.
16. Exercise stale source, unavailable source, provider timeout, DB timeout, Redis loss, and restart.
17. Run desktop/mobile browser smoke and capture 1440, 1024, 768, and 390 pixel evidence.
18. Run a seven-day schedule/alert soak and verify no duplicate jobs, journeys, alerts, or emails.

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
- [ ] NOWPayments sandbox catalog matches the public Plan Catalog.
- [ ] GitHub branch protection requires every Release Gate job.
- [ ] Legal, source-rights, religious-governance, privacy, refund, and incident policies are approved.

## Usability Study

Pending: test with at least ten target users and record whether each person can distinguish Screened
Market, My Screened Watchlist, and Watch Plan; understand a Passport; activate a Watch Plan without
help; test Telegram; and explain why an alert did or did not occur.

## Release Decision

**Not ready for external beta yet.** Repository blockers have been substantially corrected, but a
post-change green CI run, the staging pilot, external delivery/payment/edge tests, seven-day soak,
ten-user usability study, and accountable owner/legal/governance approvals remain mandatory.
