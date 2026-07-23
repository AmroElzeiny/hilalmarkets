# AI Setup Chat Implementation Report

Date: 2026-07-11

## Authenticated Evaluator Integration

The repository-root `HilalMarkets_Chatbot_AI_Evaluator` package is integrated as
`src/hm_chatbot_eval` against the real authenticated Setup Chat session/message path and Strategy
Canvas. It consumes a server-built contract derived from the validated production
`StrategyDefinition`, canonical approval hash, immutable approved version, and Canvas tree.
Test-only one-shot LLM faults and allowlisted model/prompt versions fail closed outside
`APP_ENV=test`; deployed startup rejects their configuration. Stable UI selectors and response
capture compare browser output with backend data, while the adapter refuses public Support pages.

Exact schemas, field mappings, commands, CI/manual boundaries, and generated report paths are
documented in [AI_SETUP_CHAT_EVALUATOR.md](AI_SETUP_CHAT_EVALUATOR.md).

## 1. Summary

TraceEdge now opens new monitor creation directly in the persistent, branded AI Setup Assistant.
The welcome turn offers Scanner for a one-time research scan and Monitor for the durable TraceEdge
monitor flow. The interviewer clarifies vague trader language, keeps conversation context across
refresh/navigation, compiles the final idea into the existing `StrategyDefinition` schema, displays a
complete translation sheet, and requires explicit approval of the canonical schema hash before a
monitor can be created. Visual Canvas remains available as an editable secondary view of the same
validated draft.

AI text never reaches the scanner. The deterministic strategy schema, approval hash, existing
validation services, provider availability checks, and activation gates remain authoritative.

## 2. Files changed

Application:

- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/schemas/ai_setup_chat.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/db/models/dashboard_extensions.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/ai-setup-chat.css`
- `src/ai_market_monitor/static/ai-setup-chat.js`
- `alembic/versions/7a8b9c0d1e2f_add_ai_setup_chat.py`
- `alembic/versions/8b9c0d1e2f3a_add_setup_chat_message_idempotency.py`

Configuration and documentation:

- `.env.example`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md`

Tests:

- `tests/unit/test_ai_setup_chat.py`
- `tests/integration/test_ai_setup_chat_api.py`
- `tests/browser/test_dashboard_e2e.py`

## 3. Required AI behaviors

1. **AI Setup Interviewer**
   Persistent ordered messages are sent to a server-side scoped interviewer. Questions and clickable
   options are stored with the assistant message, so they survive refreshes.

2. **Ambiguity Detector**
   Deterministic gates recognize breakout(s), strong/high/heavy volume, near support/resistance,
   momentum, clean retest, fakeout, and generic confirmation. Each blocks compilation until the user
   chooses or writes a measurable definition. The AI can add further schema-valid clarifications.

3. **Strategy Translation Sheet**
   The live panel shows the original idea, monitor name, exchange, spot market, watchlist/quote
   universe, direction, timeframes, required/optional conditions, invalidation context, trigger mode,
   cooldown, forming-alert setting, delivery channels, assumptions, and safety boundary.

4. **Rule Confidence**
   Each condition is labeled high, medium, or low using the condition's validated confidence score.
   Every low-confidence rule has an explicit checkbox and approval is disabled until all are
   confirmed.

5. **Controlled Strategy Language**
   The interviewer returns strict JSON. Compilation uses the existing OpenAI structured strategy
   schema and Pydantic `StrategyDefinition` validation. Raw AI prose is stored as conversation text
   only and cannot be saved as executable monitor logic.

6. **Strategy Linting**
   Linting blocks missing executable rules, unresolved ambiguity, blocking unsupported/provider
   conditions, and contradictory numeric thresholds. It warns about overly strict required-rule
   sets, potentially noisy single-rule setups, and noisy intrabar/short-cooldown combinations.

7. **Beginner Mode Always On**
   System instructions prohibit advice, trade execution, exchange credentials, guaranteed outcomes,
   invented data, and silent assumptions. Greetings are handled naturally; unrelated and unsafe
   requests are refused and redirected to crypto spot monitoring. Approval is always mandatory.

8. **Setup Improvement Assistant**
   The assistant displays suggestions separately, gives each one a Use action, and never applies
   one silently. Applying a suggestion creates a normal user message and recompiles the draft.
   Built-in safe
   suggestions cover volume thresholds, candle-close confirmation, invalidation, higher-timeframe
   alignment, watchlist narrowing, and cooldowns.

## 4. UI structure

The Monitors entry route opens AI Setup Chat immediately. Its welcome turn has two large,
keyboard-accessible start cards: Scanner and Monitor. Desktop chat uses a compact two-panel layout
designed to fit the initial viewport:

- Left: agent header, scrollable user/assistant bubbles, typing state, option chips, errors/retry,
  multiline keyboard-accessible composer, and new-session action.
- Right: live translation status, strategy summary, rule confidence cards, lint warnings,
  assumptions, optional improvements, safety boundary, refine action, and approval action.

The Visual Canvas button expands the current draft and reduces chat to a compact side assistant;
Return to chat restores the full assistant. At 1050px and below the panels stack. At 700px and
below the initial view is chat-first with a sticky composer; the translation panel stays out of the
initial mobile viewport and the canvas uses a bottom-sheet assistant. Labels, focus states, live
regions, keyboard send behavior, and explicit button disabled states are included.

## 5. APIs and environment

Authenticated endpoints under `/api/v1/dashboard/setup-chat`:

- `POST /sessions`
- `GET /sessions/current`
- `GET /sessions/{chat_id}`
- `POST /sessions/{chat_id}/messages`
- `POST /sessions/{chat_id}/scan`
- `POST /sessions/{chat_id}/approve`
- `GET /market-snapshot`

Environment:

- `OPENAI_API_KEY`: required for setup interviewing/compilation; server-side only.
- `OPENAI_MODEL`: optional; defaults to `gpt-5-nano` in current project settings.
- Existing `OPENAI_BASE_URL`, `OPENAI_REASONING_EFFORT`, and `OPENAI_TIMEOUT_SECONDS` apply.
- Existing `MARKET_DATA_EXCHANGE` and `MARKET_BREADTH_MAX_SYMBOLS` control snapshot coverage.

Missing `OPENAI_API_KEY` returns an actionable `openai_not_configured` response. No secret is
rendered into HTML or JavaScript.

