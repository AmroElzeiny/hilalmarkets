# Hilal Markets Architecture

Date: 2026-08-14 (layer map re-checked against the real module tree)

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
| Observability | `observability/` | The one place the product decides what is measured, what is promised, and what wakes somebody up: metric registry, service-level objectives, alert rules and their delivery, durable measurements, the deduplicated issue queue, and customer-facing degradation banners. Startup refuses to boot on an objective nothing emits, or an alert routed through the subsystem it watches. |
| Launch exposure | `core/launch_stage.py` | The four stages, the exposure table that says what each one shows, and the legal moves between them. Menus, sitemap, schema.org offers and the public assistant all read it; none of them decides for itself. |
| Web | `Hilal-Markets-Website/`, `templates/hilal/`, `static/hilalmarkets*` | Supplied React landing/contact, shared dashboard/public shells, production read models, guided Watchlist Builder UI, consent, and accessibility behavior |
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
  legacy redirects, plan-catalog binding, and the minimal React runtime shell for `/` and
  `/contact`.
- `Hilal-Markets-Website/` is the source of truth for the landing and contact presentation. Its
  Docker-verified Vite build is packaged under `static/landing/`; no tracking or server endpoint
  depends on section order, visible copy, or generated Figma class names.
- `templates/hilal/base_public.html` and `base_dashboard.html` own their shells. Shared partials and
  macros render navigation, footer, consent, statuses, opportunity cards, evidence rows, and empty
  states.
- Public Pricing and dashboard Billing may explain all public plans while billing is disabled, but
  paid actions remain unavailable and cannot create a checkout or entitlement.
  Internal, trial, and paid catalog entries remain available to entitlement/provider tests but
  cannot be purchased through a hidden form value.
- `hilalmarkets-consent.js` stores a versioned first-party preference. Consent defaults are emitted
  before optional scripts, with analytics and advertising storage denied. On the React surface,
  `analytics.ts` is the only GA4/GTM/Meta loader; the consent manager supplies live category
  updates but receives no GTM ID, preventing duplicate initialization.
- `api/routers/public_forms.py` and `services/public_forms.py` own anonymous CSRF, idempotent
  waitlist/contact persistence, one office-email delivery, and optional retryable server-only
  Google Apps Script delivery. Sheet endpoints and secrets are never rendered into browser state.
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

### Setup Chat Evaluator Boundary

`src/hm_chatbot_eval` targets only the authenticated Setup Chat and Strategy Canvas. Its default
backend adapter creates an owned durable session and sends idempotent messages through the real
dashboard APIs. The server response includes an evaluator contract projected from a successfully
validated `StrategyDefinition`; it is not a second compiler or approval path. The contract exposes
canonical fields, approval readiness and immutable version references plus a deterministic Canvas
node/group/edge projection. JSON Schemas and the canonical field map are exported from production
Pydantic models under `tests/evaluator/contracts`.

One-shot LLM faults and model/prompt comparison labels are context-local test controls at the real
OpenAI boundary. They require `APP_ENV=test` and explicit test flags. Staging and production startup
reject the flags and all variant mappings. Labels select only server-owned configurations, and no
customer request can supply a model or prompt. The Playwright adapter requires the authenticated
Setup Chat marker and refuses pages containing the public Support marker. See
`docs/AI_SETUP_CHAT_EVALUATOR.md`.

The evaluator's budget profile preserves one live backend scenario for every topic, repeats only
the API/UI and Canvas boundaries in Chromium, and reserves longer conversations for context,
correction and persistence topics. Cost accounting uses authoritative usage returned with the
assistant message for both API and UI targets. Each chat request correlates every recorded AI call,
including coordinator and tool-invoked compiler calls, before returning one authoritative mixed-
model cost. The evaluator adds its own model usage, runs serially, and stops fail-closed at the
configured all-in cap. Model-version variants are repeated only by the drift topic in this profile.

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
one-time 7-day Monitor trial. `services/entitlements.py` syncs that catalog into `Plan`, calculates the
current entitlement from subscription/trial/default state, enforces active-strategy, symbol,
timeframe and delivery limits, snapshots entitlement decisions, records idempotent usage, and
pauses excess strategies after downgrades without deleting user data.

