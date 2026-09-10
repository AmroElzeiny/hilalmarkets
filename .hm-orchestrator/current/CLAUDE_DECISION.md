> # SUPERSEDED - 2026-09-10. DO NOT EXECUTE THIS FILE.
>
> The owner stopped the session that owned this mission. Everything below is kept only
> as a record of what was asked and why.
>
> **`.hm-orchestrator/current/MISSION.md` is now the only mission for this repository.**
> It already lists which Phase C2 items landed in the tree (keep them) and which never
> finished (they are WP9 there).
>
> Two instructions below are now **false** and must not be followed:
> - "MISSION.md belongs to a different Claude session" - it does not; it is yours.
> - "Never touch anything about an Ask AI button" - that work is yours too.
>
> One section below is still **in force**: the **Visual contract** (V1-V8) near the end.
> WP9 of the live mission points at it for the checkout screenshots.

# Mission - Phase C2: finish Phase C, and close the defect Phase A missed

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, money)
Date: 2026-09-10

## Read this first

**This file is your mission.** It is named `CLAUDE_DECISION.md` because `MISSION.md` belongs
to a different Claude session sharing this repository.

Two files will be copied into your run directory that are **not yours**:

| File in your run directory | Whose it is | What to do |
|---|---|---|
| `MISSION.md` | a copy of this file | this is your mission |
| `VISUAL_CONTRACT.md` | the other session's "Ask AI" contract | **ignore it completely** |

Never touch anything about an "Ask AI" button, `--hm-sky` tokens, or `test_ask_ai_*` files.
The 4 `ruff check tests` findings in those files are the other session's. Leave them.

## Why this run exists

The previous run (`20260910T010643Z-c3d54570`) was killed when this machine ran out of
memory. It is the sixth kill. It got a long way first, and **you keep everything it made.**

Already done. Do not redo:

| Made by the killed run | Where | State |
|---|---|---|
| 18 screenshots, S1-S6 x 3 viewports | `.hm-orchestrator/runs/20260910T010643Z-c3d54570/screens/` | complete |
| browser test file, 14 tests, 39 asserts | `tests/browser/test_checkout_pay_button_e2e.py` | written, parses, **has two defects - see C2-R2** |

Claude looked at S1 and S2 personally and confirms the customer's original bug is gone in the
popup: before choosing, the button is white and reads "Choose how to pay"; after choosing
Card it turns green and reads "Go to the card payment page".

**Copy the whole `screens/` folder into your own run directory** and reuse it. Do not start a
browser to take those pictures again. Memory is the reason the last five runs died.

## C2-R1 - BLOCKER. Phase A fixed one call site and missed another.

Claude found this while the run was down, and confirmed it in the code.

The rule "this account already holds a paid plan, so it may not buy another one" has **two
owners again**, and they disagree. This is the same defect class Phase A was supposed to
end.

| Surface | What decides | Result for a customer holding **Trader** who opens **Pro** |
|---|---|---|
| the popup (`dashboard_test.py`) | `services/billing.py:557 plan_checkout_availability` - includes `holds_other` | correct: no way to pay is offered, a plain sentence explains why |
| the review page (`dashboard.py:3831 billing_checkout_review`) | a hand-written check at **line 3905**: `already_subscribed=plan.code in review_active_paid_plan_codes` | **wrong: catches the same plan only** |

Follow it through the template, `templates/hilal/dashboard/checkout.html`:

- line 73 `{% if already_subscribed %}` - false, because they hold Trader, not Pro
- line 79 `{% elif not billing_method_offers.values()|selectattr('available')|list %}` - false,
  because `payment_method_offers_by_method` only asks "can the company sell this plan", never
  "may this account buy it"
- so the page falls through to the **full payment form**, at line 99 onward

The customer fills in the form, presses Pay, and the server refuses with
`change_plan_instead`. That is the customer's original complaint - a button that leads
nowhere - alive on a second page.

Phase B's reviewer saw this and wrote it off as finding **L3, "no customer impact"**. That
judgement was wrong. Reaching a dead end after typing your name and address is impact.

**The fix is extraction, not patching.** `plan_checkout_availability` is the one owner. Make
this page read it, exactly as the popup does. Then:

1. **Search every other call site** for the same class before you finish. Look at
   `dashboard.py` lines 3346, 3682, 3926, at `api/routers/billing.py`, and anywhere else that
   decides whether a plan may be bought. `billing_checkout_review` was missed once already
   because nobody searched. List every place you checked in the report, including the ones
   that were already correct.
2. The review page must show the refusal, not the form. The right pattern is already in the
   same file at lines 79-89 (`notice notice-error`) - reuse it, do not write a second one.
   The sentence must name what to do instead: switch plans on the billing page.
3. Add a test that fails today and passes after the fix, and that covers **every** paid plan
   crossed with **every** other paid plan - not just Trader-to-Pro. Tests assert the rule,
   not the reported case.

## C2-R2 - BLOCKER. Two tests skip themselves when they find the bug.

