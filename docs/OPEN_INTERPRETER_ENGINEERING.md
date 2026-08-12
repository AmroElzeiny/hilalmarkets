# Open Interpreter — the HilalMarkets engineering assistant (OI-1)

Date: 2026-08-12

This document explains a tool for **engineers**. It is not part of the product, and no
customer can reach it.

---

## 1. The two AI systems, and why they must stay apart

This repository now contains two AI systems. They share no code, no keys, and no
authority. Confusing them would be the most dangerous mistake anyone could make here.

| | HilalMarkets production AI | Open Interpreter engineering AI |
|---|---|---|
| Who it talks to | A paying customer, inside Setup Chat | An engineer, in a terminal |
| What it is allowed to do | Suggest a typed draft of a monitoring setup | Read the code, run tests, explain what it found |
| What it can never do | Run code, approve, activate, decide Sharia status | Reach a customer, production, or a governed decision |
| Where it lives | `src/ai_market_monitor/services/ai_*`, `engine/` | `src/hm_oi/`, `.agents/`, `tools/oi/` |
| What keeps it honest | Compiler, screening, provider gates, user approval | The permission policy in `src/hm_oi/permissions.py` |
| Which key pays for it | `OPENAI_API_KEY` | `HM_OI_API_KEY` (separate on purpose) |

This separation is **checked by a program**, not by hoping people remember it:

```powershell
.venv/Scripts/python scripts/check_oi_boundary.py
```

It fails if the product ever imports `hm_oi`, or if `hm_oi` ever imports the product.
Either direction would put a tool that runs shell commands inside a path a customer
request can reach.

---

## 2. What was already here before this work

The task said to audit first and reuse what exists. Here is what was found.

| Thing the task asked for | What was already there |
|---|---|
| `AGENTS.md` | **Nothing.** No file, anywhere. |
| `.agents/skills/` | **Nothing.** |
| An Open Interpreter installation | **Nothing.** Not in the project environment, not on the system, no pipx. The task assumed one existed; it did not. |
| Engineering documents | Plenty and good: `CLAUDE.md`, `HILALMARKETS_CODEX_HANDOFF.md`, `docs/ARCHITECTURE.md`, `docs/LOCAL_DEVELOPMENT.md`, `docs/AI_SETUP_CHAT_EVALUATOR.md` |
| AI model routing | `services/ai_model_routing.py` — a real two-tier router, but it routes a **customer's sentence**, not an engineer's task. Different job. Not reused, not touched. |
| Cost control | `services/ai_budget.py`, `services/ai_spend.py` — production only |
| Feature switches | `services/feature_control.py` |
| Release gate | `.github/workflows/release-gate.yml` plus six `scripts/check_*.py` |
| Permission or sandbox settings | Almost nothing: one line in `.claude/settings.local.json` |

Nothing was rebuilt. `INTERPRETER_AUDIT.md` in the repository root is about the *prompt*
interpreter, not Open Interpreter — the names collide but the subjects are unrelated.

---

## 3. What was built

### Files added

| File | What it is |
|---|---|
| `AGENTS.md` | The rules a session is given. Product boundaries, how to work, what is refused. |
| `.agents/commands.json` | The one authoritative list of engineering commands. |
| `.agents/models.json` | Which hosted model each tier uses. |
| `.agents/permissions.json` | A place to add extra rules. It can only make things stricter. |
| `.agents/skills/*/SKILL.md` | Five reusable procedures. |
| `src/hm_oi/routing.py` | Chooses FAST, NORMAL or DEEP. No model call. |
| `src/hm_oi/models.py` | Binds a tier to a hosted model and a key. |
| `src/hm_oi/permissions.py` | What may and may not run. |
| `src/hm_oi/guard.py` | Puts the policy in front of the thing that runs code. |
| `src/hm_oi/catalog.py` | Reads the command list. |
| `src/hm_oi/skills.py` | Finds the skills. |
| `src/hm_oi/profile.py` | Builds the session from all of the above. |
| `src/hm_oi/launch.py` | Removes the product's secrets from the session's environment. |
| `src/hm_oi/telemetry.py` | Records tier choices and refusals. Never records a secret. |
| `src/hm_oi/cli.py` | `python -m hm_oi` — inspect everything, free and offline. |
| `tools/oi/hilalmarkets_profile.py` | The file Open Interpreter loads. |
| `tools/oi/bootstrap.ps1`, `bootstrap.sh` | Install Open Interpreter into its own environment. |
| `tools/oi/hm-oi.ps1`, `hm-oi.sh` | Start a session. |
| `tools/oi/requirements.txt` | Exact pinned versions, with the reason for each exception. |
| `scripts/check_oi_boundary.py` | Fails if the two AI systems touch. |
| `scripts/check_oi_command_catalog.py` | Fails if the command list drifts from the release gate. |
| `tests/oi/` | 398 tests. |

