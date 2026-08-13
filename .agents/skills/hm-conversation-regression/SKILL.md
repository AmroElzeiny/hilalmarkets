---
name: hm-conversation-regression
description: Take a failed Setup Chat conversation, name the exact layer that broke, prove it with file and line, and propose a permanent regression test.
minimum_tier: deep
areas: [setup_chat, compiler, engine, evaluator]
read_only: true
---

# Conversation regression

A conversation went wrong. Your job is to say **which layer** broke, prove it, and write
the test that stops it happening again.

Guessing is a failure of this skill. "It looks like a grounding problem" is not an
answer. If you cannot tell, say you cannot tell and say what evidence would settle it.

## Where conversations come from — read this first

**You may not read real customer conversations.** Not from the database, not from
production logs, not from a backup file.

The product does not yet redact secrets before storing a conversation or before sending
it to a model provider, and it has no conversation retention or delete path. Until both
exist, this phase works only on committed synthetic fixtures:

```
tests/fixtures/setup_chat_language_quality_corpus.jsonl
tests/fixtures/prompt_understanding_corpus.jsonl
tests/fixtures/agent_control_corpus.jsonl
```

This is enforced in code, not by this paragraph. `hm_oi.conversation_source` refuses
anything else, and the permission rule `builder.no_customer_data` refuses queries against
customer tables. If you find yourself needing a real conversation, stop and say so.

## Step 1 — separate the three kinds of failure

Do this before naming a layer. Most wasted effort comes from treating an infrastructure
failure as a meaning problem.

| Kind | What it looks like | What fixes it |
|---|---|---|
| **Provider / infrastructure** | timeout, 5xx, circuit open, empty response, HTTP 400 about parameters | nothing in the prompt; retry, pin, or fix the client |
| **Application logic** | the model returned something sensible and the code did the wrong thing with it | a code fix and a regression test |
| **Semantic / model** | the model itself read the words wrongly | a grounding rule, a refusal, or a compiler gate — never a bigger prompt |

Check `SetupFailureClass` in `engine/setup_failure_taxonomy.py`. `PROVIDER_FAILURE` and
`FailureOwner.PROVIDER` mean the model never had a fair chance. Say so and stop; there is
no semantic bug to find.

**Say which of the three it is, in one sentence, before going further.**

## Step 2 — name the layer

Exactly one, from this list. Each row names where to look first.

| Layer | It broke here if | Look at |
|---|---|---|
| `segmentation` | one message was split into the wrong pieces, or not split | `engine/turn_fragments.py` |
| `classification` | a piece was labelled the wrong kind of thing | `engine/turn_fragments.classify_fragment` |
| `routing` | the right meaning went to the wrong handler | `services/ai_model_routing`, the turn router |
| `grounding` | a value appeared that the user never said | `engine/grounded_patch.py` |
| `authorization` | a change was applied that the user's words did not license | `engine/active_question.py`, the patch authorizer |
| `capability_resolution` | a real request became "unsupported", or an unknown term was silently mapped | `engine/comparators.py`, the capability registry |
| `compiler` | a correct draft produced a wrong or refused rule | the compiler and `engine/numeric_clause.py` |
| `response_composition` | the decision was right and the reply read wrongly | the composer, the copy rules |
| `provider` | see step 1 | `services/provider_runtime.py` |
| `ui_state` | correct server state, wrong thing on screen | templates, the dashboard JavaScript |

## Step 3 — prove it, with file and line

A layer name without evidence is still a guess. Produce all three:

1. **The exact value** as it entered the layer, and as it left.
2. **`file:line`** where it changed, from reading the code — not from memory.
3. **A cheap reproduction.** Free options first, always:

```powershell
.venv/Scripts/python scripts/replay_recorded_turns.py --run <RUN_ID>
.venv/Scripts/python scripts/probe_planner_turn.py --envelope <FILE> --message "<TEXT>"
```

Neither costs anything and both drive the real interpreter. Reach for a paid call only
when nothing else can answer the question, and say why.

## Step 4 — find the class, not the case

> A bug report names one example. The example is a symptom.

Ask: **which other inputs reach this same line?** Then search for the duplicate — in this
repository the cause is almost always two places deciding the same thing:

```powershell
git grep -n "<the phrase list or regex>" -- src
```

Confirmed duplicates found before: three movement-direction word lists, two percent-move
implementations, two reversion regexes, a comparator table parallel to the turn
classifier's. Two owners means the fix is **extraction into one owner**.

## Step 5 — propose the regression test

Write it as a **candidate**. Parametrised across the whole family — every operator phrase
against every indicator, every term in the vocabulary — so that a fix which only rescues
the reported sentence fails it.

Say plainly which existing file it belongs in, and why that one.

## What you must never do

- **Never write a Sharia, halal or haram status.** Not into a fixture, not into a test,
  not as an expected value. That status comes from the platform's review process.
- **Never activate or approve a strategy**, and never write a test that does.
- **Never promote your own test into the authoritative suite.** You propose; a person
  decides. Adding it yourself removes the only review step that exists.
- Never widen an assertion, declare two different values equivalent, or special-case the
  reported input to make something pass.
- Never quote customer text you were not given. You only have fixtures.

## Report

| Section | Content |
|---|---|
| Kind | provider / application logic / semantic — one, with the reason |
| Layer | one row from step 2 |
| Evidence | the value in, the value out, and `file:line` |
| Reproduction | the exact command, and whether it cost anything |
| Root cause | one or two plain sentences |
| Class | every other input that hits the same cause |
| Duplicate found | the second owner, or "none found" |
| Candidate test | the parametrised test, and where it belongs |
| Confidence | what would change your answer |
| Could not determine | say so here rather than guessing above |
