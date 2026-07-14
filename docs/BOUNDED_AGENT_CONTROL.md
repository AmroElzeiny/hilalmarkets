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

When `AI_AGENT_CONTROL_ENABLED=true` and the authenticated user falls inside the deterministic
`AI_AGENT_ROLLOUT_PERCENT` cohort, a typed Setup Chat turn follows this path. Shadow mode evaluates
all eligible typed turns regardless of the live percentage, but never executes a tool:

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

Approval, activation, billing, entitlements, notifications, registry mutation, dynamic-mechanic
creation/repair, arbitrary HTTP, SQL, Python, shell, filesystem, and trade tools are not exposed.
The model cannot add tools to its own catalog.

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

Defaults are conservative and cannot be changed by the model:

| Setting | Default |
|---|---:|
| `AI_AGENT_CONTROL_ENABLED` | `false` |
| `AI_AGENT_SHADOW_MODE` | `false` |
| `AI_AGENT_ROLLOUT_PERCENT` | `0` |
| `AI_AGENT_MAX_STEPS` | `4` |
| `AI_AGENT_MAX_TOOL_CALLS_PER_TURN` | `4` |
| `AI_AGENT_MAX_REPEATED_CALLS` | `1` |
| `AI_AGENT_TIMEOUT_SECONDS` | `45` |
| `AI_AGENT_TOOL_TIMEOUT_SECONDS` | `30` |
| `AI_AGENT_MAX_OUTPUT_TOKENS` | `1800` |
| `AI_AGENT_MAX_ESTIMATED_COST_USD_PER_TURN` | `0.02` |
| `AI_AGENT_PARALLEL_TOOL_CALLS` | `false` (enforced) |

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

System Brain shows rollout state, first-tool agreement in labeled shadow turns, completion/fallback
rates, contained ungrounded claims, rejected calls, deterministic compile success, clarification
turns, unsupported-condition leakage, tool success/latency, calls per turn, tokens, and estimated
cost. Tool-result summaries contain counts and states only, not prompt text or raw provider payloads.

## Rollout and Rollback

1. Apply migrations and deploy with both feature flags `false`.
2. Set `AI_AGENT_CONTROL_ENABLED=true` and `AI_AGENT_SHADOW_MODE=true` for staging. The model may
   propose one first action, but no agent-selected tool executes; users continue through legacy.
3. Exercise `tests/fixtures/agent_control_corpus.jsonl` and normal beta traffic. The corpus covers
   messy multi-intent, vague, corrective, adversarial, unsupported-data, idempotency, injection,
   monitor-status, greeting, and provider-unavailable turns.
4. Review System Brain for tool-selection agreement, forbidden attempts, invalid calls, latency,
   estimated cost, fallback rate, clarification evidence, compiler success, and any ungrounded claim.
5. Require zero forbidden executions and zero unsupported-condition leakage. Investigate every
   ungrounded final claim even though it was contained.
6. Disable shadow mode and set `AI_AGENT_ROLLOUT_PERCENT` to a small value such as `1` or `5` only
   after shadow evidence improves over legacy without safety regression. Membership is a stable
   server-side hash of the authenticated user ID; the model cannot select or expand its cohort.
   Raise the percentage in measured stages while watching the same safety and quality metrics.
7. Roll back immediately by setting `AI_AGENT_CONTROL_ENABLED=false`. No database rollback is
   required; the legacy path remains intact.

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