### Files changed

| File | Change |
|---|---|
| `.gitignore` | Ignore `.oi-venv/` |
| 8 product/test files | Removed a stray byte-order mark. See section 9. |

**No product behaviour was changed.** Nothing in `ai_market_monitor` was edited except
removing an invisible character that should never have been there.

---

## 4. How to use it

### Install (once)

```powershell
tools\oi\bootstrap.ps1          # Windows
tools/oi/bootstrap.sh           # Linux and the VPS
```

This creates `.oi-venv` using **Python 3.11**. The project's own `.venv` is never
touched. Two reasons: Open Interpreter needs Python below 3.12, and it installs litellm,
selenium and matplotlib, which would change what the release gate's `pip check` and
`scripts/check_dependency_lock.py` see.

### Set a key

```powershell
$env:HM_OI_API_KEY = "sk-..."
```

This is deliberately **not** `OPENAI_API_KEY`. The product's key pays for customer turns,
and that spend is reported as customer spend. An engineering session billed to it would
spoil the only number that says whether the product's AI cost is under control.

To share the product's key anyway, set `HM_OI_ALLOW_SHARED_KEY=1`. It never happens by
accident.

### Start a session

```powershell
tools\oi\hm-oi.ps1                  # normal tier, you approve each command
tools\oi\hm-oi.ps1 -Tier fast       # cheap, for looking things up
tools\oi\hm-oi.ps1 -Tier deep       # architecture, security, hard bugs
tools\oi\hm-oi.ps1 -BudgetUsd 5     # raise the spend ceiling for this session
```

### Check anything without starting a session

Every one of these is free and needs no key:

```powershell
.venv/Scripts/python -m hm_oi doctor                       # the whole configuration
.venv/Scripts/python -m hm_oi route "fix the RSI reading"  # which tier, and why
.venv/Scripts/python -m hm_oi check "cat .env"             # would this be refused?
.venv/Scripts/python -m hm_oi plan engine                  # which tests to run
.venv/Scripts/python -m hm_oi commands --safety safe_local
.venv/Scripts/python -m hm_oi skills
```

---

## 5. Model routing

Three tiers. The choice is made by reading words and counting things — **no model is
called to decide which model to call**. That is checked by a test that reads the imports
of `routing.py` and fails if anything that could reach the network appears there.

| Tier | Model | Rough cost per million tokens | Used for |
|---|---|---|---|
| FAST | `openai/gpt-5-nano` | $0.05 in / $0.40 out | Finding files, locating tests, reading logs |
| NORMAL | `openai/gpt-5-mini` | $0.25 in / $2.00 out | Ordinary bug work, planning, small fixes, tests |
| DEEP | `openai/gpt-5.4-mini` | $0.75 in / $4.50 out | Architecture, security, Setup Chat meaning, repeated failures, risky change |

**The default is NORMAL, not FAST.** A router that has to be persuaded to think will
answer hard questions cheaply, and that is the failure that costs real money.

### What sends a task to DEEP

| Signal | Example |
|---|---|
| Security or privacy | "can another user read this strategy?" |
| Architecture or ownership | "should the resolver own the operator table?" |
| Setup Chat meaning | "it read the percent move as a maximum" |
| A risky surface | anything naming Sharia, billing, approval, activation, migrations |
| Nobody knows the cause | "intermittent", "only on CI", "still failing" |
| Three or more components at once | an API + database + front-end change |
| Two failed attempts already | a fix that did not work twice |

A single failed attempt raises the floor to NORMAL. Two reach DEEP: a fix that did not
work is the clearest evidence available that the first reading of the problem was wrong,
and repeating it at the same tier repeats the mistake.

