# Bounded Agent Control

## Purpose

Bounded Agent Control lets the AI Setup Chat coordinator choose the next safe application action
for messy or multi-intent messages. It does not replace the capability registry, strategy compiler,
scanner, monitor services, approval workflow, or deterministic evaluator.

The enforced invariant is:

> A hallucinating model cannot execute an unknown or unauthorized action, create executable
> trading logic, invent authoritative market facts, approve a strategy, activate a monitor, or
> claim that an action succeeded without a successful recorded tool result.

This contains model errors; it does not claim that hallucinations are eliminated.

## Request Path

With the controlled-beta profile (`AI_AGENT_CONTROL_ENABLED=true`,
`AI_AGENT_SHADOW_MODE=false`, `AI_AGENT_ROLLOUT_PERCENT=100`), every authenticated user's typed
Setup Chat turn follows this path:

1. Existing authentication, ownership, message idempotency, and safety checks run first.
2. `AgentPolicyService` builds server context and a small allowed-tool list from current state.
3. `AgentControlService` sends the current request, bounded history, durable state, and only those
   tools to the OpenAI Responses API with `store=false` and parallel calls disabled.
4. A requested function name and arguments are checked against the exact offered list and strict
   local Pydantic schema.
5. Ownership, entitlement, chat state, explicit scan intent, canonical hash, duplicate fingerprint,
   timeout, call count, token use, and estimated cost are checked server-side.
6. `AgentToolService` calls an existing TraceEdge domain service and returns a standard result
   envelope. It contains no independent trading logic.
7. The function result and the model's response items are returned to the model for the next step.
8. The final structured response is validated and checked against recorded evidence.
9. If the agent is unavailable or invalid before producing authoritative work, Setup Chat uses the
   unchanged legacy flow without inserting a duplicate user message.

Scheduled scan evaluation remains deterministic and LLM-free.

## Modules

- `schemas/agent_control.py`: strict tool arguments, result envelopes, budgets, actions, and final
  response contract.
- `services/agent_policy.py`: per-step tool availability, classifications, ownership, entitlement,
  state, and fail-closed call validation.
- `services/agent_tools.py`: adapters from function calls to existing registry, compiler, provider,
  scanner, draft, and monitor services.
- `services/agent_control.py`: bounded Responses function-calling loop, grounding checks, usage,
  traces, timeout/cost containment, and deterministic fallback responses.
- `services/ai_setup_chat.py`: feature-flagged integration that preserves the legacy path and chat
  idempotency.

## Tool Catalog

| Tool | Class | Authority and limits |
|---|---|---|
| `resolve_trading_capabilities` | Safe | Resolves exact user-authored fragments against the live registry. It cannot invent keys. |
| `validate_capability_selection` | Guarded | Validates a shortlisted key, parameters, required/optional intent, direction, comparator, timeframe, availability, and source fragment. |
| `compile_strategy_draft` | Guarded | Calls the existing interpreter/compiler and returns only a schema-validated draft, lint, assumptions, translation sheet, and canonical hash. |
| `get_market_snapshot` | Safe | Calls the configured provider-backed snapshot service. Failure remains unavailable; values are never estimated. |
| `run_one_time_scan` | Confirmation required | Runs one idempotent Scanner-mode request only after an explicit current-turn request, entitlement check, and exact draft hash. |
| `inspect_current_draft` | Safe | Reads the current persisted draft, lint, unresolved issues, confidence, and approval eligibility. |
| `get_monitor_status` | Guarded | Reads only a monitor ID offered from the authenticated user's owned set. |
| `list_watch_plans` | Guarded | Lists bounded status metadata for the authenticated user's own Watch Plans. |
| `inspect_screened_watchlist` | Guarded | Reads the authenticated user's saved screened assets without overriding current policy. |
| `get_recent_scanner_result` | Guarded | Reads an authoritative recent Scanner result for the current draft/session. |
| `request_custom_capability` | Confirmation required | Queues only the exact unresolved user-authored fragment after explicit current-turn consent and an OHLCV-only scope check. |
| `get_custom_capability_status` | Guarded | Reads only the extension bound to the current authenticated chat. |

Approval, activation, billing, entitlements, notifications, registry mutation, dynamic-mechanic
repair/application, arbitrary HTTP, SQL, Python, shell, filesystem, and trade tools are not
exposed. The model cannot add tools to its own catalog. A custom-capability request calls the
existing certification service; it does not execute model-authored code or make the model the
certification authority.

## Grounding and Failure Rules

- A scan-completed claim requires a successful `run_one_time_scan` result.
- A current market value requires provider evidence from snapshot or scan output.
- A valid/ready-draft claim requires a successful compiler or draft-inspection result.
- A monitor-status claim requires a persisted, user-owned monitor result.
- Capability keys must exist in the registry and be present in the current shortlist.
- Numeric capability parameters must be present in user-authored text when they represent a user
  threshold/window; the model cannot manufacture a convenient value.
- Required/optional intent, selected direction, and crossing/comparator language must agree with
  the source fragment.
- Unknown, forbidden, not-offered, malformed, duplicate, stale-hash, and foreign-owner calls fail
  before domain execution.
