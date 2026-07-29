# AI Setup Chat Launch V2

## Architecture change

Before:

```text
message
  -> turn classifier
  -> bounded agent / policy / tools
  -> OpenAI router
  -> legacy interviewer or fallback
  -> capability or hybrid resolution
  -> accumulated-text interpretation
  -> formula compiler
  -> approval handling
```

After:

```text
message
  -> exclusive deterministic intent gate
  -> zero-call parser or one StrategyPatch extraction
  -> authoritative StrategyDraftV2
  -> deterministic compiler and semantic validator
  -> inactive Canvas preview
  -> one authenticated approval action
  -> existing activation services
```

## Production path

```text
Authenticated message API
  -> deterministic exclusive intent gate
  -> zero-call parser or one structured StrategyPatch extraction
  -> StrategyDraftV2 patch validation and versioning
  -> deterministic StrategyDefinition compiler
  -> semantic invariant validation
  -> inactive Canvas preview
  -> authenticated Review and approve endpoint
  -> existing verification, versioning and activation services
```

The public Support agent is not part of this path.

## Disabled launch paths

The authenticated production route does not call bounded-agent orchestration,
legacy interviewing, semantic fallback, hybrid capability resolution, runtime
custom-capability creation, nearest-capability substitution, tool loops, or
whole-history recompilation. Those modules remain available only to isolated tests
and experiments. `SETUP_CHAT_LEGACY_TEST_COMPAT_ENABLED=true` is rejected in staging
and production.

## Canonical field map

| Concern | `StrategyDraftV2` field | Compiled DSL field |
|---|---|---|
| Draft identity | `draft_id`, `version`, `semantic_hash` | strategy-version metadata |
| Scanner or monitor | `mode` | `trigger_mode` and launch action |
| Included assets | `universe.included_symbols` | `universe.include_symbols` |
| Excluded assets | `universe.excluded_symbols` | `universe.exclude_symbols` |
| Exchange and quote | `market_scope` | `universe.exchange`, `quote_currencies` |
| Direction | each condition `direction` | condition parameters and strategy direction |
| Formula | each condition `formula`, `operands` | left operand parameters |
| Operator | each condition `operator` | condition comparator |
| Threshold and unit | each condition `threshold`, `unit` | right constant and parameters |
| Timeframe roles | trigger/context/confirmation/reference fields | base/supporting timeframes and parameters |
| Boolean logic | recursive `condition_ast` | recursive `ConditionGroup` |
| Exact evidence | `source_turn_id`, `source_fragment` | condition source fragment |
| Missing definitions | `unresolved_fields` | blocks compilation |
| Unsupported mechanics | `unsupported_requirements` | blocks compilation |
| Provider needs | `provider_requirements` | blocks when unavailable |
| Approval | `approval` binding | immutable approved strategy version |

## Approval contract

Approval occurs only through the authenticated Review and approve action. Chat
phrases never approve. The request identifies the compiled schema hash, V2 draft
version and V2 semantic hash. The server binds those values to the authenticated
user, full conversation snapshot hash and UTC timestamp. Any material patch or
reversion creates a new version and clears the approval binding.

## Migration

When a session has no V2 document, the migration adapter reads its existing compiled
`StrategyDefinition` once. New writes then use only `context_json.strategy_draft_v2`.
The old free-text fragments and flat state remain read-only compatibility evidence.
Legacy operators outside AND, OR and NOT are retained for review and become blocking
unsupported requirements; they are never silently flattened into executable logic.

## Semantic gates

- Included and excluded universes are disjoint.
- A symbol named in order to exclude it is never also added as an inclusion.
- Excluded symbols cannot enter executable condition fields.
- Every executable condition has turn and source-fragment provenance.
- Numerical conditions have an exact right operand or threshold.
- Every condition carries the operator, unit and direction its own formula allows —
  see `FORMULA_CONTRACTS` in `schemas/strategy_draft_v2.py`.
- Every condition states its own trigger timeframe; it never borrows a neighbour's.
- Trigger, context and confirmation roles are exclusive: one timeframe, one role.
- Recursive AST order and grouping are preserved.
- Blocking unresolved, unsupported or provider requirements prevent eligibility.
- Approval version and hash must match the current canonical draft.
- No capability is selected by similarity.

### The formula contract

A formula fixes which comparisons mean anything, what the threshold counts, and
which side it can measure. Without the table, `cross` compared with `gte`,
`sweep_and_reclaim` carrying a percentage, and a `high_to_low` move called long all
serialized, compiled and monitored a market event nobody described.

