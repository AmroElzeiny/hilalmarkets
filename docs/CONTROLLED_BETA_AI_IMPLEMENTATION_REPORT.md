# Controlled Beta AI Implementation Report

Date: 2026-07-18

Status: implementation complete and locally regression-verified. GitHub-hosted CI and live
staging/provider delivery remain external release gates and are not claimed as completed.

## 1. Initial Architecture And Gaps

TraceEdge already had the correct authority boundary: deterministic strategy schemas,
capability resolution, approval hashes, scanner services, user ownership checks, and immutable
strategy versions. The work extended those systems rather than replacing them.

| Area | Initial state | Result |
|---|---|---|
| Setup coordinator | Bounded agent existed behind rollout controls | Enabled release profile, expanded policy and telemetry |
| Capability extensions | Generation and certification pipeline existed | Added exact-fragment consent, OHLCV scope, provider rejection, fail-closed reuse and repair controls |
| Scheduled scans | Deterministic | Remains deterministic and LLM-free; certified artifact validation now fails closed |
| Public support | Deterministic FAQ retrieval and inquiry form | Added grounded Responses-based support with bounded read tools and deterministic fallback |
| Public conversation state | Session-local behavior | Added durable, expiring, idempotent conversation and turn records |
| Operational visibility | Partial System Brain counters | Added agent, clause, provider, certification, repair, cost, and public-support metrics |
| Acceptance evidence | Focused tests only | Added policy regressions and a reviewed 250-question support corpus; ran full backend/browser suites |

## 2. Live Configuration Profile

The normal and production examples now describe the controlled private-beta profile:

```dotenv
AI_AGENT_CONTROL_ENABLED=true
AI_AGENT_SHADOW_MODE=false
AI_AGENT_ROLLOUT_PERCENT=100
CAPABILITY_EXTENSION_ENABLED=true
PUBLIC_CHAT_AI_ENABLED=true
```

Deployed startup validation requires a real server-side OpenAI key, full rollout, non-shadow
operation, capability extensions, grounded public support, SMTP for public inquiries, and the
private-beta Binance preflight exchange. Core code defaults remain conservative when no release
environment is loaded.

Emergency rollback requires no migration: set `AI_AGENT_CONTROL_ENABLED=false` and restart the
API/workers. Setup chat returns to the existing guided flow. This does not delete drafts,
certified artifacts, traces, or approvals.

## 3. Release Invariants

`scripts/check_release_invariants.py` now enforces the controlled-beta environment profile and
checks release exposure, API route security, provider configuration, retired-channel references,
test/development methodology exposure, hidden plans, and generated artifacts.

Deployed startup also rejects partial live-agent rollout, shadow mode, disabled extension
certification, missing keys, unsafe custom-mechanic exchange selection, or ungrounded public chat.

Final local result:

```text
PASS: release exposure, route security, provider, and artifact invariants hold.
```

## 4. Dashboard Conversation Stage Policy

The coordinator uses explicit stages:

`DISCOVER_INTENT`, `CLARIFY_SETUP`, `RESOLVE_CAPABILITIES`, `BUILD_DRAFT`,
`REVIEW_TRANSLATION`, `RUN_MARKET_CHECK`, `EXPLAIN_RESULTS`, `REQUEST_APPROVAL`,
`MANAGE_EXISTING_PLAN`, `CREATE_CUSTOM_CAPABILITY`, `RECOVER_FROM_FAILURE`, and
`GENERAL_PRODUCT_HELP`.

The current stage, user intent, confidence, source-clause coverage, validated tool results, and
draft identity are persisted as bounded structured state. The dashboard renders stage progress
and custom-mechanic status from server state. Approval and activation remain separate explicit
HTTP/UI actions.

## 5. Tool And Authority Matrix

