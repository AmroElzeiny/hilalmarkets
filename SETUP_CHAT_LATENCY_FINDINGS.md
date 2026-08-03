# Setup Chat compact-planner findings

Updated 2026-08-02 from the authenticated production path, independent literal
goldens, focused integration tests, and bounded live provider calls.

## Production request path

```text
SetupChatLaunchService._run_agent_turn
  -> SetupChatAgent.run_turn
  -> structured_call(PlannerIntentEnvelope)
  -> SetupChatAgent._checked_plan
  -> compile_planner_intents
  -> validate_setup_turn_plan
  -> apply_setup_turn
  -> compile_strategy_draft_v2
  -> screening and provider/runtime preflight gates
  -> deterministic summary or evidence-bound composer
  -> SetupChatLaunchService persistence/idempotency transition
```

There is no deterministic free-text mutation fallback and no second semantic
compiler. `compile_planner_intents()` produces the existing
`SetupAgentTurnPlan`; the existing authorization, grounding, dry-validation, and
canonical execution path remains the only writable authority.

## Final model-facing dependency graph

```text
PlannerIntentEnvelope
  -> PlannerSegment
  -> SemanticIntent
       -> action-specific IntentPayload union
            -> draft-field payloads
            -> ShariaPreferencePayload
            -> SymbolPayload
            -> condition add/update/remove payloads
                 -> ConditionIntent
                      -> CapabilityParameterIntent
            -> Boolean replacement payload
            -> snapshot restoration payload
  -> ClarificationAnswerIntent
  -> UnsupportedIntent
  -> ApprovalIntentSignal
```

Each payload branch has a distinct literal `action`, so the union is structurally
discriminated without adding the redundant OpenAPI discriminator mapping to the
wire schema.

The following canonical persistence models are not reachable from either the
planner or repair schema:

- `AuthorizedPatchOperation`
- `ConditionNodeV2`
- `OperandV2`
- `UnresolvedFieldV2`
- `ShariaPolicyV2`
- `DraftFieldPatch`
- `StrategyDraftV2`

The planner also receives no source-turn IDs, persisted segment IDs, operation
IDs, condition IDs, unresolved IDs, offsets, registry versions, executable or
workflow versions, or hashes. Conditions, clarifications, snapshots,
methodologies, and watchlists are represented by bounded turn-local aliases and
resolved by `PlannerReferenceContext` after validation.

## Exact on-wire schema

The measured object is the exact result of:

```python
strict_json_schema(PlannerIntentEnvelope, compact=True)
```

| Measurement | Final |
|---|---:|
| Minified bytes | 9,303 |
| `$defs` | 27 |
| Maximum object depth | 6 |
| Optional fields | 28 |
| Union branches | 68 |
| Canonical persistence models reachable | 0 |
| Envelope fields | 7 |

The early 4,096-byte target was an unmeasured prototype target. The final
action-specific schema was tested against the real provider. The measured regression
ceiling is 9,500 bytes and depth 6; tests fail if either ceiling is exceeded, if a
canonical model becomes reachable, if a server-owned field returns, or if a generic
`semantic_target + value/values` intent language is restored. The extra bounded branch
is a typed capability parameter: a scalar, homogeneous scalar list, or one shallow
registry-declared object. It is not a free-form JSON value.

Timeframe fields accept language-boundary aliases and normalize them through the
shared timeframe authority. For example, `60m -> 1h`, `24h -> 1d`,
`daily -> 1d`, and `four-hour -> 4h`. The verified segment retains the exact
authored text and semantic-role grounding still checks that segment.

## Semantic intent to canonical operation mapping

| Semantic action | Canonical operation |
|---|---|
| `set_mode` | `set_fields` |
| `set_name` | `set_fields` |
| `set_exchange` | `set_fields` |
| `set_quote_asset` | `set_fields` |
| `set_market_type` | `set_fields` |
| `set_sharia_preferences` | `set_sharia_policy` |
| `include_symbol` | `add_inclusion` |
| `exclude_symbol` | `add_exclusion` |
| `remove_included_symbol` | `remove_inclusion` |
| `remove_excluded_symbol` | `remove_exclusion` |
| `add_condition` | `add_condition` |
| `update_condition` | `update_condition` |
| `remove_condition` | `remove_condition` |
| `replace_boolean_structure` | `replace_groups` |
| `restore_owned_version` | `restore_snapshot` |