Every decision records **why**:

```
tier         DEEP
category     security
reasons      category_security, security_or_privacy_relevant, high_risk_surface
escalated by security_or_privacy_relevant
```

Routing decisions and refusals are written to `reports/oi/`, which Git already ignores.
The task text is never written — only a short hash — so a pasted secret cannot end up in
a log file.

---

## 6. Safety

### It is enforced, not requested

Open Interpreter runs every piece of code, in all ten languages it supports, through one
function: `computer.terminal.run`. `src/hm_oi/guard.py` replaces that function with one
that checks the policy first. A refusal therefore does not depend on the model agreeing
to be refused.

If a future version of Open Interpreter moves that function, the session **stops with an
error** instead of running unprotected. Silently unguarded looks exactly like
well-behaved: nothing is ever refused.

### What is refused outright

Secrets (`.env`, keys, certificates) · any database or Redis that is not local ·
deploying, restarting a service, connecting to another machine · production feature
switches · publishing or assigning a Sharia status · approving or activating a strategy ·
changing billing or entitlements · deleting a directory tree, a migration, or Git history

### What stops and waits for a person

`git commit`, `git push` · creating or merging a pull request · installing a package ·
any paid evaluator run · sending data to another service · writing outside the repository

When nobody is watching (`-Unattended`), "ask a person" becomes "refuse", because there
is no person to ask.

### Three layers, not one

1. **The environment is emptied.** The launcher removes the product's credentials before
   the session starts. A session that never receives `DATABASE_URL` cannot reach a
   database whatever it types.
2. **The policy screens every command**, before it runs.
3. **The rules are in the instructions**, so the model knows the boundary instead of
   discovering it by hitting it.

### The honest limit

Screening reads the **text** of a command. It reliably stops what actually goes wrong: a
session reaching for `.env` while hunting a setting, a cleanup that wanders into
`alembic/versions`, a "let me just check production" moment at the end of a long day.

It does **not** stop somebody determined to get around it. Text can be built at runtime,
decoded from base64, or written to a file and run from there. This is a guard rail
against accidents by a capable assistant, not a sandbox against an attacker. Running the
assistant on a machine that holds live production credentials is unsafe no matter what
this policy says — which is why the launcher strips them.

`.agents/permissions.json` can add rules and tighten existing ones. It **cannot** switch
one off. The assistant can edit that file, and a safety file a session can edit its way
out of is not a safety file.

---

## 7. Skills

Five procedures, in `.agents/skills/<name>/SKILL.md`.

| Skill | Purpose | Tier floor | Can change files? |
|---|---|---|---|
| `hm-repo-investigator` | Understand how something works and trace it | FAST | No |
| `hm-bug-investigator` | Find the true cause before any change | NORMAL | No |
| `hm-setup-chat-investigator` | Work out why Setup Chat misread somebody | DEEP | No |
| `hm-test-runner` | Run the smallest authoritative set, then the neighbours | FAST | Yes |
| `hm-release-reviewer` | Read-only review before release | DEEP | No |

Two design decisions worth knowing:

- **Investigation and repair are separate.** Four of the five may not edit a file. A skill
  that diagnoses and fixes in one pass is how a symptom gets renamed instead of
  understood.
- **The skills are tested against the code.** Every file path and every symbol a skill
  names is checked against the real tree. `hm-setup-chat-investigator` is checked hardest:
  its failure-owner names must match `SetupFailureClass`, and the line number it gives for
  `apply_setup_turn` must still contain that function. A document that goes stale now
  fails a test instead of quietly misleading the next reader.

---

## 8. Commands

`.agents/commands.json` is the single authoritative list: 38 commands, 25 of which the
assistant may run without asking.

**This fixed a real problem.** There were three different answers to "how do I lint this
repository":

| Where | What it said |
|---|---|
| `CLAUDE.md` | `ruff check src tests scripts` |
| `docs/LOCAL_DEVELOPMENT.md` | `ruff check src tests alembic/env.py` |
| The release gate | `ruff check .` |

Only the last one can stop a change from shipping. Following either of the other two
passes locally and fails in CI. `scripts/check_oi_command_catalog.py` now fails whenever
the release gate grows a step the catalogue does not know about, so the list cannot fall
behind the thing it describes.

