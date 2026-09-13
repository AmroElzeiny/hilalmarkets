# Evidence — FIX A (page/server held-plan agreement) + FIX B (owner properties for the whole family)

Run: 20260912T193031Z-4125cfbe · Branch: cloudflare-access-service-tokens · Baseline: 75b19580

## Files changed (by this worker only)

| File | What changed |
|---|---|
| `src/ai_market_monitor/api/routers/dashboard.py` | 3 page surfaces (billing page, resume route, checkout review page) now load `paid_plan_codes_for_replacement_decisions` — the same grace-aware held set `prepare_checkout` reads — instead of `active_paid_plan_codes`. Import swapped; no other use of the old name remained in this router. |
| `src/ai_market_monitor/api/routers/dashboard_test.py` | Subscription page: same swap at its one call site (was line 1711→1733). Import swapped. |
| `tests/integration/test_plan_change_journey.py` | Two new reproducer tests + helpers (`_let_the_card_period_lapse`, `_billing_page_availability`, `_post_checkout`), 4 parametrised cases. |
| `tests/integration/test_paid_plan_replacement.py` | FIX B: `_seed_old_payment` now takes provider + billing cycle; 3 property tests parametrised across held shapes; one rename and one provider-conditional assertion (details below). |
| `tests/unit/test_invariant_resume_checkout_cycle.py` | The monkeypatch target followed the moved call: `dashboard_module.active_paid_plan_codes` → `dashboard_module.paid_plan_codes_for_replacement_decisions` (3 places) + the stub comment. Same fake (returns an empty held set), same assertions — patching the removed name would have raised `AttributeError`. |

## FIX A — status: DONE (no escalation needed)

Root-cause class: two sets answering "which paid plans does this account hold" — the
checkout route (`services/billing.py:1741-1745`) was grace-aware, the pages were not.
In the window where a recurring card's period has ended (status ACTIVE, end within the
last 30 days, `cancel_at_period_end` false) the pages said "not held" while the route
said "held": the pages sold what the route refused with `already_subscribed`, and hid
the `paid_amount_missing` refusal for the other plan (`holds_other` was false).

All four required call sites now read the route's set. Access semantics untouched:
`active_paid_plan_codes` remains the owner for access/entitlement (no other consumer of
it remained in those routers). No change outside these routers was required.

### Reproducer — command and before/after

```
.venv\Scripts\python -m pytest "tests/integration/test_plan_change_journey.py::test_a_lapsed_recurring_card_plan_is_held_by_every_page_exactly_like_the_route" "tests/integration/test_plan_change_journey.py::test_a_lapsed_plan_with_no_payment_record_refuses_replacement_on_every_surface" -q -p no:randomly
```

* BEFORE (the four FIX-A call sites temporarily reverted to the no-grace set, then the
  routers restored byte-for-byte — hash-checked): 4 failed / 0 passed.
  `repro_prefix_output.txt` — failures are the behavior assertions, e.g.
  `assert held_availability["holds_this"] is True` → "the page says this account does
  not hold trader, whose card is still scheduled to charge", and
  `holds_other False` with a clean purchasable answer for the other plan.
* AFTER: 4 passed (`repro_postfix_output.txt`).

Test 1 (lapsed card WITH a completed payment record), per held plan:
billing-page availability JSON says `holds_this=True, purchasable=False` with the
held-plan sentence, review page draws no `checkout-confirm-form`, subscription page
draws no `data-s-choose` for the held plan — and pressing checkout meets the route's
`400 already_subscribed`; the OTHER plan stays purchasable on all three surfaces, the
route accepts it, and the created attempt is frozen onto the lapsed subscription.

Test 2 (lapsed card WITHOUT a payment record), per held plan: every page now carries the
route's own `paid_amount_missing` sentence for the other plan (no confirm form, no
choose button), and the route answers `400 paid_amount_missing`; the held plan is refused
`already_subscribed` on both page and route.

Scope note (boundary, not an escalation): the billing page's Pay-button *presence* is
still decided by `PlanChangeService.paid_subscription` (`plan_changes.py:239-268`, no
grace) — so the button element is rendered; its live/disabled state and the popup come
from the availability payload, which now matches the route (`hilalmarkets-billing.js:148`
disables the button, `:272` shows the refusal). The requirement — pages answer the buy
question with the route's set — is met inside the routers. A follow-up could move the
button-presence question to the same owner; it needs no change now and was not in scope.

## FIX B — status: DONE (all simulated cases pass; gaps stated honestly)

Owner rule `OWNER_DECISION_WP8.md:59-64`, both properties, for every ordered plan pair
(trader→pro, pro→trader — the purchasable plans are exactly `("trader","pro")`,
`core/plans.py:11`) crossed with the held-plan shapes:

| Held-plan shape | Property 1 `test_confirmed_move_leaves_one_charge_and_one_money_record` | Property 2a `test_abandoned_checkout_leaves_the_old_plan_untouched` | Property 2b `test_failed_payment_leaves_the_old_plan_untouched` |
|---|---|---|---|
| card-monthly (`creem`, `monthly_auto_renewal`, 30-day period) | yes | yes | yes |
| card-annual (`creem`, `annual_auto_renewal`, 365-day period, paid at the annual price) | yes | yes | yes |
| crypto (`nowpayments`, `one_time_30_day`) | yes | yes | yes |

