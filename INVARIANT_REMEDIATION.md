# Invariant remediation — AI Setup Chat / Strategy Compiler

Evidence base: three evaluator runs.

| Run | Cases | Passed | Stop | Notes |
|---|---|---|---|---|
| `20260726T164155Z` | 0 | 0 | `PAUSED_RATE_LIMIT` | HTTP 429 before any case; a stray `OPENAI_API_KEY` in the environment shadowed the `.env` key |
| `20260726T171424Z` | 30 | 0 | `STOPPED_BUDGET` ($1.9012 > $1.9000) | broadest suite: Arabic, Arabizi, dialect, capability-hallucination topics |
| `20260727T081613Z` | 15 | 1 | `STOPPED_BUDGET` ($1.0050 > $1.0000) | mapping and conversation topics |

## Audit of every reported error against the current system

| # | Error from the reports | Root cause | State |
|---|---|---|---|
| 1 | `"I hit an internal error… Nothing was created"` (HTTP 500) across 10+ cases | two length caps raising while the compiler reported a problem | **fixed** |
| 2 | `instruction_not_converted` / `prompt_fragment_unclassified` blocking on approval, Sharia policy, rollbacks, questions | non-mechanic wording entering capability resolution | **fixed** |
| 3 | `"What do you mean by 'BTCUSDT' / 'APPROVE' / 'ETHUSDT' in this setup?"` | same as 2, via the resolver's clarification generator | **fixed** — resolver returns no question for these |
| 4 | `"I do not have a verified candle-data rule for explicitly exclude XRPUSDT"` | exclusions routed as candle mechanics | **fixed** |
| 5 | `bearish % change ≥ 1.0%` compiled `lt 1.0` | comparator read from an unscoped window | **fixed** |
| 6 | `price_down_2_5pct` for a bullish rule; `price_up_1_0pct` for a bearish one | three disagreeing direction vocabularies | **fixed** |
| 7 | `weekly_high_low` / `support_retest` compiled from a question | nearest-capability substitution on a decision request | **fixed** |
| 8 | `rsi_level_required` on text containing no RSI | `"rsi" in text` matched `ve**rsi**on` | **fixed** |
| 9 | `is_approval_instruction("no auto-greenlight… after I say I approve")` → True | conditional approval read as a grant | **fixed** |
| 10 | **Arabic / Arabizi scored 0.11–0.22, always `clarification_required` sentinel** | the movement vocabulary was English-only, so nothing compiled | **fixed this pass** |
| 11 | **Budget overshoot; `STOPPED_BUDGET` only after the whole suite** | budget checked while charging, never before scheduling | **fixed this pass** |
| 12 | **Completed cases discarded when a chunk raised** | `asyncio.gather` without `return_exceptions` | **fixed this pass** |
| 13 | **`"Capability resolution accepts only exact user-authored source fragments."` shown to the user** | internal tool warnings used verbatim as the assistant message | **fixed this pass** |
| 14 | `ConnectError: getaddrinfo failed` → `TARGET_CONNECT_TIMEOUT` | INV-13 DNS rule | already correct; now locked by a test |
| 15 | `TARGET_INVALID_JSON` (`model_version_drift`) | truncated target/judge body | **left** — see R8 |
| 16 | `context_fact_recall` / `explicit_approval_granted` demanded from the chatbot | evaluator asks the target to emit evaluator-only metrics | **left** — `INVALID_TEST_CONTRACT`, EVAL-01 |
| 17 | `grouping_accuracy` 0.0–0.05 (nested boolean, precedence) | no real boolean AST | **left** — R3 |
| 18 | include ∩ exclude non-empty (`threshold_mapping`) | no disjointness validator | **left** — R2 |
| 19 | context/trigger timeframe drift | no role-consistency validator | **left** — R4 |
| 20 | p50 latency 85–468 s; `ReadTimeout` on long turns | not investigated | **left** — R6 |

Older run `20260726T164155Z` contributes one further item: the evaluator stopped at
HTTP 429 having completed nothing, and its own error text notes that a different
`OPENAI_API_KEY` in the process environment was ignored in favour of the `.env` key.
Key precedence is now stated in the run summary, but the rate-limit path still gives
up rather than backing off — **left**, R9.

The rest of this document covers the invariants behind the fixed rows.

---

Original evidence base: `chatbot_eval_runs/20260727T081613Z`
(`cases.jsonl`, `failures.csv`, `summary.json`, `report.md`) — 1 of 14 quality cases
passed, `release_gate: INCOMPLETE`, `execution_status: STOPPED_BUDGET`.

The run artifacts are used as **regression evidence**. No fix below reads a scenario
ID, a symbol, a threshold, or an expected transcript.

---

## Root-cause architecture

```
User turn
  │
  ├─ fragment classification ─────────── INV-04 ✗ violated  (bespoke keyword heuristic,
  │                                       parallel to the real classifier)
  ├─ canonical state patch ────────────── INV-01 ✓ present  (strategy_state.py patch log)
  │
  ├─ semantic AST / formula reading ───── INV-07 ✗ violated  (comparator + direction read
  │                                       from unscoped windows and 3 disagreeing lists)
  ├─ capability binding ───────────────── INV-03 ✗ violated  (a question resolved to a
  │                                       capability and compiled)
  ├─ validated DSL ────────────────────── INV-02 ✗ violated  (compiled condition with no
  │                                       user-authored source)
  ├─ approval / version state ─────────── INV-10 ✗ blocked   (never reachable: see below)
  └─ derived compiler request ─────────── INV-13 ✗ violated  (schema overflow → HTTP 500)
```

**The dominant failure was not semantic — it was availability.** Replaying all 14
recorded conversations through the real compiler at `HEAD`, **10 of 14 raise an
unhandled exception** and never produce a draft at all. The evaluator recorded those
as HTTP 500 (`revert_correction` turns a4/a5/a6 are visible in `cases.jsonl`); the
other seven cases masked it, because the crash lands on a turn whose reply the judge
scored as a normal — but empty — answer.

The second failure is categorical: non-trading wording — approval gating,
Sharia/labelling policy, rollback requests, open questions, instructions about the
conversation — was routed into capability resolution and emitted as **blocking**
findings. A blocking finding makes a draft ineligible for approval, and *no answer the
trader could give would ever clear these*, because the wording was never a market
mechanic. Every approval assertion in `failures.csv` is downstream of that.

### Measured, like for like

Both columns are the identical probe (`scripts/replay_recorded_turns.py`) over the
recorded conversations of both quality runs. `before` is a clean `git worktree` at
`HEAD` (`02f0418d`) carrying only the `source_fragment` crash-guard, without which it
cannot run far enough to be measured at all.