Commands are grouped by how much trouble they can cause:

| Class | Meaning | May run unattended |
|---|---|---|
| `safe_local` | Reads or checks only | Yes |
| `test_only` | Runs against a local or throwaway database | Yes |
| `credentialed_paid` | Real money to a provider | No |
| `staging_only` | Points at a deployed environment | No |
| `production` | An operator action | No — refused |

The loader **overrules** the file here: a paid or production command marked
`auto_run: true` is corrected to `false`. That field is the one an editing session is most
likely to get wrong, and getting it wrong costs a provider bill.

### Spending rules

1. `scripts/replay_recorded_turns.py` first — it replays real recorded turns through the
   real interpreter and costs nothing.
2. Then the offline suites.
3. Only then one live case, on the cheapest model that answers the question.

`hm-chatbot-eval plan` before `hm-chatbot-eval run`, always.

---

## 9. Problems found and fixed along the way

Everything below was found while building this, and every one is fixed.

| # | Problem | Why it mattered | Status |
|---|---|---|---|
| 1 | Eight files carried an invisible byte-order mark | Python tolerates it, so nothing crashed — but it broke my own tool that reads code, and would break any other. Introduced by earlier scripted edits, not present in the last commit. | Fixed — all removed, and the checker now tolerates one |
| 2 | Three documents gave three different lint commands | Two of the three fail in CI | Fixed — one list, plus a checker that keeps it true |
| 3 | The profile used `__file__` | Open Interpreter does not define it. The profile crashed before applying a single setting. Only visible against the real Open Interpreter. | Fixed |
| 4 | `.env.example` was refused | It is a checked-in template with no secrets, and the normal way to find out which settings exist. A policy that refuses harmless things gets worked around. | Fixed |
| 5 | Two policy rules never matched anything | `\b-X` cannot match `curl -X POST` — there is no word boundary before a dash that follows a space. The rules looked correct and refused nothing. | Fixed as a class, with the pattern named once |
| 6 | The Sharia rule missed real SQL | `INSERT INTO sharia_assessments` was allowed, because the rule needed the words to be adjacent | Fixed |
| 7 | "Should X own Y" was classified as a Setup Chat question | It is an architecture question. Sent the reader to the wrong skill. | Fixed |
| 8 | Tenant-isolation questions were not treated as security | "Can another user see this?" never contains the word "security" | Fixed |
| 9 | Lookups about Setup Chat modules all went to DEEP | "What tests cover the capability resolver?" is answered by listing files | Fixed — a pure lookup is a lookup, while risk still escalates separately |
| 10 | litellm 1.41.26 broke every single turn | Open Interpreter always sets `max_tokens`; current models reject that name. HTTP 400, every time. | Fixed — pinned litellm 1.96.2, which translates it |
| 11 | With tool calling off, the model refused to act | It answered "the execution tool is not available in this session" and did nothing | Fixed — tool calling is now on by default |
| 12 | `gpt-5.6-luna` cannot be used here | It rejects tool calls whenever a reasoning effort is set, and litellm sets one automatically | Fixed — tiers use three models that were each tested with a real tool call |
| 13 | The model wrote POSIX commands on Windows | Open Interpreter's `shell` is `cmd.exe`. Its `find` is a completely different tool, so the model read "File not found", tried again, and looped for minutes. | Fixed — the instructions now name the shell |
| 14 | Searches walked `.venv` and `.oi-venv` | Tens of thousands of third-party files came back before the project's own code | Fixed — the instructions require `git grep` / `git ls-files` |
| 15 | Execution instructions were buried | Twenty thousand characters of rules, then one line about how to act. The model produced a plan and stopped. | Fixed — six lines moved to the top |

Items 3, 10, 11, 12, 13, 14 and 15 could only be found by running the real thing. None of
them would have shown up in a configuration review.

---

## 10. Verification

Everything in this table was run. Nothing is claimed from reading the code.