| Tool | Classification | Authority |
|---|---|---|
| `resolve_trading_capabilities` | Safe | Registry candidates and ambiguities only |
| `validate_capability_selection` | Guarded | Exact registered key and parameter validation |
| `compile_strategy_draft` | Guarded | Validated draft only; no approval or activation |
| `get_market_snapshot` | Safe | Provider-backed facts or unavailable |
| `run_one_time_scan` | Confirmation required | Valid current scanner draft and entitlement only |
| `inspect_current_draft` | Safe | Current authoritative draft state |
| `get_monitor_status` | Guarded | User-owned persisted monitor only |
| `list_watch_plans` | Guarded | User-owned summaries only |
| `inspect_screened_watchlist` | Guarded | User-owned saved assets only |
| `get_recent_scanner_result` | Guarded | User-owned recorded result only |
| `request_custom_capability` | Confirmation required | Exact unresolved source fragment only |
| `get_custom_capability_status` | Guarded | User-owned extension status only |

Approval, activation, billing changes, entitlements, notification sending, arbitrary HTTP, SQL,
shell, filesystem, code execution, registry mutation, generated-code execution, and trades are
not agent tools. Unknown, unoffered, malformed, repeated, out-of-state, or unauthorized calls
fail before domain execution.

## 6. Structured Conversation Memory

Agent calls receive bounded durable state instead of an unbounded transcript. Stored state covers:

- current stage and setup mode;
- recent user and assistant messages;
- corrections and unresolved clarifications;
- capability bindings and source fragments;
- current canonical draft hash and draft version;
- validated tool-result summaries;
- clause-level coverage and provider requirements.

Server-derived user, entitlement, ownership, approval, and rollout values are never model
arguments. Hidden reasoning and raw provider payloads are not persisted.

## 7. Source-Clause Coverage

Every meaningful source clause is assigned one of:

- `COVERED`
- `NEEDS_CLARIFICATION`
- `PROVIDER_UNAVAILABLE`
- `INTENTIONALLY_OPTIONAL`
- `NON_EXECUTABLE_CONTEXT`
- `REJECTED_BY_USER`
- `CONFLICTING`

Coverage counts and failures are persisted on the agent run. A draft cannot be represented as
approval-ready while critical clauses remain unresolved, provider-limited, or conflicting.
Unsupported language cannot be silently mapped to a nearby capability.

## 8. Adaptive Model Routing

`ai_model_routing.py` scores setup complexity from bounded conversation and draft signals, then
selects the configured simple or complex model and reasoning effort. Corrections, ambiguity,
provider dependencies, custom mechanics, nested logic, and prior failures can raise complexity.
The route is an orchestration choice only; it does not change registry authority, validation,
approval, or execution rules.

## 9. Custom Capability Creation And Certification

Custom creation is available only when all of these are true:

1. Capability extensions are enabled.
2. A pending clarification names an exact unresolved source fragment.
3. The current user explicitly chooses the build action for that fragment.
4. The request is expressible from closed crypto spot OHLCV data.
5. Daily limits and bounded AST/history/market-test budgets permit the request.

A generic `yes` to another question cannot authorize creation. Direct tests cover exact-fragment
consent and reject stale or unrelated consent.

News, sentiment, social data, macro events, order book/order flow, liquidation data, whale-wallet
data, and other provider-only concepts are rejected transparently. They are not approximated with
price or volume.

The implementation is represented as a bounded deterministic expression, checked for node count,
depth, parameter schema, data availability, candidate-rate floor/ceiling, and market evidence. An
independent AI review may critique implementation, but only deterministic certification can
produce the immutable artifact hash. Certification is user-scoped unless separately promoted by
an authorized process.

## 10. Monitoring, Repair, Quarantine, And Rollback

Certified mechanics retain scan counts, candidate counts, alert observations, repair generation,
validation reports, and event logs. Repeated empty scans or no-notification observations can queue
review, but cannot silently replace active logic.

Implementation-only repairs are re-tested and re-certified. A successful repair creates a new
strategy revision and waits for explicit user approval. If review finds user logic is the problem,
the existing artifact remains active and the user receives a plain-language strictness result.

