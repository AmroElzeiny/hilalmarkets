# Setup Agent launch-readiness closure

The AI-first direction was right. This pass fixed the cross-layer authority, lifecycle
and grounding defects that were left, without replacing `SetupChatAgent`,
`SetupAgentTurnPlan`, `apply_setup_turn`, `StrategyDraftV2`, the compiler or the
approval service.

## Root causes found

| # | Root cause | Why it mattered |
|---|---|---|
| 1 | Chat status was derived from the compiler, never from the approval binding | An approved setup was reset to `ready_for_approval` by a turn that only asked a question. The user's approval silently vanished |
| 2 | `apply_setup_turn` returned `eligible` on an already-approved draft | The caller then archived the approval and cleared the approved strategy ids |
| 3 | Screening, provider availability and the final status ran **after** the reply | A message could announce a ready draft that screening then blocked, and `_persist_draft_state` re-ran the compiler and could discover a *different* blocker than the one the user had just been told about |
| 4 | A patch arrived as one bag of operations grounded against the whole message | In `drop LTC, and is 5% a lot on a 15m candle?` the 5% and the 15m belong to a question — message-wide grounding let a question author a rule |
| 5 | Grounding was `str(value).casefold() in message.casefold()` | `1` matched `15m`; `2` matched the `20` in `20 candles`; `5` in `5m` grounded a 5 **percent** move; `gte` never matched `at least`, so correct readings were refused |
| 6 | Applied evidence came from `StrategyInstructionPlan.intent_summary` | That is the model's statement of intent, not a record of outcome. A reply could describe a change the compiler refused, and one instruction was attributed every condition in the draft |
| 7 | A clarification was a sentence, and `resolves_question` was trusted | An open item disappeared while the draft stayed blocked for exactly the reason the question existed. Closing one depended on `CorrectionV2.target` happening to equal an internal key |
| 8 | A capability node was accepted on its key alone | Nothing checked executability, availability, supported operator/direction/timeframe, required parameters, unknown parameters, schema bounds, or whether a user-facing number was grounded |
| 9 | The router received only capability *keys* | `low_capability_confidence` and `custom_terminology` were unreachable, so an ambiguous turn was priced as a simple one |
| 10 | The message was whitespace-collapsed before anything saw it | Line breaks and list structure — which tell three numbered rules from one sentence — were destroyed, and stored provenance disagreed with what the user typed. The current turn was also echoed back inside `recent_dialogue` |
| 11 | Idempotency was "has this key been seen?" | A retry after a mid-turn crash returned a session with **no assistant answer and no error** — the user's message appeared to vanish |
| 12 | Any segment could create an `UnsupportedRequirementV2` | Asking for advice once made a draft permanently unapprovable |
| 13 | The composer returned a free-form question | It could invent an executable clarification the server never agreed was needed, and re-ask one already answered |
| 14 | `AI_AGENT_*` bounds and docs implied they governed Setup Chat | They never did. The rollback instruction pointed at a flag with no effect on that path |

## Data flow

Before:

```text
message (whitespace-collapsed)
  -> agent: plan -> apply_setup_turn (semantic + compile only)
  -> compose reply                     <- reply written here
  -> _persist_draft_state: compile AGAIN, screening, status
                                       <- could contradict the reply
```

After:

```text
raw message (line breaks intact)
  -> agent: plan
  -> apply_setup_turn
       authorising segment per operation
       typed grounding, scoped to that segment
       capability contract against the registry
       apply patch -> canonical diff
       semantic validation
       compile
       Sharia policy + screened universe      <- gate
       provider availability                  <- gate
       approval status (binding first)
       final chat status
     => one SetupTurnExecutionResult
  -> compose reply from that result            <- nothing later can contradict it
  -> persist the same result
```

## Updated schemas

`schemas/setup_authorization.py` (new)

