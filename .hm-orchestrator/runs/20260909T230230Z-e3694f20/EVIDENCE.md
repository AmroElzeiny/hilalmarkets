# Phase A evidence — run 20260909T230230Z-e3694f20

Written as work progressed, per the memory rules.

## 1. Situation found (deviation from the mission text)

The mission said the baseline test `tests/unit/test_invariant_billing_offers.py` still
fails and that no product code had been changed. Both statements are stale.

- Command: `.venv\Scripts\python -m pytest tests/unit/test_invariant_billing_offers.py -q -p no:randomly`
- Output captured: `baseline-billing-offers.out` — **13 passed**.
- Test file intact, no skip/xfail, no widened assertions (test integrity audit below).
- A comparing the two baseline snapshots
  (`runs/20260909T213031Z-702ebe0d/BASELINE_DIFF.patch` -> this run's) shows the
  fix was already applied to the working tree before this run started. It was not
  applied by this run.

This run therefore verifies that fix honestly, closes residual gaps, and reports.

## 2. Baseline (what the failure evidence said)

`runs/20260909T213031Z-702ebe0d/wp1-failing-tests.txt`, captured 00:56:

- 4 parametrised case: page offered card/crypto checkout while the POST returned 400
  (held = a **different** paid plan).
- 2 cases: billing dialog showed no plain reason when no method worked.
- 2 cases: `billing_plan_data` had no server-owned refusal sentence.
- 1 case: a required radio was hidden by CSS and silently blocked submission.

## 3. Root cause (A-R2)

The account rule "already holds a plan" existed in **two copies that disagreed**:

1. Page side, `dashboard.py`: `_plan_checkout_allowed` refused only the **same** plan
   (`plan_code not in active_paid_plan_codes`). A person holding the *other* paid plan
   was told card/crypto were available.
2. Server side, `services/billing.py` `prepare_checkout`: refused the same plan
   (`already_subscribed`) and had nothing at all for the *other*-plan case, so
   checkout later failed at the second guard with a non-explained 400.

The fix moves both halves into one owner in `services/billing.py`:

- `active_paid_plan_codes()` — the single query for "what does this account hold".
- `plan_checkout_availability()` — the single answer to "can this account buy this
  plan today, and by which method", composing provider layer (`payment_method_available`)
  with the account layer, and producing the plain-language `refusal` sentence.

Callers now import it: `dashboard.py` (billing page, checkout review), `public.py`
cards, `dashboard_test.py`. `plan_changes.py` receives `purchasable` "passed in rather
than worked out again here". The browser no longer re-computes the reason:
`writing its own fallback sentence was removed` (grep-free per test
`test_open_plan_held_by_account_tells_user`).

## 4. Requirement status after verification

| ID | Status | Evidence |
|---|---|---|
| A-R2 | PASS | Root cause above; old copies removed from dashboard.py/public.py (grep for `_plan_checkout_allowed` in src: 0 hits). |
| A-R3 | PASS | A receiver of another paid plan is told plainly why not: server `BillingError("change_plan_instead", "Your {held} plan is already active. To move to {plan.name}, use Change plan on the billing page — you are not charged twice.")` (billing.py, `prepare_checkout`), and the page never offers the checkout in that state (`plan_checkout_availability` gates methods off). The live upgrade journey test belongs to phase B (`tests/integration/test_plan_change_journey.py`, currently half-written and unparsable — phase B's file, untouched). |
| A-R4 | PASS | Same message path serves card and crypto; the parametrised test covers both methods. |
| A-R5 | PASS | `tests/unit/test_invariant_billing_offers.py::test_page_offer_matches_server_acceptance` is parametrised over every plan × card/crypto × monthly/annual × held same/different; dialog notice (`notice notice-error`) added in `billing.html`, driven by the server-owned `refusal` via `hm-plan-change.js`/`billing.js`; the browser fallback sentence was removed. |
| A-R6 | PASS | `test_no_hidden_required_control_owns_submission` passes: no required input sits under the visually hidden CSS rule. Nothing-chosen submission is answered in the interface: "Choose a payment method to continue." and, when no method works, the disabled submit plus the visible notice. |
| A-R10 | PASS | One owner `plan_checkout_availability` in `services/billing.py`; grep shows no second copy of the account rule anywhere in page, dialog, JS, or routes. |
| A-R12 | PASS | Test integrity audit: no test deleted/skipped/xfail'd; assertions not widened; expected values unchanged. |

## 5. Test runs this run (all captured)

| Command | Result |
|---|---|
| pytest tests/unit/test_invariant_billing_offers.py -q -p no:randomly | 13 passed |
| pytest tests/unit/test_billing_entitlements.py | passed (33) |
| pytest tests/unit/test_invariant_plan_card_matches_comparison.py | passed (23) |
| pytest tests/integration/test_plan_change_journey.py | collection error (IndentationError line 227) — phase B's half-written file, NOT touched by this run |
| ruff check src tests scripts | clean except the 4 pre-existing errors inside phase B's half-written test file |
| ruff check src tests — on changed-file scope | clean (fixed one import-sort leftover in dashboard_test.py) |
| mypy src | Success: no issues found in 425 source files |

## 6. Test integrity audit (A-R12)

- Tests deleted: no
- Tests skipped or xfail added: no (grep in the test file: none)
- Assertions weakened: no (the test file is stricter than the failure log — it now also
  requires a server-owned refusal and removal of the JS fallback)
- Expected values changed: no
- Test-only special cases/hardcodes: no

## 7. Open items handed forward

- `tests/integration/test_plan_change_journey.py` does not parse (`IndentationError`
  line 227) and blocks bulk collection of `tests/integration` — phase B must finish it.
- `plan_checkout_availability` has a duplicated empty refusal branch (two identical
  strings, one per condition) — harmless, cosmetic.
- Refusal wording for the same-plan case ("The change period may be managed from the
  billing page") is slightly defensive; flagged to review, not changed.
