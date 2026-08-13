# The autonomous builder (OI-2)

**Date:** 2026-08-13
**What it is:** Open Interpreter allowed to change code on its own — but only inside an
isolated copy, and only through a fixed set of steps it cannot skip.

**What it is not:** it never merges, never pushes, never deploys, never touches a
customer, and never decides a Sharia status. The end of a successful task is a branch
and a written summary. A person opens the pull request.

---

## 1. The precondition gate — read this first

This phase was scheduled to run *after* two pieces of product work. Both were checked at
commit `211aecc5`, and both are unfinished.

| Check | Result | Evidence |
|---|---|---|
| **P1** Secrets removed before a conversation is stored and before it is sent to a model | **FAIL** | `services/system_brain_privacy.py:17` — the redactor exists, but only the internal admin paths call it. `ai_setup_chat.py` never does. It has no seed-phrase pattern and no bare API-key pattern. |
| **P2** Conversation retention, export, delete, and role-based access with auditing | **FAIL (partial)** | Access *is* audited — `services/system_brain_conversations.py:141` records who looked, why, and when. Retention, export and delete do not exist. |
| **P3** OI-1 exists and works | **PASS** | `AGENTS.md`, 21 enforced permission rules, FAST/NORMAL/DEEP routing, 5 skills, 398 tests green. |

### What that means in practice

A customer who pastes a wallet seed phrase into Setup Chat today has it **stored as
typed and sent to the model provider as typed**. That is a product finding and it is not
fixed by this phase.

Because P1 and P2 failed, this phase gets **no real conversations, no production logs and
no customer database**. All conversation material comes from three committed synthetic
fixtures.

**This is enforced in code, not written in a prompt.** `src/hm_oi/conversation_source.py`
will only open the three files on its allowlist. It refuses anything else three separate
ways: by name, by resolved location, and by shape. There is deliberately **no environment
variable that turns it off** — a switch would eventually be found and used.

The permission rule `builder.no_customer_data` additionally refuses any command naming a
customer table or a database backup.

Every audit record carries the reason for the restriction, so the evidence and the reason
travel together.

**When can it be lifted?** When P1 and P2 actually land. Not before, and not by editing
the allowlist.

---

## 2. How a task runs

```
reproduce → failing test → fix → focused tests → adjacent tests → review → done
```

Each step must record its evidence before the next can start. This is a state machine in
`src/hm_oi/workflow.py`, not an instruction in a prompt.

Asking a model to follow an order works most of the time. "Most of the time" applied to
"did you actually prove the bug was real" produces a codebase full of fixes for problems
nobody demonstrated.

### The two gates that matter

**No fix without a reproducing test.** Not "a test exists" — a test that *ran* and
*failed*, before the change, with its output kept. A test written after the fix only
proves the code does what it does.

```
>>> task.record_fix(["src/engine/comparators.py"], diff)
WorkflowViolation: REJECTED: a fix was submitted with no reproducing test.
```

A regression test that *passes* before the fix is also refused. It did not reproduce
anything, whatever it is called.

**No completion on a red suite.** `complete()` reads the recorded runs, not the model's
summary of them. One failure anywhere and the task stays open.

### Adjacent tests are found, not guessed

"Adjacent" means every test module that mentions a file the diff touched. It is found by
searching the repository. Asking the model returns a plausible list, and a plausible list
is the wrong tool for finding the test nobody remembered.

If nothing is found, that is recorded honestly as "no adjacent tests discovered". It does
**not** run an unrelated suite and call its green a verification.

---

## 3. Where the work happens

Every task gets its own Git worktree at `C:\hm-oi-wt\<task>`, on its own branch named
`oi/<task-slug>`. The name is deterministic, so a retry continues the same work instead
of scattering near-identical branches.

A worktree, not a clone: a clone of this repository copies 794 MB of history per task,
which on a machine with 2 GB free is the difference between a usable tool and one nobody
starts.

Because a worktree shares the real object store, `workspace.py` refuses operations by
**destination** as well as by command text: `push`, `merge`, `rebase`, `tag`, `reset`,
`filter-branch` and `remote` are refused outright, and `main`, `master`, `develop`,
`release` and `production` can never be named.

