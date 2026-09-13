# Fix worker report — run 20260912T193031Z-4125cfbe (branch cloudflare-access-service-tokens)

## FIX 1 (BLOCKER) — dedupe key rejected provider-shaped event ids: FIXED
- Owner: `src/ai_market_monitor/observability/issues.py` adds public `sanitize_dedupe_key(key) -> str`
  (casefold → forbidden chars to `-` → leading `_.:/-` stripped so first char is `[a-z0-9]` →
  truncate to 160 → final pattern check). Raises `IssueQueueError` only when nothing safe remains:
  empty, whitespace anywhere (prose/timestamp keys — the volatile keys the queue has always refused,
  keeping `test_a_volatile_dedupe_key_is_refused` intact), or no alphanumeric left.
- `record_occurrence` normalises once at entry and uses `safe_key` for the SELECT, the insert and
  `_validate_payload`. A key that already fits is returned unchanged (byte-identical) — so the
  existing NOWPayments-colon test at line 285 still passes and stored/lookup keys agree.
- `billing.py`: `_plan_move_failure_key` and `_save_resurrection_alert` now build their keys through
  the owner. `_close_replacement_failure` calls `_plan_move_failure_key`, so the read matches.
- Reproducers (failed before, pass after):
  - `tests/unit/test_operational_issue_queue.py::test_a_provider_event_id_shape_never_loses_the_alert`
    (parametrised × 4: NOWPayments colons, Stripe mixed case `evt_01JABCdefGHI`, slash, id ≥160) —
    before: collection ImportError (owner missing) + direct repro printed
    `IssueQueueError: Issue dedupe key 'billing:plan-move-failed:evt_01JABCdefGHI' is not a stable
    low-cardinality key.` (fix1_failure_before.txt, fix1_repro_before.txt).
  - `tests/unit/test_operational_issue_queue.py::test_sanitize_dedupe_key_always_lands_inside_the_pattern`
    (× 10 shapes, incl. idempotency) and `..._refuses_nothing_safe_remains` (× 7).
  - `tests/integration/test_paid_plan_replacement.py::test_a_mixed_case_event_id_failed_move_still_leaves_alert_and_failed_event`
    — before: `IssueQueueError` killed the alert and the failed BillingEvent before the commit.
    After: failed BillingEvent + open critical issue stored under
    `billing:plan-move-failed:evt_01jabcdefghi`.

## FIX 2 (SERIOUS) — late non-payment events resurrected ended subscriptions: FIXED
- Owner: `billing.py:_upsert_subscription` — the `event_type in self._PAID_EVENT_TYPES` condition was
  removed; ANY event (paid or not) that would move a CANCELED/EXPIRED row to ACTIVE/TRIALING is
  refused with `subscription_resurrected_after_cancel`. The `_PAID_EVENT_TYPES` set itself was
  deleted (its only use was this guard; nothing else in src/tests/scripts referenced it).
- Alert accuracy: `_save_resurrection_alert` summary no longer claims a charge happened. New text
  (measured at exactly 197 chars, inside the queue's 200-char limit — verified by AST-reading the
  literal out of the source): "An event from the payment company tried to make a subscription our
  system shows as ended live again. The subscription was left ended. A person must check whether
  the payment company charged for it."
- Reproducer: `tests/integration/test_paid_plan_replacement.py::test_a_late_event_of_any_type_cannot_resurrect_an_ended_subscription`
  parametrised over subscription.paid, subscription.updated, subscription.update,
  customer.subscription.updated, subscription.scheduled_cancel, subscription.trialing.
  Before: the 5 non-paid params FAILED (no guard, row would go live); subscription.paid passed —
  exactly the half the old guard covered (fix2_failure_before.txt). After: all 6 pass — row stays
  CANCELED, event is a committed failed BillingEvent, committed open critical issue, one live sub.

## FIX 3 (MINOR copy) — "in full" removed where a code can lower the charge: FIXED
- Grep evidence: `in full today` appeared in exactly 2 source strings and 3 test assertions.
- `plan_replacements.CONSENT_PLAN_REPLACEMENT`:
  old: "I understand I pay the new plan price in full today. ..." →
  new: "I understand I pay the new plan price today. ..."
  (The suggested "plan's" apostrophe was rejected on evidence: the integration invariant asserts the
  constant appears in the page byte-for-byte, and the page HTML-escapes `'` to `&#39;`; the first
  attempt failed `test_every_checkout_tick_box_says_what_happens_to_the_plan_held`. No apostrophe
  keeps the owner's rule and all surfaces identical.)
- `templates/hilal/dashboard/billing.html`: "You pay the {{ plan.name }} price in full today." →
  "You pay the {{ plan.name }} price today."
- Tests updated for the new wording (assertion text only, not loosened):
  `tests/integration/test_plan_change_journey.py` (2 asserts: "price in full today" → "price today"),
  `tests/browser/test_checkout_pay_button_e2e.py` (1 assert, same edit). All other consent tests read
  the constant and self-update. The owner's rule (pay new plan today; old plan ends only after
  confirmed payment; unused value returned within 48 hours) is unchanged; no Sharia/return/guarantee
  claim added.