## 6. Safety guardrails

- Crypto spot monitoring only.
- No auto-trading or exchange-key handling.
- No buy/sell-now language, guarantees, predictions, or profit claims.
- No raw AI text execution.
- Strict output parsing and Pydantic schema validation.
- Existing capability/provider classification remains authoritative.
- Blocking unsupported conditions and ambiguities prevent approval.
- Low-confidence rules require explicit user confirmation.
- Canonical schema hash must match the reviewed draft during approval.
- Approval creates an approved draft version; activation remains behind existing validation,
  entitlement, disclaimer, channel, and preview gates.

## 7. Market snapshot behavior

“How is the market today?” is routed to the configured `MarketDataProvider`. TraceEdge lists eligible
spot symbols, retrieves provider metadata, and reports a UTC timestamp, provider name, scanned count,
BTC/ETH status when present, advancing/declining/unchanged counts, average 24-hour change,
dispersion value/label, and top gainers/losers. Values are bounded by
`MARKET_BREADTH_MAX_SYMBOLS` and identify the actual provider class. If symbols or percentage data
are unavailable, the response explicitly says data is unavailable and does not guess.

## 8. Tests and results

Added coverage for:

- Vague prompt -> deterministic clarification -> clickable choices -> compiled rules -> durable state.
- Out-of-topic refusal and greeting behavior.
- Provider-backed and unavailable-safe market snapshot behavior.
- Ambiguity detection and contradictory-threshold linting.
- Missing `OPENAI_API_KEY`.
- Invalid OpenAI JSON/schema output.
- Authenticated session creation/resume/approval.
- Canonical approved strategy/version creation.
- Server-side key non-exposure.
- Mobile single-column browser layout.

Baseline results recorded before the AI-first Scanner/Monitor follow-up:

- Focused setup-chat unit/integration tests: **29 passed**.
- Broader dashboard/interpreter regression group: **64 passed**.
- Browser suite: **12 passed**, with JUnit output at `reports/playwright/playwright-results.xml`.
- Full pytest suite: **1,689 passed** in 336.11 seconds.
- Targeted Ruff checks for all changed Python/test files: **passed**.
- Mypy for the setup-chat service/schema: **passed**.
- JavaScript syntax check for the chat client: **passed**.
- Alembic head check: **`8b9c0d1e2f3a (head)`**.

## 9. Known limitations

- Live snapshot breadth and freshness depend on the configured exchange/provider and its public API
  availability. TraceEdge reports unavailable data instead of substituting fixture or invented data.
- Canvas is an editable secondary view of the current chat draft. Final monitor activation still
  intentionally occurs through the established validation, notification-channel, and publishing
  flow.

## 10. Manual QA checklist

1. Run `alembic upgrade head`.
2. Configure a valid server-side `OPENAI_API_KEY`; optionally set `OPENAI_MODEL`.
3. Sign in and open `/dashboard/strategies/new`; confirm Scanner and Monitor cards appear in chat.
4. Refresh after the welcome message and confirm the same session resumes.
5. Enter “Find bullish breakouts with strong volume on 15m Binance spot.”
6. Confirm breakout and volume definition chips appear one question at a time.
7. Complete the questions and inspect all translation-sheet sections.
8. Confirm any low-confidence rule before approval.
9. Refine a rule in chat and verify the translation changes and old hash cannot be approved.
10. Use Open Visual Canvas and confirm the current draft is visualized; return to chat and verify the full assistant is restored.
11. Select Scanner, define a measurable trigger, run it, and confirm it creates only research results (no lifecycle or notification delivery).
12. Select Monitor and verify its required filters and confirmations remain required after compilation.
13. Confirm Start Monitoring still requires validation and a connected notification channel.
14. Ask for a cupcake recipe and verify the scoped refusal.
15. Say “Hi, how are you?” and verify the natural response.
16. Ask “How is the market today?” with provider access, then test provider unavailability.
17. Test Enter, Shift+Enter, tab navigation, retry, 390px mobile, and desktop layouts.
18. Inspect page source/network requests and confirm the OpenAI API key is absent.

## Follow-up Patch Results

### Files changed

- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/schemas/ai_setup_chat.py`
- `src/ai_market_monitor/db/models/dashboard_extensions.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/static/ai-setup-chat.js`
- `src/ai_market_monitor/static/ai-setup-chat.css`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/templates/dashboard.html`
- `alembic/versions/8b9c0d1e2f3a_add_setup_chat_message_idempotency.py`
- `tests/unit/test_ai_setup_chat.py`
- `tests/integration/test_ai_setup_chat_api.py`
- `tests/browser/test_dashboard_e2e.py`
- `docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md`

### Optimistic messages, chips, and retry

Typed messages and option selections receive a browser-generated `client_message_id`, appear as a
user bubble immediately, clear the composer, and show the assistant typing state before the API
returns. The database enforces one client message ID per chat. A failed request leaves the bubble in
place with a retry state; retry reuses the same ID, and the server returns the existing conversation
if the first request had already completed. Canonical server messages reconcile and remove their
temporary browser copy, preserving order without duplication.

Option clicks send immediately instead of filling the composer. The selected chip is highlighted,
the entire option group is disabled while the turn is pending, and the canonical option message is
stored with its label/value. Refresh restores the persisted user selection and subsequent interviewer
state from the server.

### Suggestions, compilation, and approval consistency

Every applicable suggestion has a Use action. It sends a visible `Apply: ...` user message and
recompiles from the complete accumulated conversation, not from an AI summary alone. The resulting
canonical schema hash changes when the rules change, so an earlier approval hash is rejected.

The deterministic compiler and linter now decide whether approval is possible. An AI response cannot
claim readiness while lint, ambiguity, unsupported data, or provider requirements block the draft.
Each compiled monitor has one clearly labelled primary trigger, while every user-declared required
filter and confirmation remains required and continues to block a match when it fails. Only rules
the user explicitly treats as optional are rendered as suggestions. Clarifications are presented one
at a time as `Question n of total`, including clickable Yes/Ready confirmation when appropriate.

### Concise translation and beginner mode

