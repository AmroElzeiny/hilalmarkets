# HilalMarkets Architecture

Date: 2026-07-17

## Repository evolution

HilalMarkets is an incremental evolution of the existing monitoring system, not a greenfield
rewrite. Strategy compilation, deterministic evaluation, lifecycle evidence, screening,
authentication, billing, delivery, and administration remain authoritative domain services. The
current public and dashboard presentation layers bind those services to shared HilalMarkets
templates without replacing persisted models or approval boundaries.

## Decisions

1. **Modular monolith first.** FastAPI owns the HTTP surface; domain services remain independent
   of HTTP and can move to workers or separate services when load justifies it.
2. **PostgreSQL is authoritative.** Onboarding, setup lifecycles, proofs, alerts, billing events,
   and audit history are durable relational records. Redis is for queues, locks, cooldowns, and
   ephemeral caching, never the sole source of user state.
3. **Immutable strategy versions.** Editing creates a new `StrategyVersion`. Approval records the
   exact schema hash. Activation is forbidden if the content changed, approval is absent, preview
   failed, or entitlement/disclaimer requirements are unmet.
4. **Deterministic market decisions.** Interpretation may translate natural language into the
   strategy DSL. Rule evaluation, actual indicator values, Near-Miss scores, and lifecycle changes
   are deterministic and store condition-level evidence.
   AI Setup Chat persists its interview and translation sheet, but its text is never executable.
   Only a schema-validated `StrategyDefinition` with an approved canonical hash can continue into
   monitor validation and activation.
5. **One persistent setup instance.** A `(strategy_version, exchange, symbol, timeframe, setup_key)`
   identifies a lifecycle. State transitions append events instead of emitting disconnected alerts.
6. **Identity is separate from user.** Provider identities are uniquely constrained and linked to
   one user. Short-lived, single-use continuation tokens bridge Telegram and web onboarding without
   duplicating accounts.
7. **Adapters behind protocols.** Market data, interpretation, notification, billing, charts, and
   task dispatch are interfaces. Core services are testable with deterministic fakes.
8. **JSON only for variable evidence/configuration.** Query-critical ownership, status, timestamps,
   relationships, deduplication keys, and monetary values use typed columns and indexes.
9. **Billing events are the source of commercial truth when billing is enabled.** Plan definitions
   live in one catalog and entitlement checks run in API/services/workers. Private beta disables
   checkout and exposes only invite access without deleting provider-accurate payment history.
10. **WhatsApp is a dormant opt-in delivery and navigation channel.** The official Meta Cloud API adapter
    accepts only verified inbound-linked recipients. Free-form replies require an open service
    window; business-initiated delivery outside it requires an explicitly configured template.
    Strategy approval and activation remain authenticated dashboard actions.
11. **Registry keys are the AI execution boundary.** Natural-language retrieval produces a compact
    capability shortlist. AI may rerank those keys and extract schema-defined parameters, but every
    AI condition must carry an immutable `capability_key`. The backend rejects unknown keys and
    rebuilds operands from the registry before coverage audit, approval, or scanning. Unknown terms
    become clarification questions, never silent assumptions. See `docs/CAPABILITY_RESOLVER.md`.
12. **Adaptive models do not receive adaptive authority.** Setup Chat deterministically chooses a
    configured inexpensive or complex model tier from request shape, corrections, terminology and
    resolver confidence. Both tiers receive the same bounded context and remain subordinate to the
    capability registry, compiler, lint, canonical hash and user approval.
13. **Public product chat is a separate non-executing boundary.** It retrieves from server-owned
    public content and records hashed answer telemetry. It cannot authenticate, inspect private
    records, compile rules, call market providers, or invoke Setup Chat tools. Unknown questions may
    become consented inquiries through a separate bounded outbox.

## Layer map

