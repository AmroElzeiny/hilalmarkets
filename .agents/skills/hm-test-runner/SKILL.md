---
name: hm-test-runner
description: Choose and run the smallest authoritative set of tests for what changed, then the right adjacent regressions.
minimum_tier: fast
areas: [all]
read_only: false
---

# Test runner

Run the smallest set that would catch the mistake, then widen. Do not invent a command:
`.agents/commands.json` is the authoritative list, and several documents in this
repository name commands that no longer exist.

## Rules

- **`pytest-timeout` is not installed. Never pass `--timeout`.** It fails immediately.
- Use `-p no:randomly` for the offline suites so a failure is reproducible.
- The working tree usually carries unrelated uncommitted changes. A failure is not
  automatically caused by what you just did.
- Never delete, skip, or loosen a test to get a green run. If a test is wrong, say so and
  change the assertion deliberately, with the reason.

## Choose the set

Look up the area in `test_selection` in `.agents/commands.json`, or use this:

| What changed | Run first | Then |
|---|---|---|
| `engine/` | `tests/engine tests/unit` | `tests/interpreter tests/services`, replay probe |
| compiler, capabilities | `tests/unit tests/engine` | `tests/interpreter tests/integration`, replay probe |
| Setup Chat | `tests/interpreter tests/unit` | `tests/services tests/integration tests/evaluator`, replay probe |
| `services/` | `tests/services tests/unit` | `tests/integration` |
| `api/routers/` | `tests/integration` | `tests/services`, route-security check |
| `db/models/`, `alembic/` | `tests/integration` | `alembic heads`, `tests/unit` |
| templates, `static/` | jinja + javascript checks | `tests/browser` |
| `worker.py` | `tests/unit` | `tests/integration`, worker smoke |
| `src/hm_oi/`, `.agents/` | `tests/oi` | boundary + catalog checks |

For a single symbol, find its tests rather than guessing the directory:

```powershell
git grep -ln "<symbol>" -- tests
```

## Run it

```powershell
# One test, while you iterate.
.venv/Scripts/python -m pytest tests/unit/<file>.py::<test> -q -p no:randomly

# The offline suites. Fast, no network, no provider.
.venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly

# Everything the release gate runs except the browser. Slow — before finishing, not while iterating.
.venv/Scripts/python -m pytest --ignore=tests/browser
```

Static checks, which are fast and catch a different class of mistake:

```powershell
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy src/ai_market_monitor src/hm_chatbot_eval
```

## The free compiler probe

For anything touching interpretation or compilation, this is worth more than any single
test. It replays real recorded turns through the real interpreter and costs nothing:

```powershell
.venv/Scripts/python scripts/replay_recorded_turns.py --run <RUN_ID>
```

Report its numbers as it prints them: readable, compiled, crashes, blocking findings.

## Paid tests

`hm-chatbot-eval run`, `run_isolated_setup_chat_smoke.ps1` and
`probe_planner_turn.py --live` call a paid provider for real.

Never run one to check a routine change. Before any of them:

1. State why the offline suites and the replay probe cannot answer the question.
2. Run `hm-chatbot-eval plan --mode budget` and report the estimate.
3. Ask the engineer. The permission policy will stop you otherwise.

## Attributing a failure

Before calling something a regression:

1. `git status --porcelain` — see what else is uncommitted.
2. Create a clean worktree at `HEAD`. Use a short path: `git worktree add C:\wt-head HEAD`.
   This repository's nested directories overflow the Windows path limit otherwise.
3. Run the failing test IDs there.
4. Copy **only** the files you changed onto that worktree and run them again.

That is the only way to know the change caused the failure.

## Report

| Section | Content |
|---|---|
| Commands run | Exactly, one per line |
| Result | Pass or fail, with the counts printed |
| Failures | Test ID and the assertion, not a paraphrase |
| Attribution | Caused by this change, or pre-existing — and how you know |
| Not run | What you did not run, and why |

Never write a pass rate you did not see printed.