---

## 4. Permissions — what is enforced and what is not

Autonomous mode uses OI-1's 21 rules **plus** 9 more that are strictly tighter. A test
asserts that autonomous mode is never looser than interactive mode, for every command.

### Technically enforced

| Control | How |
|---|---|
| Cannot read `.env` or any credential file | Rule `secret.env_file`, checked in code before the shell sees it |
| Product keys never reach the session | `launch.py` builds a clean environment; the process never receives them |
| Cannot push, merge, rebase, tag, or delete a branch | Refused twice: by rule, and by destination in `workspace.py` |
| Cannot open or merge a pull request | Rule `builder.no_pull_request_action` |
| Cannot reach a production database, Redis, or deploy | 13 refusing rules |
| Cannot install packages | Rule `builder.no_install` |
| Cannot spend money on the evaluator | Rule `builder.no_paid_evaluator` |
| Cannot read customer data | Rule `builder.no_customer_data`, plus the fixture allowlist |
| Cannot change the engineer's working tree | It works in a separate worktree |
| A task cannot loop forever | Three ceilings: attempts, wall time, spend |
| Nothing is complete on a red suite | `Task.complete()` reads the runs |
| No secret in a commit or pull-request body | `refuse_if_secret` — it stops, it does not quietly redact |
| No secret, conversation text or model reasoning in an audit record | `audit.write_record` refuses the whole record |

### Advisory only — these are findings, not controls

| Not enforced | What this means |
|---|---|
| **Everything in `AGENTS.md` about how to work** | Instructions to the model. Nothing checks them. |
| **The skill procedures** | Same. A skill marked read-only is not prevented from writing. |
| **"Do not promote your own test into the authoritative suite"** | Written in the skill, not enforced. A person must check. |
| **Command screening can be worked around** | The rules read command text. Text can be assembled at run time or hidden in a file. This stops accidents by a capable assistant, not an adversary. |
| **`CONFIRM` rules in an attended session** | These depend on a person reading the command. Real, but human, not technical. Autonomous mode converts the dangerous ones to outright refusal for exactly this reason. |
| **No network egress limit** | Outbound *writes* are refused by rule, but nothing at the OS level restricts where the process can connect. |

---

## 5. When it cannot solve something

```
attempt 1  gather more evidence, same model
attempt 2  a stronger model
attempt 3  the architecture and security reviewer
then       stop, and hand a person everything collected
```

The ladder matters less than the ceiling. An agent that retries until it succeeds will,
on a task it cannot do, retry until the money runs out. Three independent limits, and
reaching **any** one stops the work:

| Limit | Default |
|---|---|
| Attempts | 3 |
| Wall-clock time | 30 minutes |
| Spend | $2.00 |

Every escalation must record *why* in words. "Attempt 2 failed" is refused as a reason;
"the fix did not make the regression test pass" is accepted. Without it, a person reading
the log later cannot tell a hard problem from a broken harness.

---

## 6. The independent reviewer

A separate context that sees **only** the task, the diff, and the test runs. It never
sees the implementer's reasoning.

That is the whole design. An implementer that has just spent twenty minutes convincing
itself a change is correct writes a very persuasive explanation, and a reviewer who reads
it is reviewing the explanation.

It refuses:

| Rule | What it catches |
|---|---|
| `no_reproducing_test` | No test recorded failing before the fix |
| `no_test_in_diff` | Code changed, no test added |
| `weakened_assertion` | An assertion deleted, replaced with `assert True`, or loosened from `==` to `in` |
| `disabled_test` | A test skipped, marked expected-to-fail, or removed |
| `scope_creep` | Files outside the task's stated scope |
| `governed_authority` | Sharia, approval, activation, capability, ownership or billing |
| `secret_in_diff` | Anything credential-shaped in the added lines |
| `red_suite` | Any recorded run still failing |

Every rule runs on the diff text **before** any model is asked anything. A rule that does
not need a model cannot be talked out of its answer.

A rejection returns the task to the implementer with its reasons. It never passes
silently, and it never fails silently.

---

## 7. The audit record

One JSON object per task, appended to `reports/oi/builder-<date>.jsonl`. That folder is
already ignored by Git and already refused by `check_release_invariants.py`, so a record
cannot reach a commit by accident.

