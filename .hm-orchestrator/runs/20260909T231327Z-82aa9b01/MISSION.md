# Mission — Phase B: repair the broken test file, then prove upgrade and downgrade

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, money)
Date: 2026-09-10

This is **phase B of three**. Phase A is done and verified by Claude. Phase C follows
(screenshots against the visual contract, full regression suites, adversarial + vision
reviewers).

## Memory rules — hard limits, not advice

Four earlier runs were killed because this machine ran out of memory.

- **Do not run the full offline suite.** Targeted test files only, one at a time.
- **Do not start a browser.** Phase C does that.
- **One worker at a time.** No parallel sub-agents.
- **One reviewer**, at the end.
- **Write evidence files as you go.** If this run is killed, the next must continue from what
  you wrote.

## B-R0 — URGENT, do this first

`tests/integration/test_plan_change_journey.py` **does not parse**. It has
`IndentationError: unexpected indent` at line 227.

Claude confirmed the damage: `pytest tests/integration --collect-only` ends with
`Interrupted: 1 error during collection`. **One broken file is blocking the whole
integration suite from running.** An earlier run was killed while half-way through writing it.

Repair it first, before anything else. It is meant to be the upgrade and downgrade journey
test, so finishing it properly *is* the work of B-R7 and B-R8 — do not delete it and do not
replace it with a stub. When it parses, `pytest tests/integration --collect-only` must succeed.

## What Phase A already delivered (verified by Claude — do not redo it)

The root cause was **two copies of the "already holds a plan" rule that disagreed**:

- `dashboard.py` refused only the *same* plan, so a person holding the other paid plan was
  shown card and crypto as available.
- `services/billing.py` had no case for the other-plan situation, so checkout failed later
  with an unexplained 400.

The fix put the rule in one place. Claude verified this independently:

| Check | Result |
|---|---|
| `tests/unit/test_invariant_billing_offers.py` | 13 passed |
| `mypy src` | no issues, 425 files |
| old duplicate `_plan_checkout_allowed` | 0 hits — gone |
| one owner | `services/billing.py:523 active_paid_plan_codes`, `:557 plan_checkout_availability`, imported by `dashboard.py` and `dashboard_test.py` |

Six product files carry the fix: `services/billing.py`, `api/routers/dashboard.py`,
`api/routers/dashboard_test.py`, `static/hilalmarkets-billing.js`,
`templates/hilal/dashboard/billing.html`, `templates/hilal/dashboard/checkout.html`.

**Do not undo or rewrite this fix.** Build on it. If you believe it is wrong, say so with
evidence rather than changing it quietly.

## Requirements for phase B

| ID | Requirement | Acceptance evidence |
|---|---|---|
| B-R0 | The integration suite collects again. | `pytest tests/integration --collect-only -q` succeeds. The repaired file is real, not a stub. |
| B-R7 | **Upgrade works in practice.** Moving to a higher plan really changes what the customer gets and what they are charged. | End-to-end evidence: what the customer could use before and after, the money effect, the record written, and the confirmation the customer sees. Cover upgrading while on monthly and while on annual. |
| B-R8 | **Downgrade works in practice**, including what happens to the period already paid for. | Same evidence shape. State clearly **when** the change takes effect and prove the code does exactly that. Include downgrade to the free plan if the product allows it. |
| B-R9 | A page may never offer a way of paying, or a plan change, that the server would refuse. | A test tying what the page offers to what the server accepts, so the two cannot drift apart again. |
| B-R13 | **Phase A gets its independent review.** Phase A's reviewer was killed before it ran, so the billing fix has not been reviewed by anyone but Claude. | One independent logic reviewer reads the six changed files and the Phase A evidence, and attacks the money path: right plan, right amount, no double charge, no method offered that the server would refuse, and no customer left paying without access. |
| B-R14 | The cosmetic defect Phase A recorded is closed. | `plan_checkout_availability` has a duplicated empty refusal branch (two identical strings). Remove the duplication. Also review the same-plan refusal wording — Phase A flagged it as too defensive for a beginner. |
| B-R12 | No test weakened. | Answer explicitly: any test deleted, skipped, xfail'd, assertion widened, expected value changed, or special case added? Repairing a file that does not parse is not "weakening" — but replacing it with something that asserts less **is**, and is forbidden. |

## The product rule that decides B-R7 and B-R8

Find the product's own stated rule for **when** a plan change takes effect and how money is
handled. Follow it. Do **not** invent a proration rule.

If the code and the stated rule disagree, that disagreement is a defect to report, not
something to smooth over. If no stated rule can be found anywhere, stop and escalate — that is
a commercial decision the user owns.

## Runtime/source-of-truth authority

- `src/ai_market_monitor/services/plan_changes.py`
- `src/ai_market_monitor/services/billing.py`
- `src/ai_market_monitor/services/entitlements.py`
- `src/ai_market_monitor/api/routers/billing.py`, `dashboard.py`
- `src/ai_market_monitor/static/hm-plan-change.js`
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html` (the switch and cancel dialogs)
- `src/ai_market_monitor/core/plans.py`
- `docs/BILLING_SETUP.md`, `CLAUDE.md`, `AGENTS.md`

## Non-negotiable invariants

- **No completed real charge.** Creating a provider session to prove a path is allowed;
  spending real money is not. If a live charge is the only remaining proof, escalate — that is
  the user's decision.
- **Never print a value** from `.env` or `.env.production`. Key names only. Claude has already
  confirmed both real files hold Creem product IDs for `trader_monthly` and `pro_monthly`, so
  there is no missing-credential problem to hunt.
- **Fail closed.** Never offer a plan change the server would refuse. Never guess.
- **Never weaken a test.**
- Messages must read for a beginner. No "entitlement", "provider", "proration", "capability".
- Copy rules from `core/copy_rules.py` apply: **Hilal Markets** in prose, **Shariah** in
  technical usage, forbidden-claims list on every customer-visible word.
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
