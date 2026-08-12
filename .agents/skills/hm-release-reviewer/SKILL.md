---
name: hm-release-reviewer
description: Read-only review of a change before release — boundaries, tests, migrations, invariants, security, and what is still blocking.
minimum_tier: deep
areas: [all, release, security]
read_only: true
---

# Release reviewer

Review only. Change nothing.

**You cannot say a change is ready for production.** You can see one developer machine.
You cannot see production traffic, production data, the deployed configuration, or the
real rollout. The most you may conclude is: *the local checks that exist all pass, and I
found no blocker in what I could read.* Say it that way.

Runs at the **deep** tier. The whole value of this skill is noticing the thing everyone
else missed.

## 1. Know what actually changed

```powershell
git status --porcelain
git diff --stat
git diff
```

This tree usually carries unrelated uncommitted work. Separate "part of this change" from
"already here" before reviewing anything, or you will review the wrong diff.

## 2. Architecture boundaries

Look for a change that quietly moves authority. These are the ones that matter.

| Check | What a violation looks like |
|---|---|
| Deterministic authority | a model call added inside `engine/` |
| Single owner | a second word list, operator table, or regex for a concept that already has an owner (`AGENTS.md` §4) |
| Fail closed | a `try`/`except` or an `or default` that turns a refusal into a value |
| Sharia governance | any code path that sets, infers, or implies halal or haram |
| Approval binding | approval or activation reachable without the hash-bound authenticated route |
| Provider door | a new `httpx.AsyncClient(...)` instead of `provider_runtime.provider_request` |
| State authority | business state whose only home is Redis |
| Engineering separation | `ai_market_monitor` importing `hm_oi`, or the reverse |
| Scanner vs Monitor | the two concepts merged |

Run the checks that enforce some of this:

```powershell
.venv/Scripts/python scripts/check_release_invariants.py
.venv/Scripts/python scripts/check_api_route_security.py
.venv/Scripts/python scripts/check_oi_boundary.py
```

## 3. Tests

- Do the new tests assert a **rule**, parametrised across the family — or only the one
  reported case? A test that passes for the reported input and no other is not coverage.
- Was any test deleted, skipped, `xfail`ed, or had its assertion widened? Find out why.
  This is the most common way a real defect ships.
- Would the new test have failed before the change? If not, it proves nothing.

```powershell
git diff -- tests | Select-String "^-.*(assert|def test_)"
```

## 4. Migrations

```powershell
.venv/Scripts/python -m alembic heads
```

One line only. Two heads means two migrations each claim to be last, and a deploy will
pick one.

Also check: is the new column nullable or does it have a default? A `NOT NULL` column
added without one fails on a table that already has rows. Does anything drop a column or
a table? That is not reversible.

## 5. Release invariants and generated files

- Nothing tracked that should not be: databases, logs, `reports/`, `test-results/`,
  `playwright-report/`, `.venv/`.
- `git diff --exit-code` after a test run — the release gate fails if running the tests
  changed a tracked file.
- Exported contracts still match their models:
  `.venv/Scripts/python scripts/export_setup_chat_eval_contracts.py --check`

## 6. Security and privacy

| Check | Look for |
|---|---|
| New routes | authenticated and ownership-checked, in `check_api_route_security.py` |
| Secrets | a key, token, or password in code, in a test fixture, or in a log line |
| Logging | a customer's message, an email address, or a provider key written to a log |
| Error responses | a stack trace, an internal ID, a prompt, or a database field reaching the browser |
| Input | user text reaching a template unescaped, or a query built by string joining |
| Dependencies | a new package — is it pinned, and is it in `pyproject.toml`? |

## 7. Documents against code

Where the diff changes behaviour, does a document still describe the old behaviour? Name
the file and the line. A stale document is how the next person gets it wrong.

Do **not** treat a document as evidence that the code works. Documents in this repository
have gone stale before.

## 8. Environment assumptions

Look for anything that only works on the machine it was written on: an absolute path, a
hard-coded port, a value read from a developer's `.env`, a test that depends on the local
clock or the local time zone, a test that assumes the order other tests ran in.

## 9. Report

| Section | Content |
|---|---|
| Change | What it does, in two plain sentences |
| Checks run | The exact commands and their output |
| Blockers | Things that must be fixed first, each with a file and a line |
| Concerns | Things worth fixing, not blocking |
| Not checked | What you could not verify, and why |
| Conclusion | "The local checks pass and I found no blocker in what I read" — never "ready for production" |

Order blockers by how bad it is if it ships, not by how easy it is to fix.