Assistant translation messages include a designed paragraph card describing what TraceEdge
understood. The live sheet uses one card per rule and labels each as Primary Trigger, Required
Filter, Required Confirmation, or Optional Suggestion. Common
terms including RVOL, HTF, breakout, retest, confirmation, invalidation, and candle close receive
short beginner-safe explanations. Full prior chat history is sent to the interviewer, while the
current message is sent once rather than duplicated in history.

### Market snapshot

The provider-backed snapshot now includes UTC capture time, provider name, scanned-symbol count,
BTC/ETH status when available, top gainers and losers, average 24-hour change, market breadth counts,
and dispersion value/label. Failure responses identify the unavailable stage (symbol universe,
ticker metadata, or percentage data) and explicitly state that no values were invented.

### Safety and QA coverage

New tests cover cupcake/coding/fact requests, buy-now and pump predictions, leverage guidance,
exchange-key requests, order-book imbalance, CVD, liquidation heatmaps, whale wallets, Fear and
Greed, news sentiment, beginner terminology, idempotent retry, history integrity, preservation of
multiple required rules, suggestion hash invalidation, and deterministic lint authority.

Visual QA captures from the prior green Playwright run:

- `reports/playwright/visual-qa/ai-setup-chat-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-mobile-390.png`
- `reports/playwright/visual-qa/ai-setup-chat-option-chips.png`
- `reports/playwright/visual-qa/ai-setup-chat-lint-approval-disabled.png`
- `reports/playwright/visual-qa/ai-setup-chat-translation-suggestions-ready.png`

Final verification:

- Focused setup-chat tests: **29 passed**.
- Full browser suite: **12 passed**.
- Full pytest suite: **1,689 passed**.
- Ruff, mypy, JavaScript syntax, and Alembic-head checks: **passed**.

## AI-First Entry and Scanner/Monitor Structure

### Files changed

- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/ai-setup-chat.js`
- `src/ai_market_monitor/static/ai-setup-chat.css`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/on_demand_scans.py`
- `src/ai_market_monitor/schemas/ai_setup_chat.py`
- `src/ai_market_monitor/schemas/on_demand.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `tests/unit/test_ai_setup_chat.py`
- `tests/integration/test_ai_setup_chat_api.py`
- `tests/browser/test_dashboard_e2e.py`

### Old flow versus new flow

The old new-monitor entry showed Canvas and Chat as equal paths and exposed a separate Quick Scan
route. The new route, `/dashboard/strategies/new`, opens the AI Setup Assistant immediately. Its
welcome message has two deliberate starting modes:

- **Scanner** compiles the same validated `StrategyDefinition` into a one-time `OnDemandScanService`
  run. It needs at least one measurable required rule, never creates a persistent monitor, and
  returns confirmed/forming/not-matched evidence plus common failed conditions when available.
- **Monitor** compiles a persistent draft that requires explicit approval before it can enter the
  existing validation, notification-channel, and publishing flow.

The former standalone `/dashboard/scan-now` page is now an internal compatibility redirect to
`/dashboard/strategies/new?mode=scanner`; it is not present in dashboard navigation or page UI.

### Monitor rule model

The compiler preserves the existing nested AND/OR strategy schema. A compiled monitor labels one
existing required rule as the **Primary Trigger** for orientation, but it does not downgrade the
rest: user-declared required filters and required confirmations stay required and can block a match.
Explicitly optional ideas alone become Optional Suggestions. Invalidation, alert timing, lifecycle
behavior, and delivery channels remain part of the validated strategy definition.

### Canvas and mobile behavior

Open Visual Canvas turns the current chat draft into the existing connected-rule canvas, including
its condition tree, logical links, warnings, and timing. Chat contracts to a fixed assistant panel
on desktop and a bottom sheet on mobile; Return to chat restores the full chat-first page. The chat
client emits a `traceedge:chat-draft` event after each valid draft update, and the canvas listens for
that event so clarification answers, suggestion use, Scanner changes, and Monitor changes use the
same current schema.

At 700px and below, the initial view is the full chat with a sticky composer and touch-sized Scanner
and Monitor cards. The translation panel does not consume the initial mobile viewport; canvas can be
opened from the secondary control.

### Tests and visual QA

Added or updated coverage verifies direct chat-first entry, absent standalone navigation, Scanner and
Monitor cards, Scanner trigger gating, multi-required-rule preservation, Canvas expansion/return,
mobile chat-first behavior, canvas synchronization from a chat draft, the legacy Scanner redirect,
and existing optimistic-message/chip/safety flows. The unit suite also runs Scanner through the
shared `OnDemandScanService` and checks that it leaves monitor approval IDs empty.

The browser visual-QA test writes these captures when run:

- `reports/playwright/visual-qa/ai-setup-chat-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-option-chips.png`
- `reports/playwright/visual-qa/ai-setup-chat-lint-approval-disabled.png`
- `reports/playwright/visual-qa/ai-setup-chat-translation-suggestions-ready.png`
- `reports/playwright/visual-qa/ai-setup-chat-expanded-canvas-minimized-chat.png`
- `reports/playwright/visual-qa/ai-setup-chat-mobile-390.png`

Historical environment note: an earlier follow-up session could not run the suite because the project
virtual environment briefly pointed to a missing Python 3.12 executable. The environment was restored
for the clarification-flow reliability patch below; the focused backend and browser suites were rerun
successfully.

### Clarification flow reliability fix

Option answers now resolve the clarification currently shown by the server, rather than trusting an
option key supplied by the browser. This prevents a stale or malformed key from leaving the active
question unresolved and repeating it indefinitely. Conversation state is assigned as a fresh JSON
dictionary before persistence, so Scanner/Monitor choices and answered-question state survive a
database refresh. User message bubbles explicitly force white text, including nested text and the
timestamp, to preserve contrast on the purple sender background.

Verification for this fix: focused setup-chat unit/integration tests **34 passed**, the focused
Playwright chat suite **4 passed**, and the full browser suite **13 passed**. The browser test also
asserts `rgb(255, 255, 255)` for a user message bubble.

## Question Flow and PDL Follow-up

### Repeated-question prevention

The chat now stores both the clarification key and a semantic question identity. A reply to
"Which timeframe should I use?" therefore also resolves a later rephrasing such as "What timeframe
should this monitor use?" The pending-question list is filtered by both values before the next AI
call, preventing repeat loops caused by a new AI-generated key. A resolved clarification that makes
the interviewer ready now follows the standard translation path, including its review message,
rather than returning without a final response.

When a genuinely new question group is required, the conversation writes a bordered **Current step:
clarifying** checkpoint before the first `Question n of total` message. Subsequent questions from
that same group remain sequential and do not create another checkpoint.

### Clear refusal reasons

Translation payloads now include a canonical `refusal_reasons` list. It deduplicates overlapping
lint, ambiguity, and unsupported-condition records using their code and normalized message. The
review panel renders each reason as one numbered card beneath **What needs attention**, instead of
repeating the same reason under separate lint and unsupported sections. The conversational response
is brief and directs the user to the exact review items.

### PDL / PDH sweep support

`PDL` and `PDH` are now discoverable aliases and compile to deterministic price-action rules.
Phrases including `swept PDL`, `swept through PDL`, and the common spelling `sweeped PDL` compile to
`daily_low_swept`; the corresponding PDH wording compiles to `daily_high_swept`. The deterministic
definition uses the previous UTC day: a PDL sweep requires the current candle to trade below that
daily low and close back above it; PDH is the inverse. A plain close below PDL remains a different
breakdown condition and is not silently substituted for a liquidity sweep.

### Follow-up verification

- Focused chat and interpreter suite: **42 passed**.
- Setup-chat API integration suite: **5 passed**.
- Full browser suite: **13 passed** when run serially.
- JavaScript syntax check: **passed**.
- Focused Ruff check and `git diff --check`: **passed**.
- Browser visual-QA regression now asserts that a rejected translation has exactly one numbered
  refusal-reason card for the fixture’s duplicated lint source.

The full backend pass completed **1,685 tests** successfully. An attempted concurrent browser run
failed only during test setup because both pytest processes migrated the same fixed
`test-results/browser/browser-e2e.sqlite` file at once (`integration_health already exists`). The
browser suite was rerun serially and passed; this is a test-runner resource collision, not an
application failure.

## Registry-Driven Capability Resolver

The setup compiler now uses a versioned registry resolver before AI interpretation. Prompt
fragments are matched by aliases, tags, examples, direction, and temporal metadata; OpenAI receives
only that shortlist and its typed parameter schemas. Every AI condition must include an immutable
`capability_key`. The backend rejects unknown/out-of-shortlist keys, unavailable providers, invalid
timeframes, comparators, and parameters, then reconstructs canonical operands from the registry.

Dedicated `previous_daily_low_sweep` and `previous_daily_high_sweep` capabilities unify PDL/PDH,
previous-day wording, and common `swept`/`sweeped` phrasing. Unknown words and acronyms now pause the
chat for clarification, while timeframe, universe, direction, or threshold questions already
answered in the prompt are suppressed. Clarification answers remain context instead of becoming
new conditions. Assistant summaries are no longer appended to compiler input, fixing the case where
a valid PDL-only request was contaminated by example breakout/volume prose and then refused by the
coverage audit.

Implementation and safe coverage-extension guidance are documented in
`docs/CAPABILITY_RESOLVER.md`. Contract tests cover metadata completeness, PDL aliases, unknown-term
clarification, immutable keys, unknown parameters, invalid thresholds, and canonical operand
reconstruction.

### Resolver quality audit

The final prompt corpus verifies that clear phrases for RSI, volume ratios, EMA position, previous
candle color, negated doji, Bollinger squeeze plus bullish engulfing, VWAP plus volume, percentage
moves, consecutive candle color, New York session, and PDL proceed without noisy clarification.
Compound `with` clauses are resolved separately only when both sides retrieve real candidates.
Specific contiguous aliases outrank broad ordered matches, preventing `bullish candle` from
overriding `bullish engulfing`. Unknown language remains visible: `RSI below 30 with frobnicate
alpha confirmation` pauses and asks about `frobnicate alpha` instead of silently dropping it.

Percentage moves use the registered `percent_change_lookback` capability with validated direction,
threshold, and lookback parameters. The backend selects `percent_change_up` or
`percent_change_down`; those evaluator names are never supplied by AI.

Final verification after this follow-up:

- Full pytest suite: **1,710 passed**.
- Focused resolver/chat/OpenAI/semantic suite: **57 passed**.
- Focused Ruff checks: **passed**.
- `git diff --check`: **passed**.

## Hybrid Capability Compiler and Conversation Reliability

### Failure cause and chat-flow repair

The repeated weekly-sweep question was not caused by a missing AI model. Clarification answers were
being appended to the setup prompt as internal prose such as `Capability meaning ...` and then
parsed again as if those labels were trader instructions. The unknown-word detector consequently
asked about ordinary words from its own metadata, including `want`, `meaning`, and `definition`.
Typed replies were also treated as new clauses instead of answers to the active question.

The repaired flow binds every answer to the active clarification, persists its semantic identity,
and never feeds internal keys or labels back into strategy text. Conversational request frames such
as `I want`, `Bring me`, `Show me`, and `Check whether` are ignored for capability matching while
the market-mechanic words remain intact. A custom `Other (type in chat)` selection keeps the current
question open; the next typed message resolves that question rather than starting another one.

Selecting Monitor now asks the user to describe the first measurable market event and explains that
filters and timing follow. It does not claim that the monitor is ready before any rules exist.

### Hybrid compiler boundary

The compiler now follows this path:

1. Split prompt fragments and normalize conversational framing.
2. Retrieve a high-recall shortlist using aliases, semantic tags, intent examples, temporal and
   direction metadata, and typo-tolerant similarity.
3. Give the shortlist plus recent chat history to OpenAI for contextual reranking and parameter
   extraction.
4. Accept only a `capability_key` present in that fragment's shortlist.
5. Validate every parameter against the registered JSON schema, provider availability, timeframe,
   comparator, and threshold rules.
6. Build canonical deterministic condition nodes and run the normal coverage audit.

AI cannot create executable operands, formulas, provider values, or capability keys. An invented
key is discarded. A missing required parameter causes a focused clarification. If OpenAI is absent
or fails, deterministic matches and explicit user capability choices continue to work; unresolved
language remains blocked rather than guessed.

### Broader concept coverage

The registry now contains 502 executable capabilities. The new parameterized
`reference_period_sweep` primitive covers previous day, week, or month high/low sweeps with UTC
period boundaries and deterministic breach-and-reclaim proof. This fixes the reported request about
the current candle sweeping the previous weekly candle: the assistant asks only whether the user
means the previous high or low, then compiles the selected mechanic.

Broader strategy variety comes from composing tested primitives through AND, OR, NOT, sequence,
and time-window logic instead of generating unreviewed Python at runtime. Truly unsupported ideas
are recorded in the Capability Coverage Console for alias additions, provider work, or a new
versioned capability with evaluator and proof tests.

### Verification

- Supported-prompt candidate-recall audit: **2,643 / 2,643 (100.00%)** with a top-eight shortlist.
- Focused resolver, hybrid compiler, chat, interpreter, OpenAI, evaluator, and API tests:
  **93 passed**.
- Focused Ruff checks for changed compiler/chat files: **passed**.
- Full pytest suite after all changes: **1,748 passed in 408.9 seconds**.
- Coverage artifact: `reports/capability_prompt_coverage.json`.
- Architecture detail: `docs/HYBRID_PROMPT_COMPILER.md`.

Candidate recall is deliberately not described as guaranteed semantic accuracy. It verifies that
supported mechanics reach the AI/backend decision boundary; contextual reranking, explicit
clarification, immutable-key validation, and regression telemetry determine the final selection.

## Certified Non-Existing Mechanics

### What changed

An explicitly confirmed unsupported candle mechanic can now move from chat into a production
certification pipeline. It is not arbitrary code generation. AI proposes and critiques a bounded
JSON expression, while deterministic services retain compilation, validation, market evaluation,
proof generation, artifact hashing, strategy revisioning and execution authority.

The chat displays short progress states such as creating the mechanic, testing it on the market,
reviewing an implementation issue and waiting for user approval. Telegram receives corresponding
status updates. A generated mechanic is installed only after certification and still cannot become
an active monitor without the existing user approval and activation gates.

### Files added

- `alembic/versions/ad1e2f3a4b5c_add_capability_extension_pipeline.py`
- `alembic/versions/be2f3a4b5c6d_add_capability_stage_metrics.py`
- `alembic/versions/cf3a4b5c6d7e_add_pending_mechanic_revision.py`
- `src/ai_market_monitor/db/models/capability_extensions.py`
- `src/ai_market_monitor/engine/capability_index.py`
- `src/ai_market_monitor/engine/dynamic_mechanics.py`
- `src/ai_market_monitor/schemas/capability_extensions.py`
- `src/ai_market_monitor/services/capability_extension_ai.py`
- `src/ai_market_monitor/services/capability_extensions.py`
- `src/ai_market_monitor/services/capability_registry.py`
- `tests/unit/test_capability_extension_ai.py`
- `tests/unit/test_capability_extensions.py`
- `tests/unit/test_capability_index.py`
- `tests/unit/test_capability_registry.py`
- `tests/unit/test_dynamic_mechanics.py`
- `docs/CAPABILITY_EXTENSION_PIPELINE.md`

### Files integrated

- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/db/models/system_brain.py`
- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/capability_resolver.py`
- `src/ai_market_monitor/engine/evaluator.py`
- `src/ai_market_monitor/engine/price_action.py`
- `src/ai_market_monitor/main.py`
- `src/ai_market_monitor/schemas/strategy.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/hybrid_capability_resolution.py`
- `src/ai_market_monitor/services/scanner.py`
- `src/ai_market_monitor/services/strategy.py`
- `src/ai_market_monitor/services/system_brain.py`
- `src/ai_market_monitor/worker.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/templates/system_brain.html`
- `.env.example`
- `.env.production.example`
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/HYBRID_PROMPT_COMPILER.md`

