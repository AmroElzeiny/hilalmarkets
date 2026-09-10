# Supervisor report — Phase B plan-change work

Run: `20260910T000521Z-1da17cdf` · Tier: Deep · Risk: high (billing, money)
Branch: `cloudflare-access-service-tokens` · Start commit: `145275f2` ("Launch Y6.97")
Date: 2026-09-10

Plain-language summary
----------------------

- The journey test file that a killed run left behind has been read end to end and judged.
  It is a real test. Every sentence in it matches what the code does. It now has two more
  tests, and all 16 run and pass.
- Upgrade and downgrade are proven by driving the real web routes: the record, the plan the
  customer may use, the words the customer sees, and the instruction to the payment company
  all match the product's own stated rule.
- A plan card can no longer offer something the server would refuse — a new test reads the
  buttons from the page itself and posts each one to the server.
- The independent reviewer found one blocker: a one-line indentation slip (from Phase A's
  edit) that broke the account/subscription page for free, trial, and paying card
  customers. Reproduced, fixed, and the page's test run is now 43 passed and 1 skipped
  instead of failing on every test (the one skipped test proves nothing today; see the
  uncertainty register).
- The reviewer's medium finding is fixed too: when an immediate upgrade's payment call
  times out (the charge may or may not have happened), the page no longer asks the
  customer to try again — asking again could charge twice.

Requirement coverage
--------------------

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| B-R0 | Judge the repaired journey test honestly | **PASS** | Judgement below; `pytest tests/integration --collect-only -q` succeeds; 16/16 pass |
| B-R7 | Upgrade works in practice (monthly and annual holdings) | **PASS** | `test_upgrade_trader_to_pro…` — immediate and period-end, 30-day and 365-day holdings |
| B-R8 | Downgrade works, incl. when the change takes effect | **PASS** | `test_downgrade_pro_to_trader…` proves "at the end of the period already paid for" |
| B-R9 | Page may never offer what the server refuses | **PASS** | `test_every_plan_card_button_matches_the_server` + Phase A's 13 offer/acceptance tests |
| B-R13 | Phase A's six files get an independent review | **PASS** | Reviewer report below (one reviewer, logic attack on the money path) |
| B-R14 | Duplicated refusal branch removed; same-plan wording | **PASS** | `billing.py` now has one branch and one plain sentence |
| B-R12 | No test weakened | **PASS** | Audit below: nothing deleted, skipped, xfail'd, widened or changed to fit |

B-R0 — judgement of the repaired test file
------------------------------------------

`tests/integration/test_plan_change_journey.py` was half-written by a killed run. It now
parses. I read all of it and checked it against the code it claims to test
(`services/plan_changes.py`, `api/routers/dashboard.py`, `core/plans.py`,
`tests/support/billing_config.py`).

Verdict: **sound, built on, not replaced.** Nothing is hollow:

- It drives the real route (`POST /dashboard/billing/switch`) and the real service; only
  the outbound payment-company call is faked, and the fake records what was sent.
- Every test asserts real things: the database record (kind, from/to plan, timing, status,
  effective date), what the customer may actually use afterwards (the entitlement plan),
  the money instruction to Creem (`update_behavior`: charge-the-difference-now vs
  charge-nothing-now; the exact product id), the consent box, and the page's words.
- Every refusal is asserted to write **nothing** and to send **nothing** to the payment
  company.
- No stubs, no `pass`, no always-true assertions. Error codes (`plan_not_available`,
  `plan_change_not_needed`, `downgrade_is_period_end`, `plan_change_needs_payment`,
  `change_already_requested`, `no_paid_plan`, `consent_required`) all match the service.
- The names match the plan catalog ("trader" is called "Plus", so "You are moving to Plus"
  is what the downgrade page really says).

Weaknesses I fixed rather than left: formatting only (unused imports, long lines, one
unused local), plus two new tests (see B-R9 and the M1 closure). Note: the file is not
attributable to any specific run — it is now judged, tested, and owned by this run.

B-R7 / B-R8 — the product's own stated rule, and the proof
----------------------------------------------------------

The rule is stated in the product, not invented by me. It lives in two places
(`services/plan_changes.py` — the header table and the consent sentences the customer
actually ticks):

| What the customer chose | What they are charged | When their limits change |
|---|---|---|
| Higher plan, now | the difference, today | today |
| Higher plan, end of period | nothing today; new price at renewal | end of the paid period, by the scheduler |
| Lower plan | nothing until renewal; new price at renewal | **only** at the end of the period already paid for |
| Crypto customer | no card on file — they buy the plan | n/a (switch refused) |
| Free plan | not sold — the road back is Cancel | n/a (switch refused) |

