# Setup Chat compact-planner findings

Updated 2026-08-03 from the authenticated production path, Runs 9-11 artifacts,
independent literal goldens, focused production-path integration tests, schema export,
type/lint checks, and migration verification. Real-provider claims are kept separate
from deterministic or mocked-provider evidence.

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
| Minified bytes | 10,016 |
| `$defs` | 30 |
| Maximum object depth | 8 |
| Optional fields | 27 |
| Union branches | 66 |
| Canonical persistence models reachable | 0 |
| Envelope fields | 7 |

The early 4,096-byte target was an unmeasured prototype target. The current flat
Boolean action-specific schema has a regression ceiling of 10,500 bytes and depth 8;
tests fail if either ceiling is exceeded, if a
canonical model becomes reachable, if a server-owned field returns, or if a generic
`semantic_target + value/values` intent language is restored. The extra bounded branch
is a typed capability parameter: a scalar, homogeneous scalar list, or one shallow
registry-declared object. It is not a free-form JSON value.

The ceiling is not being presented as provider acceptance. It preserves measured room
for the non-recursive Boolean graph. A fresh real-provider matrix is still required for
the exact 10,016-byte schema.

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

## Runs 9-11 reproduced baseline

The stored artifacts were inspected before changing behavior. Their aggregate is:

| Run | Focus | Cases | Strict | Schema | Semantic | Repair |
|---|---|---:|---:|---:|---:|---:|
| `20260802T220621Z` | timeframe mapping | 10 | 80% | 80% | 80% | 0/1 |
| `20260802T232050Z` | nested Boolean logic | 10 | 10% | 50% | 40% | 0/7 |
| `20260803T000036Z` | operators and precedence | 20 | 50% | 60% | 50% | 0/10 |
| Combined | | 40 | 47.5% | 62.5% | 55% | 0/18 |

The largest measured failure family was Boolean/schema handling (Runs 10-11), followed
by repair returning no recovery value, repeated terminal 422 loops, and inclusive
operator loss. The named failure artifacts are preserved as production-path regression
tests in `test_setup_chat_run_9_11_closure.py`.

Root-cause traces:

1. `ConditionIntent.child_intents` made model output recursively describe executable
   trees. The provider omitted or flattened topology; the old classifier then called
   model omissions compiler invariants.
2. The repair path selected an arbitrary first path or received a contract that could
   not express the missing structure. Eighteen paid attempts recovered zero cases.
3. No persisted same-intent proof survived a refusal, so equivalent instructions paid
   again and hit the same 422.
4. Comparator output was trusted even when exact text said `at most`; one case compiled
   `lt` instead of inclusive `lte`.
5. Old Boolean evaluator scenarios reused generic scope/timeframe goals and could not
   distinguish correct membership from a same-shaped wrong tree.

## Code closure and proof

| Boundary | Production enforcement | Persisted/returned proof | Regression proof |
|---|---|---|---|
| Flat Boolean grammar | `validate_boolean_topology()`, `compare_topology()`, `compile_planner_intents()` | canonical `condition_ast`, topology derivations, executable hash | `test_invariant_boolean_topology.py`, Run 10/11 closure tests |
| Exact operators | `normalize_stated_comparator()` before operation creation | `DETERMINISTIC_OPERATOR_NORMALIZATION` derivation and canonical operator | `test_invariant_operator_authority.py`, operator-026 regression |
| Typed taxonomy | `_classify_plan_failure()`, `_failure_record()` | `SetupChatTurn.reply_json.failure_proof`, `last_turn_failure.proof` | `test_invariant_failure_taxonomy.py` |
| Bounded repair | `decide_repair()`, `SetupChatAgent._settled_plan()` | repair decision, attempts, usage, latency and cost in turn telemetry | failure-taxonomy and Run 9-11 integration suites |
| Retry convergence | `grounded_requirements_from()`, `repeat_state()` | `validated_intent_snapshots`, retry/failure funnel counters | unrelated-evidence, multi-leaf evidence and repeated-loop tests |
| Approval completeness | `apply_setup_turn()`, `validate_draft_semantics()`, `compile_strategy_draft_v2()` and launch gates | approval eligibility, executable/schema hashes, screening/preflight evidence | closure and approval lifecycle suites |
| Transport parity | persisted `SetupChatTurn` result rendered by backend/UI target adapters | explicit rendered/backend contract, status and requirement match fields | `test_run_9_11_semantic_contracts.py` |
| Operational controls | `SetupChatLaunchService.handle()`, `_enforce_user_cost_budget()` | fail-closed error code, immutable usage events, Redis reservation | launch-config and request-guard tests |
| Operator/repeat queue | `_queue_operational_issue()` and admin issue endpoints | `setup_chat_operational_issues` row plus immutable admin audit event | launch V2 and admin API integration tests |