### AI and deterministic ranking flow

1. A process-wide registry index, cached by deterministic `registry_hash`, retrieves existing
   capabilities through lexical aliases and optional secondary embeddings.
2. AI can rerank only the retrieved keys and extract parameters declared by their schemas.
3. A genuinely unsupported OHLCV mechanic requires explicit user confirmation before extension.
4. `gpt-5.4-nano` with low reasoning drafts the constrained expression from the original
   conversation history.
5. The deterministic compiler validates the AST, parameters, history, proof and repeatability.
6. Bybit spot preflight measures execution and candidate behavior before user installation.
7. A separate AI review classifies the problem as implementation, user logic, market data,
   delivery or ordinary rarity. The candidate count never grants permission to change intent.
8. A certification score combines independent checks; only a certified immutable artifact can be
   compiled into a strategy rule.

Initial no-candidate/imbalanced escalation is nano-high Flex review, nano-low Flex
implementation-only repair, another market test, and mini-medium Flex review when needed. After
five live empty scans the reviewer is mini-low Flex. After five candidate-producing scans with no
queued notifications it is mini-high Flex. Repairs are applied by nano-low Flex, retested, reviewed
again, and materialized as a pending user-approved strategy revision. Models, budgets, thresholds,
timeouts, exchange and embedding settings are environment-configurable.

### Safety and version behavior

- AI output cannot execute Python, imports, files, network calls, SQL or provider lookups.
- Unknown AST operations and parameters fail closed.
- Every generated rule persists immutable key, version, artifact hash and resolved parameters.
- The internal dynamic operand is not exposed as a generic public capability.
- A user-scoped artifact cannot be used by another user.
- A tampered, uncertified, stale or unknown artifact cannot be approved or activated.
- A repair cannot silently loosen/tighten the user's market logic to create candidates.
- The current active monitor remains unchanged until a repaired revision is explicitly approved.
- Provider-dependent concepts remain blocked; OHLCV is not used as fake order-book, derivatives,
  on-chain, news or sentiment evidence.

### System Brain observability

`/system-brain` now reports every generated mechanic's stage, certification score, scans, symbols,
candidate rate, queued notifications, attempts, model, reasoning effort, service tier, usage, cost
estimate, manifest and build log. Capability quality is split into retrieval recall, reviewed AI
selection agreement, parameter validation accuracy and evaluator/template correctness. Approved
aliases are versioned registry artifacts; successful clarifications remain evidence until an admin
reviews them.

### Final verification

- Feature-focused extension/registry/chat/scanner/admin suite: **55 passed**.
- Full repository suite: **1,748 passed in 408.9 seconds**.
- Browser suite: **13 passed in 58.4 seconds**.
- Browser JUnit: `reports/playwright/playwright-results.xml`.
- Supported-prompt shortlist audit: **2,643 / 2,643 (100.00%)**.
- Coverage artifact: `reports/capability_prompt_coverage.json`.
- Feature-local Ruff checks: **passed**.
- Fresh SQLite migration from empty database through `cf3a4b5c6d7e`: **passed**.