| Layer | Package | Responsibility |
|---|---|---|
| API | `api/` | Validation, authentication context, transport errors, webhooks |
| Core | `core/` | Configuration, database sessions, security, logging |
| Domain persistence | `db/models/` | Entities, constraints, indexes, lifecycle evidence |
| Schemas | `schemas/` | Strategy DSL and public request/response contracts |
| Services | `services/` | Onboarding, identity, approval, preview, activation, billing, entitlements |
| Engine | `engine/` | Deterministic indicators, rule evaluation, Near-Miss, risk, proofs, forensics |
| Telegram | `telegram/` | Async command/callback application service and alert rendering |
| WhatsApp | `whatsapp/` | Signed Meta webhooks, verified opt-in linking, interactive navigation, template/session delivery, and status reconciliation |
| Workers | `worker.py` | Idempotent scan scheduling, scan execution, expiry, delivery and health jobs |
| Web | `templates/hilal/`, `static/hilalmarkets*` | Shared public/dashboard shells, production read models, guided Watch Plan UI, consent, and accessibility behavior |
| Reliability | `services/reliability.py` | Market-data health, incidents, delivery failure state, metrics |
| Admin | `api/routers/admin.py`, `services/admin_dashboard.py` | RBAC dashboard APIs and audited admin actions |
| Public product assistant | `api/routers/public_chat.py`, `services/public_chat.py` | Grounded public answers, consented inquiry intake, bounded email outbox and feedback |

## Public and Presentation Architecture

- `core/site_content.py` owns public navigation, dashboard navigation, footer groups, page
  metadata, help articles, customer-facing status labels, purchase FAQs, and prohibited analytics
  properties.
- `services/public_site.py` emits bounded public read models from active methodology and current
  assessment records. It never substitutes prototype assets or readiness values.
- `api/routers/public.py` owns the public route set, canonical URLs, JSON-LD, sitemap, robots,
  legacy redirects, and plan-catalog binding.
- `templates/hilal/base_public.html` and `base_dashboard.html` own their shells. Shared partials and
  macros render navigation, footer, consent, statuses, opportunity cards, evidence rows, and empty
  states.
- Public Pricing and dashboard Billing expose only the free plan while billing is disabled.
  Internal, trial, and paid catalog entries remain available to entitlement/provider tests but
  cannot be purchased through a hidden form value.
- `hilalmarkets-consent.js` stores a versioned first-party preference and is the only optional GTM
  loader. Consent defaults are emitted before it, with analytics and advertising storage denied.
- `/system-brain` is absent from customer navigation. Production can require Cloudflare Access
  headers before the existing application password, email OTP, database session, and CSRF gates.
- `services/public_chat.py` builds its knowledge index from `site_content.py`, public-page metadata
  and the server Plan Catalog. The browser receives related-route enums and same-origin links, not
  arbitrary model URLs. Anonymous answer telemetry stores hashes and source IDs rather than raw
  questions; contact details are stored only after explicit inquiry submission.

## Deterministic Strategy Engine

The engine layer is deliberately free of LLM calls. It accepts an approved `StrategyDefinition`,
market metadata, candle sets and a strategy-version identifier, then produces an immutable
`EvaluationResult`.

Implemented engine modules:

- `indicators.py`: canonical SMA, EMA, RSI, ATR, volume ratio, MACD, Bollinger Bands,
  stochastic, ADX and VWAP implementations.
- `market_filters.py`: exchange, quote, allow/blocklist, stablecoin, leveraged-token, liquidity,
  spread, listing-age, data-quality, history and optional market-cap filters.
- `evaluator.py`: condition/group evaluation with timezone-aware timestamps, active-candle handling,
  warm-up errors, unsupported-condition errors and no look-ahead behavior.
- `scoring.py`: weighted Near-Miss scoring, mandatory-failure caps, proximity scoring, thresholds,
  one-condition-remaining alerts and score trend.
- `risk.py`: optional user-defined fixed-percent, ATR and swing/technical stops, entry zones,
  targets, reward-to-risk, estimated fees/slippage and explicit-balance position sizing.