| Formula | Operators | Unit | Side it cannot measure |
|---|---|---|---|
| `open_to_close_percentage` | `gt gte lt lte eq` | percent | — |
| `close_to_close_percentage` | `gt gte lt lte eq` | percent | — |
| `reference_to_current_percentage` | `gt gte lt lte eq` | percent | — |
| `high_to_low_percentage` | `gt gte lt lte eq` | percent | long |
| `low_to_high_percentage` | `gt gte lt lte eq` | percent | short |
| `previous_candle_reference` | `gt gte lt lte eq` + crosses | price | — |
| `fixed_reference_level` | `gt gte lt lte eq` | price | — |
| `lookback_reference_level` | `gt gte lt lte eq` + crosses | price | — |
| `cross` | `crosses_above crosses_below` | price | — |
| `sweep_and_reclaim` | `is_true is_false` | boolean | — |
| `capability` | the registry's own contract | — | — |

A *signed* threshold is deliberately not restricted. `-2%` with a long bias is a
legitimate dip rule, and the trader's own sign is never overruled.

### One comparison vocabulary

`engine/comparators.py` owns every operator phrase and exposes
`comparator_alternation()` so a caller can embed the exact vocabulary in its own
pattern. Hand-written copies had already drifted: the launch parser knew `equal to`
but not `at least`, and the gate in front of it knew `above` but not `equal to`, so
`price is equal to 3500` was refused by the gate and never reached the parser that
understood it.

## Transport failures are never chatbot answers

`setup_chat_error_envelope` classifies a failed turn before it can be scored. Every
transport failure returns a `TARGET_*` code at stage `provider` with
`retryable=true`; only a genuine strategy defect returns a compile code. The chain is
walked, so a transport error re-raised by its caller is still found.

| Failure | Code |
|---|---|
| refused connection | `TARGET_CONNECTION_REFUSED` |
| server closed the response early | `TARGET_PARTIAL_STREAM` |
| any other transport error | `TARGET_TRANSPORT_FAILURE` |
| DNS | `TARGET_DNS_RESOLUTION_FAILURE` |
| connect / read / total timeout | `TARGET_CONNECT_TIMEOUT`, `TARGET_READ_TIMEOUT`, `TARGET_TOTAL_TIMEOUT` |
| 409 / 429 / 5xx | `TARGET_HTTP_409`, `TARGET_HTTP_429`, `TARGET_HTTP_5XX` |
| body was not valid JSON | `TARGET_INVALID_JSON` |

## Running a paid evaluator run against a local target

Any planned topic that injects a fault makes the readiness probe send an evaluator
fault header. The target refuses that header unless **all three** of these hold:

```text
APP_ENV=test
AI_SETUP_EVALUATOR_ENABLED=true
AI_SETUP_EVALUATOR_FAULTS_ENABLED=true
```

`APP_ENV=development` is not enough, and it is the one people miss because the two
flags look right. A refusal returns HTTP 403 `evaluator_control_unavailable: <the
setting that caused it>`, the evaluator classifies it as
`EVALUATOR_FAULT_CONTROL_UNAVAILABLE`, and the run stops as
`FAILED_CONFIGURATION` **before** spending anything.

`hm-chatbot-eval doctor` reads the target's `APP_ENV` from `/health` and reports
"Backend accepts evaluator fault control", so this is knowable for $0 before a run.
`validate_runtime_configuration` still forbids these settings in staging and
production, so a real deployment can never accept an injected fault.

## Commands

```powershell
python scripts/export_strategy_draft_v2_schemas.py
python -m pytest tests/unit/test_setup_intent_v2.py tests/unit/test_strategy_draft_v2.py
python -m pytest tests/unit/test_invariant_launch_v2_contracts.py
python -m pytest tests/integration/test_setup_chat_launch_v2.py
python -m hm_chatbot_eval launch-core
python -m hm_chatbot_eval recorded-replay <SOURCE_RUN_ID>
python -m hm_chatbot_eval doctor          # run before any paid run
```

## Second pass — 2026-07-29

The first pass recorded 13/13 launch-core. Re-running it found **11/13**, and the
audit that followed found seven more defects. All are fixed and covered by
`tests/unit/test_invariant_launch_v2_contracts.py`.

| # | Defect | Where the rule now lives |
|---|---|---|
| 1 | The gate in front of the launch parser hand-listed operators. It knew `above` but not `equal to`, so `price is equal to 3500` was refused and never reached the parser that understood it. | `comparators.comparator_alternation()` |
| 2 | `sweeps below … and reclaims it` is one mechanic, but the fragment reader cuts it at `and`. The gate saw a pierce with no reclaim and refused. | `_is_direct_primitive_fragment(turn_text=…)` |
| 3 | Nothing checked that a formula's operator, unit and direction belong together. `cross` with `gte`, `sweep_and_reclaim` with a percentage, and a `high_to_low` move called long all compiled. | `FORMULA_CONTRACTS` |
| 4 | One marker word gave its role to every timeframe in the clause, so `using the 4h as context when the 15m rises` produced two context timeframes and no trigger. | `extract_timeframe_roles` |
| 5 | `confirmed on the 1h` and `with 1h confirmation` made the confirming candle the trigger. | `_resolve_timeframe_roles` |
| 6 | A refused connection, a dropped response, and an unparseable body were all reported as `STRATEGY_COMPILE_FAILED` — infrastructure counted as chatbot quality. | `setup_chat_error_envelope` |
| 7 | Every symbol a turn *mentioned* was copied into the inclusions, including the ones it named in order to exclude them, so `BTCUSDT only, exclude LTCUSDT` was rejected whole. `but not LTC/USDT` added LTC instead of excluding it. | `deterministic_strategy_patch`, `_EXCLUSION_MARKERS` |