Owners can:

- quarantine a mechanic, making approval and scheduled scans fail closed;
- restore the same certified artifact without changing its hash;
- approve a separately certified repair revision;
- discard a pending repair while retaining the current approved artifact.

Failed or uncertified artifacts never become executable.

## 11. Grounded Public Support Architecture

The public assistant uses one server-side coordinator with strict structured output. It receives
only versioned application-owned knowledge records, allowlisted route IDs, bounded history, and
the read tools permitted for that request.

Public/read-only tools are:

- `public_passport`
- `account_state`
- `telegram_status`
- `watch_plan_summary`
- `recent_alerts`
- `entitlement_usage`
- `screened_watchlist`

Only `public_passport` is available anonymously. Authenticated tools derive the current user from
the session, enforce suspension/ownership, and return safe unavailable/blocked envelopes. No
public support tool writes account or strategy state.

## 12. Public Support Stages

The validated support stages are:

`GREETING_AND_PROFILE`, `UNDERSTAND_QUESTION`, `RETRIEVE_PRODUCT_DATA`, `ANSWER`,
`CLARIFY`, `PUBLIC_PASSPORT_LOOKUP`, `AUTHENTICATED_ACCOUNT_SUPPORT`, `TROUBLESHOOT`,
`KNOWLEDGE_GAP`, `INQUIRY_FORM`, `INQUIRY_CONFIRMED`, `RATING`, `FOLLOW_UP`, and
`REFUSAL`.

The UI supports remembered or session-only profiles, multi-turn follow-up, new-conversation reset,
offline retry, inquiry handoff, rating, deletion/redaction, keyboard focus containment, and mobile
full-screen presentation.

## 13. Retrieval And Grounding

Answers must cite supplied source IDs, successful tool evidence, or both. Route IDs are checked
against the server allowlist and converted to real links by the backend/UI. The model cannot emit
an arbitrary URL or claim that a read tool ran when no successful result exists.

Investment advice, pump predictions, leverage advice, religious rulings, credential requests,
cross-account access, prompt injection, and unsupported authority claims are intercepted before
the AI call. On model, schema, timeout, circuit, or grounding failure, the service returns the
deterministic grounded fallback or a transparent inquiry handoff. It never guesses.

## 14. Multi-Turn And Multilingual Behavior

Migration `2bdce3f40516` adds expiring public conversations and idempotent turns. It also adds
model, stage, intent, token, latency, cost, validation-failure, conversation, and user audit fields
to answer events. The migration has one parent (`1acbd2e3f405`) and is the single Alembic head.

The reviewed corpus contains 250 unique questions across 25 categories. Expected outcomes cover
answer, authenticated tool, escalation, and refusal. It includes English, Arabic, Egyptian Arabic,
Arabizi, mixed-language, and typo-heavy prompts. Successful corpus clarifications are evidence;
they do not become automatic production aliases.

## 15. Authenticated Boundaries

All `/api/v1` routes are either authenticated or explicitly annotated public. Public support
profiles do not grant account access. Authenticated support tools use the server principal and do
not accept authoritative user IDs from model or browser input. Cross-account and suspended-user
requests fail closed.

Final route-security result:

```text
PASS: every /api/v1 route is authenticated or explicitly annotated.
```

## 16. Inquiry And Async Email Flow

An accepted inquiry creates exactly two idempotent outbox rows:

1. customer confirmation;
2. office/support notification.

Submission does not wait for SMTP. Delivery records have unique event keys, retry state, provider
message IDs, and bounded attempts. Inquiry references and feedback tokens support one rating and
token-authorized redaction. Retention cleanup deletes expired conversations/events and redacts
expired inquiry identity/content.

No live SMTP delivery was performed in this local verification.

## 17. Security, Rate, Timeout, And Cost Controls

- Setup-agent step, tool-call, repeat, timeout, output-token, and estimated-cost limits remain
  server-controlled.
