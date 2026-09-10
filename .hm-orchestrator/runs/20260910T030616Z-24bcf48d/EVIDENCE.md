# Phase C2 evidence log (running file - updated as work completes)

Run: 20260910T030616Z-24bcf48d
Baseline commit: 145275f2206dfbd8b9b0ab2ab5156ac0fe9aa269 (copied 18 screenshots from
20260910T010643Z-c3d54570 - complete, in screens/)

## C2-R1 investigation (complete before any edit)

Call sites of "may this account buy this plan" (`active_paid_plan_codes` /
`plan_checkout_availability`), all checked:

| Site | Reads | Verdict |
|---|---|---|
| `services/billing.py:557 plan_checkout_availability` | owns the rule incl. `holds_other` | one owner, correct |
| `services/billing.py:1441 prepare_checkout` | server-side POST refusal (`change_plan_instead`) | correct (fail-closed enforcement) |
| `dashboard.py:3346 billing_page` | `plan_checkout_availability` | correct |
| `dashboard_test.py:1702 subscription_page` + `_plan_card:1610` | `plan_checkout_availability` (`buyable`) | correct |
| `dashboard_test.py:1778-1786 open_for_plan` | card `buyable` only | correct |
| `dashboard.py:3682 resume_billing_checkout` | resumes own attempt only | not a purchase decision |
| `dashboard.py:3905 billing_checkout_review` | `plan.code in review_active_paid_plan_codes` | **WRONG - the defect. Only catches same plan.** |
| `dashboard.py:3963 billing_checkout POST` | redirects non-public; refuses via `prepare_checkout` | correct |
| `api/routers/billing.py` | `/checkout` delegates to BillingService; no plan-availability logic | correct |

Search terms used across `src/`: `plan_checkout_availability`, `active_paid_plan_codes`,
`holds_other`, `already_subscribed`, `purchasable`. No other render-time site decides
"may this account buy this plan" by its own rule. Defect is exactly one site.

Template flow (`templates/hilal/dashboard/checkout.html`): `already_subscribed` false for
Trader-holder-opens-Pro -> `{% elif not billing_method_offers.values()|selectattr('available') %}`
false (methods are about the company, not the account) -> falls to the full form at line 91
onward. Confirmed.

## C2-R2 investigation (complete before any edit)

`tests/browser/test_checkout_pay_button_e2e.py`, 14 tests. Confirmed defects:

1. `test_the_checkout_page_offers_no_disabled_path_to_a_trader_holder` ~line 391:
   `pytest.skip("Unexpected 'already subscribed' notice...")` - skip on unexpected state.
2. ~line 400: `pytest.skip("Checkout page shows the form for a trader holder...")` -
   **skips itself when it finds the defect (the form is the bug).**
3. Also weak: line ~408 trailing `if error_notice.count()` - the without-scroll check
   silently vanishes in the skip paths.
4. `test_a_trader_holder_sees_no_way_to_pay_for_pro_in_the_popup` line ~331:
   `if note.count():` - the "note explains why" assertion quietly vanishes.
   Fix: unconditional assertion (a disabled button without an explanation is a defect).

## C2-R6 state

`SUPERVISOR_REPORT_CONTRACT.md` in the working tree already names the 14 required
top-level keys, their types, and says the schema file is the authority. Keys verified
identical to `supervisor-report.schema.json` `required` array. Item 1 of C2-R6 was
already done before this run (probably by the killed run's Claude session). Remaining:
repair the Phase B report's shape and validate both files.

## C2-R7 state

`tests/integration/test_dashboard_test_subscription.py:168`: param list
`[code for code, offer in PLAN_OFFERS.items() if not offer.monthly_available]`
- every plan is on sale today -> empty -> the test proves nothing. Record in
  uncertainty register; do not delete, do not fake a parameter.

## Copy fixes remaining

- `base_dashboard.html` lines 98-99: already fixed in the working tree (verified - the
  two lines are now beginner wording).
- `checkout.html:35` "Shown by the payment provider where applicable" -> "payment company".
- `checkout.html:72` "the configured payment provider's secure page" -> beginner wording.

## Plan of browser runs (memory limits: ~2.2 GB free)

1. RED: run only `test_the_checkout_page_offers_no_disabled_path_to_a_trader_holder`
   before the fix, capture the failure.
2. Fix.
3. GREEN: same single test, captured.
4. Screenshots in one run: `-k "screenshot_s4_trader_refusal or screenshot_s7_pro_refusal"`
   -> post-fix S4 (3 viewports) + S7 (3 viewports). S1-S3, S5, S6 reused from the copy.
5. Browser file regression by path with `-k "not screenshot"` (no fresh screenshot DOM
   repetition) - serves C2-R8.