`tests/browser/test_checkout_pay_button_e2e.py` contains two `pytest.skip` calls, at roughly
lines 391 and 400. The second one says out loud:

> "Checkout page shows the form for a trader holder. The refusal happens at POST time, not at
> render time."

That is C2-R1. The test **found the defect and then skipped itself.**

A test that skips when the product is broken cannot ever fail. It is worse than no test,
because it reports green. This breaks the "never weaken a test" invariant.

Remove both skips. Replace them with assertions that:
- fail on today's code, and
- pass once C2-R1 is fixed.

Run the test **before** the fix and capture it failing. Then fix. Then capture it passing.
Both captures go in the report. A reproducing test with no captured red run is not evidence.

Also read the whole file again with fresh eyes: 14 tests, 39 asserts. Anything else in it
that cannot fail - a stub, a bare truthy check, a branch that quietly passes - gets the same
treatment. It was written by a run that was killed, so nobody has judged it.

## C2-R3 - one more screenshot, one browser run only

After C2-R1 is fixed, add **S7**: the Pro review page, opened by an account holding Trader,
showing the refusal instead of the form. Three viewports: 1440x900, 1024x768, 390x844.

Save to your own run's `screens/` folder beside the 18 you copied.

**One browser run, one file, headless.** Only `tests/browser/test_checkout_pay_button_e2e.py`,
by path. Never the whole `tests/browser/` suite.

## C2-R4 - vision review

`opencode-go/deepseek-v4-flash-vision-exp` opens the 21 PNGs and checks them against the
visual contract below. It must really look at the images. If it cannot, mark visual
verification **unverified** and escalate. Never infer a visual pass from CSS.

## C2-R5 - adversarial review

`opencode-go/glm-5.3-flash`, independent of whoever wrote the fix. Give it the Phase A, B and
C diffs and all evidence. Its job is to break the money path:

- can a customer be charged twice?
- can a customer pay and get no access?
- wrong amount, or wrong plan?
- **can any page, on any surface, in any state, still offer something the server refuses?**
  C2-R1 is proof this question was answered too quickly last time. Make it hunt.
- does the downgrade take away a period the customer already paid for, or give one away?
- is any customer-visible sentence wrong, frightening, or in words a beginner cannot act on?

Every finding is fixed or escalated with a named reason. **"No customer impact" is not a
reason** unless you can show the path is unreachable, and say how you proved it.

## C2-R6 - close the report-format defect class

The Phase B report was rejected by `validate-report.py` for its **shape**, not its facts.
Claude checked the facts and they are correct.

Root cause - two owners for one idea:

| Owner | What it says a report is |
|---|---|
| `.hm-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md` | prose, 12 sections, **never names one JSON key** |
| `.hm-orchestrator/policy/supervisor-report.schema.json` | the machine, 14 exact top-level keys |

1. Make the prose name the keys. Add the exact required top-level keys and their types to
   `SUPERVISOR_REPORT_CONTRACT.md`, and say plainly that the schema file is the authority.
   Keep the 12 sections. Read the schema and copy what is really in it. Invent nothing.
2. Repair `.hm-orchestrator/runs/20260910T000521Z-1da17cdf/SUPERVISOR_REPORT.json` in place.
   **Change only the shape.** Every fact stays exactly as written. Add one field saying the
   shape was repaired by this run and why. Do not soften or drop a claim.
3. Prove it: run
   `.venv/Scripts/python tools/hm-orchestrator/validate-report.py <file> .hm-orchestrator/policy/supervisor-report.schema.json`
   and capture the output. Do the same for your own report before you finish.

Required top-level keys: `run_id`, `status`, `baseline`, `models`, `requirements`,
`work_packages`, `changed_files`, `tests`, `test_integrity`, `hidden_error_audit`,
`diff_audit`, `reviews`, `uncertainties`, `final_verdict`. `status` is `complete` /
`escalated` / `blocked`. `requirements`, `models`, `work_packages` and `reviews` are non-empty
**arrays**.

Cheap text work. Give it `opencode-go/qwen3.8-flash`.

## C2-R7 - one overstated number, and one hollow test

Phase B's report says `test_dashboard_test_subscription.py` was "44/44 pass". The JUnit file
says 44 tests, 0 failures, **1 skipped**. It is 43 passed and 1 skipped. Correct that
sentence while repairing the file.

The skipped one is `test_a_plan_nobody_can_buy_shows_no_price[NOTSET]`. Its parameter list
came out empty - `[code for code, offer in PLAN_OFFERS.items() if not offer.monthly_available]`
- because every plan is on sale today. So it silently proves nothing.

Record it in the uncertainty register with what would make it real. Do not delete it and do
not fake a parameter.

## C2-R8 - regression

- `.venv/Scripts/python -m ruff check src tests` - only the 4 pre-existing `test_ask_ai_*`
  findings are allowed. Any new one is yours.
