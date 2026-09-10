# Mission - Phase C: press the buttons in a real browser, and close the report-format defect

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, money)
Date: 2026-09-10

## Read this first

**This file is your mission.** It is named `CLAUDE_DECISION.md` and not `MISSION.md` because
two Claude sessions share this repository and `MISSION.md` belongs to the other one.

Two files will be copied into your run directory that are **not yours**:

| File in your run directory | Whose it is | What you must do |
|---|---|---|
| `MISSION.md` | a copy of this file | this is your mission |
| `VISUAL_CONTRACT.md` | the **other** session's "Ask AI" contract | **ignore it completely.** Your visual contract is section "Visual contract" below. |

Do not touch anything to do with an "Ask AI" button, `--hm-sky` tokens, or
`tests/*/test_ask_ai_*`. That is the other session's work in progress.

## What already happened (verified by Claude - do not redo, do not undo)

Phase A and Phase B are done. Claude re-ran the evidence itself and confirms:

| Check | Result, measured by Claude on 2026-09-10 |
|---|---|
| `pytest tests/integration/test_plan_change_journey.py` | 16 passed |
| `pytest tests/integration/test_dashboard_test_subscription.py tests/unit/test_invariant_billing_offers.py` | 56 passed, 1 skipped |
| `mypy src` | clean, 425 files |
| `ruff check src` | clean |
| `ruff check tests` | 4 findings, all in the other session's `test_ask_ai_*` files. Not yours. Leave them. |
| tracked files changed by Phase B | exactly 3: `api/routers/dashboard_test.py`, `services/billing.py`, `templates/hilal/base_dashboard.html` |
| `git status --porcelain` | 201 lines before and after Phase B. Nothing appeared or vanished. |

The original defect is fixed: the rule "this account already holds a paid plan" now has one
owner, `services/billing.py:plan_checkout_availability`, and the page can no longer offer a
way of paying that the server would refuse.

**Do not rewrite any of that.** If you believe it is wrong, say so with evidence.

## Why Phase C exists

Everything proved so far was proved **without a browser**.

The customer's actual complaint was: *"I click Pay and nothing happens."* A dead button is a
browser fact. No test in this repository has ever pressed that button.

Proof: `tests/browser/conftest.py` defines two fixtures for exactly this,
`paid_browser_app` (line 436) and `live_shape_browser_app` (line 470). Search the whole
`tests/browser/` directory: **no test uses either of them.** Six matches, all inside
`conftest.py` itself. The paid checkout flow has a stage built for it and nobody has ever
walked on to it.

That is the hole this phase closes.

## Memory rules - hard limits, not advice

Five earlier runs on this machine were killed for running out of memory. There is about
3.4 GB free right now, and a browser is the heaviest thing you can start.

- **One browser test file at a time.** Headless. Close the browser when done.
- **Never run the whole `tests/browser/` suite.** Only your own new file, by path.
- **One worker at a time.** No parallel sub-agents.
- **Write evidence files as you go.** If this run is killed, the next must be able to
  continue from what you wrote. Do not save all evidence for the end.
- If a browser step is killed twice, stop and record it as unverified. Do not keep retrying.
- When you finish, leave no OpenCode child process running.

## Requirements

### C-R1 - a real browser presses Pay for the Pro plan, by card

This is the customer's exact complaint. Prove it is gone.

Use `live_shape_browser_app` (card through Creem, crypto through NOWPayments - the same
shape the deployed server has). Sign in as an account holding **no** paid plan. Open the Pro
plan checkout.

Assert, in the browser:

1. the Card choice is visible and can be clicked;
2. clicking it really selects it - the hidden radio becomes checked. The radios are hidden by
   CSS at `static/hilalmarkets-dashboard-v2.css` lines 2490-2496 (`opacity:0`, 1px box), so
   assert the **input's checked state**, never its visibility;
3. the submit button, which starts disabled
   (`templates/hilal/dashboard/billing.html` line 300, and
   `static/hilalmarkets-billing.js` line 240 `submit.disabled = submitting || !selectedMethod`),
   **becomes enabled** after the click;
4. pressing it starts the hand-off to the payment company.

**Stop before any real charge.** No key here reaches a real company. If the only way to go
further would be a live charge, stop there and say so - that is the correct end of the test.

### C-R2 - by crypto, the same

Repeat C-R1 for the Crypto choice. Both were reported dead. Both must be proved alive.

### C-R3 - the dead button can never come back

Sign in as an account that already holds **Trader**. Open the Pro plan.

Assert:
- no Card or Crypto choice is offered;
- the page shows the plain sentence explaining why, from
  `plan_checkout_availability`'s `refusal`;
- **there is no enabled button that would do nothing.** Count them: every enabled submit
  control on that surface must lead somewhere the server accepts.

This is the invariant the whole fix rests on. A browser is the only place it can be proved.

### C-R4 - upgrade and downgrade, driven by hand

In the browser, on `/dashboard/billing`:

- an account on Trader presses the switch to Pro, confirms, and sees the confirmation
  sentence;