- `proofs.py`: proof receipt builder from deterministic results.
- `forensics.py`: deterministic "Why Wasn't I Alerted?" reconstruction result.
- `dedup.py`: duplicate-event hashing and alert-fatigue guard.
- `forward_test.py`: hypothetical live-data forward-test records, never presented as executed trades.

## Setup Chat Interpretation Boundary

The authenticated Setup Chat persists structured intent separately from assistant prose: confirmed
requirements, corrections by field, required and optional conditions, capability keys/versions,
timeframes, universe, alert timing, invalidation, delivery, unresolved conflicts, clarification
evidence and exact user-message references. A clause-coverage audit maps every meaningful
user-authored fragment to covered, clarification, provider-blocked, intentionally optional or
non-executable status. Unaccounted meaningful clauses block approval.

`services/ai_model_routing.py` selects only configured model and reasoning tiers. Greetings and
clear one-condition turns use the simple tier; complex Boolean logic, multiple timeframes,
contradictions, repeated corrections, low resolver confidence, custom terminology, multilingual
text and clarification friction use the complex tier. The selected route and reason are retained in
usage telemetry and System Brain aggregates. This routing changes cost/interpretation capacity, not
the executable policy boundary.

## Telegram Application Layer

`telegram/service.py` implements framework-independent async handlers used by the production
Bot API webhook adapter. The service persists conversation state, validates callback
ownership through Telegram user identity, stores callback receipts for idempotency, records audit
events, and uses existing onboarding/strategy services for approval and activation.

Current Telegram capabilities:

- `/start` with deep-link attribution, referral and shared-template metadata.
- Risk disclaimer acceptance and trial activation.
- Persistent menu labels for Create Monitor, My Monitors, Near-Miss Radar, Latest Setups, Why No
  Alert, Performance, Subscription, Support and Settings.
- Describe-my-setup monitor creation, interpretation summary, approval, historical preview and
  activation.
- Near-Miss list rendering through a provider interface.
- Confirmed and lifecycle alert rendering from proof-backed `EvaluationResult`.
- Feedback and support request creation without silently changing strategy rules.
- Duplicate Telegram delivery guard using `AlertDelivery`.
- Secret-validated `/api/v1/telegram/webhook`, update-idempotency receipts, real Bot API
  send/edit/photo/callback methods and bounded delivery retries.

## WhatsApp Cloud API Layer

`whatsapp/` implements a dormant official Meta WhatsApp Cloud API adapter without a BSP or WhatsApp Web
automation. Dashboard consent creates a short-lived, digest-only `IdentityLinkToken`; an inbound
signed `LINK <token>` message verifies the sender `wa_id` and E.164 number before a
`WhatsAppConnection` becomes active. Raw Meta webhook bodies are authenticated before parsing,
expanded across every batch entry, reduced to bounded event records, and processed asynchronously.

Outbound alerts reuse `AlertDelivery` with `wa:<wa_id>` destinations. The dispatcher applies the
same schedule, mute, entitlement, and compliance preferences as other channels, plus WhatsApp
category consent and service-window/template policy. API acceptance stores the Meta `wamid`;
separate sent, delivered, read, and failed receipts update delivery state monotonically. STOP-style
commands opt out immediately and cancel unsent WhatsApp rows. Interactive menus expose safe account
and Watch Plan navigation, while strategy interpretation, approval, activation, and sensitive
account work use short-lived authenticated dashboard links. The router and customer controls are
absent when `WHATSAPP_ENABLED=false`, which is mandatory for private beta. See
`docs/WHATSAPP_CLOUD_API_RUNBOOK.md`.

## Commercial Layer

`core/plans.py` is the single plan catalog for Demo, Trader, Pro, Creator, Community and the
conditional 14-day trial. `services/entitlements.py` syncs that catalog into `Plan`, calculates the
current entitlement from subscription/trial/default state, enforces active-strategy, symbol,
timeframe and delivery limits, snapshots entitlement decisions, records idempotent usage, and
pauses excess strategies after downgrades without deleting user data.