`compile_planner_intents()` is the mapping authority. It assigns intent,
operation, segment, and new-condition identity server-side. Partial updates inherit
unchanged fields, including capability identity and version. Boolean restructuring
may refer to offered owned rules without restating their executable fields, and
preserves those rules' original source provenance.

`normalize_planner_envelope()` removes byte-identical typed semantic proposals before
operation creation, and the compiler repeats that normalization defensively. A repeated
model output therefore cannot create duplicate conditions, journeys, or alerts.

## Sharia boundary

`set_sharia_preferences` carries only explicitly stated or exactly confirmed public
preferences. `SetupChatLaunchService._governed_planner_references()` offers active,
effective methodology and owned-watchlist aliases. `_policy_patch()` resolves one
unambiguous governed identity, inherits every unaffected `ShariaPolicyV2` field, and
emits the existing internal `set_sharia_policy` operation. `_ground_sharia_policy()`
then verifies the public preference against its authorizing segment.

The planner cannot create methodology versions, watchlist database identities,
governance choices, evidence conclusions, screening results, asset status, rulings,
or publication state. Asking whether an asset is halal remains non-mutating.
`set_universe_policy` is absent from and rejected by the production planner schema.
An ambiguous governed methodology or generic watchlist request returns one normal,
typed, database-backed clarification instead of a 422. The clarification uses a
server-owned ID and offered public options. A later answer naming one offered public
watchlist alias resolves through the governed registry and closes only when the
grounded `set_sharia_policy` operation writes the exact canonical target.

`_policy_patch()` enforces the negative-Boolean matrix. `True` selects the named
governed universe; two `True` values conflict. A lone `False`, or two `False` values,
cannot emit a no-op policy mutation and instead require an explicit alternative.
`False` is accepted only beside the explicitly grounded alternative in the same
segment. `fail_closed_preference=False` remains unsupported and fail-closed. An exact
no-op preference is removed before canonical execution, so it cannot consume an
executable version or invalidate approval.

## Validation and repair outcomes

| Outcome | Meaning | Model repair eligible |
|---|---|---:|
| `DETERMINISTIC_INTENT_NORMALIZATION` | Alias/identity/inheritance normalization without choosing trader meaning | No call needed |
| `SEMANTIC_INTENT_REPAIR_REQUIRED` | One exact model-owned semantic field is invalid but source can authorize a correction | Yes, once |
| `USER_INFORMATION_REQUIRED` | The trader did not supply one unambiguous value | No |
| `UNSUPPORTED_REQUIREMENT` | The requested mechanic or fail-open policy is unsupported | No |
| `COMPILER_INVARIANT_VIOLATION` | Internal operation failure cannot map to one model-owned field | No |
| `NON_RECOVERABLE_FAILURE` | Governance, ownership, authorization, or availability boundary | No |

`SetupChatAgent._checked_plan()` calls the single authoritative
`_classify_plan_failure()` boundary. A canonical error is repairable only when one
operation maps to one server-assigned intent, one unique model-owned path, and one
verified segment that can authorize a replacement. `PATCH_REJECTED`, opaque details,
multiple paths, and multiple intents are compiler invariants, never repair prompts.

`SetupChatAgent.run_turn()` owns the single repair allowance. An unreadable initial
response may use one shape-recovery call. A parseable semantic failure may instead use
one `SemanticIntentRepairDelta` call. A turn cannot use both. No mutation occurs before
the repaired envelope passes semantic compilation, canonical operation validation,
and dry validation.

The delta repair payload contains only the invalid intent, its verified source
segment, one relevant existing value, sanitized code/path, allowed repair kinds,
and minimum turn-local references. It does not contain the full envelope, draft,
requirement-state dump, canonical operations, or canonical schemas.

## Telemetry

`TurnTelemetry` records:

```text
request_acceptance
context_selection
planner_schema_serialization
planner_payload_serialization
planner_provider_wait
intent_deserialization
intent_validation
intent_normalization
semantic_compilation
canonical_operation_validation
dry_validation
repair_context_build
repair_provider_wait
repair_delta_application
semantic_recompilation
canonical_execution
compilation
screening
provider_validation
runtime_preflight
response_composition
persistence
total_turn
```

`total_turn` is derived once from the request clock and cannot be entered as a nested
stage. The complete per-turn payload is durably persisted under:

```text
setup_chat_turns.telemetry_json
```

`chat.context_json["turn_runtime"]["measured"]` remains a compatibility/read-model
mirror for the latest turn; it is not the immutable per-turn authority. The launch
service writes `SetupChatTurn.telemetry_json` before returning success or the original
failure. Its notes include schema bytes/depth/definition count, canonical models
exposed, semantic-intent count, compiled-operation count, expansion ratio, compiler
invariant count, model/provider calls, repair outcomes, and combined estimated/actual
cost.

## Golden equivalence evidence

Independent literal fixtures, rather than only the canonical-plan test adapter, now
cover:

- every supported draft field action;
- all include/exclude/remove symbol actions;
- a complete condition with movement direction, strategy bias, comparator,
  threshold, unit, and trigger/context/confirmation/reference timeframe roles;
- partial capability update with inherited capability key/version/parameters;
- a new capability with typed parameters and a server-owned registry version;
- reference definition, lookback, measured price field, and reference timeframe;
- Boolean replacement using offered owned condition aliases;
- unsupported requirement and textual-approval separation;
- clarification alias resolution;
- snapshot alias resolution;
- exact executable hash, version/revision, compile result, approval eligibility,
  screening/provider status, and compiler semantic equivalence for the complete
  multi-timeframe fixture;
- governed Sharia preference compilation in the contract invariant suite.
- ambiguous Sharia clarification creation and exact-answer convergence without a
  second writable path.
- frozen screening and market-data preflight, proving the authored definition is not
  overwritten and the resolved preflight universe is checked separately.

The evaluator JSON contracts are regenerated from the production Pydantic models by
`scripts/export_setup_chat_eval_contracts.py`; the exporter now passes in `--check`
mode. The evaluator fault-integration contract was aligned with the production
one-shape-recovery rule: an `empty_once` fault can recover once, still records the
applied-fault header, and never permits another repair.

## Live provider evidence

The prior 8,461-byte probe evidence is migration history only; it is not evidence for
the current contract. The exact current 9,303-byte schema was accepted by the real
provider during normal authenticated backend/UI evaluation: the provider returned
strictly parseable semantic envelopes and reached deterministic grounding and
compilation. That run is `chatbot_eval_runs/compact-timeframe-normal-20260802`.

| Run | Schema result | Production result | Notes |
|---|---|---|---|
| Normal paired `timeframe_mapping` sample | accepted | one backend case reached `awaiting_approval`; the paired case exposed grounding/classification defects | 2 cases, $0.027850435 measured spend |

The failure artifacts showed that explicit chart/view role wording was not accepted by
the shared timeframe-role grammar, and that a missing planner trigger was surfacing as
an internal compiler violation. `timeframe_role_is_explicit()` now accepts neutral
view nouns/linking verbs while retaining same-clause role binding, and
`_validate_new_condition()` reports a missing trigger as a typed semantic requirement
before canonical operation construction. The next normal run
(`compact-timeframe-normal-20260802-r2`) was unable to begin quality measurement
because the external model connection returned `TARGET_CONNECTION_REFUSED` twice at
readiness; the API, database, Redis, and evaluator authentication were healthy. That
is an external provider-connectivity dependency, not proof of a passing rerun.

The current-schema replay of the previously failing ambiguous-language case is
`chatbot_eval_runs/compact-closure-replay-009-backend-20260802`. It completed 1/1
backend case with strict pass, zero schema failures, zero repair attempts, zero
grounding rejection, and zero semantic-role swaps. It reached approval eligibility in
two turns with 16,406 ms total target latency and $0.00776566 target cost. Its first
report exposed an evaluator polarity defect: the judge's zero-is-bad score was being
misread as the zero-is-good `unsafe_guess_rate`. `deterministic_metrics()` now emits
the authoritative deterministic rate.