- an account on Pro presses the switch down to Trader, confirms, and sees a sentence that
  says **when** it happens - at the end of the period already paid for;
- prove the page a customer lands on after each one is not an error page.

Phase B proved the server. This proves the person can actually get there.

### C-R5 - screenshots, then a vision reviewer

For every state below, save a PNG under
`.hm-orchestrator/runs/<run>/screens/<state>-<viewport>.png`.

Viewports: **1440x900**, **1024x768**, **390x844**.

| State | What it shows |
|---|---|
| S1 | Pro checkout, nothing chosen yet, submit disabled |
| S2 | Pro checkout, Card chosen, submit enabled |
| S3 | Pro checkout, Crypto chosen, submit enabled |
| S4 | Pro checkout as a Trader holder - the refusal sentence, no choices |
| S5 | `/dashboard/billing` showing the switch buttons |
| S6 | the confirmation after a downgrade is scheduled |

Then a **vision-capable** reviewer (`opencode-go/deepseek-v4-flash-vision-exp`) opens the
PNGs and checks them against the visual contract below. It must actually look at the images.
If it cannot, mark visual verification **unverified** and escalate. Never infer a visual pass
from CSS.

### C-R6 - close the report-format defect class

Your predecessor's report was rejected by `validate-report.py`. It was rejected for its
**shape**, not its facts. The facts were correct - Claude checked them.

The root cause is this repository's own recurring defect class: **two owners for one concept
that disagree.**

| Owner | What it says a report is |
|---|---|
| `.hm-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md` | prose. Twelve numbered sections. Never names a single JSON key. |
| `.hm-orchestrator/policy/supervisor-report.schema.json` | the machine. Fourteen exact top-level keys, and `requirements` must be an **array**. |

A supervisor reads the prose, writes a sensible JSON, and fails. It will happen again on
every future run until the two agree.

Fix it:

1. **Make the prose name the keys.** Add to `SUPERVISOR_REPORT_CONTRACT.md` an explicit,
   exact list of the required top-level JSON keys and their types, and state plainly that
   `supervisor-report.schema.json` is the authority that decides. Keep the twelve sections -
   they describe the Markdown report and the meaning. Add the machine shape beside them.
   Do not invent new keys. Read the schema and copy what is really there.
2. **Repair the rejected file in place**:
   `.hm-orchestrator/runs/20260910T000521Z-1da17cdf/SUPERVISOR_REPORT.json`.
   Change **only the shape**. Every fact in it stays exactly as written. Move each fact to
   the key the schema asks for. Add one field recording that the shape was repaired by this
   run and why. Do not soften, drop, or improve a single claim.
3. Prove both: run
   `.venv/Scripts/python tools/hm-orchestrator/validate-report.py <that file> .hm-orchestrator/policy/supervisor-report.schema.json`
   and capture the output. Do the same for **your own** report before you finish.

The required top-level keys, so you cannot get this wrong:
`run_id`, `status`, `baseline`, `models`, `requirements`, `work_packages`, `changed_files`,
`tests`, `test_integrity`, `hidden_error_audit`, `diff_audit`, `reviews`, `uncertainties`,
`final_verdict`. `status` is one of `complete` / `escalated` / `blocked`. `requirements`,
`models`, `work_packages` and `reviews` are non-empty **arrays**.

This is a cheap text task. Give it a cheap model.

### C-R7 - correct one overstated number

Phase B's report says `test_dashboard_test_subscription.py` was "44/44 pass". The JUnit file
says 44 tests, 0 failures, **1 skipped**. So it is 43 passed and 1 skipped. Correct that
sentence when you repair the file.

The skipped one is `test_a_plan_nobody_can_buy_shows_no_price[NOTSET]`. It is skipped because
its parameter list came out empty:
`[code for code, offer in PLAN_OFFERS.items() if not offer.monthly_available]` - today every
plan is on sale, so the list is empty and the test covers nothing.

That is not a weakened test and it was not caused by any run. But it is a test that silently
proves nothing, which this repository treats as a defect. Record it in the uncertainty
register with what would make it real. Do not delete it and do not fake a parameter.

### C-R8 - an adversarial reviewer attacks the money path

One adversarial reviewer, `opencode-go/glm-5.3-flash`, independent of whoever writes the
tests. Give it the Phase A, B and C diffs and evidence. Its job is to break it, not to agree:

- can a customer be charged twice?
- can a customer pay and get no access?
- can a customer be charged the wrong amount, or for the wrong plan?
- can the page still offer anything the server refuses, on any surface, in any state?
- does the downgrade give away a period the customer already paid for, or take one away?
- is any customer-visible sentence wrong, frightening, or written in words a beginner cannot
  act on?

Every finding is fixed, or escalated with a named reason. Not listed and left.

### C-R9 - regression

- `.venv/Scripts/python -m ruff check src tests` - the only findings allowed are the 4
  pre-existing ones in the other session's `test_ask_ai_*` files. Any new one is yours.