| Schema | Purpose |
|---|---|
| `AuthorizedPatchOperation` | One change plus the `authorizing_segment_id` that permits it. Twelve kinds, each validating its own payload |
| `ClarificationContract` | `question_id`, `target_type`, `target_field`, `target_condition_id`, `expected_answer_schema`, `mutating`, `allowed_options` |

`schemas/setup_agent.py`

* `SetupAgentTurnPlan.strategy_patch` → **`operations: list[AuthorizedPatchOperation]`**. One
  authorised route, so nothing can mutate without an author.
* `SetupTurnExecutionResult` gains `screening_status`, `provider_status`,
  `final_chat_status`, `allowed_clarifications`, `draft_read_model`, and three new
  consistency rules: eligibility is impossible while screening blocks or a provider is
  unavailable; an approved status cannot coexist with a mutation; an invalidation cannot
  happen without one.
* `AppliedInstruction` gains `operation` and typed `changes` from the canonical diff.
* `SetupAgentReply.clarification` → **`clarification_question_id`**, chosen from the
  server's list.
* `SetupConversationContext` gains `active_question` (the full contract),
  `answered_question_ids` and `clarifications_asked`.

`schemas/strategy_draft_v2.py` gains `StrategyPatch.remove_unresolved_keys` and
`remove_unsupported_keys` — closing an open item by its exact key instead of by prose.

## New deterministic validators

| Module | What it owns |
|---|---|
| `engine/semantic_grounding.py` | Typed grounding for symbols, numbers, percentages, prices, timeframes, operators, directions, formulas, lookbacks and Boolean shape. Numbers match on token boundaries **and** unit; comparators go through `detect_comparator`; formulas through `parse_percentage_formula`; timeframes through the turn reader's normaliser |
| `engine/draft_diff.py` | `diff_drafts(before, after)` → twenty typed change kinds, each carrying only the condition ids it touched. `is_material` separates a real change from closing an open item |
| `engine/capability_contract.py` | One registry-owned check: offered, executable, available, operator supported, direction supported, timeframe supported, higher-timeframe satisfied, required parameters present, no unknown parameters, type/enum/min/max from `parameter_schema`, trader-controlled numbers grounded, provider requirements emitted |

## Approval lifecycle corrections

`_has_valid_approval` reads the binding: approved **and** bound to this version **and**
this semantic hash. `_approval_status` then returns:

| Situation | Status |
|---|---|
| No semantic change, valid binding | `approved` |
| Material change, approval also requested this turn | `invalidated_by_edit` |
| Material change | `eligible` / `not_eligible` |
| Otherwise | `eligible` / `not_eligible` |

`_final_chat_status` reads that status first, so `approved` survives. `_assert_lifecycle`
then asserts the transition:

* a non-material turn cannot change the semantic hash
* a non-material turn cannot drop an approval
* a non-material turn cannot leave an approved setup unapproved
* a material change cannot keep the earlier approval

`SetupChatLaunchService` archives a previous approval and clears the approved strategy
ids only when `outcome.material_change` is true.

## Idempotency state

Keyed by `chat_session_id + client_message_id`, stored in the session document beside
the draft so state and completion commit in one write.

| Stored status | On a repeat |
|---|---|
| `COMPLETED` | The same final answer, no model call, no second patch |
| `RETRYABLE_FAILURE` / `RECEIVED` | Reprocess — nothing was applied |
| `PLANNING` / `EXECUTING` | HTTP 409 in-progress envelope, never a silent no-op |
| `PERMANENT_FAILURE` | The stored classified error |

## Removed misleading flags and documentation

* `AI_AGENT_CONTROL_ENABLED` now defaults to **false** in both env examples, with the
  comment stating plainly that it has no authority over Setup Chat and that turning it
  off is not a Setup Chat rollback. `AI_AGENT_ROLLOUT_PERCENT` drops to 0.
* The production example's claim that the flag gives "an immediate legacy-flow rollback"
  is replaced with the truth: there is no Setup Chat flag, so a rollback is a deployment
  rollback.