6 cases per property × 3 properties = 18 parametrised runs; the file went from 24 to
36 tests. Property 1 still asserts exactly one live subscription, exactly one live
recurring card charge, one money record (`0 < owed <= paid_amount`, annual prices
included), one customer email and one staff notice, plus the Creem replay guard.

### What is NOT simulated, and why (not faked)

1. An **annual NEW checkout** (the plan being bought on a yearly cycle) cannot be driven
   end-to-end: `PLAN_OFFERS` has `annual_available=False` for both purchasable plans
   (`core/plans.py:190-204`) and `_normalize_billing_cycle` raises
   "Annual billing is not available yet." (`billing.py:1918-1922`). Opening it for a
   test would mean editing the product's live offer. The closest reachable case is
   covered instead: an annual paid period being *replaced* (card-annual held shape) —
   which is what the money rule actually reads (period dates + the amount really paid).
2. An **annual CRYPTO held plan** is not a shape the product can reach: NOWPayments
   takes one 30-day invoice per buy (`billing_cycle_code` = `one_time_30_day`; the cycle
   aliases reject "annual" for a provider without recurring capability), so there is no
   annual crypto period to hold. Left out rather than faked.

### Existing expectations touched (named, with justification)

* Rename: `test_confirmed_card_move_leaves_one_charge_and_one_money_record` →
  `test_confirmed_move_leaves_one_charge_and_one_money_record`. The name said "card"
  while the test now proves the property for crypto and annual held plans too.
  All assertions kept.
* Old `assert len(cancel_calls) == 1` + `retry is True` + `mutation_committed is False`
  (for every shape): kept unchanged for a held CARD plan; for a held CRYPTO plan the
  test now asserts `cancel_calls == []`. Justification: a crypto plan has no recurring
  charge to cancel (`_cancel_old_recurring_plan` returns without a call for a
  non-card provider, `plan_replacements.py:417-419`), so asking the payment company to
  cancel it would itself be the "two charges" defect in a different costume. Neither
  branch loosens the other.
* No other expected value changed.

## Commands run and results (single process each, `-p no:randomly`, no `--timeout`)

| Suite | Result |
|---|---|
| reproducers before the fix | 4 failed (behavior assertions) |
| reproducers after the fix | 4 passed |
| `tests/integration/test_plan_change_journey.py` | 32 collected, exit 0 (incl. `test_every_plan_card_button_matches_the_server`, `test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page`) |
| `tests/integration/test_paid_plan_replacement.py` | 36 collected, exit 0 |
| `tests/unit/test_invariant_resume_checkout_cycle.py` | 4 collected, exit 0 |
| `tests/unit/test_invariant_plan_checkout_availability.py` | 81 collected, exit 0 (incl. the no-copy scan — new variable names are not membership copies) |
| `tests/unit/test_invariant_plan_replacement_money.py` | 9, exit 0 |
| `tests/unit/test_operational_issue_queue.py` | 44, exit 0 |
| `tests/unit/test_invariant_annual_only_plan_card.py` | 5, exit 0 |
| adjacent: `test_held_plan_renewal_words.py` 96 / `test_checkout_and_payment_email.py` 64 / `test_invariant_billing_offers.py` 16 / pages+templates+static 114 | all exit 0 |
| whole `tests/integration` | 2232 dots, 0 failures, exit 0 |
| offline suites (`tests/unit tests/engine tests/interpreter tests/services`) | 27663 dots, 0 failures, exit 0 |
| `ruff check .` | 4 findings — all pre-existing in `tools/hm-orchestrator/validate-report.py`, committed at baseline 75b19580, a do-not-edit file; `ruff check src tests` → All checks passed |
| `mypy src` (types-all command) | Success: no issues found in 426 source files |

Saved outputs in this directory: `repro_prefix_output.txt`, `repro_postfix_output.txt`,
`fixb_paid_plan_replacement.txt`, `final_*.txt`, `final_results.txt`, `ruff_*.txt`,
`mypy_src.txt`, `adjacent_*.txt`, `adj_*.txt`, `unit_*.txt`.

## Uncertainties / notes for the supervisor

1. `services/billing.py:2002-2010` (`open_checkout_attempt`) still answers from the
   no-grace set. It is a second server-side gate, not a page: every reachable flow that
   hits it first passes `prepare_checkout`, which refuses with the grace set, and the
   resume route (the "time passed in between" path) now uses the grace set. So it
   cannot re-open the page/server disagreement. Not changed (outside the assigned four
   sites); worth a deliberate decision in the next pass.
2. In the no-JS case the billing page still renders the held plan's button element
   (presence is `paid_subscription`'s answer); it is disabled and refusal-worded from
   the fixed availability payload. Flagged above as a possible follow-up convergence.
3. The pre-fix reproducer run was done with a byte-for-byte temporary revert of only the
   four call sites (imports kept working), then restored and hash-verified.