| | before (HEAD) | after |
|---|---|---|
| conversations that crash (`20260727T081613Z`) | **10 / 14** | **0 / 14** |
| conversations that crash (`20260726T171424Z`) | not measurable | **0 / 28** |
| conversations reaching a draft | 4 / 14 | 42 / 42 |
| blocking findings, on the 4 comparable conversations | 22 | 16 |
| hallucinated `weekly_high_low` condition | present | gone |
| Arabic / Arabizi conversations compiling a condition | 0 | all |

**Three separate HTTP-500 causes** were found, all the same shape — *a value hits a
schema constraint and the ValidationError escapes as an internal error for the whole
turn*:

1. accumulated `setup_text` past its 5000-character cap;
2. `InterpretationIssue.source_fragment` past its 500-character cap — raised **while
   the compiler was reporting a problem**, and the largest single cause;
3. `RiskPolicy.minimum_reward_to_risk` = 100 against `le=50`, from a loose `r:?r`
   pattern matching an unrelated number.

The 42 conversations that now complete carry 115 (14-case run) and 197 (28-case run)
blocking findings between them. Those counts have **no `before` value to improve on** —
a crash is not a better score — and they are the honest remaining work, itemised as R1.

---

## INV-04 — Non-trading instructions never enter capability matching

**Formal rule.** A fragment may be reported as an unconverted *trading* capability
only when its deterministic category is `TRADING_MECHANIC`.

**Violating code paths.**
- `services/interpreter.py::_unparsed_instruction_issues` — a bespoke
  `recognized_terms` / `requirement_terms` keyword heuristic, entirely parallel to
  `engine/turn_fragments.classify_fragment`. Any fragment containing `only`, `not`,
  `no `, `must`, `never` and no indicator name became a blocking finding.
- `services/interpreter.py` coverage loop — every `unclassified` coverage fragment
  became `prompt_fragment_unclassified`, `blocking=True`, unconditionally.

**Evidence.** `timeframe_mapping-001` blocked on `'it must be measurable)'`.
`universe_mapping-001` blocked on the trader's own approval sentence.
`long_context_retention-001` blocked on the universe instruction *and* the
"no religious/ethical status" instruction — both quoted in `failures.csv`.

**Implementation.**
- `engine/turn_fragments.py`: new deterministic categories `APPROVAL_INSTRUCTION`,
  `PRODUCT_POLICY`, `CONVERSATION_CONTROL`, `DECISION_REQUEST`, `REVERSION`, all in
  `NON_MECHANIC_KINDS`; `names_market_mechanic()` as the positive gate.
- Both interpreter emit sites now consult `classify_fragment(...).category`.

**Runtime assertion.** The classifier is the *only* gate. An earlier iteration also
downgraded findings whose fragment carried no market vocabulary; that was reverted
because it wrongly let `"only if the chart feels unusually optimistic"` through as
non-blocking. That fragment *is* a market instruction — it just names nothing
measurable, which is precisely why it must be defined before the setup can run
(`tests/unit/test_interpreter.py::test_interpreter_reports_mandatory_fragments_it_cannot_convert`
catches the regression).

**Deterministic test.** `tests/unit/test_invariant_fragment_routing.py`
**Status.** `INVARIANT_FIX` — done.

### INV-04a — Product policy must not edit the universe

"Confirm you will not attach any labels/statuses to LTCUSDT" names LTCUSDT in order
to *protect* it. The exclusion reader treated the negation as an exclusion marker and
would have dropped the one asset the trader asked for. Policy fragments now carry no
universe edit (`keeps_universe=False`). Covered by INV-05.

---

## INV-10 — Approval is application state, not strategy logic

**Formal rule.** The model may request approval; it can never grant it.

**Violation found (pre-existing, security-relevant).**
`is_approval_instruction("absolutely no auto-greenlight; you only confirm after I say
I approve")` returned **True** — the sentence that *forbids* auto-approval was read as
granting approval, because `\bi\s+approve\b` matched inside "after I say I approve".
Typographic apostrophes (`won’t`, `don’t`) also bypassed the negation patterns
entirely, since those were written with ASCII apostrophes only.

**Implementation.** `_APPROVAL_NEGATIONS` gained the *conditional* forms
(`after/once/when/only after I say…`), the `no auto-approval` / `auto-greenlight`
forms, and `approval must stay/remain…`; all approval matching is now done on
apostrophe-normalised text.

**Deterministic test.** `test_describing_the_approval_gate_never_grants_approval`
(4 phrasings) plus `test_an_actual_approval_is_still_recognised`.
**Status.** `INVARIANT_FIX` — done.

*Not changed:* `terminal=true` alongside `needs_clarification`. In
`services/setup_chat_lifecycle.py`, `terminal` means "a client may stop polling this
turn", not "the strategy is finished". The evaluator read it as completion. That is an
`INVALID_TEST_CONTRACT` (EVAL-03), not a compiler defect, and is listed under
*Remaining* below.

---

## INV-07 — Formula, direction, operator and threshold are atomic

**Formal rule.** The operator and the direction that govern a threshold are the ones
**nearest to its left**, inside the clause that owns it.

### 07a — Comparator read from an unscoped window

`formula_compiler._comparator` scanned `text[value.start-48 : value.end+24]` and took
the *first* operator found anywhere in it.

**Evidence** (`timeframe_mapping-001`, turn u4, verbatim):

> `(close < open)` AND `(abs(bearish % change) ≥ 1.0%)`

The `<` belongs to the candle-body definition. It sat inside the window, was found
first, and won. Compiled result: `comparator: "lt"`, `threshold: 1.0` — a **maximum**
1% move where the trader asked for a **minimum**. The alert fires on the opposite of
the requirement, and the artifact is schema-valid, so nothing downstream catches it.

**Implementation.** `engine/comparators.py::find_comparator_before(text, position)`
returns the nearest operator to the left, built from the one shared `OPERATOR_TERMS`
table (longest-phrase-first, so `no less than` is never read as the `less than`
inside it). `formula_compiler._comparator` uses it.

### 07b — Three disagreeing direction vocabularies

`price drops at least 3%` compiled as an **up** move: one list held `drop` but not
`drops`. `down move no more than 1.25%` compiled as **up**: no list held `down` at
all. A third code path recomputed direction from the caller's default and **discarded
the direction the trader had stated**.

**Implementation.** New `engine/price_movement.py` — one vocabulary (`UP_TERMS`,
`DOWN_TERMS`, `MOVEMENT_PATTERN`), one resolution rule (`movement_direction_before`,
same nearest-left principle as the operators). `formula_compiler._formula_direction`
and `interpreter._percent_move` both consume it; the discard branch now reuses the
resolved direction.