Automated tests use deterministic market and AI doubles; they do not spend production API budget.
A staging run with the configured OpenAI account, Flex availability and live Bybit public-market
data remains required before enabling extension creation for beta users.

### Reliability estimate and competitor gap

For registered, supported language, shortlist retrieval is measured at 100% across 2,643 generated
variants. A reasonable engineering expectation is that most ordinary paraphrases of registered
concepts will be resolved or clarified correctly, but top-one AI selection accuracy is not yet
measurable at production confidence because the reviewed-user corpus is still small. Custom OHLCV
mechanics have strong execution containment and certification tests, but no honest global success
percentage can be assigned until real beta prompts and market regimes are labeled.

Therefore the system is reliable enough for a guarded private beta, not for claiming that 95% of
all possible trading ideas will compile. “Correctly handled” should include clear clarification and
safe provider blocking, not only automatic compilation.

To approach mature competitors, TraceEdge still needs a much larger reviewed prompt corpus,
per-capability semantic and market-regime test packs, broader licensed provider coverage, human
promotion review for recurring custom mechanics, shadow/canary deployment, quality SLOs and a
version rollback console. The implementation path is: collect stage-separated evidence, label it,
approve aliases, promote recurring certified mechanics into the global registry with regression
tests, add real provider adapters, and deploy new versions in shadow mode before enabling them for
all users.

## Context-Aware Turn Routing Patch

The website chat now classifies each typed turn before any text can enter `setup_fragments` or the
capability resolver. The server sends the OpenAI router a curated, deduplicated conversation, the
current setup, the active clarification in user-visible language, and a bounded registry shortlist.
The strict response separates conversation, product questions, option questions, clarification
answers, setup instructions/revisions, mixed turns, market snapshots, unsafe requests, and
out-of-scope requests. It also records sentence-level categories for audit.

Only exact user-authored spans classified as technical can continue to registry retrieval. AI does
not choose an executable operand directly: capability keys, parameters, provider availability,
compiler validation, linting, and approval remain deterministic gates. Unknown technical mechanics
continue into the existing clarification and explicitly approved capability-extension flow.

Behavioral fixes:

- Casual conversation can happen before or during an interview without changing the draft.
- Capability questions such as “Do you support FVG?” are answered from a bounded registry shortlist
  and are not compiled as conditions.
- Questions about assistant-provided choices preserve the active question and its chips.
- Exact clarification answers still take precedence over a mistaken AI route classification.
- Machine option values such as `all_supported_spot_pairs` are never treated as indicators; the
  stored setup fragment uses the visible human answer.
- Operational/process messages and repeated identical dialogue are removed from model history.
- The model prompt forbids quoting old turns or asking users to define assistant-authored wording.
- Rephrased FVG, persistence, alert-timing, invalidation, universe, timeframe, and related question
  families share stable clarification identities to prevent loops.
- Website-originated mechanic build/test status stays in the website chat. Telegram status is sent
  only for a Telegram-originated extension request; normal alert delivery is unchanged.

Files changed for this patch:

- `src/ai_market_monitor/schemas/ai_setup_chat.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/capability_extensions.py`
- `tests/unit/test_ai_setup_chat.py`
- `tests/unit/test_capability_extensions.py`

Verification on 2026-07-14:

- Full repository suite: **1,780 passed in 785.21 seconds**.
- Browser suite: **14 passed in 111.06 seconds**.
- Browser JUnit: `reports/playwright/playwright-results.xml`.
- Final chat unit/API regression set after the routing safety guard: **52 passed**.
- Feature-local Ruff checks: **passed**.
- Targeted mypy checks for the chat schema/service and capability-extension service: **passed**.

## Bounded Agent Control Layer

Date: 2026-07-14

AI Setup Chat now has an optional, feature-flagged Responses function-calling coordinator for messy
and multi-intent turns. The legacy interviewer remains the default and the fallback. The coordinator
does not replace the deterministic capability registry, compiler, scanner, monitor services,
approval flow, or scheduled evaluator.

### Architecture and files

- `src/ai_market_monitor/schemas/agent_control.py`: strict tool arguments, result envelopes,
  budgets, actions, and final response contract.
- `src/ai_market_monitor/services/agent_policy.py`: per-step tool offering, classifications,
  ownership, entitlement, state, hash, and argument validation.
- `src/ai_market_monitor/services/agent_tools.py`: adapters to existing capability resolution,
  strategy compilation, market snapshot, Scanner, draft inspection, and monitor health services.
- `src/ai_market_monitor/services/agent_control.py`: bounded Responses loop, sequential function
  outputs, time/token/call/cost limits, grounding checks, traces, shadow mode, and fallback.
- `src/ai_market_monitor/services/ai_setup_chat.py`: feature-flagged integration that preserves
  durable history, optimistic-message idempotency, canonical hash changes, and legacy fallback.
- `src/ai_market_monitor/db/models/system_brain.py` and migration
  `b4c5d6e7f8a9_add_bounded_agent_control.py`: redacted run/tool traces.
- `src/ai_market_monitor/services/system_brain.py` and `templates/system_brain.html`: rollout,
  safety, tool, latency, token, cost, fallback, and shadow-comparison metrics.
- `src/ai_market_monitor/services/on_demand_scans.py`: releases the database transaction before
  provider network work.
- `tests/unit/test_agent_policy.py`, `tests/unit/test_agent_tools.py`,
  `tests/unit/test_agent_control.py`, and
  `tests/integration/test_ai_setup_chat_agent_control.py`: deterministic fake-transport coverage.
- `docs/BOUNDED_AGENT_CONTROL.md`: operational and extension guide.
- `.env.example`, `.env.production.example`, and `src/ai_market_monitor/core/config.py`: disabled
  rollout flags, strict budget bounds, and the kill switch.
- `src/ai_market_monitor/db/models/__init__.py`: bounded-agent model exports.
- `src/ai_market_monitor/api/routers/dashboard_api.py`: shadow-comparison finalization without
  duplicate chat persistence.
- `src/ai_market_monitor/static/ai-setup-chat.js`: fixed enum-to-UI action mapping; the model cannot
  emit URLs or executable client actions.
