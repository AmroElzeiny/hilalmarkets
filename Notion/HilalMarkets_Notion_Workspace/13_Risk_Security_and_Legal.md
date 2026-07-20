# 🛡️ Risk, Security, and Legal Readiness

> **Workspace status:** Updated 17 July 2026. Product name: **HilalMarkets**.

## Main risk categories

### Religious credibility
**Risk:** a label is treated as a universal ruling or AI decision.  
**Controls:** disclosed methodology, qualified governance, source-backed Passport, cautious wording, version history, no AI status authority, correction and appeal.

### Financial promotion and user harm
**Risk:** users interpret opportunities as recommendations or performance promises.  
**Controls:** no execution, no guaranteed-return language, user-defined mechanics, evidence and risk disclosure, no personalized investment advice.

### Source and identity integrity
**Risk:** wrong asset, stale source, changed token identity, wrapper, bridge, or delisting.  
**Controls:** canonical asset identity, exact exchange mapping, source hashes, versioning, fail-closed behavior, use-specific coverage.

### Market-data rights and quality
**Risk:** unlicensed redistribution, incomplete feeds, stale candles, or real-time claims without rights.  
**Controls:** provider contracts, explicit delay labels, data-health gates, historical evidence, no silent synthetic data.

### AI
**Risk:** fabricated sources, arbitrary rules, hidden assumptions, unauthorized action.  
**Controls:** registered URLs, strict schemas, bounded tools, deterministic execution, approval gates, usage/audit records, no arbitrary code.

### Cybersecurity
**Risk:** credential compromise, admin route discovery, session abuse, CSRF, SSRF, uploads, direct-origin bypass.  
**Controls:** password/OTP/session/RBAC/CSRF, secret redaction, URL/upload review, optional Cloudflare Access, origin firewall or tunnel, no-index headers, rate limits.

### Billing
**Risk:** browser price manipulation, duplicate webhooks, wrong entitlements, duplicate email.  
**Controls:** server plan catalog, verified provider event authority, idempotency, durable attempts/outbox, entitlement snapshots, audit.

### Operational concentration
**Risk:** one founder holds product, review, publishing, support, and deployment authority.  
**Controls:** documented roles, reviewer profiles, optional four-eyes, assignment and SLA, external advisers, runbooks, audit export.

### Regulatory and legal
**Risk:** jurisdiction-specific licensing, advisory, privacy, consumer, digital-asset, or religious-claim requirements.  
**Controls:** UAE and target-market counsel, clear service boundaries, no custody/execution, source licensing, reviewed legal pages.

## Pre-launch legal review list

- Terms of Service
- Privacy Policy
- Cookie Policy and consent implementation
- Risk Disclosure
- methodology and religious-claim wording
- financial-promotion language
- subscription, cancellation, tax, refund, and renewal terms
- third-party data and source rights
- user-generated strategy privacy
- international data transfers
- incident and breach response
- support and complaint process

## Security deployment requirements

- Cloudflare proxied domain or tunnel.
- Direct-origin access blocked by firewall/security group.
- Cloudflare Access for `/system-brain` as defense in depth.
- Application ADMIN authentication remains mandatory.
- Secrets mounted outside Git and image layers.
- Deep health endpoints restricted.
- Production logs redact credentials and private strategy content.
- Backups encrypted and restoration tested.
- CI dependency, container, secret, and migration checks.
- Staging provider tests before production changes.

## Go/no-go condition

Do not launch a public Sharia-screened market merely because the interface and database tables exist. Launch requires real governance, published assessments, source operations, legal review, delivery reliability, and a tested production rollback.