`services/billing.py` provides a capability-declared billing-provider protocol. Stripe supports
automatic subscription renewal and its customer portal; the configured NOWPayments launch path
uses signed one-time invoices for 30-day access and no cancellation portal. The service validates
checkout ownership, plan, amount, and currency before an idempotent `BillingEvent` can change an
entitlement. It also handles access expiry, refunds, payload redaction, trial conversion, downgrade
pauses.
`services/trials.py`, `services/referrals.py` and `services/admin.py` cover trial eligibility,
monitoring cycles, qualifying-alert attribution, no-alert renewal decisions, reminder state,
referral foundations and audited commercial overrides.

## Scanning Pipeline

`services/scanner.py` provides the production REST scanning vertical slice:

- deterministic scan buckets and database-enforced idempotency keys;
- worker-level approval, schema-hash and entitlement revalidation;
- capped universe resolution and per-symbol fault isolation;
- shared CCXT clients with timeframe-aware candle closure;
- live-engine evaluation for preview, forward test and scans;
- persistence of scan results, condition evidence, Near-Miss history, setup instances,
  lifecycle events, alerts, deliveries and usage;
- stale, duplicate and out-of-order market-data warnings;
- partial-job status when one symbol fails.

Setup rows use optimistic concurrency through `lifecycle_version`. Both-direction strategies expand
to separate long and short evaluations and receipts.

## Reliability, Support and Admin

`services/reliability.py` records per-symbol market-data health with provider, exchange, symbol,
timeframe, latest candle, retrieval time, data age, candle count, missing intervals, duplicate and
out-of-order counts. Confirmed alerts can be blocked when health is missing, stale, incomplete or
degraded unless a strategy explicitly permits that behavior.

Incidents are durable `Incident`, `IncidentImpact` and `IncidentUpdate` rows. They track severity,
status, affected users/strategies, material impact and user-visible updates. Admin APIs can open and
resolve incidents, and strategy pauses caused by incidents are audited.

`services/support.py` implements the Tier 0-3 escalation path. Tickets automatically attach plan,
Telegram connection state, strategy/scan/alert identifiers, delivery logs and recent health
records so users do not need to repeat technical context.

`api/routers/admin.py` exposes API-first admin dashboard endpoints for user search, subscription and
trial status, strategy actions, health, scan/alert/delivery history, support resolution, incidents,
billing-event reprocessing and audit events. Admin routes require persisted `ADMIN` or `SUPPORT`
roles; mutating actions require `ADMIN`.

`services/security_review.py` codifies local security checks for SSRF-prone URLs, unsafe uploads,
secret redaction and user strategy text that attempts code execution. It is a guardrail, not a
replacement for CI dependency/container vulnerability scanning.

## Security boundaries

### Capability Coverage Console

`/system-brain` is a separate administrator security boundary for capability-quality
operations. `services/system_brain.py` verifies an environment-configured PBKDF2 password,
delivers an expiring email OTP, persists revocable administrator sessions, rate-limits failed
logins, and audits authentication and alias-review actions. It does not rely on development
`X-User-ID` headers or ordinary dashboard cookies.

The same service persists capability-resolution evidence and OpenAI usage returned by the API.
The console aggregates unmatched fragments, low-confidence matches, clarification selections,
false rankings, provider blocks, registry alias gaps, reviewed alias proposals, capability
compatibility status, registered users and model/reasoning cost estimates. Approved aliases are
review records for a tested registry release; they never mutate deterministic production rules
silently. See `docs/CAPABILITY_COVERAGE_CONSOLE.md`.

### Hybrid Prompt Compiler

`services/hybrid_capability_resolution.py` sits between deterministic candidate retrieval and
strategy compilation. The resolver removes conversational framing, retrieves a typo-tolerant
shortlist from the live capability registry, and supplies recent chat context. OpenAI can rerank
only those candidate keys and extract schema-declared parameters. `CapabilityResolver` validates
the immutable key, parameters, provider state and timeframe before a capability binding reaches
the rule-based/OpenAI strategy compilers. See `docs/HYBRID_PROMPT_COMPILER.md`.