- `tests/fixtures/agent_control_corpus.jsonl`, `tests/unit/test_agent_control_corpus.py`,
  `tests/unit/test_system_brain.py`, and `tests/integration/test_system_brain_web.py`: messy-request
  corpus and admin-metric coverage.
- `README.md`, `docs/ARCHITECTURE.md`, and `docs/OPERATIONS.md`: architecture, environment,
  rollout, rollback, and safe-tool-extension documentation.
- `reports/playwright/playwright-results.xml`: regenerated browser-test JUnit evidence.

The implementation follows the Responses function-calling lifecycle and strict function schemas:

- <https://developers.openai.com/api/docs/guides/function-calling#the-tool-calling-flow>
- <https://developers.openai.com/api/docs/guides/function-calling#defining-functions>

### Tool and safety boundary

Only seven domain tools exist: capability resolution, capability-selection validation, draft
compilation, provider-backed market snapshot, one-time Scanner execution, draft inspection, and
user-owned monitor status. Approval, activation, billing, entitlement changes, arbitrary
notifications, code/SQL/shell/filesystem/HTTP execution, registry mutation, dynamic-mechanic
creation/repair, and trades are never offered.

The allowed list is rebuilt each step from authenticated server state. Every call is checked against
the exact offered list and a strict local Pydantic model. User/role/ownership fields are absent from
model arguments. Registry keys must be shortlisted; timeframes, numeric thresholds, required versus
optional intent, and comparator direction must be grounded in user-authored text. Scanner requires
explicit current-turn intent, entitlement, Scanner mode, and the current canonical hash.

Successful-action and market/strategy/monitor claims are checked against recorded tool results.
Unknown evidence, invented IDs, model-authored URLs, approval/activation claims, current market
numbers without provider evidence, and scan claims without a successful scan are contained. A tool
failure remains unavailable and cannot become an estimate. The server renders authoritative tool
data separately from the model paraphrase.

### Budgets, traces, and rollout

The defaults are disabled control, disabled shadow, a 0% deterministic live-user cohort, four
sequential steps, four tool calls, one
retry for retryable failure, 45 seconds total, 30 seconds per tool, 1,800 cumulative output tokens,
and an estimated USD 0.02 stop. Parallel calls are rejected and `store=false` is retained. Hidden
reasoning, secrets, raw provider payloads, and raw setup fragments are not persisted in traces.

Shadow mode records and validates the proposed first tool without executing it, then runs the legacy
flow. The resulting draft/classification determines the expected first action so System Brain can
measure actual first-tool agreement. Non-shadow execution uses a stable authenticated-user hash and
`AI_AGENT_ROLLOUT_PERCENT`, allowing 1%, 5%, and larger measured cohorts without model influence.
The kill switch is only
`AI_AGENT_CONTROL_ENABLED=false`; no database rollback is required.

### Verification

Focused policy, tool, coordinator, chat-integration, and System Brain tests use fake OpenAI
transports and deterministic provider doubles. They cover malformed/unknown/forbidden/not-offered
calls, ownership, entitlement, state, duplicate calls, every budget class, prompt injection,
unsupported capability invention, ungrounded claims, provider failure, sequential tools, history,
hash invalidation, shadow mode, disabled legacy behavior, and no duplicate messages.

Verification completed on 2026-07-14:

- Bounded-agent policy, tool, coordinator, chat integration, corpus, and System Brain tests:
  **54 passed**.
- Existing Setup Chat, API, scanner, interpreter, resolver, registry, evaluator-alignment,
  prompt-to-strategy, and provider-blocking regressions: **135 passed**.
- Full repository suite: **1,828 passed in 520.89 seconds**.
- Browser suite: **14 passed in 78.33 seconds**; JUnit output is stored at
  `reports/playwright/playwright-results.xml`.
- Ruff: all changed Python modules, tests, and the migration passed.
- Mypy: **8 changed source modules passed** with no issues.
- JavaScript syntax: `src/ai_market_monitor/static/ai-setup-chat.js` passed `node --check`.
- Alembic: a clean SQLite database upgraded through every revision to `b4c5d6e7f8a9`; the schema
  check reported no new upgrade operations.

No live OpenAI or exchange API was used by the bounded-agent tests. Function calls, provider
results, failures, usage, and sequential Responses turns were exercised through deterministic
fakes.

## Clarification Provenance and Technical Pattern Patch

Implemented on 2026-07-14 after reproducing two user-visible failures from persisted chat state.

### Root cause and flow correction

- Server-authored records such as `Clarification answer for rsi_timeframe: Use the trigger
  timeframe` were retained as compiler context but incorrectly audited as new user instructions.
  They are now explicitly marked as non-meaningful provenance and cannot create an unsupported
  instruction warning.
- Hybrid retrieval was appending an older user message to every unresolved current fragment. That
  polluted phrases such as `forming head & sholders` with stale breakout language and allowed a
  wrong capability shortlist to preempt the AI interviewer. Retrieval now ranks the exact current
  fragment. Conversation history is still supplied to AI reranking, while only explicit correction
  wording can augment deterministic retrieval context.
- Bare clarification answers such as `0`, `0%`, `none`, `yes`, and `no` are context, never new
  capabilities. Normal routing text such as `alert me on the 1m chart` supplies timing and does not
  become a separate market rule.
- Unknown rules that can be expressed with available closed OHLCV data now offer `Build and test
  this rule`. AI may draft the bounded mechanic, but schema validation, deterministic evaluation,
  market tests, approval, and activation gates remain authoritative. Requests that require an
  unavailable external provider still fail closed and are not replaced with invented data.

### Added technical pattern capabilities

The live registry now contains 502 capabilities. Ten new versioned, executable chart-pattern
capabilities use confirmed pivots and closed OHLCV data:

- Head and shoulders formed and neckline break.
- Inverse head and shoulders formed and neckline break.
- Double-top and double-bottom neckline breaks.
- Ascending-triangle breakout and descending-triangle breakdown.
- Symmetrical-triangle breakout and breakdown.

Each capability has aliases, including common `head and sholders` wording, bounded parameters,
direction support, temporal behavior, composition metadata, and a proof template. Forming structure
and neckline confirmation remain separate rules, so the prompt `forming head & sholders ... once
the neckline is broken ... on the 1m chart` compiles both intended stages on `1m` without a generic
meaning question.