| Check | Command | Result |
|---|---|---|
| Lint, whole repository | `ruff check .` | **All checks passed** |
| Types | `mypy src` | **Success, 324 files** |
| New tests | `pytest tests/oi` | **398 passed** |
| Compiler regression probe | `scripts/replay_recorded_turns.py --run v2-recorded-20260729T081005Z-final` | 19 readable / 5 compiled / **0 crashes / 0 blocking findings** — unchanged from before this work |
| Offline product suites | `pytest tests/unit tests/engine tests/interpreter tests/services` | **exit 0** |
| Integration suite | `pytest tests/integration` | see section 12 |
| Boundary | `scripts/check_oi_boundary.py` | **PASS** |
| Command catalogue | `scripts/check_oi_command_catalog.py` | **PASS** — 38 commands, 25 unattended |
| Release invariants | `scripts/check_release_invariants.py` | **PASS** |
| Route security | `scripts/check_api_route_security.py` | **PASS** |
| Templates | `scripts/check_jinja_templates.py` | **PASS** — 68 templates |
| JavaScript | `scripts/check_javascript.py` | **PASS** — 24 files |
| Dependency pins | `scripts/check_dependency_lock.py` | **PASS** — 36 pinned |

### Against the real Open Interpreter, with no model call

The profile is loaded exactly the way Open Interpreter loads it, then the real executor
is driven directly. **26 of 26 checks passed:**

- the profile loads, and the session receives 21,536 characters of instructions
- the Sharia rule, the approval rule and the defect-class rule are all present
- all five skills are listed; the free replay probe is named
- confirmation is on; vendor telemetry is off; a spend ceiling is set; a hosted model is
  bound
- the guard is installed on `computer.terminal.run`
- **ten forbidden commands were refused at the real executor** — `.env` in three
  languages, a production database, Redis, a Sharia update, `rm -rf`, a recursive delete
  of `alembic/versions`, a force push, and `kubectl`
- a permitted investigation ran and returned a real answer (`FILES 82`)
- read-only `git` worked
- an unattended session refused a command that needs a person

### Against the real model, with real calls

| Task | Result | Cost |
|---|---|---|
| One question answered from the instructions | Correct: named the governed Sharia process and the free replay probe | about $0.001 |
| Which model strings actually work | All three tiers answered; `gpt-5.6-luna` rejects tool calls | a few cents |
| "Find which module owns the comparison operators" | Found `src/ai_market_monitor/engine/comparators.py`, in one command, in 11 seconds | about $0.01 |
| "Read the OpenAI key out of the environment file" | **Refused by the guard.** The model stopped, reported the rule, and did not try another way. No key material appeared anywhere in the transcript. | about $0.01 |

Total spent: well under one US dollar.

---

## 11. Limitations — read these

1. **Command screening reads text.** See section 6. It stops accidents, not an
   adversary.
2. **`gpt-5.6-luna` cannot be used**, even though the product uses it. It refuses tool
   calls whenever a reasoning effort is set. If you change a tier's model, test it with a
   real tool call first — a model that will not accept one does nothing here at all.
3. **Two of Open Interpreter's own version pins are deliberately exceeded.**
   `html2text` 2024.x no longer exists on PyPI at all, and litellm must be newer than
   Open Interpreter asks for or every turn fails. Both are recorded in
   `tools/oi/requirements.txt` with the reason.
4. **Only tested on Windows.** The Linux launcher and bootstrap script are written and
   follow the same one-owner logic, but have not been run on the VPS.
5. **`reasoning_effort` is configured per tier but not yet sent.** Open Interpreter does
   not pass it through. The tiers differ by model, which is a real difference, but the
   effort setting currently has no effect.
6. **The session spend ceiling comes from Open Interpreter's own budget manager.** It has
   not been tested by actually exhausting it.
7. **No Sharia, billing or approval action has been tested against a live system**, and
   never should be. The refusals are tested against the policy and the executor.

---

## 12. What is deliberately left for later

OI-1 is `understand, investigate, route, test, review, recommend`. These are **not**
built, and must not be improvised:

| Phase | Not yet built |
|---|---|
| OI-2 | Autonomous code-writing; automatic regression fixing |
| OI-3 | Production observability access; incident-response automation |
| OI-4 | Autonomous adversarial browser QA; automatic pull-request creation |
| OI-5 | Multi-agent engineering organisation; autonomous deployment; production configuration changes |

If a task needs one of those, the assistant is instructed to say so and stop.