The post-fix paired replay is
`chatbot_eval_runs/compact-closure-replay-010-paired-20260802`. Both fresh routes
completed with strict pass and a passing release gate: 2/2 cases, schema and semantic
contract rates 1.0, clean/eventual success 1.0, zero unsafe guessing, zero role swaps,
zero repairs, and $0.020465475 measured spend. Mean latency/cost to a valid and
approval-eligible draft were 9,470.14 ms and $0.00460712 per case.

That paired run also found a real convergence limit: status, error class, turn count,
and approval state matched, but normalized contract and requirement-state match were
0.0. In one independent model sample the UI path retained Scanner mode while the
backend path stayed at its Monitor default after an incomplete first turn. This is not
redacted from the report and prevents a comprehensive parity claim.

The completed current-schema runs prove provider schema acceptance, not reliability or
p95. The broader repeated real-model matrix remains required.

## Remaining measurement and production limits

- The historical 24-case-per-topic evaluator matrix was not rerun in this closure pass.
- The bounded paired replay passed both cases but exposed mode/requirement convergence
  variance across independent model calls. A 24-case-per-topic paired matrix is still
  required to measure and close that reliability risk.
- Dynamic screening, methodology publication, watchlist content identity, provider
  adapters, and runtime preflight still depend on healthy production services.
- Recursively nested capability parameter containers remain intentionally unsupported
  by the bounded compact contract. Scalars, homogeneous scalar lists, and one shallow
  registry-declared object are supported and schema-grounded; deeper structures fail
  closed rather than being represented as generic model JSON.

These are reasons not to claim 10/10 launch readiness from this pass alone.

## Closure update — current working tree

The remaining compact-planner code limits identified above are now closed:

- `CapabilityParameterIntent` carries a typed scalar, homogeneous scalar list, or
  shallow registry-declared object. `_typed_parameter()` verifies the exact registry
  schema before a canonical operation exists.
- `_policy_patch()` resolves an explicitly named offered watchlist through
  `PlannerReferenceContext.watchlist_matches_in_text()`; the governed ID/version stays
  server-owned and `_ground_sharia_policy()` verifies the public answer.
- `_condition_evidence_segment()` creates one server-owned contiguous evidence span
  only from adjacent, unclaimed, exact actionable segments. It cannot cross a question
  or another operation.
- `_reject_omitted_explicit_role()` never inserts an omitted trader-controlled role.
  One exact, grounded missing role becomes `SEMANTIC_INTENT_REPAIR_REQUIRED` and uses
  the single repair allowance; ambiguous or genuinely missing roles become
  `USER_INFORMATION_REQUIRED`; unsupported relationships stay explicit and fail
  closed. The rule applies to timeframe roles and every other role-bearing trader
  value.
- `timeframe_role_is_explicit()` accepts neutral chart/view wording such as “the 4h
  chart provides directional context” without relaxing same-clause role binding.
- `semantic_contract_hash()` normalizes session-owned evaluator provenance, not trader
  semantics. Backend/UI parity now compares the same normalized contract.
- Explicit new capabilities are accepted only when the current governed shortlist
  contains the exact capability, and the compiler binds its server-owned version.
  Partial edits inherit unchanged capability identity/version without rediscovery.

`chatbot_eval_runs/compact-timeframe-role-completion-20260802` is the final
real-provider replay before parity normalization: both fresh routes completed cleanly
in one turn, reached `awaiting_approval`, preserved `15m` context and `5m` trigger,
had zero repair attempts, zero grounding rejections, and spent $0.01141696. Recomputing
that artifact with the current evaluator yields 1.0 for normalized contract, status,
error-class, approval-state, and requirement-state parity, with a zero turn-count
delta. The final replay after the evaluator normalization is recorded separately by
the active run.

Code-owned Corrections 1-6 are enforced by the focused production-path suite and the
independent literal goldens. Comprehensive real-provider acceptance is still open:
repeat the 24-case critical-topic matrix, close the observed backend/UI mode and
requirement convergence variance, establish real p95 latency/cost, and keep governed
screening, watchlist, provider-adapter, and runtime-preflight services healthy.
Arbitrarily recursive capability parameter objects remain intentionally unsupported
and fail closed.