### 07c — Metric spellings missing from the expression reader

`%move >= 7.5 for direction=long with operator=gte` — fully specified — was reported
back to the trader as an instruction the compiler could not convert, because
`%move` / `move%` were absent from `_FORMULA_COMPARISON_RE` and `≥`/`≤` were absent
from the operator token map. `_unparsed_instruction_issues` now asks the **compiler**
whether a fragment is convertible instead of consulting a keyword list.

**Deterministic test.** `tests/unit/test_invariant_operator_direction_binding.py` —
every operator phrase × every direction word, both orderings, plus every term in
`UP_TERMS`/`DOWN_TERMS` individually.
**Status.** `INVARIANT_FIX` — done.

### 07d — Indicator names matched as substrings

`"rsi" in text` also matched **ve*rsi*on**, **reve*rsi*on** and **dive*rsi*fy**. A
conversation about rolling back to a previous *version* raised a blocking
`rsi_level_required` finding for an indicator nobody mentioned — three occurrences in
the run. Replaced with `names_indicator()` (word-boundary) at every mention site for
`rsi`, `macd`, `atr`.
**Status.** `INVARIANT_FIX` — done.

---

## INV-02 / INV-03 — No unrequested conditions, no nearest-capability substitution

**Formal rule.** A condition may exist only with a user-authored source, and a
capability matches only when every material dimension matches.

**Evidence** (`universe_mapping-001`): a compiled condition
`capability_key: "weekly_high_low"`, `ai_interpreted: true`, `confidence: 0.779`,
whose `source_fragment` was
`"and the precise trigger definition on 1m (close-to-close vs high/low)"` — a
**question about which definition to use**. The words `high/low` matched the
capability lexically. The trader never asked for a weekly high/low rule.
`contradiction_resolution-001` shows the same `weekly_high_low` intrusion.

**Implementation.** A fragment presenting alternatives or asking which reading applies
is now `DECISION_REQUEST`, which is outside `TRADING_MECHANIC`, and
`CapabilityResolver.resolve_prompt` already refuses anything that is not
`TRADING_MECHANIC`. The question therefore produces a clarification instead of a rule.

**Status.** `INVARIANT_FIX` — done.

### The grounding rule (`ARCHITECTURAL_ENABLER`)

`engine/grounded_patch.py` states the contract for AI-assisted reading, which is the
part of INV-01/02/03 that a confidence threshold cannot enforce:

* the model may only **fill fields of a type the compiler already has** — it never
  names a capability and never returns text that becomes a rule;
* every value it fills must be **grounded**: the number, the comparison and the
  direction must each be findable in the trader's own words;
* anything ungrounded is **refused**, not softened into a default.

`verify_grounding()` checks numbers by numeric equality (so `7.5` matches `7.50`),
comparators against the shared operator vocabulary, and directions against the shared
movement vocabulary. `formula_compiler.grounding_violations()` applies it to any
`PercentageFormulaSpec` regardless of origin, so a model proposal clears exactly the
same bar the deterministic parser clears by construction.