| Field | Meaning |
|---|---|
| `task_id`, `description` | What was asked |
| `branch` | Where the work is |
| `disposition` | `completed` or `escalated_to_human` |
| `changed_files` | Every file touched |
| `regression_test` | The test that proves it |
| `tests` | Every run: command, counts, exit code, green or red |
| `adjacent_selection` | Which modules were checked, and how they were found |
| `model_tier`, `tier_reason` | Which model, and why it was chosen or escalated to |
| `cost_usd` | What it spent |
| `review_verdict`, `review_reasons` | What the reviewer said |
| `escalation` | Every rung, with its reason |
| `restrictions` | Why this phase was limited to fixtures |

**Never recorded:** raw conversation text, model reasoning, or anything secret-shaped.
The record is refused outright — not quietly cleaned — if it carries a field on the
never-log list. Redaction runs over the whole record on the way out, because call-site
redaction works until somebody adds a field and forgets.

---

## 8. Runbook

### Check what a command would do, without running anything

```powershell
.venv\Scripts\python -m hm_oi check "git push origin main"
```

### Run the harness's own tests

```powershell
.venv\Scripts\python -m pytest tests/oi -q -p no:randomly
```

The end-to-end tests create a real worktree and are slow. Skip them while iterating:

```powershell
.venv\Scripts\python -m pytest tests/oi -q -p no:randomly -k "not end_to_end"
```

There is deliberately no `slow` marker: this repository runs pytest with
`--strict-markers`, and registering one would mean editing shared configuration for one
file's benefit.

### Clean up a worktree by hand

```powershell
.venv\Scripts\python -c "from hm_oi.workspace import remove_workspace; remove_workspace('<task-id>')"
```

The branch is kept on purpose. Deleting branches is forbidden precisely so an automated
tidy-up can never throw away a task's only output.

### Read today's audit records

```powershell
Get-Content reports\oi\builder-*.jsonl | ConvertFrom-Json | Format-Table task_id, disposition, review_verdict
```

---

## 9. What this does **not** make safe

Say this plainly, because a document describing controls invites the belief that
everything is controlled.

1. **It does not make the model correct.** Every gate here is about process — was there a
   test, is the suite green, did a second opinion look. None of them tell you whether the
   fix is the *right* fix. A person still reads the diff.
2. **It does not stop a determined bypass.** Command screening reads text. Text can be
   assembled at run time. This is a guard rail against accidents, not a sandbox against
   an adversary.
3. **It does not fix the product's secret-handling.** P1 and P2 are still failing. A
   customer's seed phrase is still stored and forwarded as typed. This phase avoids that
   data; it does not protect it.
4. **It does not protect the engineer's working tree from the engineer.** The isolation
   applies to the harness. A person running Open Interpreter interactively against the
   real repository has OI-1's weaker rules, which still allow writing to product files.
5. **It has never run with a real model.** The workflow, the gates, the reviewer and the
   escalation ladder are all proven with a scripted implementer. Whether a real model
   produces good work inside this harness is untested — see the report.
6. **The audit log is local and unsigned.** It can be edited by anyone who can write to
   `reports/`. It is a record for honest review, not evidence against a bad actor.

---

## 10. Files

| File | What it does |
|---|---|
| `src/hm_oi/workflow.py` | The state machine and the two hard gates |
| `src/hm_oi/workspace.py` | Worktrees, branch naming, destination-based refusal |
| `src/hm_oi/reviewer.py` | The independent review rules |
| `src/hm_oi/escalation.py` | The ladder and the three ceilings |
| `src/hm_oi/audit.py` | The record, and what may never be in it |
| `src/hm_oi/redaction.py` | Secret shapes, detection and removal |
| `src/hm_oi/conversation_source.py` | The fixture allowlist, enforced |
| `src/hm_oi/builder_permissions.py` | OI-1's rules, tightened for unattended work |
| `src/hm_oi/builder.py` | Puts them together and runs one task |
| `.agents/skills/hm-conversation-regression/SKILL.md` | Naming the layer that broke |
| `tests/oi/test_invariant_builder_*.py` | The eight validation cases |