- Public support has message/history/read-tool/output/cost limits, provider retries, and a circuit
  breaker.
- Authentication, AI chat, public chat, inquiry, market, checkout, support, delivery tests, and
  admin mutations use scoped request limits.
- Chat turns and scans retain idempotency boundaries.
- Prompt/tool text is untrusted data and cannot modify coordinator instructions.
- Secrets, raw credentials, hidden reasoning, and unrestricted provider responses are not stored
  in traces.

The browser harness keeps rate limiting enabled but uses an isolated suite-sized quota so its many
two-request signup flows do not share a production-sized IP window.

## 18. Tests And Exact Results

Final commands and authoritative JUnit results:

| Command | Result |
|---|---|
| `pytest tests/dashboard tests/engine tests/interpreter tests/services -q` | 1,445 passed, 0 failed |
| `pytest tests/integration -q` | 165 passed, 0 failed |
| `pytest tests/unit -q` | 422 passed, 0 failed |
| Backend total | 2,032 passed, 0 failed |
| `pytest tests/browser` | 20 passed, 0 failed |
| `ruff check .` | Passed |
| `mypy src/ai_market_monitor` | Passed, 180 source files |
| `scripts/check_release_invariants.py` | Passed |
| `scripts/check_api_route_security.py` | Passed |
| `scripts/check_jinja_templates.py` | Passed, 62 templates |
| `scripts/check_javascript.py` | Passed, 17 files |
| `scripts/check_dependency_lock.py` | Passed, 27 exact-pinned dependencies |
| `python -m pip check` | No broken requirements |
| `python -m alembic heads` | One head: `2bdce3f40516` |
| `git diff --check` | Passed; line-ending notices only |

The first monolithic backend invocation exceeded the local command wrapper timeout. The complete
suite was then run in three non-overlapping directory slices, which collected and passed all 2,032
backend tests. Intermediate browser runs exposed shared signup-rate quota, stale terminology
assertions, ambiguous locators, and a CSS rule that overrode `hidden`; these were corrected before
the final 20/20 run.

The acceptance additions directly cover provider-only rejection, exact-fragment creation consent,
certified reuse, repair rejection, pending-repair discard, quarantine/restore, unsupported advice,
tool grounding, source/route validation, inquiry idempotency, and the 250-question corpus.

## 19. GitHub Release Gate

`.github/workflows/release-gate.yml` contains:

- Python 3.12, PostgreSQL, Redis, dependency lock, and `pip check`;
- single Alembic head plus previous-revision-to-head upgrade;
- Ruff, MyPy, route security, release invariants, Jinja, and JavaScript checks;
- full backend and Chromium desktop/mobile suites with artifacts;
- dependency audit and Gitleaks;
- runtime image build and Trivy HIGH/CRITICAL scan;
- generated-artifact cleanliness check.

This workflow is configured but was not executed on GitHub in this session. A local green result is
not a substitute for required hosted checks.

## 20. Live Staging Verification

Not performed in this local run:

- real OpenAI setup-agent and public-support calls;
- real Binance custom-capability preflight/candidate scan;
- worker/scheduler soak for repair thresholds;
- real SMTP customer and office inquiry delivery;
- live Telegram status/delivery;
- PostgreSQL backup upgrade and restore drill;
- GitHub dependency, secret, and container scanners;
- VPS/production kill-switch drill.

These remain release conditions. Test fakes, fixture candles, memory email, and local Chromium are
not presented as live-provider proof.

## 21. Visual QA Evidence

Public support captures:

- `reports/playwright/visual-qa/public-chat/public-chat-desktop-1440.png`
- `reports/playwright/visual-qa/public-chat/public-chat-desktop-1024.png`
- `reports/playwright/visual-qa/public-chat/public-chat-desktop-768.png`
- `reports/playwright/visual-qa/public-chat/public-chat-mobile-390.png`

Related controlled-flow captures include:

