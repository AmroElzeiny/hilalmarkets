---
name: hm-repo-investigator
description: Understand how something in this repository works and trace it end to end, without changing any code.
minimum_tier: fast
areas: [all]
read_only: true
---

# Repository investigator

Answer "how does this work?" with evidence from the code, not from a document.

**You may not edit a file while running this skill.** If you find a defect, write it down
and finish the investigation. Fixing it is `hm-bug-investigator` and a separate decision.

## Why this exists

The documents in this repository go stale. Several name files that were renamed, commands
that were replaced, and architecture that was changed afterwards. An answer built from a
report reads well and can be wrong. An answer built from the code and the tests can be
checked.

## Steps

### 1. Name the concern in one sentence

Write it down before you search. "Where is the Sharia screening applied?" is a concern.
"Tell me about screening" is not, and it will produce four pages nobody reads.

### 2. Find the authoritative module

Look in this order. Stop as soon as you have the real owner.

| Question | Look here first |
|---|---|
| A word the product uses | `docs/ARCHITECTURE.md` layer map, then `engine/` |
| A shared vocabulary (operators, direction, fragments) | the owner table in `AGENTS.md` §4 |
| An HTTP route | `src/ai_market_monitor/api/routers/` |
| A stored record | `src/ai_market_monitor/db/models/` |
| A background job | `src/ai_market_monitor/worker.py` |
| A screen | `templates/hilal/`, `static/` |

Use search, not guessing:

```powershell
.venv/Scripts/python -m pytest --collect-only -q tests/unit | Select-String "<term>"
git grep -n "<term>" -- src
```

### 3. Check whether the concept has more than one owner

**This is the step that finds real problems.** The recurring defect in this codebase is
two modules that each decided what a word means and each understood a different subset.

Before you say "module X owns this", search for a second implementation:

```powershell
git grep -n "<the regex, constant, or phrase list>" -- src
```

If you find two, that is a finding. Report it with both file paths and say which callers
use which. Do not fix it inside this skill.

### 4. Trace the path

Follow the call chain from the entry point to the stored result. Name each hop with a
file and a line. A trace with a gap in it is a guess.

For a Setup Chat concern the path is:

```text
API message
→ deterministic intent gate
→ at most one bounded structured AI extraction call
→ deterministic patch
→ StrategyDraftV2
→ deterministic compiler
→ semantic equivalence validation
→ inactive Canvas preview
→ explicit authenticated approval
→ activation gates
→ Scanner or Monitor runtime
```

### 5. Find the tests that hold it in place

```powershell
git grep -ln "<symbol>" -- tests
```

Tests named `test_invariant_*.py` state a **rule**, not a case. They are the best
description of what the code is required to do. Read them before you read the
implementation's comments.

### 6. Report

| Section | Content |
|---|---|
| Answer | Two or three sentences, plain words |
| Path | Each hop as `file.py:line` |
| Owner | The one module responsible, named |
| Tests | Which tests would fail if it broke |
| Uncertain | What you could not confirm, and why |
| Found on the way | Anything wrong you noticed, not fixed |

Never say "verified" about something you only read. Reading proves the code says
something. Running a test proves it does something.
