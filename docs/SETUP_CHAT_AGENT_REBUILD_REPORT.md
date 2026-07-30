# Setup Chat Agent rebuild

## The defect this removes

A user wrote three lines of exact market logic. The reply was:

> I'm ready. Describe the market behavior you want to scan or monitor.

That sentence was not a bug in a template. It was the visible end of an architecture:
regular expressions decided what a message *was* before the model saw it, gave the
whole message one label, and every label had a fixed answer.

## Architecture

Before:

```text
message
  -> decide_setup_intent (regex, authoritative, one label for the whole message)
  -> if not STRATEGY_PATCH: hardcoded sentence, turn over
  -> else deterministic regex extraction
  -> if regex found nothing: one bounded model call
  -> apply patch
  -> template chosen from the compiler outcome
```

After:

```text
authenticated message
  -> bounded Setup Agent (first semantic layer)
  -> multi-segment SetupAgentTurnPlan
  -> apply_setup_turn            <- the only executable authority
  -> StrategyDraftV2 patch validation
  -> deterministic compilation + semantic validation
  -> SetupTurnExecutionResult
  -> AI reply composed from that result
```

Per free-text turn, at most: **one planning call, one deterministic execution, one
reply call.** No tool loops, no interviewer, no fallback orchestrator.

Only authentication, ownership, idempotency by `client_message_id`, rate and input
limits run before the agent. Explicit UI actions — Scanner/Monitor, a server-offered
option, Review and approve — stay deterministic and cost no model call.

## Root causes found

| # | Root cause | Where it lived |
|---|---|---|
| 1 | One intent was assigned to a whole free-text message before the model saw it | `engine/setup_intent.py`, called authoritatively |
| 2 | `SetupIntent` is mutually exclusive, so a mixed turn could not be represented | schema shape |
| 3 | Five hardcoded sentences answered five intents | `_respond_without_mutation` |
| 4 | Regex extraction ran *before* the model and could end the turn | `LaunchStrategyPatchExtractor.extract` |
| 5 | The extraction call received the draft and the message but no dialogue | `_extraction_context` |
| 6 | `StrategyPatchExtraction.answer` was parsed and thrown away | `StrategyPatchNonMutation` handling |
| 7 | A turn could produce a patch *or* an answer, never both | one-intent orchestration |
| 8 | The model was told to pick an exact registered capability but never shown the register | `_PATCH_PROMPT` |
| 9 | Replies were templates picked around the compiler, not statements about what happened | `_render_current_draft` |
| 10 | README described an interviewer and a live Bounded Agent coordinator for Setup Chat | `README.md` |

Two further defects surfaced while building and are fixed:

| # | Found | Fix |
|---|---|---|
| 11 | Plan-level unsupported segments never reached the draft, so a request the platform cannot express compiled as though it had been understood | `_patch_with_unsupported` folds them into the applied patch, where they block eligibility |
| 12 | Repeating an instruction added a byte-identical duplicate condition. The old text-hash cache hid it; removing that cache exposed it | `_append_conditions` drops an addition whose measured fields already exist |

### Four more found only by calling a real model

None of these could be seen with a scripted model. Each one refused a *correct* turn.

| # | Found against `gpt-5.4-mini` | Fix |
|---|---|---|
| 13 | The model quoted the message perfectly and then reported offsets that were wrong. Language models cannot count characters, and the schema failed the plan on that arithmetic | The quote is the grounding check; the server locates the span itself (`TurnSegment.located_in`, `_locate_spans`). An absent quote is still refused |
| 14 | A strict schema requires every property, so the model sent `set_fields: null` — it had no field changes and cannot omit a key. Pydantic rejected the whole patch | `schemas/strict_mode.drop_absent_nulls`: a `null` for a non-nullable field means "no opinion", so the declared default applies |
| 15 | The model set `target_condition_id` to a name for the rule it was *creating*, and the turn was refused for referencing a rule that does not exist | Edits (`update_conditions`, `remove_conditions`) are still refused strictly. A segment or instruction *hint* that points nowhere is dropped, not fatal. The prompt now says the field names an existing rule only |
| 16 | Grounding required each value inside the one span the model quoted. In `on the 15m when the candle rises 5%` a model reasonably quotes `the candle rises 5%`, and the timeframe it read correctly is outside that quote | Values are checked against the **whole message**, which is what section 4 actually specifies. A value absent from the message is still refused |