### Review presentation

- The assistant now places its concise paragraph summary in the chat and directs the user to the
  Translation Sheet.
- The Translation Sheet renders named fields for mode, monitor name, market, watchlist, timeframes,
  direction, trigger, filters, confirmations, optional ideas, logic, alert timing, delivery, and
  invalidation.
- `What needs attention` uses numbered cards with a plain title, explanation, and next step. Raw
  lint codes and repeated compiler wording are not used as the primary user-facing explanation.

### Verification

- Full repository suite: **1,865 passed in 603.64 seconds**.
- Browser suite: **15 passed in 104.17 seconds**; JUnit evidence is at
  `reports/playwright/playwright-results.xml`.
- Focused resolver, hybrid reranker, pattern evaluator, Setup Chat, Setup Chat API, and
  prompt-to-strategy suites passed.
- Ruff passed all changed Python modules and tests.
- Mypy passed all 7 changed source modules.
- `src/ai_market_monitor/static/ai-setup-chat.js` passed `node --check`.
- Docker API, worker, and scheduler images rebuilt successfully. API, worker, database, and Redis
  reported healthy, and the scheduler reported running.
- The exact reported head-and-shoulders prompt was executed inside the rebuilt API container. It
  selected `head_and_shoulders_formed` and `head_and_shoulders_neckline_break`, excluded routing
  text from rule fragments, and required no capability clarification.

## Private-Beta Interpretation and Shadow Patch

Date: 2026-07-17

### Adaptive model routing

`services/ai_model_routing.py` now selects between configured simple and complex model tiers. The
complex route is used for four or more conditions, mixed Boolean logic, multiple timeframes,
possible contradictions, repeated corrections, repeated clarification friction, low capability
confidence, custom terminology, and multilingual/mixed-language text. Model names and reasoning
efforts come only from environment settings. Routing never changes the available tools, registry,
compiler, lint, approval, activation, provider, or evaluation authority.

The route is used consistently by the turn classifier, interviewer, structured strategy client,
and Bounded Agent shadow coordinator. Actual selected model/effort drives usage and cost records.
System Brain aggregates route tier, reasons, model and effort from persisted usage events rather
than creating a second telemetry source.

### Structured intent and clause coverage

Every chat refreshes an application-owned intent state containing setup-message references,
correction history and latest correction by field, required and optional conditions,
capability/version references, timeframes, universe, alert timing, invalidation, delivery choices,
resolved clarifications, unresolved conflicts and clause coverage. This state is review evidence;
the deterministic schema remains execution authority.

The Translation Sheet now shows `Your wording, accounted for`. Each meaningful user clause is
classified as covered, needs clarification, provider-blocked, intentionally optional, or outside
executable logic. A meaningful unclassified clause creates a critical lint finding and blocks
approval instead of disappearing silently.

### Language quality and feedback

`tests/fixtures/setup_chat_language_quality_corpus.jsonl` contains reviewed English, Arabic,
Egyptian Arabic, Arabizi, mixed Arabic/English and common-misspelling cases. Every row labels final
capability intent, parameters, timeframe, direction, required/optional intent, Boolean structure,
and expected correction bound. Deterministic English rows are compiled field by field in CI;
multilingual and typo rows verify configured complex routing and are explicitly marked for shadow
semantic review. This does not misreport shortlist recall as final semantic accuracy.

Translation feedback accepts Correct, Partially correct, Wrong condition, Wrong timeframe, Missing
condition, Wrong required/optional status and Asked an unnecessary question. Feedback is retained
as audit evidence only and cannot promote aliases or executable capabilities automatically.

### Files added or materially changed

- `src/ai_market_monitor/services/ai_model_routing.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/services/openai_interpreter.py`
- `src/ai_market_monitor/services/agent_control.py`
- `src/ai_market_monitor/services/system_brain.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/static/ai-setup-chat.js`
- `src/ai_market_monitor/static/ai-setup-chat.css`
- `.env.example`, `.env.production.example`
- `tests/unit/test_ai_model_routing.py`
- `tests/unit/test_setup_chat_language_quality.py`
- `tests/fixtures/setup_chat_language_quality_corpus.jsonl`
- relevant Setup Chat, System Brain and dashboard API tests

### Verification status

The focused model-routing, Setup Chat, Bounded Agent, System Brain, public-chat, request-guard and
dashboard feedback selection passed before the local Python 3.12 installation became unavailable.
The complete post-patch backend/browser/static result is intentionally reported only in
`docs/PRIVATE_BETA_READINESS_REPORT.md`; the historical counts above do not prove this later patch.

## Controlled-Beta Live Agent and Certified Mechanic Boundary

Date: 2026-07-18

- The release profile now runs Bounded Agent Control live for every authenticated beta user:
  control enabled, shadow disabled, and rollout at 100 percent. The control flag remains the single
  rollback switch to the durable guided flow.
- The coordinator exposes authenticated Watch Plan, Screened Watchlist, recent Scanner result, and
  exact-fragment custom-capability request/status tools. Approval, activation, arbitrary delivery,
  registry mutation, and repair application remain outside the model tool surface.
- Custom capability requests require explicit current-turn consent and must match the unresolved
  user-authored fragment exactly. Provider-only concepts are rejected before queueing. Certified
  artifacts are rechecked during strategy approval and every scheduled scan; quarantine blocks
  both. Restore and repair-discard actions are owner-scoped and never replace the active revision.
- Final agent traces now retain stage, routing, correction count, and clause-coverage counts without
  retaining hidden reasoning or raw provider payloads. System Brain combines live-agent,
  certification, public-support grounding, inquiry, email, rating, latency, and cost evidence.
- The separate public assistant is now structured, multi-turn, AI-generated, and grounded by
  server-owned product documents plus optional read-only current-user tools. Unknown sources,
  routes, tools, and ungrounded factual answers fail closed into retry/handoff. Anonymous account
  lookup remains blocked.
- `tests/fixtures/public_support_question_corpus.json` records 250 reviewed answer, authenticated
  tool, refusal, and escalation expectations across English, Arabic, Egyptian Arabic, Arabizi,
  mixed-language, typo, account, safety, and product cases.

The authoritative implementation and current verification status, including unperformed live
provider checks, is in `docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md`.