* `SETUP_CHAT_LAUNCH_V2_ENABLED` is documented as non-optional, matching the startup
  validation that already rejects false.
* New `SETUP_AGENT_*` settings are the ones that bound Setup Chat: planner retries,
  per-stage output-token caps, per-stage timeouts, per-turn cost cap, circuit-breaker
  failures and cooldown.
* README rewritten to describe segment-scoped authorization, typed grounding, the
  gates-before-composition order and the approval lifecycle.

## The fifteen invariants, and where each is enforced

Every one is asserted in `tests/unit/test_invariant_setup_closure.py` (51 cases).

| # | Invariant | Enforced at | Test |
|---|---|---|---|
| 1 | Pure conversation cannot alter draft, approval or status | `SetupAgentTurnPlan.requires_tool`; `_verify_authorization` rejects every `REPLY_ONLY_KINDS` segment | `test_1_*` (×16) |
| 2 | A product question cannot block a strategy | `_build_patch` accepts an unsupported segment only from `STRATEGY_INSTRUCTION` | `test_2_*` (×4) |
| 3 | A user question cannot authorize an executable rule | Same authorization check; also `TurnSegment.validate_span` forbids `action_required` on a non-actionable kind | `test_1_a_reply_only_segment_*` |
| 4 | Every mutation has exactly one actionable authorizing segment | `AuthorizedPatchOperation.authorizing_segment_id` is required; `validate_internal_references` rejects an unknown id; `validate_payload` rejects a malformed operation | `test_4_*` |
| 5 | Values from one segment cannot authorize another's mutation | `_verify_operation_grounding` grounds each value in that segment's own text; a new rule's fields are checked against its own clause | `test_5_*` |
| 6 | Every applied summary is derived from canonical before/after state | `_applied_instructions` reads only `diff_drafts`; the model's summary goes to the trace as `model_intent_summaries_diagnostic_only` | `test_6_*` (×2) |
| 7 | Clarifications cannot clear without resolving their declared target | `_resolved_questions` + `_target_resolved`; `ClarificationContract` validators | `test_7_*`, plus two agent-level cases |
| 8 | Composer clarifications must be server-authorized | `_allowed_clarifications` builds the list; `validated_clarification` is the only path to a stored question | `test_8_*` (×2) |
| 9 | Capability parameters satisfy the registry schema and source grounding | `capability_contract.validate_capability_node` | `test_9_*` |
| 10 | Final replies reflect screening and provider gates | Both gates run inside `apply_setup_turn`; `SetupTurnExecutionResult` refuses eligibility otherwise | `test_10_*` (×2) |
| 11 | Approved status survives every non-material turn | `_approval_status`, `_final_chat_status`, `_assert_lifecycle`, `_draft_is_approved` | `test_11_*` |
| 12 | Material edits always invalidate approval | Same, plus `apply_strategy_patch` resetting the binding | `test_12_*` (×2) |
| 13 | Same-key retries never duplicate work or disappear | `_replayed_turn` + `_record_turn` | `test_13_*` (×2) |
| 14 | Raw message structure reaches the planner unchanged | `handle` keeps `raw`; `SetupAgentTurnInput.normalized_message` is the only collapsed copy; `_recent_dialogue(exclude_message_id=…)` | `test_14_*` (×2) |
| 15 | No nearest-capability substitution exists | `_verify_capability_keys` at plan level plus the registry contract | `test_15_*` (×4) |

## Files changed

New: `schemas/setup_authorization.py`, `engine/semantic_grounding.py`,
`engine/draft_diff.py`, `engine/capability_contract.py`,
`tests/unit/test_invariant_setup_closure.py`, `tests/support/setup_agent_plans.py`,
`docs/SETUP_AGENT_LAUNCH_CLOSURE.md`.

Changed: `engine/setup_turn_execution.py` (rewritten around authorised operations and
the full gate sequence), `schemas/setup_agent.py`, `schemas/strategy_draft_v2.py`,
`schemas/strict_mode.py`, `engine/strategy_draft_v2.py`,
`services/setup_chat_agent.py`, `services/setup_chat_launch.py`,
`services/ai_model_routing.py`, `core/config.py`, `README.md`, `.env.example`,
`.env.production.example`, and both agent test suites.

