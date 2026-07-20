# ⚙️ Operations and Launch Playbook

> **Workspace status:** Updated 17 July 2026. Product name: **HilalMarkets**.

## Launch stages

### Stage 0 — Software complete, screening closed
- Deploy the HilalMarkets application.
- Keep screening enforcement fail closed.
- No customer-visible eligible assets without publication.
- Validate authentication, dashboard, billing, and provider health.

### Stage 1 — Governance pilot
- Approve one real methodology.
- Import and review a small pilot set one asset at a time.
- Test identity, evidence, AI factual dossier, review, publication, Passport, and universe invalidation.
- Verify reviewer notification and System Brain workflow.

### Stage 2 — Private beta
- Invite a limited guided active-investor group.
- Require explicit feedback and monitoring.
- Cap Watchlists and asset universe.
- Run support and incident playbooks.
- Measure activation and retention.

### Stage 3 — Paid beta
- Turn on verified provider checkout.
- Test payment, renewal, cancellation, failed payment, downgrade, payment email, and support.
- Keep controlled acquisition.

### Stage 4 — Public launch
- Open registration only after reliability and governance gates are met.
- Expand search acquisition and partner distribution gradually.

## Golden path

1. Create account.
2. Verify email.
3. Review risk and service boundary.
4. Open Screened Market.
5. Open Passport.
6. Create or select Watchlist behavior.
7. Resolve screening policy and eligible universe.
8. Approve mechanics.
9. Check the Market Now.
10. Connect Telegram.
11. Send test alert.
12. Activate Watchlist.
13. Complete first scheduled scan.
14. Create an Opportunity Journey.
15. Open proof.
16. Run a missed-alert investigation.
17. Trigger controlled compliance change.
18. Verify affected asset behavior and drift delivery.
19. Complete purchase and successful-payment email.
20. Pause, downgrade, cancel, and restore safely.

## Daily operations

- API, database, Redis, worker, and scheduler health.
- Scan queue, stale jobs, and failed symbols.
- Market-data quality and provider incidents.
- Telegram/Discord/email delivery retries.
- Source monitoring and changed evidence.
- Review queue, overdue cases, and publication failures.
- Billing webhooks and checkout/email failures.
- Support tickets and high-severity user impact.

## Weekly operations

- User activation and retention review.
- Review SLA and asset evidence freshness.
- Open incidents and postmortems.
- Support themes and onboarding friction.
- Experiment decision.
- Partner and accelerator pipeline.
- Security and dependency review.

## Production checklist

- [ ] HilalMarkets.com and app domain configured
- [ ] SSL, Cloudflare, and origin firewall validated
- [ ] PostgreSQL backup and restoration tested
- [ ] Single Alembic head and migration rehearsal
- [ ] Real methodology and reviewed pilot assets
- [ ] Screening enforcement enabled; legacy unscreened disabled
- [ ] Worker and scheduler tasks registered
- [ ] Market-data provider staging smoke
- [ ] Telegram test
- [ ] Discord test where enabled
- [ ] SMTP test
- [ ] Stripe/NOWPayments sandbox test for chosen provider
- [ ] Consent and GTM validated
- [ ] Search Console and sitemap submitted
- [ ] Terms, privacy, cookies, and risk reviewed
- [ ] Support route, response time, and escalation ready
- [ ] Rollback rehearsed

## Incident severity

- **P0:** wrong public screening status, cross-tenant exposure, unauthorized admin action, credential leak, or widespread incorrect alert evidence.
- **P1:** screening unavailable, large scan outage, delivery outage, billing entitlements wrong, or material source-monitoring failure.
- **P2:** partial provider degradation, individual Watchlist failures, slow review, or non-critical UI failure.
- **P3:** cosmetic, copy, or isolated low-impact issue.

Every incident should record impact, timeline, evidence, root cause, correction, user communication, and prevention.