`services/billing.py` provides a capability-declared billing-provider protocol with independent
card and crypto selection. Creem and Stripe provide recurring card subscriptions and customer
portals; NOWPayments provides signed one-time crypto invoices for 30-day access with no
cancellation portal. Every hosted checkout starts from an authenticated, server-priced,
idempotent `BillingCheckoutAttempt`. Only a signed provider webhook bound to that attempt may
change an entitlement or queue one logical receipt email. The service also handles access expiry,
refunds, disputes, payload redaction, provider trials, and downgrade pauses.
`services/trials.py`, `services/referrals.py` and `services/admin.py` cover trial eligibility,
the one-time monitoring cycle, qualifying-alert attribution, expiry decisions, reminder state,
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

### Operational truth layer

`observability/` is the one place the product decides what is measured, what is promised, and what
wakes somebody up. It replaced three parallel recorders — the scanner's `OperationalMetric` rows,
Setup Chat's per-turn telemetry and the bounded agent's own counters — which spelled the same
provider three different ways and could not answer "is the product healthy" from any one of them.

| Module | Owns |
|---|---|
| `labels.py` | The only allowed metric labels, and the redaction rule |
| `metrics.py` | The registry of every metric, and the in-process recorder |
| `slos.py` | Eleven objectives, each naming a metric from that registry |
| `alerts.py` | Twelve rules that page or ticket when an objective breaks |
| `issues.py` | The deduplicated operational issue queue |
| `banners.py` | The customer-facing degradation messages |
| `asgi.py` | Turning one HTTP request into its two metrics |
| `durable_metrics.py` | Writing the measurements down, and bounding what is kept |
| `alert_delivery.py` | Sending a page at most once, through a route that still works |

Four properties are enforced rather than documented:

1. **A metric must be declared before it can be recorded.** An undeclared name raises, and a
   declared metric states its complete label set, so a metric cannot mean different things at two
   call sites.
2. **A label must be bounded.** Enumerated labels have a closed value set; identifier labels must
   match an identifier shape and carry a per-label ceiling on distinct values. A UUID, an email or
   a long hex run is refused outright. The ceiling is what catches a label by user id, because each
   individual value looks reasonable and only the count gives it away.
3. **An objective must be computable.** `undeclared_metric_names()` is read by both a unit test and
   `core/startup.py`; an objective over a metric nothing emits stops the deployment. Such an
   objective would otherwise read "no data" forever, which on a dashboard looks like health.
4. **An alert may not travel through what it watches.** `DELIVERY_ROUTE_DEPENDENCIES` maps each
   route to the components it needs, and `validate_alert_rules()` refuses a rule whose watched
   service appears there. There is no external paging service in this product, so every route
   depends on something; a page-worthy alert therefore names a primary **and** a fallback whose
   dependency sets do not overlap, and a rule that names one route, or two that share a
   dependency, is refused at startup.

The layer is read-only with respect to the product. It writes only its own four tables —
`operational_issues`, `operational_issue_events`, `operational_metric_deltas` and
`operational_alert_deliveries` — and never touches strategy, Passport, entitlement or approval
state. Recording is in-process and never opens a transaction, so instrumentation cannot fail the
request that produced it.

**How the measurements survive and add up.** Recording stays in memory for speed; a separate step
writes each process's *movement since its own last write* into a row keyed by that process's writer
identity. Reads add the rows up. Nothing is read, modified and written back, so two processes
flushing at the same moment cannot overwrite each other, and a retried write cannot count twice —
a delta is marked as stored only after its transaction commits, and only against the exact reading
it was taken from. A restarted process gets a new writer identity, so it starts a new row instead
of continuing a dead process's.

Growth is bounded by one scheduled task. `compact_operational_metrics` folds every process's rows
for an old window into a single row, then deletes rows past retention. Both ages are configured and
startup refuses a retention shorter than the rollup age, because rows would then be deleted before
they were ever folded and the history would silently stop going back as far as it claims.

