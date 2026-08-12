---
name: hm-setup-chat-investigator
description: Investigate a Setup Chat turn that misunderstood a person, and tell a meaning problem apart from an infrastructure problem.
minimum_tier: deep
areas: [setup_chat, compiler, engine]
read_only: true
---

# Setup Chat investigator

Setup Chat reads what a person wrote and turns it into a typed draft of a monitoring
setup. When it goes wrong the visible result is almost always the same — a refusal, or a
setup that watches the wrong thing — while the cause could be at any of a dozen stages.

Runs at the **deep** tier. A cheap model guesses a plausible stage and stops there, and a
plausible wrong stage costs a day.

## First: is this even a meaning problem?

Do this before anything else. It is the most common misdiagnosis.

Read `engine/setup_failure_taxonomy.py`. The turn's recorded failure carries a
`SetupFailureClass` and a `FailureOwner`.

| `FailureOwner` | What it means | Where to look |
|---|---|---|
| `PROVIDER` | the model call itself failed — `PROVIDER_FAILURE`: timeout, 5xx, circuit open | `services/provider_runtime.py`, `provider_reliability.py`. **Not a prompt problem.** |
| `MODEL` | the model answered, and answered wrongly | continue below |
| `COMPILER` | the draft was fine; compiling it broke an invariant | `engine/strategy_compiler_v2.py` |
| `CANONICAL_VALIDATOR` | compiled, then failed the equivalence check | `engine/validated_intent_snapshot.py` |
| `USER` | the person has not said something that is needed | this is correct behaviour, not a bug |

`USER_INFORMATION_REQUIRED` and `UNSUPPORTED_REQUIREMENT` are the system **working**. It
refuses to invent a value. Do not "fix" them.

## The pipeline, in order

Find the first stage where the meaning was already wrong. Everything after it is a
consequence, not a cause.

| # | Stage | Owner | Goes wrong as |
|---|---|---|---|
| 1 | Segmentation into fragments | `engine/turn_fragments.py` | one sentence read as one thing when it was three |
| 2 | Fragment kind — social, question, instruction, correction | `engine/turn_fragments.classify_fragment` | a question treated as an instruction, or a correction ignored |
| 3 | Active question resolution | `engine/active_question.py` | "yes" attached to the wrong open question |
| 4 | Model tier routing | `services/ai_model_routing.py` | a hard turn priced as a simple one |
| 5 | Capability shortlist | `engine/capability_shortlist.py`, `capability_resolver.py` | the right meaning, no matching capability offered |
| 6 | Grounding | `engine/grounded_patch.py`, `semantic_grounding.py` | a value that is not in the person's own words |
| 7 | `apply_setup_turn` | `engine/setup_turn_execution.py:405` | the patch applied to the wrong target |
| 8 | `StrategyDraftV2` | `engine/strategy_draft_v2.py` | state lost between turns |
| 9 | Semantic validation | `engine/validated_intent_snapshot.py` | draft and intent disagree |
| 10 | Compiler | `engine/strategy_compiler_v2.py` | refused, or compiled to the wrong rule |
| 11 | Semantic diff | `engine/draft_diff.py` | the change shown is not the change made |
| 12 | Response composition | `services/setup_chat_agent.py` | the answer describes something the draft does not do |

Stage timings are recorded under the names in `engine/turn_timing.py:STAGES`. Use them to
see where a slow turn actually spent its time instead of guessing.

## Reproduce it without spending money

In this order:

```powershell
# 1. Free. Replays recorded real turns through the real interpreter.
.venv/Scripts/python scripts/replay_recorded_turns.py --run <RUN_ID>

# 2. Free. Shows the compiler's own typed reason, which the evaluator artifacts do not record.
.venv/Scripts/python scripts/probe_planner_turn.py --envelope <FILE> --message "<TEXT>"

# 3. Costs a few tenths of a cent, and saves the envelope so it is free from then on.
.venv/Scripts/python scripts/probe_planner_turn.py --live --message "<TEXT>" --save <FILE>
```

Recorded runs live under `chatbot_eval_runs/`.

**Measure on the production path.** The chat compiles canonical state, not the joined text
of the conversation. A probe that feeds joined chat text reports findings the real service
would never produce.

## The invariants a fix may not break

If your proposed change touches one of these, it is wrong.

- **Fail closed.** Meaning that cannot be represented becomes a blocking issue. Never
  substitute a nearest capability, a default level, or a default comparator.
- **Never invert.** A capability that only expresses "at least" refuses an upper bound. It
  does not compile it as the opposite.
- **Never clamp.** `RSI at least 999` is out of domain. Refuse it; do not make it 100.
- **Only `TRADING_MECHANIC` fragments reach capability resolution.** Approval gating,
  Sharia and labelling policy, rollback requests, open questions and remarks about the
  conversation are separate categories. Emitting them as blocking capability findings
  produces issues that no user answer can ever clear.
- **The AI never approves.** It may ask for approval. Only the application's own
  hash-bound route grants it. Wording that describes the approval gate must never read as
  granting it.
- **Every AI value must be grounded** — findable in the person's own text. Confidence is
  the model's opinion of itself and cannot detect an invented threshold. Only the source
  text can.
- **A diagnostic must never become the failure.** Field caps on error and provenance
  payloads truncate; they never raise. Two HTTP 500 classes came from a length cap firing
  while the compiler was reporting a problem.
- **Sharia status is never assigned, inferred, or implied** from chat, from a model, or
  from a heuristic.

## Report

| Section | Content |
|---|---|
| What the person asked for | Their own words |
| What the system did | In plain words |
| Owner | `PROVIDER` / `MODEL` / `COMPILER` / `CANONICAL_VALIDATOR` / `USER` |
| First wrong stage | One row from the pipeline table, with the file |
| Cause | One or two plain sentences |
| Class | Which other phrasings hit the same cause |
| Reproduced with | The exact command, and whether it cost anything |
| Invariant check | Which invariants the proposed fix touches, if any |
| Uncertain | What is still unknown |