### Bounded Agent Control

`services/agent_control.py` optionally coordinates typed AI Setup Chat turns through the OpenAI
Responses function-calling flow. `services/agent_policy.py` computes a small allowed-tool set from
authenticated server state on every step; `services/agent_tools.py` adapts those calls to existing
registry, compiler, provider, Scanner, draft, and monitor services. Strict local schemas, ownership,
entitlement, state, canonical-hash, duplicate, timeout, token, step, call, and estimated-cost checks
run before domain execution. Parallel calls are disabled.

The coordinator cannot approve or activate a strategy, modify billing/entitlements, send arbitrary
notifications, execute code, mutate the registry, call arbitrary URLs, or create/repair a dynamic
mechanic. Final prose is separately checked against successful tool evidence; invalid or unavailable
agent turns fall back to the unchanged legacy flow without duplicating chat messages. Scheduled scan
evaluation remains deterministic and LLM-free.

`agent_runs` and `agent_tool_calls` store redacted decisions, results, evidence, timings, usage,
budgets, and shadow comparisons, never hidden reasoning or credentials. Live execution is gated by
a deterministic authenticated-user percentage cohort; shadow mode bypasses cohort selection but
executes no tools. System Brain exposes rollout and safety metrics. See
`docs/BOUNDED_AGENT_CONTROL.md`.

### Certified Capability Extensions

An explicitly approved missing OHLCV mechanic enters `services/capability_extensions.py` rather
than being passed to the scanner as raw AI output. `services/capability_extension_ai.py` produces
and reviews strict schemas; `engine/dynamic_mechanics.py` is the only compiler and evaluator for the
bounded JSON expression. It rejects arbitrary code, unknown operations, unsafe parameters,
nondeterminism and missing proof evidence.

The pipeline performs Bybit spot preflight testing, independent failure classification and bounded
AI escalation. A market candidate does not prove correctness, and no-candidate evidence does not
permit silent logic relaxation. Live five-scan reviews distinguish implementation, user logic,
market data and delivery failures. Certified repairs become pending immutable strategy revisions;
the active monitor remains unchanged until the user approves and activates the revision.

`services/capability_registry.py` initializes a process-wide search index at startup, keyed by a
deterministic registry hash. Versioned approved aliases and optional embeddings contribute only to
retrieval. Every final rule persists its immutable capability key, version, resolved parameters and
artifact hash. See `docs/CAPABILITY_EXTENSION_PIPELINE.md`.

- Secrets come only from environment settings and are excluded from logs.
- Continuation tokens are signed; only their SHA-256 digest is stored.
- Continuation secrets are exchanged in POST bodies, not URL query strings that access logs retain.
- Production identity creation requires a five-minute provider assertion minted by a trusted bot,
  OAuth callback, or email magic-link adapter.
- Provider subject IDs and normalized emails have uniqueness constraints.
- Webhook secret/signature verification lives at adapter boundaries before service calls.
- Admin and user roles are distinct. API authorization is ownership-based.
- No exchange trading credentials are requested or modeled for version one.
- Disclaimer acceptance is append-only and versioned.

## Reliability model

Scan jobs, results, alert deliveries, and billing events have idempotency keys and retry fields.
Health records distinguish market-data and integration degradation. Audit events record actor,
request correlation, action, target, and redacted metadata. PostgreSQL constraints provide the last
line of defense against duplicate identities, versions, deliveries, and provider events.

Capability extensions add attempt, scan, certification and repair audit records. The System Brain
console reports retrieval, selection, parameter and evaluator quality separately, along with model,
reasoning, service tier, usage, cost estimate, candidate rate and delivered-notification evidence.

## Sharia-first screened-market boundary