`SystemBrainUserAdminService.delete_profile()` removes the issue row's user/chat/turn
links, exact source excerpt and proof payload while retaining only non-identifying
aggregate failure classification. Completed idempotent replays are checked before
dynamic kill-switch, beta-list and cost gates because replay is a read-only return of
the exact committed result, not new model work.

Retry evidence is scoped to the same normalized request and the same canonical draft.
Each condition/Boolean leaf gets an independent semantic path, so two thresholds or
timeframes cannot overwrite each other. It is planner context only; it cannot create an
operation or bypass the canonical writer.

The evaluator now uses topic-specific Boolean builders, a deterministic AST comparator,
typed product-failure proof, explicit transport versus stochastic metrics, zero semantic
coverage when no structured strategy exists, and challenger stopping only after the
same typed failure fingerprint repeats.

## Verification performed on 2026-08-03

- Exact schema: 10,016 bytes, 30 definitions, depth 8, 27 optional fields, 66 union
  branches, zero forbidden canonical models, exactly seven envelope fields.
- Focused production/evaluator suite: 592 passed.
- Wider unit/integration/evaluator suite: 3,608 passed, 1 skipped, 0 failed in
  549.6 seconds (3,609 collected).
- `ruff` over every changed and untracked Python file: passed.
- targeted `mypy` over 37 production/evaluator source files: passed.
- evaluator contract export `--check`: passed.
- Alembic graph: one head, `f6c24d8a10b7`.
- clean temporary SQLite upgrade through Alembic head: passed.
- all four environment files: 374 keys each, zero missing and zero duplicates.

## Real-provider evidence and acceptance boundary

Earlier artifacts prove that older compact schemas reached the configured provider and
also expose the Run 9-11 failures. They do **not** prove acceptance or reliability of
the final 10,016-byte flat-Boolean schema. On 2026-08-03 the Docker daemon was
unavailable, so the normal authenticated target could not be rebuilt or started. No
paid evaluator budget was authorized in this turn. Consequently, no new run ID, fresh
backend/UI pair, three-run critical-topic matrix, p50/p95 latency, or fresh cost result
exists for this exact tree.

## Remaining limits and readiness

- Run at least 24 unique scenarios per affected topic, backend and UI, for three fresh
  consecutive runs against this exact schema and code.
- Demonstrate >=98% independent canonical convergence; transport equality alone does
  not prove stochastic model consistency.
- Establish operator/timeframe/topology, 422, repair, latency and cost gates from those
  fresh runs.
- Exercise real authenticated approval separately from textual approval intent.
- Verify live methodology/watchlist registries, Sharia screening, provider adapters,
  market data, delivery and billing entitlement in monitored smoke tests.
- Drill the rollback/runbook and connect the new issue queue and anomaly metrics to the
  deployed alert destination.
- Recursively nested capability parameter objects remain intentionally unsupported;
  scalar, homogeneous-list and one shallow registry-owned object forms are supported.

Code-owned Run 9-11 closure is strongly regression-covered, but controlled private-beta
readiness is **not yet proven**. Current rating: **8/10 code readiness, ungraded launch
acceptance** until the external target and required repeated real-provider matrix pass.