- An unavailable tool result permits only an unavailable/error explanation, never an estimate.
- Approval or activation language in a model response has no state-changing effect and is rejected
  as an ungrounded claim.
- URLs and executable UI payloads are not accepted in the model's final response. UI actions are
  server-defined enums.

## Limits

Application defaults remain conservative. The controlled-beta environment profile deliberately
overrides only the three rollout fields and enables certified extensions:

| Setting | Application default | Controlled beta |
|---|---:|---:|
| `AI_AGENT_CONTROL_ENABLED` | `false` | `true` |
| `AI_AGENT_SHADOW_MODE` | `false` | `false` |
| `AI_AGENT_ROLLOUT_PERCENT` | `0` | `100` |
| `CAPABILITY_EXTENSION_ENABLED` | `false` | `true` |
| `AI_AGENT_MAX_STEPS` | `4` | `4` |
| `AI_AGENT_MAX_TOOL_CALLS_PER_TURN` | `4` | `4` |
| `AI_AGENT_MAX_REPEATED_CALLS` | `1` | `1` |
| `AI_AGENT_TIMEOUT_SECONDS` | `45` | `45` |
| `AI_AGENT_TOOL_TIMEOUT_SECONDS` | `30` | `30` |
| `AI_AGENT_MAX_OUTPUT_TOKENS` | `1800` | `1800` |
| `AI_AGENT_MAX_ESTIMATED_COST_USD_PER_TURN` | `0.02` | `0.02` |
| `AI_AGENT_PARALLEL_TOOL_CALLS` | `false` | `false` (enforced) |

No database transaction is intentionally held during an OpenAI or market-provider network call.
Before each Responses call, the server estimates the maximum possible call cost from the serialized
request size and configured output-token ceiling. It refuses the call before transmission when that
upper bound would exceed the remaining per-turn budget, then checks reported usage again after a
response.
If `OPENAI_MODEL` has no positive input, cached-input, and output rates in
`OPENAI_MODEL_PRICING_USD_PER_MILLION`, the coordinator performs no OpenAI call and falls back to
the legacy flow because its cost budget cannot be enforced.

## Persistence and Privacy

Migration `b4c5d6e7f8a9` adds `agent_runs` and `agent_tool_calls`. Traces store model, effort, timing,
step/call counts, token usage, estimated cost, budget/timeout outcomes, redacted error type, policy
decision, argument hash, redacted arguments, evidence references, and shadow comparison. They do not
store hidden reasoning, credentials, raw provider payloads, or raw setup fragments in tool arguments.

System Brain shows live rollout state, completion/fallback rates, contained ungrounded claims,
rejected calls, deterministic compile success, approval conversion, correction and clause-gap
counts, provider-limited turns, custom certification/repair/quarantine state, tool success/latency,
calls per turn, tokens, and estimated cost. Public-support source coverage, validation failures,
inquiries, email state, ratings, latency, cost, and knowledge gaps appear in the same protected
owner console. Tool-result summaries contain counts and states only, not prompt text or raw
provider payloads.

## Rollout and Rollback

1. Apply migrations and exercise `tests/fixtures/agent_control_corpus.jsonl`, the Setup Chat
   regressions, and the public-support corpus before deployment. The corpora cover
   messy multi-intent, vague, corrective, adversarial, unsupported-data, idempotency, injection,
   monitor-status, greeting, provider-unavailable, multilingual, typo, account, refusal, and
   escalation turns.
2. Run a controlled staging proof with the exact release profile: agent enabled, shadow disabled,
   rollout 100, capability extensions enabled, and public AI support enabled.
3. Review System Brain for forbidden attempts, invalid calls, latency,
   estimated cost, fallback rate, clarification evidence, compiler success, and any ungrounded claim.
4. Require zero forbidden executions and zero unsupported-condition leakage. Investigate every
   ungrounded final claim even though it was contained.
5. Open the private beta only after the live OpenAI, Binance, worker, Scanner, Watch Plan, public
   multi-turn, multilingual, inquiry-outbox, and SMTP delivery checks succeed.
6. Roll back immediately by setting `AI_AGENT_CONTROL_ENABLED=false` and restarting the API. No
   database rollback is required; persisted drafts remain available to the guided flow. Do not use
   shadow mode or a partial cohort as the deployed private-beta posture.

## Adding a Tool Safely

1. Add a domain-specific argument model with strict types, bounds, and no user/role/ownership fields.
2. Add the name to the catalog and classify it in `agent_policy.py`.
3. Define exactly when authoritative server state may offer it.
4. Recheck ownership, entitlement, state, and immutable references in policy and domain service.
5. Adapt an existing service in `agent_tools.py`; do not add trading logic to the adapter.
6. Return the standard result envelope with evidence and safe next-action enums.
7. Add grounding rules for any success/fact claims enabled by the tool.
8. Add unknown/not-offered/malformed/duplicate/timeout/ownership/prompt-injection tests and shadow
   evaluation cases before rollout.

## Remaining Model Risk

The model can still choose an inefficient valid sequence, ask an imperfect clarification, or phrase
a poor explanation. The server contains those errors through the offered-tool boundary, strict
arguments, deterministic services, recorded evidence, final-response validation, budgets, and safe
legacy fallback. Human review and explicit external approval remain required for strategy logic.