Launch-core grew from 13 to 16 contracts: `timeframe_role_accuracy` now compares all
four roles instead of the trigger alone, and three contracts exercise context,
confirmation and reference roles.

## Verification record

First pass, completed locally on 2026-07-29:

- Ruff: passed.
- MyPy: passed for 244 source files.
- Jinja validation: 66 templates loaded.
- JavaScript validation: 21 files passed Node syntax checks.
- Dependency lock: 37 direct dependencies are exact-pinned.
- `pip check`: no broken requirements.
- API route-security audit: every `/api/v1` route is authenticated or explicitly
  public.
- V2 unit, integration, property and evaluator tests: passed.
- Dashboard browser suite: 25 passed, including inactive preview and authenticated
  approval.
- Interpreter reliability corpus: passed.
- Three consecutive launch-core runs: 13/13 passed in each run, zero model calls,
  zero cost, zero false executable output, zero substitutions and no semantic
  inversions. The slowest measured case was 125 ms.

Historical run `20260729T081005Z` spent $0.6821, measured only 7 of 22 cases,
reported 15 infrastructure failures and passed no judged cases. Replaying the same
stored evidence through V2 costs $0 and passes all 3 cases that contain sufficient
deterministic contracts. The remaining 19 are explicitly `NOT_MEASURED`; they are
not counted as passes or failures.

Generated reports:

- `chatbot_eval_runs/launch-core-v2-final-1/report.html`
- `chatbot_eval_runs/launch-core-v2-final-2/report.html`
- `chatbot_eval_runs/launch-core-v2-final-3/report.html`
- `chatbot_eval_runs/v2-recorded-20260729T081005Z-final/report.html`

### Second pass, 2026-07-29

- Ruff: passed. MyPy: passed for 244 source files.
- `tests/unit/test_invariant_launch_v2_contracts.py`: 259 cases, all passed.
- Launch-core, three consecutive runs: **16/16** each, zero model calls, zero cost,
  `stable_regression`, `critical_safety`, `workflow` and `infrastructure` all PASS,
  semantic accuracy 1.0, timeframe-role accuracy 1.0, grouping accuracy 1.0,
  approval integrity 1.0, false-executable rate 0.0, p95 47-63 ms.
  Reports: `chatbot_eval_runs/launch-core-v3-{1,2,3}/report.html`.
- Deterministic recorded replay of run `20260728T132409Z` through V2: 34 cases, zero
  cost, zero model calls, zero excluded-symbol leaks, zero direction, timeframe or
  operator inversions, zero unrelated capability substitutions. The rejected patch
  found in the first replay (`one patch cannot include and exclude the same symbol`)
  is gone. Report: `chatbot_eval_runs/v2-recorded-20260728T132409Z/report.html`.

  All 34 cases are `NOT_MEASURED`, and that is honest rather than a pass: 12 of the
  source conversations never reached the server, and the rest need the one structured
  extraction call this zero-cost replay deliberately does not make. Nothing is scored
  as a pass on evidence that does not exist.
- Compiler regression probe on the recorded corpus (`scripts/replay_recorded_turns.py`),
  blocking findings left at the end of each conversation, zero crashes throughout:

  | Recorded run | Before | After |
  |---|---:|---:|
  | `20260728T132409Z` (21 conversations) | 30 | 14 |
  | `20260726T171424Z` (28 conversations) | 28 | 24 |
  | `20260727T081613Z` (14 conversations) | 12 | 11 |

## Unsupported launch features

Runtime custom-capability creation, nearest-capability substitution, agent-selected
tool loops, free-text history recompilation and legacy interview fallback are not
part of the launch path. Requests outside the exact launch primitives remain typed,
blocking unsupported requirements with their original source fragments.

No remote staging URL is configured in the evaluator. `doctor` currently verifies
the local authenticated target at `http://127.0.0.1:8000`; this is not evidence of a
deployed staging smoke test.

The repository-wide release-invariant audit remains blocked by pre-existing tracked
runtime artifacts and unrelated production environment-policy contradictions. No
database, report, screenshot or export was deleted as part of this refactor.