## Verification

| Check | Result |
|---|---|
| `ruff` | clean |
| `mypy` | 254 files, no issues |
| Full offline suite | **exit 0** |
| `test_invariant_setup_closure.py` | 52 cases, all pass |
| `test_setup_chat_agent_turns.py` | 58 cases, all pass |
| launch-core ×3 | 16/16 each, 0 model calls, $0.00, false-executable 0.0 |

### One real paid turn, deliberately adversarial

Model `gpt-5.4-mini`, routed complex by `multiple_timeframes` and
`mixed_conversation_and_instruction`. **Two** model calls.

```text
hey, thanks for the help earlier!

please set this up:
1. BTC/USDT on the 15m, rises open-to-close by at least 5%
2. exclude ETH/USDT

and why does the timeframe matter? is 20% a lot for a 1h candle?
```

The `20%` and the `1h` are a decoy: they exist only inside the question.

| Segment | Quoted text |
|---|---|
| `SOCIAL_REPLY` | `hey, thanks for the help earlier!` |
| `STRATEGY_INSTRUCTION` | `please set this up:\n1. BTC/USDT on the 15m, rises open-to-close by at least 5%` |
| `STRATEGY_INSTRUCTION` | `2. exclude ETH/USDT` |
| `USER_QUESTION` | `and why does the timeframe matter? is 20% a lot for a 1h candle?` |

The line breaks survived into the segments, and the numbered list produced **two**
separate instruction segments.

Result: `applied`, version 1 → 2, `compiled`, `semantic_violations=[]`,
`approval=eligible`, `included=['BTC/USDT']`, `excluded=['ETH/USDT']`. The draft holds
exactly one rule — 15m, 5% — and **nothing from the question**. Invariant 5 holds against
a real model.

Applied evidence, generated from the canonical diff rather than the model's prose:

```text
added the rule open to close percentage gte 5% on 15m
added BTC/USDT to the watchlist
excluded ETH/USDT
```

The reply acknowledged the thanks, stated exactly those three changes, answered the
question, hedged on the part it could not ground (*"I'm not certain how common that is in
every market"*), and said the preview does nothing until approved.

One defect this turn exposed and that is fixed: the trace reported **3** model calls for a
two-call turn, because the planner was counted both inside `_plan_with_one_retry` and
again by its caller. That is the number an operator polices cost with, so it is now
asserted at 2 by `test_14_one_turn_never_reports_more_than_two_model_calls`.

## Remaining limitations

1. **Turn records live in the session document, not a dedicated table.** That gives one
   atomic write with the draft, which is what makes the state honest, but it caps history
   at 50 keys per session and cannot be queried across sessions.
2. **The circuit-breaker settings exist and are not yet wired to a shared store.** With
   one service instance per request there is nowhere process-local to keep the count, so
   `SETUP_AGENT_CIRCUIT_BREAKER_*` currently documents the intended bound rather than
   enforcing it. The per-turn call cap and the single bounded retry *are* enforced.
3. **Provider availability is conservative.** Only candle data is treated as wired;
   every other feed blocks approval. That is safe but will reject a capability whose
   provider is in fact configured elsewhere.
4. **`grounds_boolean_shape` checks operators, not structure.** It confirms the user wrote
   an `or`/`not`, not that the parenthesisation matches theirs.
5. **The composer's own wording is unverified.** It is constrained to the execution result
   and to `allowed_clarifications`, but nothing checks that the sentence it writes is
   faithful beyond those inputs.
6. **`LaunchStrategyPatchExtractor` still serves server-offered option answers** — a
   deliberate zero-model path, but a second route that can produce a patch.
7. **No staging smoke test**, and the paid verification is a single turn. Interpretation
   quality across the topic corpus is unmeasured.