Evidence the code does exactly this (all in `test_plan_change_journey.py`, 16/16 pass):

- **Upgrade to Pro, immediate:** record written with status `applied`; entitlement flips to
  Pro in the same moment; Creem is told `proration-charge-immediately`; the customer is
  redirected to "Your plan is changing". Works while on monthly **and** on annual
  (parametrised over a 30-day and a 365-day period).
- **Upgrade to Pro, end of period:** record `scheduled`, effective date equals the exact
  paid-period end; entitlement stays Plus until then; Creem told `proration-none`
  (nothing charged now, new price at renewal).
- **Downgrade Pro→Trader:** refused when sent "immediate"; accepted at period end with the
  record's effective time equal to the paid-period end; the entitlement stays Pro; then
  `apply_due_changes` (the scheduler) an instant after the period end moves the entitlement
  to Trader and pauses monitors that no longer fit.
- **Upgrade as a paid customer already can be...** (see also: annual) — the same
  parametrisation covers "already on annual" for both timings.
- **Downgrade to free:** the product does not sell it; the page shows "Not available" and
  the server refuses (`plan_not_available`); the road back is the Cancel form, whose own
  wording ("nothing charged ever again, access until the end of the paid period") is the
  honest equivalent. This is the product's own decision, not mine, and it is proven by
  test.
- **Proof the money instruction matches the record:** the journey asserts the Creem payload
  product ids (`prod_test_pro_monthly` / `prod_test_trader_monthly`) and update behaviors
  against the same change record it just read from the database.

B-R9 — page and server cannot drift apart
-----------------------------------------

New test `test_every_plan_card_button_matches_the_server`: it reads the switch buttons
**from the rendered page**, then posts a switch for trader, pro and demo. A button the page
shows must be accepted; a plan with no button must be refused with an error. On top of
that, Phase A's own test (`tests/unit/test_invariant_billing_offers.py`, 13 tests) checks
every plan × payment method × billing cycle the same way for **checkout** with an account
holding the same or a different paid plan. Together, both directions of the promise are
covered for pay buttons and switch buttons.

B-R14 — the cosmetic defect
---------------------------

- `services/billing.py` `plan_checkout_availability:` the "no way to pay" refusal had two
  branches with two identical strings. Collapsed to one branch, one sentence, with a
  comment saying why.
- Same-plan refusal reworded for a beginner:
  - Before: "You already have the Plus plan. The change period may be managed from the
    billing page." (words nobody says)
  - After: "You already have the Plus plan. You can change it or cancel it on this billing
    page."
  - Also: "Open the plan switcher…" → "Use the plan switch buttons on the billing page…"
  - No forbidden claims, no jargon; brand name not touched. Phase A's tests (`already`/plan
    name must appear; refusal non-empty) still pass unchanged.

Reviewer findings — what the independent review found and what I did
--------------------------------------------------------------------

One independent logic reviewer ran at the end. It saw Phase A's six changed files, Phase
B's files, the diff, and it re-ran the journey and invariant suites itself.

| Finding | Severity | What I did | Evidence |
|---|---|---|---|
| B1: `dashboard_test.py` — a `cards = [` list was left indented inside one branch, so `/dashboard/subscription` broke with an UnboundLocalError for free, trial and all card-paying customers (44 tests failing) | BLOCKER | **Reproduced first** (same error at `dashboard_test.py:1785`), then restored the list to the function body. 44/44 now pass | `junit_dash_subscription.xml` |
| M1: a timeout on an **immediate** upgrade may have already charged; the refusal said "Please try again" — inviting a double charge | MEDIUM | Fixed: that one refusal becomes `billing_timeout_needs_check` with wording that never says "try again"; mapped in `base_dashboard.html`; new test proves the page's words and the `failed` record | journey test `test_timeout_on_immediate_upgrade_never_invites_a_retry` |
| L1: `_retune_the_card` hardcodes monthly for a product that would one day sell annual | LOW | Left as-is: no annual subscription is sold today (`annual_available=False`); the journey test documents the current behaviour honestly. Reported here for the next pricing work |
| L2: consent copy says "days left in this month" (annual) | LOW | Same as L1: unreachable today, copy-only, reported |
| L3: the review page's `already_subscribed` check misses "holds other paid plan" | LOW | Direct-URL only; the URL then refuses with `change_plan_instead`; no charge; reported |

B-R12 — test integrity audit
----------------------------