All four are locked in by regression tests, including the paired negative case for each,
so widening the check did not weaken it.

## New schemas

`schemas/setup_agent.py`

| Schema | Purpose |
|---|---|
| `SegmentKind` | The ten things a span of a message can be doing |
| `TurnSegment` | One span with `exact_source_text`, `start_offset`, `end_offset`, kind, confidence, optional `target_condition_id` |
| `StrategyInstructionPlan` | One executable instruction, its segment, and the shortlisted key it chose |
| `ClarificationAnswer` | The user answering the open question — resolves it, never becomes a condition |
| `ClarificationRequest` | The smallest question that would unblock the draft |
| `ResponseDirective` | What the reply must cover, not the sentence to say |
| `UnsupportedSegment` | Something no exact mechanic expresses |
| `ApprovalIntent` | Recorded, never acted on |
| `SetupAgentTurnPlan` | The model's whole reading of the turn. A proposal |
| `SetupTurnExecutionResult` | What the server did. The only source of fact for the reply |
| `SetupConversationContext` | Language-only memory: open question, recent references, last changed/explained rules |
| `SetupAgentReply`, `SetupAgentPlanEnvelope` | The composed reply, and plan-or-direct-reply |

One invariant is enforced in the schema itself, so a bad plan cannot be built:

* a non-actionable kind (`SOCIAL_REPLY`, `USER_QUESTION`, …) **cannot** set
  `action_required` — conversation can never be marked executable

And two in the result, so a reply cannot overclaim:

* `applied` requires something recorded in `applied_instructions` or `answered_questions`
* `approval_eligible` requires `compile_status == "compiled"`

## One-tool orchestration

`engine/setup_turn_execution.py` — `apply_setup_turn(SetupTurnRequest) -> SetupTurnOutcome`

The ten checks, in order:

1. every segment's quoted text must be found in the **real** message; the server
   locates it and overwrites the model's offsets with the position it found
2. two actionable segments may not cover the same characters
3. the patch is validated by the existing `StrategyPatch` schema
4. every `condition_id` an *edit* names must exist in the current draft; a pointer that
   only labels a segment is dropped instead of failing the turn
5. new values must appear somewhere in this turn; an *edit* may inherit unchanged fields
   from the rule it names — `change that to at least 8%` does not restate the timeframe
6. every `capability_key` must come from this turn's server shortlist
7. the patch is applied to `StrategyDraftV2`
8. `validate_draft_semantics` runs
9. `compile_strategy_draft_v2` runs when the draft is eligible
10. a `SetupTurnExecutionResult` is returned

The model cannot weaken any of them: they run after it, on the server, on the raw
message.

## Capability context

`engine/capability_shortlist.py` builds the shortlist deterministically from the
registry snapshot before the agent is called. Each candidate carries
`capability_key`, `label`, description, supported operators, parameter schema,
direction support, supported timeframes, provider requirements, availability, plus
`covers` and `does_not_cover` examples.

The plan may name **only** a key from that list. A key outside it is refused as
`CAPABILITY_NOT_OFFERED`, however plausible it looks. When nothing is exact the
correct output is an unsupported segment or a clarification. Nearest-capability
substitution is not restored anywhere.

## Conversation context

`SetupConversationContext` persists in `context_json.setup_conversation_context`,
separate from executable state. It holds the active question and its answer shape,
recent references, and the rules last changed or explained. It exists so `yes`,
`the second option`, `make that stricter` and `remove the one we just added` can be
understood — and it can never become executable by itself, because nothing in it is a
condition.

## Model routing

`select_setup_model` previously received only the current message and an empty
accumulated setup, so a four-word turn like `make that stricter` routed to the cheap
tier. It now also receives `draft_condition_count`, `unresolved_field_count` and
`previous_turn_failed`, and adds six signals: `complex_existing_draft`,
`reference_to_previous_turn`, `answering_open_question`,
`previous_interpretation_failed`, `user_objection`,
`mixed_conversation_and_instruction`.

Measured:

| Turn | Tier | Reason |
|---|---|---|
| `hello` | simple | — |
| `RSI below 30 on 15m` | simple | — |
| `make that stricter` | complex | reference to a previous turn |
| `yes` with an open question | complex | answering an open question |
| `that is not what I said` | complex | user objection |
| mixed greeting + instruction + question | complex | mixed turn |
| same instruction, 6-condition draft | complex | complex existing draft |
| same instruction after a failure | complex | previous interpretation failed |

Model choice changes interpretation quality only. It never changes authority.

## Failure behaviour

Four stages stay distinct and map to distinct HTTP envelope stages:

| Agent stage | Envelope stage | Draft |
|---|---|---|
| `planning` | `extract` | untouched, `last_turn_failed` recorded, safe retry with the same key |
| `tool_validation` | `patch` | untouched, refusal reason recorded |
| `compile` | `compile` | applied but blocked, refusal shown in plain words |
| `response_composition` | — | **applied and kept** |

When execution succeeds but composing the reply fails, the reply is built
deterministically from `SetupTurnExecutionResult` by `deterministic_summary`. The turn
is never discarded and never reported as conversation.

## Observability

`context_json.last_turn_trace`, redacted, holds `message_id`, `planner_model`,
`planner_route_reasons`, `planner_latency_ms`, `segments`, `plan_confidence`,
`tool_called`, `patch_validation`, `semantic_diff`, `compile_status`,
`response_model`, `response_latency_ms`, `failure_stage`, `capability_shortlist`,
`lexical_hint`, `model_call_count`. No hidden reasoning, no credentials.

It answers: why was this classified this way (segments + kinds), which phrase caused
the patch (`applied_instructions.source_text`), why was a phrase ignored
(`ignored_non_actionable_segments.reason`), why was a question asked
(`clarifications`), why was a capability rejected (`capability_shortlist` plus the
refusal code), and what exactly changed (`semantic_diff`).

## Files changed

New:

* `src/ai_market_monitor/schemas/setup_agent.py`
* `src/ai_market_monitor/engine/capability_shortlist.py`
* `src/ai_market_monitor/engine/setup_turn_execution.py`
* `src/ai_market_monitor/services/setup_chat_agent.py`
* `src/ai_market_monitor/services/openai_structured_call.py`
* `tests/integration/test_setup_chat_agent_turns.py`
* `docs/SETUP_CHAT_AGENT_REBUILD_REPORT.md`

Changed:

* `src/ai_market_monitor/services/setup_chat_launch.py` — free text routes to the
  agent; `_respond_without_mutation` and `_draft_explanation` deleted; draft
  persistence split from message templating
* `src/ai_market_monitor/services/ai_setup_chat.py` — `launch_agent` injection
* `src/ai_market_monitor/services/ai_model_routing.py` — conversation-level signals
* `src/ai_market_monitor/engine/strategy_draft_v2.py` — identical-condition dedupe
* `tests/integration/test_setup_chat_launch_v2.py` — drives the agent
* `README.md`

## Removed

* the five hardcoded `_respond_without_mutation` sentences, including the readiness phrase
* `CONVERSATION` as an outcome of unrecognised market wording
* regex-first authority over free text — `decide_setup_intent` is now a hint carried in
  the planner payload as `lexical_hint_non_authoritative` and stored for telemetry
* `_draft_explanation`, dead once replies became contextual
* the text-hash result cache for free text: in a context-aware agent the same words can
  mean different things, so `yes` twice must be re-read. A genuine retry is still free —
  `client_message_id` catches it earlier
* README claims of an interviewer and a live Bounded Agent coordinator for Setup Chat

## Verification

`tests/integration/test_setup_chat_agent_turns.py` — 47 cases. The model is scripted
through a mock transport; the real planner payload, the real `apply_setup_turn` and the
real compiler all run. Assertions check what the server did, never the assistant's
wording, and every reply is checked against the banned readiness phrase.