## FIX 4 (MINOR money) — full-price renewal after discounted first period refused as overpaid: FIXED
- `billing.py:_hydrate_checkout_data`: when `attempt.status == "completed"` and the plan has not
  changed, the plan's current effective price is passed as `also_expected` to
  `_validate_paid_amount_and_currency`. The validator now accepts any of: stored figure, plan
  effective price, and (only when the provider reports its own discount) that provider-reported
  discount taken off either base. An amount above every candidate is still `payment_overpaid`
  (tolerance per candidate unchanged); below every candidate still `payment_underpaid`.
  On success `attempt.amount` is set to the figure really paid (existing assignment kept).
- Reproducer: `tests/integration/test_checkout_and_payment_email.py::test_a_full_price_renewal_after_a_discounted_first_period_lands`
  — before: `BillingError: payment_overpaid` at billing.py:3106 (fix4_failure_before.txt).
  After: full-price renewal processed and `attempt.amount == full`; discounted-again renewal
  (provider_discount_amount reported) processed and amount follows; `full + 5.00` still refused.

## DO NOT FIX (documented)
The `float(amount)` at billing.py:1308 is a JSON-number serialisation of a 2-decimal Decimal with no
binary arithmetic on it, and every path that reads it back re-parses with `Decimal(str(...))`, so it
does not lose money precision; left unchanged as instructed.

## Commands and results
- `.venv\Scripts\python -m pytest tests/unit/test_operational_issue_queue.py -q -p no:randomly` → 44 passed (23 before, +21 new)
- `... tests/integration/test_paid_plan_replacement.py ...` → 24 passed (17 before, +7 new)
- `... tests/integration/test_plan_change_journey.py ...` → 28 passed
- `... tests/integration/test_checkout_and_payment_email.py ...` → 64 passed (63 before, +1 new)
- `... tests/unit/test_invariant_plan_replacement_money.py tests/unit/test_invariant_plan_checkout_availability.py ...` → 90 passed
- `... tests/unit/test_billing_entitlements.py ...` → 41 passed (adjacent: amount validator, webhook upserts)
- `... tests/integration/test_held_plan_renewal_words.py tests/unit/test_invariant_discount_codes.py tests/unit/test_invariant_payment_methods.py ...` → all passed (adjacent: renewal/discount/amount gates)
- `... tests/integration/test_admin_status_api.py tests/integration/test_phase5_observability_and_stage.py ...` → 37 passed (reprocess + issue queue surfaces)
- `... tests/unit/test_invariant_billing_next_step.py tests/unit/test_invariant_billing_offers.py tests/unit/test_invariant_payment_company_naming.py tests/unit/test_invariant_resume_checkout_cycle.py ...` → 194 passed
- `... tests/integration/test_dashboard_test_subscription.py tests/integration/test_affiliate_journey.py tests/integration/test_launch_offer_pricing.py ...` → 73 passed
- `.venv\Scripts\python -m ruff check src tests scripts` → All checks passed! (ruff_after_fixes.txt)
- `.venv\Scripts\python -m mypy src` → Success: no issues found in 426 source files (mypy_after_fixes.txt)

## Files changed by this worker
- src/ai_market_monitor/observability/issues.py
- src/ai_market_monitor/services/billing.py
- src/ai_market_monitor/services/plan_replacements.py
- src/ai_market_monitor/templates/hilal/dashboard/billing.html
- tests/unit/test_operational_issue_queue.py
- tests/integration/test_paid_plan_replacement.py
- tests/integration/test_checkout_and_payment_email.py
- tests/integration/test_plan_change_journey.py (wording assertions only)
- tests/browser/test_checkout_pay_button_e2e.py (wording assertion only)

## Uncertainties left
- Browser e2e files were not executed (Playwright harness is a person-started surface here); their
  wording edits mirror the integration asserts that do run and pass.
- `sanitize_dedupe_key` truncation at 160 can in theory merge two provider ids that share their
  first 160 sanitised characters into one issue row; accepted per the assignment's truncate rule.
- The `_upsert_subscription` `event_type` parameter is now unused by the guard; kept because six
  call sites pass it and removing it is churn outside this defect class.