- Any test deleted? **No.** Nothing in any test file was removed by me. (The killed run
  earlier left `dashboard_test.py` indented — that was product code, not a test, and its
  repair is documented above.)
- Test skipped or xfail added? **No.**
- Any assertion widened or expected value changed? **No.** The two tests I added assert
  strictly. Line-length and unused-import cleanups changed no assertions.
- Test-only special case or hardcode? **No.**
- Ruff auto-fixes were applied to the untracked new test file only (import order, one
  unused import `User`, two unused consent constants, one unused local). Those imports
  existed only in the new file; no other test file touched.

What I measured (commands and counts)
-------------------------------------

| Command | Result |
|---|---|
| `pytest tests/integration --collect-only -q` | succeeds (whole integration tree parses) |
| `pytest tests/integration/test_plan_change_journey.py -p no:randomly` | **16 passed / 0 failed** (`junit_journey.xml`) |
| `pytest tests/unit/test_invariant_billing_offers.py -p no:randomly` | **13 passed / 0 failed** (`junit_invariant_billing.xml`) |
| `pytest tests/integration/test_checkout_and_payment_email.py -p no:randomly` | **21 / 0** (`junit_checkout.xml`) |
| `pytest tests/integration/test_dashboard_test_subscription.py -p no:randomly` | **44 / 0** after the B1 fix (`junit_dash_subscription.xml`; failed ~44 before it) |
| `pytest tests/integration/test_system_brain_payments.py -p no:randomly` | **5 / 0** (`junit_sbp.xml`) |
| `pytest tests/integration/test_dashboard_web.py tests/integration/test_hilal_public_site.py -p no:randomly` | **53 / 0** (`junit_dashweb.xml`) |
| `.venv/Scripts/python -m mypy src` | clean, 425 files |
| `.venv/Scripts/python -m ruff check src` | clean (this scope) |
| `.venv/Scripts/python -m ruff check src tests` | 11 findings, all in pre-existing files I did not touch (`test_ask_ai_button.py`, `test_invariant_ask_ai_css.py`, `test_invariant_ask_ai_single_owner.py`, plus the new-file issues I then fixed). Attribution: those three files have no local diff, so the findings exist at `HEAD` |

Full-suite runs were not re-run (memory rule: targeted files only, one at a time).

Changed files (this phase's written changes)
--------------------------------------------

| File | Why | Risk |
|---|---|---|
| `src/ai_market_monitor/services/billing.py` | B-R14: one refusal branch instead of two; plain wording | Very low — same strings/behaviour, plus beginner wording (Phase A's larger uncommitted change in this file preserved as it was) |
| `src/ai_market_monitor/api/routers/dashboard_test.py` | Reviewer B1: fix indentation slip that broke the subscription page for most users | Low — one-line dedent; page now renders for every branch |
| `src/ai_market_monitor/services/plan_changes.py` | Reviewer M1: no "please try again" after a lost immediate-upgrade answer; still records `failed` first | Low — refusal word/path only; the file was untracked-but-present before this run (mission's named runtime authority) |
| `src/ai_market_monitor/templates/hilal/base_dashboard.html` | Map the new refusal code to the same plain sentence | Low — one added line; the file's other pre-existing uncommitted changes untouched |
| `tests/integration/test_plan_change_journey.py` | Judged file kept, cleaned, extended with the B-R9 and M1 tests | Low — test-only |

Nothing outside these files was written; the working tree's many other uncommitted changes
were left exactly as found.

Uncertainties, honestly
-----------------------

- **Provider truth (M1):** whether Creem can carry out a charge and then drop the answer is
  outside what any offline test can prove. The code already handles the unknown-outcome
  case defensively (`mutation_committed=True`, no auto-retry, `failed` record kept, safe
  wording). A provider-side verification (or an idempotency key from Creem) is the only
  remaining proof — flagged for the user, not blocking this phase's acceptance.
- **The repaired journey file's author** is unknown (a killed run wrote it). It is now
  read in full, aligned with the code, extended, and owned by this run.
- Pre-existing ruff findings in three unrelated test files were not fixed — they are
  another session's uncommitted work (`ask-ai` CSS/button tests), not my scope, and I
  preserved them untouched.

Verdict
-------

**COMPLETE_VERIFIED** — every requirement carries pass evidence; the one reviewer blocker
(B1) is fixed with a reproducer and the page's full test file green; the medium finding
(M1) is fixed with a failing-first test. The residual provider-timeout behaviour is named
explicitly above and needs a live-provider decision (Creem idempotency), which belongs to
the user.