**How a page is sent once.** `dispatch_due` evaluates the rules against the stored measurements and
claims each page with an `idempotency_key` built from the rule, the issue's dedupe key and the
current repeat window. The key is unique in the database, so two workers racing on the same firing
rule produce one message and a one-hour outage sends one page rather than sixty. `process_due` then
sends it; when the primary route refuses, the row moves to the fallback and records that it had to.
A ticket-worthy rule is never sent at all — it is recorded in the issue queue and waits.

`OperationalIssue` is deliberately separate from `Incident`. An incident is a declared,
customer-facing event with impact rows and a published timeline. An issue is the layer underneath:
one deduplicated row per recurring problem, with an occurrence count and an append-only audit trail,
before anybody has decided it is an incident.

Not yet built: an external metric store or dashboarding tool. Three days of history lives in the
product's own database and is read by `/api/v1/admin/health`; there is no query language over it and
nothing ships it anywhere else. Delivery has been exercised against stub transports only — no
message has been sent to a real Telegram chat or mailbox from this code.

### Launch stage and product boundaries

`core/launch_stage.py` is the server-owned authority for how open the product is. Four stages —
`internal`, `private_beta_invite`, `public_waitlist`, `public_launch` — each declare in one table
what they advertise, whether pricing and checkout are exposed, which channels are offered and what
the public assistant may claim.

`PUBLIC_WAITLIST_MODE` remains as a hard ceiling, not an authority. No surface reads it directly;
they read `settings.waitlist_mode`, derived from the resolved stage. While the ceiling is on, the
product can be no wider than `public_waitlist` whatever the stage says, and a disagreement is
reported through `ResolvedStage.clamped_by_environment` rather than applied silently. Widening moves
one step at a time; narrowing is allowed from any stage to any narrower one, because pulling the
product back is an emergency action.

`core/product_boundaries.py` is the versioned registry of what the product does, does not do yet,
and will never do. A refusal names the missing capability and carries no substitute — there is no
`suggested_alternative` field, because that is the hook that turns "we cannot do that" into "we
quietly did something else". `core/copy_rules.py` owns the forbidden phrases and the Shariah
spelling rule, imported by both the release gate and the tests so the two cannot drift apart.

### Public Support AI

`services/public_chat.py` and `services/public_support_ai.py` keep the public Support assistant
separate from Setup Chat. The model selects one of six validated modes: grounded product fact,
normal product conversation, general trading education, authenticated read-only account support,
out of scope, or safety refusal. Product facts require an authoritative server source or successful
read tool; account facts require a successful server-owned user-scoped tool. Greetings and neutral
education do not require fabricated citations.

The AI may only advertise that human support is available. It cannot open the Support form. Each
completed response creates an immutable answer event; the visitor then records exactly one
session-bound Yes or **No. Submit a support form** choice. The inquiry endpoint rejects submissions
without the negative choice and stores answer metadata separately from editable form fields.
Provider failures, invalid model output, low confidence, knowledge gaps, clarification, refusals,
and greetings remain in chat.

`services/notion_knowledge.py` indexes only bounded `.md`, `.csv`, `.json`, and `.txt` files below
the configured project `Notion/` root. It rejects symlinks and oversized files, redacts secret-like
lines, and returns path-contained snippets as `context_only`. This corpus can improve wording and
topic recall but cannot satisfy product-fact grounding. No generic filesystem tool is exposed.

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
notifications, execute code, mutate the global registry, call arbitrary URLs, or install an
uncertified dynamic mechanic. With exact user-fragment consent it may request the existing bounded
capability-certification pipeline and read its status. Certification, user ownership, artifact
hashes, quarantine, strategy revision, approval, activation, and scheduled execution remain
application-owned. Final prose is separately checked against successful tool evidence; invalid or
unavailable agent turns fall back to the unchanged guided flow without duplicating chat messages.
Scheduled scan evaluation remains deterministic and LLM-free.

`agent_runs` and `agent_tool_calls` store redacted decisions, results, evidence, timings, usage,
budgets, clause-coverage counts, and optional comparison evidence, never hidden reasoning or
credentials.