- `reports/playwright/visual-qa/ai-setup-chat-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-option-chips.png`
- `reports/playwright/visual-qa/ai-setup-chat-lint-approval-disabled.png`
- `reports/playwright/visual-qa/ai-setup-chat-translation-suggestions-ready.png`
- `reports/playwright/visual-qa/ai-setup-chat-expanded-canvas-minimized-chat.png`
- `reports/playwright/visual-qa/ai-setup-chat-mobile-390.png`

Generated screenshots and JUnit files remain ignored runtime artifacts; CI uploads them instead of
requiring them in Git history.

## 22. Remaining Dependencies And Release Decision

Code-level status: ready for a controlled staging deployment.

External blockers before private-beta release:

1. Run the GitHub Release Gate on the exact commit.
2. Upgrade a staging PostgreSQL backup to `2bdce3f40516` and complete a restore drill.
3. Run real OpenAI, Binance, SMTP, and Telegram controlled tests with redacted logs.
4. Observe custom-mechanic certification, empty-scan monitoring, repair review, quarantine, restore,
   and discard across worker/scheduler restarts.
5. Verify System Brain metrics and alerts against actual staging traffic and cost limits.
6. Exercise `AI_AGENT_CONTROL_ENABLED=false` and confirm immediate guided-flow rollback.

No claim is made that AI hallucinations are eliminated. The enforced property is narrower and
stronger: model output cannot authorize an unknown action, invent an executable capability, approve
or activate a strategy, invent authoritative market/account facts, or report a successful action
without a validated recorded result.

## Files Changed

Configuration and release:
`.env.example`, `.env.production.example`, `.github/workflows/release-gate.yml`, `README.md`,
`scripts/check_release_invariants.py`.

Documentation:
`docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md`, `docs/ARCHITECTURE.md`,
`docs/BOUNDED_AGENT_CONTROL.md`, `docs/OPERATIONS.md`, `docs/PRODUCTION_DEPLOYMENT.md`, and this
report.

Migration and persistence:
`alembic/versions/2bdce3f40516_add_grounded_public_chat_state.py`,
`src/ai_market_monitor/db/models/__init__.py`, `src/ai_market_monitor/db/models/public_chat.py`.

Agent, capability, strategy, and scan services:
`schemas/agent_control.py`, `services/agent_control.py`, `services/agent_policy.py`,
`services/agent_tools.py`, `services/ai_model_routing.py`, `services/ai_setup_chat.py`,
`services/capability_extension_scope.py`, `services/capability_extensions.py`,
`services/interpreter.py`, `services/openai_interpreter.py`, `services/scanner.py`,
`services/strategy.py`, `services/template_catalog.py`, `provider_context.py`,
`services/market_preview.py`, and `worker.py` under `src/ai_market_monitor`.

Public support and API:
`services/public_chat.py`, `services/public_support_ai.py`, `services/public_support_tools.py`,
`schemas/public_chat.py`, `api/request_guards.py`, `api/routers/dashboard.py`,
`api/routers/dashboard_api.py`, `api/routers/public_chat.py`, `api/routers/system_brain.py`,
`core/config.py`, `core/startup.py`, and `main.py` under `src/ai_market_monitor`.

UI and operations visibility:
`static/hilalmarkets-public-chat.css`, `static/hilalmarkets-public-chat.js`,
`templates/hilal/dashboard/partials/builder_workspace.html`,
`templates/hilal/partials/public_chat.html`, `templates/system_brain.html`,
`services/system_brain.py`, and `telegram/rendering.py` under `src/ai_market_monitor`.

Tests and corpus:
`tests/conftest.py`, `tests/browser/conftest.py`, `tests/browser/test_dashboard_e2e.py`,
`tests/fixtures/public_support_question_corpus.json`, integration tests for setup chat, dashboard,
public chat, System Brain, and WhatsApp, plus unit tests for policy, tools, model routing,
capability extensions, release security, System Brain, and the support corpus.