`ShariaScreeningService` is the authority for approved methodology versions, effective-dated asset
assessments, evidence, history, passports, and comparison. `ShariaUniverseResolver` is the single
fail-closed boundary used by one-time Scanner mode, persistent Watch Plans, preview/validation, and
the worker. It intersects the technical spot universe with one approved methodology, selected
statuses, exchange/quote filters, explicit symbols or an owner-scoped approved watchlist. Missing
evidence is `insufficient_information` and never becomes an ordinary technical non-match.

`ComplianceWatchService` ingests idempotent structured changes and routes final decisions to an
authenticated reviewer. A configured provisional safety hold may pause an asset as `under_review`
without replacing the last approved assessment. Approval creates a superseding assessment, status
history, cache invalidation, monitor impact, immutable drift evidence, and notification deliveries
in one flow. Non-critical external events can be grouped by `ComplianceDigestService`; in-app
evidence is immediate.

Historical scan/lifecycle/alert proof stores methodology ID/version, assessment ID, status at
evaluation, universe snapshot, and policy decision. Current status is resolved separately, so later
reviews cannot rewrite prior proof. `ActivityReadService` composes the user-owned read model without
merging source tables. AI can explain these records but has no status mutation or review tool.

The SC Malaysia governance workflow sits before that existing screening boundary. The idempotent
`SCMalaysiaImporter` retains the official response and imports only rows with explicit
`Shariah-compliant` wording, an SAC meeting, and a parseable decision date. Canonical mapping checks
name, symbol, native/token identity, chain/contracts, official URLs, and a current spot-market
identity; ticker-only matches create a blocking conflict case. `ShariaResearchPipeline` fetches a
stable, verified official-source registry sequentially and makes one bounded Flex analysis request
per asset/run. Its schema contains factual findings and review recommendations, but no publication
or religious-status field.

`ShariaGovernanceService` is the only publication bridge. It rechecks application `ADMIN` authority,
records an immutable decision, preserves evidence snapshot references, and then requires a separate
publication action before publishing the two-layer Passport transactionally and invalidating
affected screened universes. Governance role grants distinguish `SYSTEM_ADMIN`, `RESEARCHER`,
`REVIEWER`, and `PUBLISHER`; one owner may hold all four. With `REQUIRE_SECOND_REVIEWER=true`, the
reviewer cannot publish the same decision. Rejection retains the source, dossier, AI result, and
decision while creating no public assessment. Published assets alone enter
`ShariaSourceMonitoringService`; unchanged sources make no AI request, while material changes create
a human review and can apply a fail-closed operational hold without rewriting history. The admin-only
System Brain reads real aggregates and is absent from customer navigation.

`ShariaPassportReadService` composes one current/historical Passport model for Quick View, the full
page, and event references. Published versions are immutable and linked through
`supersedes_publication_id`. Scan results, setup instances, and alerts retain the exact assessment,
Passport publication, methodology, screened-universe snapshot, and policy decision used at
evaluation. Historical routes resolve that publication directly and display current status as a
separate fact. Missing legacy evidence is shown as unavailable rather than reconstructed.

Checkout is prepared from the server-owned Plan Catalog and persisted in `BillingCheckoutAttempt`.
Verified idempotent provider events activate entitlements and enqueue one `PaymentEmailDelivery`
logical event. The payment email worker renders HTML/plain-text messages and retries bounded failures
without letting browser prices, webhook replays, or email retries create another entitlement event.
Paid checkout is disabled for private beta; these retained paths are compatibility for a separately
approved later release.

The migration pauses legacy active monitors until a real approved methodology and resolved policy
are attached. Governance migration `d6e7f8a9b0c1` adds normalized source, dossier, review,
publication, monitoring, and Telegram-attempt records. It seeds the SC Malaysia methodology
family/version but no asset conclusion. See
`docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md`.

Governance/Passport/checkout migration `e7f8a9b0c1d2` adds exact historical references, reviewer
roles/profiles/assignments, Passport problem reports, immutable decision details, superseding
publication linkage, checkout attempts, and payment-email delivery state. See
`docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md`.