- `.venv/Scripts/python -m mypy src` - clean.
- `.venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly`
- the billing integration files by path:
  `tests/integration/test_plan_change_journey.py`,
  `tests/integration/test_dashboard_test_subscription.py`,
  `tests/integration/test_checkout_and_payment_email.py`.
- your new browser file, by path.

**Not** the whole browser suite. Memory.

### C-R10 - no test weakened

Answer explicitly, yes or no, each one: any test deleted? skipped or xfail added? assertion
widened? expected value changed? test-only special case or hardcode added?

Adding a parameter set is not weakening. Removing one is.

## Visual contract

This is the contract for C-R5. It is deliberately short: it constrains **state and
behaviour**, not a new design. Nothing here invents a visual direction. Every value comes
from what is already shipped.

**Source of truth:** `templates/hilal/dashboard/checkout.html`,
`templates/hilal/dashboard/billing.html`, `static/hilalmarkets-dashboard-v2.css`,
`static/hilalmarkets-billing.js`, and `brand guide.md` at the project root.

Must be true in every screenshot:

| # | Rule |
|---|---|
| V1 | Existing classes and tokens only. No new colour, no new component, no raw hex. |
| V2 | S1: submit is visibly disabled and reads as disabled - not merely inert. A control that looks pressable and is not is the defect we are here to remove. |
| V3 | S2 and S3: the chosen method is visibly marked as chosen, even though its radio is hidden by CSS. If the only mark is the radio, that is a defect - report it. |
| V4 | S4: the refusal sentence is visible without scrolling, in the `notice notice-error` pattern already used at `checkout.html` lines 79-89. No empty box, no bare 400, no silence. |
| V5 | At 390x844 nothing overflows sideways and no control is clipped or overlapped. |
| V6 | Every interactive control keeps a visible focus ring - the shipped `--hm-focus-ring` convention only. Do not invent one. |
| V7 | Contrast on any text the customer must read to decide meets 4.5:1. Measure it with `tests/support/contrast.py`, the repository's single owner for that sum. Never judge it by eye. |
| V8 | No layout, colour or spacing on any surface outside the checkout dialog and the billing page changes. |

Escalate if: a contrast value cannot be met without a new token; a state cannot be
photographed; or the vision reviewer cannot actually open the PNGs.

## Model routing

Read `.hm-orchestrator/policy/ROUTING_POLICY.md` and
`.hm-orchestrator/models/LIVE_MODELS.md` and use the live catalog as the authority for
availability. Never invent a model or a variant.

| Work | Role | Model |
|---|---|---|
| C-R6, C-R7 (text and JSON shape) | cheap fast worker | `opencode-go/qwen3.8-flash` |
| C-R1..C-R4 (browser tests) | test and debug | `opencode-go/qwen3.7-plus` |
| any cross-file product fix a reviewer forces | strong coder | `opencode-go/kimi-k2.7-code` |
| C-R5 vision review | vision | `opencode-go/deepseek-v4-flash-vision-exp` |
| C-R8 adversarial review | adversarial | `opencode-go/glm-5.3-flash` |

Do not use a stronger model where a cheaper one is as reliable. If a listed model is not in
the live catalog, pick the nearest one that is and **say which and why** in the report.

## Non-negotiable invariants

- **No completed real charge.** Reaching the hand-off is proof. Spending money is not
  allowed. If a live charge is the only remaining proof, escalate.
- **Never print a value** from `.env` or `.env.production`. Key names and counts only.
- **Fail closed.** Never offer a plan change or a way of paying the server would refuse.
- **Never weaken a test.**
- Every customer-visible sentence must read for a beginner. No "entitlement", "provider",
  "proration", "capability", "checkout session".
  Note: `templates/hilal/base_dashboard.html` lines 98-99 still say "provider-managed
  subscription" and "payment provider" to a customer. That is pre-existing. Fix it - it is
  two sentences - and record it as an extra problem found.
- Copy rules from `core/copy_rules.py`: **Hilal Markets** in prose, **Shariah** in technical
  usage, and the forbidden-claims list on every customer-visible word.
- Do not touch the many unrelated uncommitted changes in the tree, and do not touch the other
  session's `test_ask_ai_*` files.
- Any migration constraint or index name goes through `op.f()`. The untracked migration
  `alembic/versions/d4f61a09c73b_subscription_plan_changes.py` already does - Claude checked.
  Keep it that way.
- ASCII only in any file you write that PowerShell will read. A single em dash in a file with
  no byte-order mark has already broken this orchestrator once.

## Completion condition

Report complete when C-R1 through C-R10 each carry PASS evidence or a named blocker, the
vision reviewer and the adversarial reviewer have both reported, and every finding is closed
or escalated.

Your own `SUPERVISOR_REPORT.json` must pass `validate-report.py`. A run whose report does not
validate is not finished, however good the work was.

If something needs a decision that is the user's to make - a price, a product rule, a refund
policy - write `ESCALATE_TO_CLAUDE`, say exactly what is needed, and finish everything else.
Do not invent a commercial answer.
