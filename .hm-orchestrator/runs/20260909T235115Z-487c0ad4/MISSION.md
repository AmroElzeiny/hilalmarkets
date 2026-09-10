# Mission — Phase B: verify the repaired test file, then prove upgrade and downgrade

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, money)
Date: 2026-09-10

**Why this file is named CLAUDE_DECISION.md and not MISSION.md.** Two Claude sessions share
this repository. `.hm-orchestrator/current/MISSION.md` belongs to a different session and was
overwritten mid-flight, which sent an earlier run the wrong mission. This file is a mission
file. Treat it exactly as you would `MISSION.md`. **Do not read or follow
`.hm-orchestrator/current/MISSION.md` — it belongs to other work and is not yours.**

This is **phase B of three**. Phase A is complete and was verified by Claude. Phase C follows.

## Memory rules — hard limits, not advice

Five earlier runs were killed because this machine ran out of memory.

- **Do not run the full offline suite.** Targeted test files only, one at a time.
- **Do not start a browser.** Phase C does that.
- **One worker at a time.** No parallel sub-agents.
- **One reviewer**, at the end.
- **Write evidence files as you go.** If this run is killed, the next must continue from what
  you wrote. Do not save all evidence for the end.
- When you finish, make sure no OpenCode child process is left running.

## B-R0 — verify the repaired test file. Do this first, and do not trust it.

`tests/integration/test_plan_change_journey.py` was left half-written and unparsable by a run
that was killed. It now parses, and `pytest tests/integration --collect-only` succeeds.

**But nobody knows who repaired it.** Claude did not, and the run that was supposed to do it
was handed the wrong mission. It may have been repaired by a stopped process or by an
unrelated session. So it is **unattributed work of unknown quality**.

Read the whole file. Judge it honestly:

- Does every test in it actually assert something real about upgrade and downgrade?
- Is anything a stub, a `pass`, an always-true assertion, or a test that would pass even if the
  product were broken?
- Does it match what the code really does, or was it written to fit an assumption?

If it is sound, say so and build on it. If it is hollow, replace it with a real test and say
plainly what was wrong. A test that cannot fail is worse than no test.

## What Phase A delivered (verified by Claude — do not redo, do not undo)

Root cause: the rule "this account already holds a paid plan" existed in **two copies that
disagreed**. `dashboard.py` refused only the *same* plan, so a customer holding the other paid
plan was shown Card and Crypto as available; `services/billing.py` had no case for that
situation and refused later with a bare 400 that nothing explained.

The fix put the rule in one owner. Claude verified this independently:

| Check | Result |
|---|---|
| `tests/unit/test_invariant_billing_offers.py` | 13 passed |
| `mypy src` | clean, 425 files |
| old duplicate `_plan_checkout_allowed` | 0 hits — gone |
| one owner | `services/billing.py:523 active_paid_plan_codes`, `:557 plan_checkout_availability`, imported by `dashboard.py` and `dashboard_test.py` |

Six product files carry the fix: `services/billing.py`, `api/routers/dashboard.py`,
`api/routers/dashboard_test.py`, `static/hilalmarkets-billing.js`,
`templates/hilal/dashboard/billing.html`, `templates/hilal/dashboard/checkout.html`.

**Do not rewrite this fix.** If you believe it is wrong, say so with evidence.

## Requirements

| ID | Requirement | Acceptance evidence |
|---|---|---|
| B-R0 | The repaired journey test is judged honestly and is real. | Your written judgement of the file, plus `pytest tests/integration --collect-only -q` succeeding. If you replaced hollow tests, say exactly which and why. |
| B-R7 | **Upgrade works in practice.** Moving to a higher plan really changes what the customer gets and what they are charged. | End-to-end evidence: what the customer could use before and after, the money effect, the record written, and the confirmation the customer sees. Cover upgrading while on monthly and while on annual. |
| B-R8 | **Downgrade works in practice**, including what happens to the period already paid for. | Same evidence shape. State clearly **when** the change takes effect and prove the code does exactly that. Include downgrade to the free plan if the product allows it. |
| B-R9 | A page may never offer a way of paying, or a plan change, that the server would refuse. | A test tying what the page offers to what the server accepts, so the two cannot drift apart again. |
| B-R13 | **Phase A gets its independent review.** Its reviewer was killed before it ran, so the six changed files have been checked by Claude alone. | One independent logic reviewer reads those six files and the Phase A evidence, and attacks the money path: right plan, right amount, no double charge, no method offered that the server would refuse, no customer left paying with no access. |
| B-R14 | The cosmetic defect Phase A recorded is closed. | `plan_checkout_availability` has a duplicated empty refusal branch (two identical strings). Remove the duplication. Also review the same-plan refusal wording — Phase A flagged it as too defensive for a beginner. |
| B-R12 | No test weakened. | Answer explicitly: any test deleted, skipped, xfail'd, assertion widened, expected value changed, or special case added? Repairing a file that does not parse is not weakening; replacing it with something that asserts less **is**, and is forbidden. |

## The product rule that decides B-R7 and B-R8

Find the product's own stated rule for **when** a plan change takes effect and how money is
handled. Follow it. Do **not** invent a proration rule.

If the code and the stated rule disagree, that is a defect to report, not something to smooth
over. If no stated rule exists anywhere, stop and escalate — that decision belongs to the user.

## Runtime/source-of-truth authority

- `src/ai_market_monitor/services/plan_changes.py`
- `src/ai_market_monitor/services/billing.py`
- `src/ai_market_monitor/services/entitlements.py`
- `src/ai_market_monitor/api/routers/billing.py`, `dashboard.py`
- `src/ai_market_monitor/static/hm-plan-change.js`
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html` (switch and cancel dialogs)
- `src/ai_market_monitor/core/plans.py`
- `docs/BILLING_SETUP.md`, `CLAUDE.md`, `AGENTS.md`

## Non-negotiable invariants

- **No completed real charge.** Creating a provider session to prove a path is allowed;
  spending real money is not. If a live charge is the only remaining proof, escalate.
- **Never print a value** from `.env` or `.env.production`. Key names only. Claude confirmed
  both real files hold Creem product IDs for `trader_monthly` and `pro_monthly`, so there is no
  missing-credential problem to hunt.
- **Fail closed.** Never offer a plan change the server would refuse. Never guess.
- **Never weaken a test.**
- Messages must read for a beginner. No "entitlement", "provider", "proration", "capability".
- Copy rules from `core/copy_rules.py`: **Hilal Markets** in prose, **Shariah** in technical
  usage, forbidden-claims list on every customer-visible word.
- Do not touch the many unrelated uncommitted changes in the tree.
- Any migration constraint or index name goes through `op.f()`.

## Verification floor

- `pytest tests/integration --collect-only -q` succeeds.
- The upgrade and downgrade journey tests pass, run as targeted files.
- `.venv/Scripts/python -m ruff check src tests` and `.venv/Scripts/python -m mypy src`.
- Targeted tests only. The full suite belongs to phase C.

## Reviewer policy

**One** independent logic reviewer at the end: `opencode-go/deepseek-v4-pro`.
It must cover both phase B's work **and** phase A's six changed files (B-R13).

The adversarial reviewer and the vision reviewer run in phase C.

## Completion condition

Report complete when B-R0, B-R7, B-R8, B-R9, B-R12, B-R13 and B-R14 each carry PASS evidence
or a named blocker, and the reviewer's findings are closed or escalated.

If the intended rule for plan changes cannot be found, write `ESCALATE_TO_CLAUDE` and say what
decision is needed. Do not invent a commercial answer.