| Scenario | Asserted |
|---|---|
| pure greeting, ×5 paraphrases incl. Arabic | no tool, no version change, one call only |
| pure acknowledgement | same |
| pure technical instruction | applied, compiled, approval eligible |
| conversation around an instruction, ×3 shapes | instruction survives; greeting recorded as not compiled |
| instruction + question | patch applied **and** question passed to the composer |
| correction + explanation request | threshold changed, no second rule added |
| answer to an active clarification, ×4 forms | question closed, zero conditions created |
| reference to an earlier condition | planner receives conditions, dialogue and context; removal applied |
| several independent conditions | each keeps its own threshold and timeframe |
| nested Boolean | `and(condition,or(condition,not(condition)))` preserved |
| unknown mechanic, ×3 paraphrases | unsupported requirement lands in the draft and blocks eligibility |
| exact registered capability | offered key accepted; invented key refused |
| excluded symbol | excluded, absent from inclusions, no violations |
| mixed Arabic/English, typo-heavy | applied |
| approval wording inside a material edit | `invalidated_by_edit`, `approval.approved` false |
| provider failure while planning, ×3 classes | stage `planning`, retryable, draft preserved |
| composing failure after success | work kept, factual summary, `failure_stage` recorded |
| fabricated span | `SPAN_NOT_GROUNDED` |
| overlapping actionable spans | `SPAN_NOT_GROUNDED` |
| ungrounded threshold | `VALUE_NOT_GROUNDED` |
| missing condition id on an edit | `CONDITION_NOT_FOUND` |
| conversation marked actionable, ×8 kinds | rejected at the schema |
| correct quote, wrong offsets | accepted; absent quote still refused |
| value stated outside the quoted span | accepted; value absent from the message still refused |
| hint pointing at a rule being created | dropped; an *edit* naming it still refused |
| strict-schema `null` for a container | the declared default applies |

### One real paid turn

Model `gpt-5.4-mini`, routed complex by `mixed_conversation_and_instruction`. Two model
calls, 6273 input and 2415 output tokens.

Input, one message doing three things at once:

> hey, thanks for the help earlier! also please monitor BTC/USDT on the 15m when the
> candle rises open-to-close by at least 5%, exclude ETH/USDT, and why does the
> timeframe matter?

Segments the model produced:

| Kind | Quoted text |
|---|---|
| `SOCIAL_REPLY` | `hey, thanks for the help earlier!` |
| `STRATEGY_INSTRUCTION` | `also please monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%, exclude ETH/USDT,` |
| `USER_QUESTION` | `and why does the timeframe matter?` |

What the server did: `status=applied`, `strategy_mutated=True`, version 1 → 2,
`compile_status=compiled`, `semantic_violations=[]`, `approval_status=eligible`,
`included=['BTC/USDT']`, `excluded=['ETH/USDT']`. The greeting and the question are
recorded in `ignored_non_actionable_segments` — answered in words, never compiled.

The reply:

> Thanks — happy to help. I've added BTC/USDT, excluded ETH/USDT, and set the 15m rule
> for a 5% open-to-close rise. The timeframe matters because the same move means
> something different on 15m, 1h, or 1d, so the rule only looks at candles in the
> interval you choose. This is still just a preview, and the next step is Review and
> approve.

It acknowledges the conversation, states exactly what changed, answers the question,
and says the preview is inactive. No readiness phrase.

## Remaining limitations

1. **One paid turn is not a quality measurement.** It proves the pipeline works with a
   real model end to end. Interpretation quality across the topic corpus is unmeasured;
   only the boundaries are proven, and those are proven with scripted plans.
2. The evaluator's `launch-core` suite still exercises the deterministic compiler, not
   the agent. A `launch-agent` suite would need paid runs.
3. The cheapest tier (`gpt-5.4-nano`, low reasoning) returned a malformed `formula` —
   an object where the schema declares an enum — on this nested plan schema. Mixed
   turns already route to the complex tier, so production traffic of this shape does not
   hit it, but a simple-tier turn on a complex draft is untested against a real model.
4. `decide_setup_intent` remains in the tree as a hint. It is no longer authoritative,
   but it is still a regex vocabulary that can drift from the model's reading.
5. The `SetupConversationContext` window is the last 20 messages. A reference to
   something older than that will not resolve.
6. `LaunchStrategyPatchExtractor` still serves server-offered option answers. That is a
   deliberate zero-model path, but it means two code paths can produce a patch.
7. No staging smoke test — no remote staging URL is configured for this repository.