> Corrected on 14 August 2026. This paragraph said the release profile "uses live execution for
> 100% of authenticated users" and that `AI_AGENT_CONTROL_ENABLED=false` is "the emergency kill
> switch". Neither is true at HEAD. The coordinator is off in production
> (`.env.production.example`, `scripts/check_release_invariants.py`), authenticated Setup Chat never
> routes to it (`services/ai_setup_chat.py:1313` returns from the launch service first), and the
> switches that do stop Setup Chat are `SETUP_CHAT_EMERGENCY_DISABLED` and the per-surface
> `SETUP_*_ENABLED` settings. See `docs/OPERATIONS.md`, "Stopping Setup Chat".

System Brain exposes live tool, grounding, compilation, approval, correction, capability, support,
latency, and cost metrics. The retired coordinator's catalog is kept for history in
`docs/BOUNDED_AGENT_CONTROL.md`.

### Certified Capability Extensions

An explicitly approved missing OHLCV mechanic enters `services/capability_extensions.py` rather
than being passed to the scanner as raw AI output. `services/capability_extension_ai.py` produces
and reviews strict schemas; `engine/dynamic_mechanics.py` is the only compiler and evaluator for the
bounded JSON expression. It rejects arbitrary code, unknown operations, unsafe parameters,
nondeterminism and missing proof evidence.

The pipeline performs Binance spot preflight testing, independent failure classification and bounded
AI escalation. A market candidate does not prove correctness, and no-candidate evidence does not
permit silent logic relaxation. Live five-scan reviews distinguish implementation, user logic,
market data and delivery failures. Certified repairs become pending immutable strategy revisions;
the active monitor remains unchanged until the user approves and activates the revision. Provider-
only source fragments are rejected before queueing, and a quarantined artifact cannot pass approval
or scheduled-scan execution until its owner explicitly restores it.

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

The authority-import workflow sits before that existing screening boundary. The idempotent
`SCMalaysiaImporter` retains the official response and imports only rows with explicit
`Shariah-compliant` wording, an SAC meeting, and a parseable decision date.
`FassetImporter` retains the published Shariah Reports response, parses each complete asset profile,
and imports only profiles whose verdict section says exactly `Shariah Compliant`. Decorative labels,
missing verdicts, non-compliant verdicts, duplicate aliases, source-shape changes, redirects, and
anti-bot challenge pages fail closed or remain excluded evidence. Both adapters create
`ExternalAssessment` records, never public assessments.

Canonical mapping checks name, symbol, native/token identity, chain/contracts, official URLs, and a
current spot-market identity; ticker-only matches create a blocking conflict case.
`ShariaResearchPipeline` fetches a stable, verified official-source registry sequentially and makes
one bounded Flex analysis request per mapped asset/run. Its schema contains factual findings and
review recommendations, but no publication or religious-status field. The active
`ALL_APPROVED_METHODOLOGIES` entry is an aggregate read policy: it unions active published
assessments, deduplicates by canonical asset with deterministic source priority, and preserves the
selected source methodology and Passport on every result.

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

Migration `6f02832495ab` adds source-neutral external-assessment fields, archives every development
test methodology, and seeds the versioned Fasset methodology plus the non-ruling `All` aggregate
view. It seeds no asset conclusion and publishes no Passport.

Migration `81b24a6c37de` adds immutable import-pack provenance, source-row idempotency, rights gates,
factual-enrichment state, and separately linked live-verification snapshots. The bounded import
service validates and retains 15 SC Malaysia, 31 Shariah Review Bureau, and 188 Fasset compliant
source rows as three independent methodologies. Every row creates an unpublished admin review
case and factual-enrichment task. The 52-row Fasset non-compliant guard is retained in the source
snapshot and can never enter an eligible assessment path. Source refreshes enrich or verify the
exact package row; they do not create a parallel assessment. See
`docs/SHARIA_METHODOLOGY_IMPORT_PACK.md`.

Governance/Passport/checkout migration `e7f8a9b0c1d2` adds exact historical references, reviewer
roles/profiles/assignments, Passport problem reports, immutable decision details, superseding
publication linkage, checkout attempts, and payment-email delivery state. See
`docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md`.
