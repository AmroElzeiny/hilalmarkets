# Architecture Decision Record: Initial Platform Foundation

Date: 2026-06-15

## Repository audit

The supplied `Trading_assistant` directory was empty and was not a Git repository. There were no
dependencies, working features, data models, or deployment assets to preserve. This implementation
is therefore a greenfield foundation following the requested stack.

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
5. **One persistent setup instance.** A `(strategy_version, exchange, symbol, timeframe, setup_key)`
   identifies a lifecycle. State transitions append events instead of emitting disconnected alerts.
6. **Identity is separate from user.** Provider identities are uniquely constrained and linked to
   one user. Short-lived, single-use continuation tokens bridge Telegram and web onboarding without
   duplicating accounts.
7. **Adapters behind protocols.** Market data, interpretation, notification, billing, charts, and
   task dispatch are interfaces. Core services are testable with deterministic fakes.
8. **JSON only for variable evidence/configuration.** Query-critical ownership, status, timestamps,
   relationships, deduplication keys, and monetary values use typed columns and indexes.
9. **Billing events are the source of commercial truth.** Plan definitions live in one catalog,
   entitlement checks run in API/services/workers, and verified billing webhooks drive subscription
   state, downgrade pauses, entitlement snapshots and Discord role-sync jobs.
10. **Discord is optional delivery and community infrastructure.** OAuth linking, destinations,
    role synchronization, setup threads, slash-command shortcuts and support context are modeled
    without making Discord the primary onboarding surface.

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
| Discord | `discord/` | OAuth linking, destination validation, embeds, threads, roles, support, moderation |
| Workers | `worker.py` | Idempotent scan scheduling, scan execution, expiry, delivery and health jobs |
| Web | `templates/`, `static/` | Acquisition page and product demonstration |
| Reliability | `services/reliability.py` | Market-data health, incidents, delivery failure state, metrics |
| Admin | `api/routers/admin.py`, `services/admin_dashboard.py` | RBAC dashboard APIs and audited admin actions |

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

## Telegram Application Layer

`telegram/service.py` implements framework-independent async handlers used by the production
Bot API webhook adapter. The service persists conversation state, validates callback
ownership through Telegram user identity, stores callback receipts for idempotency, records audit
events, and uses existing onboarding/strategy services for approval and activation.

Current Telegram capabilities:

- `/start` with deep-link attribution, referral and shared-template metadata.
- Risk disclaimer acceptance and trial activation.
- Persistent menu labels for Create Monitor, My Monitors, Near-Miss Radar, Latest Setups, Why No
  Alert, Performance, Subscription, Connect Discord, Support and Settings.
- Describe-my-setup monitor creation, interpretation summary, approval, historical preview and
  activation.
- Near-Miss list rendering through a provider interface.
- Confirmed and lifecycle alert rendering from proof-backed `EvaluationResult`.
- Feedback and support request creation without silently changing strategy rules.
- Duplicate Telegram delivery guard using `AlertDelivery`.
- Secret-validated `/api/v1/telegram/webhook`, update-idempotency receipts, real Bot API
  send/edit/photo/callback methods and bounded delivery retries.

## Commercial Layer

`core/plans.py` is the single plan catalog for Demo, Trader, Pro, Creator, Community and the
conditional 14-day trial. `services/entitlements.py` syncs that catalog into `Plan`, calculates the
current entitlement from subscription/trial/default state, enforces active-strategy, symbol,
timeframe and Discord-access limits, snapshots entitlement decisions, records idempotent usage, and
pauses excess strategies after downgrades without deleting user data.

`services/billing.py` provides a billing-provider protocol, a real Stripe Checkout/Portal adapter,
Stripe timestamped signature verification, payload redaction, idempotent `BillingEvent` processing, subscription
upserts, trial conversion, entitlement snapshots, downgrade pauses and Discord role-sync enqueueing.
`services/trials.py`, `services/referrals.py` and `services/admin.py` cover trial eligibility,
monitoring cycles, qualifying-alert attribution, no-alert renewal decisions, reminder state,
referral foundations and audited commercial overrides.

## Discord Application Layer

`discord/service.py` is framework-independent and is connected to the Discord HTTP API and signed
interaction endpoint. Current Discord capabilities:

- Short-lived OAuth state creation and verified identity linking to existing users.
- Personal DM and server-channel delivery destinations with permission validation and test sends.
- Confirmed setup and Near-Miss embed rendering from deterministic proof data.
- Per-setup thread reuse for server delivery so lifecycle updates remain grouped.
- Idempotent Discord alert deliveries with retry scheduling.
- Slash-command shortcuts for monitor creation links, monitor lists, subscription status, support,
  Near-Miss and investigation handoff.
- Billing-driven role-sync jobs; paid roles are never granted from Discord commands alone.
- Support ticket creation with available diagnostic context.
- Moderation safeguards for scam language, support impersonation, guaranteed-profit claims,
  unsafe secret requests and suspicious attachments.
- Server-side OAuth code exchange, DMs, channel messages, threads, components, role changes and
  Ed25519 interaction-signature verification.

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
Telegram/Discord connection state, strategy/scan/alert identifiers, delivery logs and recent health
records so users do not need to repeat technical context.

`api/routers/admin.py` exposes API-first admin dashboard endpoints for user search, subscription and
trial status, strategy actions, health, scan/alert/delivery history, support resolution, incidents,
billing-event reprocessing and audit events. Admin routes require persisted `ADMIN` or `SUPPORT`
roles; mutating actions require `ADMIN`.

`services/security_review.py` codifies local security checks for SSRF-prone URLs, unsafe uploads,
secret redaction and user strategy text that attempts code execution. It is a guardrail, not a
replacement for CI dependency/container vulnerability scanning.

## Security boundaries

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