- `.venv/Scripts/python -m mypy src` - clean.
- `.venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly`
- these by path:
  `tests/integration/test_plan_change_journey.py`,
  `tests/integration/test_dashboard_test_subscription.py`,
  `tests/integration/test_checkout_and_payment_email.py`
- your browser file, by path.

## C2-R9 - no test weakened

Answer yes or no, each one: any test deleted? any skip or xfail added? any assertion widened?
any expected value changed? any test-only special case or hardcode?

Removing the two skips in C2-R2 is a repair, not a change of meaning. Say so explicitly.

## Memory rules - hard limits, not advice

Six runs have now been killed on this machine for memory. About 2.2 GB is free.

- **Reuse the 18 screenshots.** One browser run only, one file, headless, closed after.
- **Never** run the whole `tests/browser/` suite.
- One worker at a time. No parallel sub-agents.
- **Write evidence files as you go.** If this run is killed too, the next must continue from
  what you wrote. Do not save evidence for the end.
- If a step is killed twice, record it unverified and move on. Do not retry a third time.
- Leave no OpenCode child process running.

## Visual contract

For C2-R4. It constrains state and behaviour, not a new design. Every value comes from what
is already shipped.

**Source of truth:** `templates/hilal/dashboard/checkout.html`,
`templates/hilal/dashboard/billing.html`, `static/hilalmarkets-dashboard-v2.css`,
`static/hilalmarkets-billing.js`, and `brand guide.md` at the project root.

| # | Rule |
|---|---|
| V1 | Existing classes and tokens only. No new colour, no new component, no raw hex. |
| V2 | S1: the submit control is visibly disabled and reads as disabled. A control that looks pressable and is not is the defect we are here to remove. |
| V3 | S2, S3: the chosen method is visibly marked as chosen. If the only mark is a hidden radio, that is a defect - report it. |
| V4 | S4, S7: the refusal sentence is visible without scrolling, in the `notice notice-error` pattern at `checkout.html` lines 79-89. No empty box, no bare 400, no silence. |
| V5 | At 390x844 nothing overflows sideways and no control is clipped or overlapped. |
| V6 | Every interactive control keeps a visible focus ring - the shipped `--hm-focus-ring` convention only. |
| V7 | Contrast on any text the customer must read to decide meets 4.5:1. Measure with `tests/support/contrast.py`, the repository's single owner for that sum. Never judge by eye. |
| V8 | Nothing outside the checkout dialog, the checkout review page and the billing page changes. |

## Model routing

Read `.hm-orchestrator/policy/ROUTING_POLICY.md` and `.hm-orchestrator/models/LIVE_MODELS.md`.
The live catalog is the authority. Never invent a model or a variant.

| Work | Role | Model |
|---|---|---|
| C2-R1 (cross-file product fix) | strong coder | `opencode-go/kimi-k2.7-code` |
| C2-R2, C2-R3 (tests, browser) | test and debug | `opencode-go/qwen3.7-plus` |
| C2-R6, C2-R7 (text and JSON shape) | cheap fast worker | `opencode-go/qwen3.8-flash` |
| C2-R4 vision review | vision | `opencode-go/deepseek-v4-flash-vision-exp` |
| C2-R5 adversarial review | adversarial | `opencode-go/glm-5.3-flash` |

Do not use a stronger model where a cheaper one is as reliable. If a listed model is not in
the live catalog, pick the nearest that is and say which and why.

## Non-negotiable invariants

- **No completed real charge.** Reaching the payment company's door is proof. Spending money
  is not allowed. If a live charge is the only remaining proof, escalate.
- **Never print a value** from `.env` or `.env.production`. Key names and counts only.
- **Fail closed.** Never offer a plan change or a way of paying the server would refuse.
- **Never weaken a test.** Never skip one to get past a failure.
- Customer-visible sentences must read for a beginner. No "entitlement", "provider",
  "proration", "capability", "checkout session".
  Note: `templates/hilal/base_dashboard.html` lines 98-99 still say "provider-managed
  subscription" and "payment provider" to a customer, and `checkout.html` line 72 says
  "the configured payment provider's secure page". Those are pre-existing. Fix them - they
  are three sentences - and record them as extra problems found.
- Copy rules from `core/copy_rules.py`: **Hilal Markets** in prose, **Shariah** in technical
  usage, forbidden-claims list on every customer-visible word.
- Do not touch the unrelated uncommitted changes in the tree.
- Migration constraint and index names go through `op.f()`.
- ASCII only in any file PowerShell will read. One em dash in a file with no byte-order mark
  has already broken this orchestrator once.

## Completion condition

Complete when C2-R1 through C2-R9 each carry PASS evidence or a named blocker, the vision
reviewer and the adversarial reviewer have both reported, and every finding is closed or
escalated.

Your own `SUPERVISOR_REPORT.json` must pass `validate-report.py`. A run whose report does not
validate is not finished, however good the work was.

If something needs a decision that is the user's - a price, a product rule, a refund policy -
write `ESCALATE_TO_CLAUDE`, say exactly what is needed, and finish everything else.