One comparator — `>=` — may be supplied by documented convention (`up 5%` means "at
least 5%") and is reported as a convention rather than hidden. An unstated **upper**
bound is always a violation, because supplying one reverses the alert.

**Deterministic test.** `tests/unit/test_invariant_grounding.py`, including a
well-formed spec carrying a threshold the trader never wrote — the failure mode a
confidence score cannot detect.
**Status.** `ARCHITECTURAL_ENABLER` — done.

---

## INV-13 — Errors preserve state and are classified

**Formal rule.** A long conversation must not become an internal error.

**Evidence.** `revert_correction-001` turns a4, a5, a6 all returned **HTTP 500**:
*"I hit an internal error … Nothing was created, changed, or activated."* The scenario
scored 0.05 and its `correction_adherence` could not be measured at all, so the
reversion behaviour the case exists to test was never exercised.

**Two independent root causes, both "a length cap turned a turn into an outage".**

**13a — accumulated setup text.** `ai_setup_chat._guided_setup` passes the accumulated
setup text (up to 30 fragments) straight into `GuidedSetupRequest.setup_text`, which
the schema caps at 5000 characters. Once a conversation crossed the cap, **every**
subsequent turn raised `pydantic.ValidationError` before any compilation happened.
The crash appears at exactly the turn where the accumulated text passes 5000.

*Fix.* `_setup_text_limit()` reads the cap **from the schema** (a hard-coded copy
would drift invisibly); `_bounded_setup_text()` keeps whole trailing lines within it.
The canonical strategy state — not the transcript — is the authority for settled
fields (INV-01), so dropping the oldest raw lines loses no decision.

**13b — the finding's own quote.** `InterpretationIssue.source_fragment` caps at 500
characters, and several call sites pass the whole message when they cannot isolate a
clause. The interpreter then raised a ValidationError *while reporting a problem*, and
the whole turn 500'd with no draft preserved — the outcome the finding existed to
prevent. This one fires on turn **u1** of a 617-character message, far earlier than
13a, and is why 10 of 14 conversations crash at HEAD.

*Fix.* A `field_validator(mode="before")` on `source_fragment` truncates instead of
rejecting. The quote is evidence, not a contract; the full text is always still on the
turn. Fixing it at the schema covers all nine call sites at once, rather than nine
slices that drift.

**Deterministic test.** `tests/unit/test_invariant_request_bounds.py` — 1, 8, 40 and
400 turns all build a valid request; the limit is asserted against the schema itself.
**Verified.** 14 of 14 recorded conversations replay end-to-end with no exception
(`scripts/replay_recorded_turns.py --run 20260727T081613Z` → `crashes: 0`).
**Status.** `INVARIANT_FIX` — done.

---

## Changed files

| File | Invariant | Change |
|---|---|---|
| `engine/price_movement.py` | INV-07 | **new** — the one movement vocabulary |
| `engine/grounded_patch.py` | INV-02/03 | **new** — the grounding contract for AI reading |
| `engine/comparators.py` | INV-07 | `find_comparator_before` (nearest-left) |
| `engine/formula_compiler.py` | INV-07, INV-02 | nearest-left operator + direction; shared vocabulary; `%move`/`≥` spellings; `grounding_violations` |
| `engine/turn_fragments.py` | INV-04, INV-10 | five non-mechanic categories; `names_market_mechanic`; approval-negation and apostrophe fixes |
| `engine/strategy_state.py` | INV-09 | reversion vocabulary now shared, not duplicated |
| `services/interpreter.py` | INV-04, INV-07 | both emit sites routed through the classifier; `names_indicator` word boundaries; compiler-as-authority convertibility check |
| `services/ai_setup_chat.py` | INV-13a | schema-derived bound on the derived request |
| `schemas/strategy.py` | INV-13b | `source_fragment` truncates instead of rejecting |
| `scripts/replay_recorded_turns.py` | — | **new** — the before/after probe used above |

## Removed duplicate parsers

- the `recognized_terms`/`requirement_terms` heuristic no longer decides fragment kind
- three direction word lists → one (`price_movement.py`)
- two reversion regexes → one (`turn_fragments.REVERSION_RE`)
- window-scanned comparator reading → `find_comparator_before`

## New tests

| File | Cases |
|---|---|
| `tests/unit/test_invariant_fragment_routing.py` | 45 |
| `tests/unit/test_invariant_operator_direction_binding.py` | 149 |
| `tests/unit/test_invariant_grounding.py` | 24 |
| `tests/unit/test_invariant_request_bounds.py` | 9 |

---

## INV-04b — Arabic and Arabizi are first-class input

**Formal rule.** A stated move must compile regardless of the script or dialect it is
written in.

**Evidence.** `20260726T171424Z`: `msa_arabic` 0.11, `egyptian_arabic` 0.15,
`arabizi` 0.22, every one producing the same artifact — a `clarification_required`
blocked sentinel plus a blocking `no_supported_monitor_condition`.

**Root cause — not translation.** Symbols, percentages and Latin timeframes were
already read correctly. The *direction of movement* was invisible: `movement_direction`
returned `None` for `صعدت`, `نزلت`, `tel3et`, `yenzel`. Without a direction the
percentage formula refuses to compile (correctly — it will not guess a side), so the
whole instruction produced nothing.

**Implementation.** The same shared-vocabulary pattern, extended rather than
duplicated: Arabic and Arabizi terms added to `price_movement.UP_TERMS`/`DOWN_TERMS`,
to `comparators.OPERATOR_TERMS` (`على الأقل`, `بحد أقصى`, `3ala el a2al`…), to the
timeframe unit table (`15 دقيقة`, `4 ساعات`, `3ala 15 de2i2a`), and to the daily-anchor
phrases (`اليوم`, `el naharda`). The word-boundary regex was widened to `؀-ۿ`, because
`[a-z]` does not bound Arabic script and `نزل` would otherwise match inside longer
words.

**Deterministic test.** `tests/unit/test_invariant_arabic_reading.py` — every Arabic
term in the vocabulary individually, plus dialect and transliteration cases through
the real compiler, plus a negative case proving the boundary holds.
**Status.** `INVARIANT_FIX` — done.

## EVAL-09 — The run stops at the budget, and keeps what it paid for

**Formal rule.** When the remaining budget cannot cover the next case, stop scheduling.

**Evidence.** Every recorded run overshot and reported `STOPPED_BUDGET` only after the
whole suite had finished: $1.9012 against $1.9000, $1.0050 against $1.0000.

**Root cause.** The budget was checked *only while charging* (`_charge`), so the run
always discovered it was over after already spending past it, and nothing gated the
start of the next case. Compounding it, `asyncio.gather` without `return_exceptions`
discarded every result in the chunk when one case raised — work already paid for was
thrown away, and the summary under-reported what had run.

**Implementation.** `EvaluationRunner.stop_reason()` is consulted **before** each
chunk is scheduled, using `projected_case_cost()` — the mean of what cases have
actually cost in this run, so the estimate tracks the suite rather than a fixed guess.
The in-flight case finishes and is kept. `_charge` still raises as a backstop against
a single runaway case. `gather` now uses `return_exceptions=True`.

**Deterministic test.** `tests/evaluator/test_budget_stops_before_next_case.py`
**Status.** `INVARIANT_FIX` — done.

## INV-13c — Internal diagnostics are never user copy

**Evidence.** Transcripts show traders answered with
`"Capability resolution accepts only exact user-authored source fragments."` and
`"The requested action was blocked by the bounded control policy. Nothing was executed."`

**Root cause.** `agent_control` took the first warning off the tool results and used it
verbatim as the assistant's message. Tool warnings are written for the model and the
audit log, in the compiler's vocabulary.

**Implementation.** `_plain_blocked_message()` selects a beginner-readable sentence
from the tool's own declared `allowed_next_actions`, so a newly added warning string
cannot leak by default. Warnings stay on the results for the audit trail.
This one matters more than its size: the product is built for beginners, and a reply
in compiler vocabulary tells them nothing about what to do next.
**Status.** `INVARIANT_FIX` — done.

## Verification

```
ruff check src tests scripts                               All checks passed
mypy src                                                   233 files, no issues
replay_recorded_turns --run 20260727T081613Z               14 conversations, crashes: 0
replay_recorded_turns --run 20260726T171424Z               28 conversations, crashes: 0
invariant suites (routing, operators, Arabic, grounding,
  request bounds, budget, turn fragments)                  468 passed
```

**Regression check.** Failing-test IDs were diffed between a clean `git worktree` at
`HEAD` and this tree, over the 76 test files present in both: `HEAD` 54 failures →
this tree 42. Three IDs appear only in this tree, all in
`test_dashboard_static_assets.py` / `test_dashboard_ux_consolidation.py`. They are
**not** from this work: copying only the nine files changed here onto the clean `HEAD`
worktree leaves all 30 of those tests passing. They come from the other uncommitted
changes already in the working tree (`api/routers/dashboard.py`,
`core/site_content.py`, `core/startup.py`, `core/plans.py`,
`Hilal-Markets-Website/*`), which the `HEAD` worktree does not have.

One further ID appeared during the second pass —
`test_reliability_security.py::test_enabled_production_integrations_require_real_adapters_and_secrets`.
It asserts on `validate_runtime_configuration` in `core/startup.py`, and its failure
text is a list of plan-catalogue and `CREEM_API_BASE` errors from `core/plans.py` /
`core/config.py`. All three files are pre-existing uncommitted changes; none of the
files changed here is on that code path.

No previously passing test in the compiler, interpreter or chat path regresses. The
one intentional contract change is R7 below.

## Second remediation pass — R1–R9

| Item | Root cause found | State |
|---|---|---|
| R9 retry | Backoff with jitter already existed and is used. The real weakness was `attempts=3` and a jitter-free path for transport errors. Raised to 5; both paths now share the jittered schedule. | fixed (my earlier "gives up instead of backing off" was wrong) |
| R8 evidence | The unparseable body was discarded, so a truncated **grader** reply was recorded as a chatbot fault. `MalformedAIResponse` now carries a sanitized excerpt, the run writes it to `evidence/`, and `EVALUATOR_INVALID_JSON` separates the two sides. | fixed |
| R2 universe | Exclusion only filtered the watch list when it came from settled state, so an exclusion stated in the current turn had no effect. Exclusion now always wins; a settled contradiction is reported. | fixed |
| R2b (found here) | `_exclude_symbols` read `only SOLUSDT` as *excluding* SOL. The universe held SOL in both lists; the old test passed only because it never checked. Root cause: `_collapse` stripped newlines before splitting, merging two turns into one fragment so `only` applied across both. | fixed |
| R1a findings | Not restatements — the splitter chopped one formula into debris (`L1`, `move %`, `Computed move % =`) and reported each piece as an unconverted instruction. Fragments that are not whole instructions are no longer reported; debris that still carries a symbol or timeframe keeps it. | fixed: 192 → 154 and 115 → 80 |
| R3 timeframe roles | A condition's timeframe was inferred from elsewhere in the message and silently outranked the role the trader stated. Rules now move onto the stated trigger; a timeframe also named as context is left alone. | fixed |
| R4 boolean shape | The compiler joins everything with AND, so `(A or B) and C` shipped as `A and B and C`. A parser now recovers the written shape; when it contains an OR the compiler cannot build, the setup is **blocked and explained** rather than silently changed. | parser + refusal done; **the OR group is now built — see the third pass below** |
| R1b canonical findings | Raising each finding once against canonical state rather than accumulated text. | **done in the third pass below** |

## Third remediation pass — R1b and R4b

### R1b — findings against canonical state

Two separate things were wrong, and the first was in the measurement, not the product.

**The probe measured a path production does not use.** `replay_recorded_turns.py`
joined the accumulated user turns and compiled that blob. The chat service compiles
`canonical_compiler_text(resolved_state)`. Measured over the same 28 conversations the
two paths disagreed by more than threefold — **154 findings on the joined text against
46 on canonical state** — so every "findings remaining" number in the pass above
describes the fallback, not what a trader sees. The script now folds each turn into
`StrategyDraftState` and compiles canonical text, exactly as `_interpret` does;
`--raw` still selects the old path for measuring the fallback itself.

The fallback in `canonical_compiler_text` was checked directly and **never fired**:
0 of 217 recorded turns across both runs. INV-01 holds in practice.

**The real defect: `classify_fragment` ended in an unconditional fall-through.**

```python
return build("trading_condition")   # everything unrecognised
```

That made *trading instruction* the default for any wording the classifier did not
understand — a JSON echo (`"approval_required": true`), a question (`not heavy
formulas?`), a note to the assistant. Each one entered `mechanic_fragments`, so it sat
in canonical state permanently, was recompiled every later turn, and became a blocking
finding **no answer from the trader could ever clear** — there was no market mechanic
in it to clarify. CLAUDE.md already states the rule ("only `TRADING_MECHANIC` fragments
reach capability resolution"); the gate was a sieve with no bottom.

What reaches the resolver is now decided by **what the fragment is**, never by whether
its words appear in a vocabulary:

| Rule | Statement |
|---|---|
| Meta-instruction | A prohibition or obligation whose verb acts on *the answer* (`interpret`, `map`, `rename`, `word`, `output`) is dialogue, whatever market nouns it quotes. |
| Action request | `go ahead and run it` asks for the next step in the conversation, not for anything to be monitored. |
| Field echo / blank | A JSON pair (`"approval_required": true`) or a markdown field label (`**Context:** …`) is a quoted artifact; `within next __ minutes` is a blank never filled in. |
| Short cut-off text | An unclosed bracket or quote in a **short** fragment is the tail of a parenthetical. |
| Carried state | Text that is not a mechanic keeps whatever configuration it holds, through the same fall-through ladder debris already used. |

**Three attempts were reverted, each caught by measurement.** They are recorded because
each looked obviously right before it was measured:

1. **Rejecting any clause that opens with `and`/`or`/`then`.** Dropped real rules —
   `and the entry condition is a bearish move with size ≤ 7.5%` carries the whole
   requirement. A leading conjunction is now *stripped* before judging completeness.
2. **Rejecting any text with unbalanced brackets.** Same failure: the splitter cuts
   long real clauses mid-parenthesis. Now limited to fragments under six words.
3. **A positive "must name known market vocabulary" gate.** This was the big one. It
   cut findings hardest (27 and 3), and it was wrong: it silenced
   `raid the weekly floor` — wording the registry does not know but that an approved
   alias later resolves. `_has_residual_content` states the contract in the same file:
   *"an unrecognised word is exactly what the capability registry exists to
   interpret."* A vocabulary gate turns every not-yet-known mechanic into
   conversation, which is the silent drop this module exists to prevent. Removed;
   `tests/unit/test_capability_resolver.py` and `test_capability_registry.py` catch it
   if it is ever reintroduced.

The honest cost of removing it: **34 and 13** blocking findings instead of 27 and 3.

Two smaller defects were found and fixed on the way:

* `_MARKET_VOCABULARY_RE` used `\b`, and `_` is a word character, so `\bclose\b` never
  matched `latest_5m_close` — and traders write their formulas exactly that way. The
  `ve`**`rsi`**`on` bug from the other side; boundaries are now alphabetic lookarounds,
  the form that also holds for Arabic.
* `names_market_mechanic` now also consults a phrase set derived from the capability
  registry (`all_capabilities()` + `prompt_aliases.normalized_phrases`), so a phrase the
  platform advertises as an alias — `head and shoulders neckline break` — can never
  read as saying nothing about the market. Whole phrases are matched, not component
  words: splitting aliases made `previous` and `failed` count as market vocabulary.

### R4b — the OR is built, not refused

`or` was always a first-class operator in `ConditionGroup`, in `LOGIC_OPERATORS` and in
the evaluator's `_evaluate_group`. Only the compiler never emitted one.

Each branch of the parsed expression is now compiled from **its own wording** through
the same parser battery the whole prompt goes through, and the results are reassembled
into the tree the trader wrote. The flat copies the group takes over are removed from
the root AND — leaving them would have re-required both sides and made the OR
meaningless.

```
(RSI below 30 or volume above 2x average) and price above the 50 EMA

AND  [entry_conditions]
  AND  [all_of_2]
    OR  [any_of_1]
      - rsi_below_30      [15m] lt 30.0
      - relative_volume   [15m] gt 2.0
    - price_above_15m_ema_50 [15m] gt
```

Fail-closed is kept where it belongs: a branch that compiles **nothing** still blocks,
and the message now names that branch. Dropping it would leave an OR with fewer
alternatives than the trader asked for — the same silent substitution flattening
produced, by another route.

`_shape_is_preserved` was also made recursive. It checked one level of nesting, so
`(A or B) and (C or D)` would have been reported as unbuilt even after being built.

### Measured

Both runs replayed through the production path. "before" is this build with the three
classifier gates switched off, which reproduces the fall-through.

| Run | Conversations | Crashes | Blocking before | Blocking after | Compiled rules before → after |
|---|---|---|---|---|---|
| `20260726T171424Z` | 28 | 0 | 54 | **34** | 39 → 38 |
| `20260727T081613Z` | 14 | 0 | 24 | **13** | 16 → 16 |

Exactly one condition disappears, and it is a fix: `long_context_retention` compiled a
`strong_swing_high` rule out of the sentence
`Don't interpret it as "Strong Swing High" specifically` — the sentence that forbade
it. That is the "never invert" invariant, broken in the classifier rather than the
comparator. No other rule was lost.

## Fourth pass — P1–P4, the stated-value defects

Four reported symptoms, one shape underneath: **a value the trader stated was dropped
and an invented one took its place, because more than one reader owned the question.**

### P1 — the window

Six readers answered "how far back?", each understanding different wording:
`formula_compiler._lookback` (default 20), `interpreter._lookback_candles` (default
100), an inline scan inside `interpreter._percent_move` (default 1),
`_lookback_label`, `_period_near`, and `prompt_semantics._lookback_candles`
(default 1).

Worse than the disagreement: `PercentageFormulaSpec.lookback` was **set on one compile
branch out of six**. `price moved up 2% over the last 3 candles` therefore compiled a
one-candle rule — the `3` was read by nobody on that path.

`engine/lookback.py` is now the single owner. It returns `None` when the text states no
window, so every caller chooses its default in the open instead of inheriting one.
Every `return` in `parse_percentage_formula` goes through one `_resolved()` helper, so
a branch cannot forget and a branch added later inherits the behaviour.

Two bugs in the new reader were caught by existing tests and fixed:

* the window is converted on the timeframe it is **measured against**, not the one the
  rule fires on — `grew 5% today` anchors to the daily open, so `today` is one
  reference candle, not 96 fifteen-minute ones;
* a duration only states a window when something introduces it as one. `a 1 minute
  candle that had a value of 1% over the past week` was reading `1 minute` — the *bar
  size* — and searching a single bar for a week-long request.

### P2 — the side

`_direction()` was a **seventh** movement vocabulary: a hand-written list of `bearish`,
`short`, `breakdown`, `reject`. `price moved down 2%` matched none of them and fell
through to the invented default `LONG`. It now asks `engine/price_movement` first,
which already knows `down`, `drops`, `dumped`, `نزل` and `yenzel`.

Two invented defaults were removed with it: `movement_direction(...) or "up"` in
`_percent_move`, and `group.get("direction") or "up"` in `prompt_semantics`. The
movement vocabulary was verified **total** — all 156 terms resolve to a side — so
removing the fallback cannot silence a term the pattern matches.

A related defect surfaced: an operand that spells its side into its own name
(`percent_change_up`) had that name set from the catalogue while its `direction`
parameter was corrected, so the two contradicted each other. The name now follows the
resolved side, for any operand ending `_up`/`_down`.

### P3 — invented sizes

`condition_template()` copies a capability's `default_parameters` — values that exist
so the builder UI has something to render. Nothing checked them, so **any capability
whose alias appeared in the text was compiled with the catalogue's example numbers**.
`alert me on a dump this week` matched the alias `dump` and compiled *price up 5%*: a
size and a side stated nowhere in the sentence.

`grounded_patch.ungrounded_quantities()` now applies the bar the model-filled path
already had to clear — a move size only enters a compiled rule if it is findable in the
trader's own words — to the deterministic path as well. Where the trader did state a
size, that size replaces the catalogue's; only when they state none is the condition
refused, so a trader who wrote `pumped 8%` is not punished for the registry's `5`.

The set is deliberately limited to the percent-of-a-move family. An RSI `period` of 14
or a wick `multiple` of 2 is part of what the mechanic *is*; refusing those would
silence named mechanics that their own name fully specifies.

### P4 — a policy word swallowing a rule

`_PRODUCT_POLICY_RE` claims the whole fragment, and `halal` matched inside
`monitor every forming head & shoulders on halal coins`. The mechanic was discarded
with the fragment, so the trader's pattern was never resolved and never asked about.

A Sharia word used as an **adjective on a market noun** — `halal coins`, `sharia-
compliant pairs` — restricts the universe, which the platform already enforces. It is
not a request to assign a status, which is what that branch exists to catch. Labelling
policy (`do not attach any religious status`) is still claimed as before.

### Also fixed here

`_parse_price_action`'s breakout read its window from the whole message, so
`prior day` from an unrelated sentence about a close comparison became the breakout's
window. It now reads from the clause that names the high — the same nearest-clause rule
the comparator and direction readers follow.

### The 26 corpus failures were not P1

Reported earlier as caused by the wrong candle count. They were not: they assert the
operand **name**. The platform has two operands for one mechanic —
`percent_change_up`, which spells the side into its name, and `percentage_change`,
which carries formula, reference field, window and side as parameters. The formula
compiler emits the latter.

All 26 were audited: every one compiles a `percentage_change` rule whose size, side,
**window** and timeframe match the prompt exactly — and the window only matches because
of the P1 fix. The equivalence is declared once in the corpus test and is direction-
aware, so `percentage_change` with `direction: down` can never satisfy
`percent_change_up`. **The duplicate operand itself is not fixed**; collapsing the two
touches the evaluator and the registry and is its own change.

### Verification

```
ruff check src tests scripts     All checks passed
mypy src                         236 files, no issues
test_invariant_stated_values     153 cases, all pass
prompt_understanding_corpus      1030 cases, all pass  (was 26 failing)
replay 20260726T171424Z          28 conversations, crashes: 0
replay 20260727T081613Z          14 conversations, crashes: 0
full suite                       43 failures -> 13
```

The remaining 13 are the same pre-existing families measured against the clean HEAD
worktree earlier in this document: `test_on_demand_scans` (7) and
`test_ai_setup_chat::test_scanner_runs_the_shared_evaluator_without_creating_a_monitor`
(plan catalogue), `test_reliability_security` (2, from `core/startup.py` and
`core/plans.py`), and `test_dashboard_static_assets` / `test_dashboard_ux_consolidation`
(3, from the unrelated uncommitted website and dashboard files). **None is attributable
to this pass.** The four `head_and_shoulders` / capability-resolver failures recorded as
pre-existing above are fixed by P4.

A before/after probe of the recorded corpus shows **no compiled rule lost** by any of
the four changes: the grounding refusal, the policy fix and the lookback/direction
fixes each produce zero condition changes across the 42 recorded conversations. The
defects they fix are exercised by the interpreter corpus, not by these transcripts.

## Fifth pass — the extras from the fourth pass, now fixed

The fourth pass listed problems it had found and not fixed. That is not an acceptable
end state (see "A problem you find is a problem you fix" in `CLAUDE.md`). They are
fixed here.

| Found | Root cause | State |
|---|---|---|
| Findings that block but can never be cleared | Approval gating stated without the word "approval" (`nothing runs until I say yes`), instructions about the assistant's own output, and requests for the artefact (`build the SOLUSDT-only watchlist`) all reached the resolver as unconvertible market mechanics. | fixed |
| The same sentence classified two ways | Every `^`-anchored dialogue pattern stopped matching when the sentence began with `And`, `Then`, `Now` or `Alright`. `And confirm the rest in one line` was a market instruction; `Confirm the rest in one line` was not. | fixed — the discourse opener is stripped before the dialogue checks |
| `set up a scanner` read as a market move | An **eighth** hand-written movement list, in `ClassifiedFragment.category`, matched the `up` inside `set up`. A scan request was categorised as a percentage-move formula and never reached the resolver. | fixed — `up`/`down` now carry a phrasal-verb guard, shared from `price_movement` |
| An upper-bound percent move **refused** | `percent_change_up` fixes its comparison at "at least", so `a bearish move of at most 2.5%` was reported as unrepresentable. It was always representable: `percentage_change` carries the comparison on the condition, and the evaluator already reports a fall as a positive magnitude. A false fail-closed. | fixed — it compiles |
| One requirement compiled **twice** | The formula compiler and the semantic parser both recognise a percentage move and build it with different operands, so `rose at least 3% today` produced two conditions joined with AND. `_conditions_equivalent` could not see it — the operands differ in name while stating the same thing. | fixed — `PERCENT_MOVE_OPERANDS` declares the family in the module that owns the formula |

Measured on the recorded corpus, production path:

| Run | Blocking before this pass | after |
|---|---|---|
| `20260726T171424Z` | 38 | **31** |
| `20260727T081613Z` | 13 | **12** |

No compiled rule lost; 0 crashes across all 42 conversations.

### Verification for this pass

```
ruff check src tests scripts     All checks passed
mypy src                         236 files, no issues
full suite                       13 failures — the same pre-existing set, none new
prompt_understanding_corpus      1030 cases, all pass (fixture migrated, workaround removed)
replay 20260726T171424Z          28 conversations, crashes: 0, blocking 31
replay 20260727T081613Z          14 conversations, crashes: 0, blocking 12
```

One reverted attempt, recorded because it looked right: replacing `category`'s narrow
token list with the full movement vocabulary. That pulled ordinary `bearish move of at
least 1%` wording out of capability resolution — the list is a *formula-token* test,
not a movement vocabulary. Only the phrasal-verb guard was needed.

### One mechanic, one operand — the duplication itself

`percent_change_up` / `percent_change_down` spell the side into the operand name and
are fixed at "at least". `percentage_change` carries side and comparison as parameters.
Two parsers each emitted one of them, which is what produced the refused upper bound,
the duplicated condition and the name contradicting its own direction.

`percentage_change` is now what the compiler emits for a percentage move, and the
earlier **workaround was removed**: the corpus test's operand-equivalence helper is
deleted, and the fixture records what is actually emitted.

The fixture migration was verified case by case rather than applied in bulk. For each
of the 234 cases the script checked that the compiled `percentage_change` rule states
the same **side, size, window and timeframe** as the prompt, and rewrote only those:

```
cases rewritten to percentage_change : 228
cases still emitting the old operand :   6   (prompts the formula compiler does not claim)
cases NOT verified (left untouched)  :   0
```

The check itself had to be corrected mid-way — it compared the window on the trigger
timeframe rather than the reference one, so 32 `…today` cases looked wrong when the
compiler was right. The same mistake the compiler had, in the tool measuring it.

Because `percentage_change` carries the side as a *parameter* rather than in its name,
both the corpus test and `test_prompt_semantics_vocabulary` now assert the side
directly. Without that, retiring the old operand would have quietly dropped the one
guarantee its name used to carry.

The old operands remain **in the evaluator** so strategies already saved against them
keep running. Nothing emits them for a percentage move any more.

### Found here, not fixed — pre-existing, outside R1b/R4b

Three defects surfaced while attributing test failures. All three predate this pass:
`parse_boolean_expression` returns `None` for each prompt below, so none of the R4b
code runs for them, and the values come from the formula/price-action path.

| # | Prompt | Compiled | Wrong because |
|---|---|---|---|
| P1 | `price moved up 2% over the last 3 candles on 1m` | `lookback: 1` | The prompt says **3** candles. The lookback is read as 1 regardless of the number stated. |
| P2 | `price moved down 2% …` | `strategy.direction = long` | `_direction()` is a hand-written word list (`bearish`, `short`, `breakdown`, `reject`) that does not consult `engine/price_movement`. `moved down` matches nothing, so it falls through to the invented default `LONG` — while the formula on the same prompt correctly records `direction: down`. This is the duplicate-vocabulary root cause, in the one place still not using the shared module. |
| P3 | `coins decreasing by 3% near midnight` | extra `percent_change_up` with `threshold_percent: 5` | Both values are invented: the prompt says **decreasing** and **3%**. An inverted direction *and* an ungrounded threshold. |
| P4 | `monitor head & shoulders on halal coins` | pattern dropped | `halal` made the whole fragment read as labelling policy. |

**All four are fixed in the fourth pass above.** The claim here that P1 caused the 26
corpus failures was wrong — those assert the operand *name*, not the window; see
"The 26 corpus failures were not P1".

### Verification for the third pass

```
ruff check src tests scripts     All checks passed
mypy src                         234 files, no issues
replay --run 20260726T171424Z    28 conversations, crashes: 0, blocking 34
replay --run 20260727T081613Z    14 conversations, crashes: 0, blocking 13
```

**A note on the failure counts in the pass above.** Those runs were piped through
`Select-Object -Last N`, so the "no new failures" comparison was made against a
*truncated* failure list and is not sound. Any full-suite number quoted before this
line should be treated as unverified. Full output is written to a file from here on.

Full suite, complete output, both sides measured the same way:

| | Failures |
|---|---|
| clean `git worktree` at HEAD (`C:\wt-head`) | **54** |
| working tree, after this pass | **43** |

Per-file, the working tree's 43 are a subset of HEAD's 54 **except** three
(`test_dashboard_static_assets` ×2, `test_dashboard_ux_consolidation` ×1). Those assert
on brand colours and dashboard layout and come from the unrelated uncommitted files in
this tree (`Hilal-Markets-Website/`, `api/routers/dashboard.py`, `core/site_content.py`),
not from the compiler.

The four that looked new were each run individually in the HEAD worktree and **fail
there identically**:

```
test_capability_resolver::test_head_and_shoulders_prompt_resolves_pattern_sequence_without_timing_noise
test_ai_setup_chat::test_misspelled_head_and_shoulders_flow_compiles_without_generic_questions
test_ai_setup_chat::test_scanner_runs_the_shared_evaluator_without_creating_a_monitor
test_agent_tools::test_scanner_resolution_uses_platform_defaults_and_screened_universe_semantics
```

The first two share one cause: `I want to monitor every forming head & sholders on
halal coins` contains **halal**, so `_PRODUCT_POLICY_RE` claims the whole fragment and
the `head_and_shoulders_formed` mechanic inside it never reaches the resolver. Product
policy is swallowing a rule stated in the same breath. Not fixed here — it needs the
same `_states_measurable_condition` guard the other policy branches already use, and
its own measured pass.

The one failure this pass did introduce
(`test_turn_fragments::…[head and shoulders neckline break]`, from the reverted
vocabulary gate) is fixed. **No failure in the working tree is attributable to this
pass.**

Two test files were rewritten because the behaviour they asserted was deliberately
replaced:

* `test_invariant_boolean_shape.py` — asserted that a requested OR is **refused**. It
  now asserts the OR is **built**, that brackets survive into the compiled tree, that
  keys stay unique across branches, and that a branch compiling nothing still blocks
  with the branch named.
* `test_invariant_fragment_routing.py::test_market_vocabulary_gate_distinguishes_market_wording`
  — `change the watchlist` flipped from `False` to `True`. `watchlist` is now market
  vocabulary, which is correct; the case was asserting the absence of a word rather
  than the rule.

### Verification for this pass

```
ruff check src tests scripts                    All checks passed
mypy src                                        234 files, no issues
replay --run 20260726T171424Z                   28 conversations, crashes: 0
replay --run 20260727T081613Z                   14 conversations, crashes: 0
full suite vs this pass's baseline              no new failures
```

The one differing test, `test_enabled_production_integrations_require_real_adapters_and_secrets`,
asserts on `validate_runtime_configuration` in `core/startup.py` and fails with
plan-catalogue and `CREEM_API_BASE` errors from `core/plans.py`. All are pre-existing
uncommitted files; it appears in some runs and not others.

## Remaining — not fixed, mapped to invariants

| # | Invariant | Item | Why not fixed |
|---|---|---|---|
| R1 | INV-11 | Across the 42 conversations, 312 blocking findings remain (down from 336 after the conversation-control generalisation). Most are the *same requirement restated across turns*: the accumulated text keeps every phrasing, so one unconverted idea is counted once per restatement. Fixing this properly means reporting findings against the canonical state (INV-01) rather than against accumulated text — the deeper fix R1 actually needs. The ten conversations that previously crashed now complete carrying 3–23 blocking findings each. Some are genuinely unsupported mechanics (`"store the max favorable low achieved"`, `"populate the watchlist entries with move% + pass/fail"`) where blocking is correct. Others are restated meta-instructions (`"you must map the operators exactly (above/below/…)"`) that should classify as `CONVERSATION_CONTROL`. | These conversations had no measurable "before" — they crashed. Separating the two groups needs its own pass over the classifier, and doing it under time pressure risks the over-correction already caught once (see INV-04 runtime assertion). |
| R2 | EVAL-03 | `terminal=true` with `needs_clarification` read as a contradiction | `terminal` is a polling signal, not a completion claim. Needs an evaluator contract fix or a renamed field — a product decision. |
| R3 | EVAL-01 | Scenarios require the chatbot to emit evaluator-only metrics (`context_fact_recall`, `explicit_approval_granted`) | `failures.csv` recommends the chatbot output evaluator metrics; EVAL-01 forbids this. `INVALID_TEST_CONTRACT`. |
| R4 | INV-06 | "5m = context" not represented as an executable node | Context timeframes legitimately have no trigger node. Needs the role-consistency validator (INV-06) before deciding whether this is a defect or an invalid expectation. |
| R5 | EVAL-09 | Run stopped at `$1.005 > $1.000` after 15 cases | Budget enforcement worked; the budget is too small for the suite. |
| R6 | — | p50 latency 85s–455s per case | Not investigated in this pass. |
| R8 | INV-13 | `TARGET_INVALID_JSON` — `JSONDecodeError: Unterminated string … char 6422` on `model_version_drift-001` | Not diagnosed. Needs the sanitized raw body retained (EVAL-08) to tell a truncated target response from a truncated judge response. Correctly classified and correctly *not* retried. |
| R9 | EVAL-09 | Rate-limit path gives up rather than backing off (`20260726T164155Z` completed 0 cases) | `EVALUATOR_HTTP_429_RATE_LIMIT` is already marked retryable; bounded exponential backoff with jitter around the run loop is not implemented. |
| R7 | INV-07 | One intentional contract change: `"a 1 minute candle that had a value of 1% over the past week"` now compiles `candle_change_percent` (`direction: absolute`) instead of `percentage_change ... gt 1.0`. The old `gt` was read from the word **"over"** in *"over the past week"* — a time phrase — and the up/down side came from the caller's default. Both were invented; every fact the trader gave is preserved. `tests/unit/test_interpreter.py` updated with the reason stated inline. | Changed deliberately, recorded here so it is not mistaken for a silent regression. |

## Not run

The 42-case evaluator replay has **not** been run and no regenerated report is
attached, so no release gate is claimed. The measurements above are from replaying the
recorded turns of run `20260727T081613Z` through the real interpreter locally.

### Reproduce

```bash
# Crash + blocking-finding counts per conversation (the before/after evidence above)
python scripts/replay_recorded_turns.py --run 20260727T081613Z
python scripts/replay_recorded_turns.py --run 20260727T081613Z --json after.json

# One conversation, with the compiled rules
python scripts/replay_recorded_turns.py --run 20260727T081613Z \
    --scenario timeframe_mapping --show-conditions

# Invariant suites
python -m pytest tests/unit/test_invariant_fragment_routing.py \
                 tests/unit/test_invariant_operator_direction_binding.py \
                 tests/unit/test_invariant_grounding.py \
                 tests/unit/test_invariant_request_bounds.py -q

# A single evaluator case
hm-chatbot-eval replay 20260727T081613Z timeframe_mapping-001-812507485 --target backend
```
