# Review — plan replacement money path (R1 logic review)

Baseline: `75b19580`. Branch: `cloudflare-access-service-tokens`.
Read-only. I ran the two named test files; both passed.

Commands run, and what they printed:

- `.venv/Scripts/python -m pytest tests/unit/test_invariant_plan_replacement_money.py -q -p no:randomly` → `......... [100%]` (9 passed)
- `.venv/Scripts/python -m pytest tests/integration/test_paid_plan_replacement.py -q -p no:randomly` → `............. [100%]` (13 passed)

The two purchasable plans are `trader` (Plus) and `pro` (Pro), so "every plan
crossed with every other plan" is two pairs, and both are in the tests.

---

## Question 1 — Can a customer be charged twice, or end with two live subscriptions?

**Answer: the tested card paths are safe. Two double-charge windows are not covered
and one of them is silent.**

The normal card move is correct. After a confirmed `subscription.paid`:

- `apply_after_payment` cancels the frozen old subscription first
  (`plan_replacements.py:320`), then marks it `CANCELED` (`:321-324`).
- The integration test `test_confirmed_card_move_leaves_one_charge_and_one_money_record`
  asserts exactly one live subscription and one recurring charge, for both plan pairs.
- A renewal of the new plan does not cancel or repay the old one again
  (`test_renewing_the_new_plan_never_ends_or_repays_the_old_one_again`).
- Two payment pages paid for one move leave one live plan
  (`test_two_payment_pages_paid_for_one_move_leave_one_live_plan`).

**Finding 1 (serious, UNCERTAIN).** A card plan whose period has just lapsed is
invisible to the "find the old plan" search. `_the_one_live_paid_plan`
(`plan_replacements.py:168-169`) and `active_paid_plan_codes` (`billing.py:743-744`)
both require `current_period_end > now` (or null). A recurring card plan keeps status
`ACTIVE` with `cancel_at_period_end = False` after its period end until the renewal
webhook arrives. In that window the customer is treated as holding no paid plan, so
`source_for_checkout` freezes nothing (`replaces_subscription_id` stays null) and
`apply_after_payment` returns early (`plan_replacements.py:244-245`). The old card is
never cancelled and keeps charging at the payment company, next to the new plan. This is
the exact "charged twice every month" failure the owner warned about, and it raises no
alert. I cannot reproduce it without a real provider, because it depends on webhook
timing. Proof of the filter: `plan_replacements.py:168-169`.

**Finding 2 (coverage gap, minor).** The tests cover only card (Creem) monthly. There is
no test for crypto-to-crypto, crypto-to-card, card-to-crypto, or the annual cycle. By
reading the code, crypto is safe: `_cancel_old_recurring_plan` does nothing for a
`nowpayments` provider (`plan_replacements.py:397-399`), and crypto does not recur. But
nothing asserts it. The owner's decision asks for "every plan crossed with every other
plan, both billing cycles, card and crypto."

---

## Question 2 — Can a customer pay and get no access?

**Answer: abandoned, declined, and duplicate webhooks are correct. The cancel-failure
case leaves the customer charged for a plan our system says they do not have.**

- Abandoned checkout: old plan untouched (`test_abandoned_checkout_leaves_the_old_plan_untouched`).
- Declined card: old plan untouched (`test_failed_payment_leaves_the_old_plan_untouched`).
- Webhook never arrives: the old plan stays; the new plan is not granted. The customer
  keeps their old plan, so they do not lose anything, but there is no automatic recovery.
  The checkout row shows "Try again", and pressing it can open a second charge. Minor,
  pre-existing.
- Webhook twice: deduped by `provider_event_id` (`billing.py:2371-2383`); the replay
  path returns the existing result.

**Finding 3 (serious).** When the cancel call fails after the new plan is live, the whole
payment is rolled back, so our database never records the new subscription. The customer
has really paid at Creem and Creem's new subscription is live and will charge again, but
our system shows no new plan and still shows the old plan. The old plan also keeps
charging, because its cancel failed. So the customer pays for a plan they cannot use in
our app, and is charged for two subscriptions, until a person reprocesses. The code does
what the owner decided: retry (`plan_replacements.py:421`), then a critical alert
(`billing.py:2627-2649`). The alert wording "the new plan was not given"
(`billing.py:2637`) is only true of our own database — Creem did give it and is charging
for it. Proof: `plan_replacements.py:451-460` raises, `billing.py:2422-2431` rolls back.

---

## Question 3 — Can the money owed be written twice, or be more than what was paid?

**Answer: no. SAFE.**

- Idempotency key `plan-move:{old.id}:{period_start}:{period_end}` plus a unique
  constraint (`plan_replacements.py:311-318`; migration `e5a72b10c94d...:112-114`).
- Check constraints in both the model and the migration: `paid_amount >= 0`,
  `amount_owed > 0`, `amount_owed <= paid_amount`
  (`commercial.py:272-274`; migration lines 65-74).
- `money_owed_for_unused_time` never returns more than `paid`
  (`plan_replacements.py:91-97`), verified by `test_money_owed_uses_paid_amount_and_stays_inside_it`.
- Replay through `reprocess_failed_event` runs the same checks again
  (`billing.py:2466-2478`), and the ended-branch `earlier` lookup plus the idempotency
  key stop a second record (`plan_replacements.py:277-286, 314-318`).

The test `test_confirmed_card_move_leaves_one_charge_and_one_money_record` asserts one
record and `owed[0].amount_owed <= source.amount`.

---

## Question 4 — Can the amount, the plan, or the period start be wrong?

**Answer: the amount comes from `BillingCheckoutAttempt.amount`, as required. SAFE with
one minor note.**

- `apply_after_payment` uses `source.amount` (`plan_replacements.py:332-337` and
  `paid_amount=source.amount` at `:355`). `source` is the frozen
  `attempt.replaces_checkout_attempt_id` in the normal path (`:252`), and
  `payment_that_bought` in the ended path (`:297`). Both are checkout-attempt amounts.
- The catalogue price (`plan.price_monthly`, `effective_monthly_price`) is never used
  for the owed amount.
- `period_start`/`period_end` are captured from the old subscription before it is
  truncated (`:303-304` before `:324`).

**Finding 4 (minor, UNCERTAIN).** Renewals do not create a new checkout attempt, so
`payment_that_bought` returns the first purchase's amount. If a customer's first period
was discounted (a code typed on Creem's own page) and later renewals were full price,
the unused time is valued at the discounted figure, under-refunding the customer. This
is an edge case and depends on discount behaviour across renewals.

---

## Question 5 — Can a page offer something the server then refuses?

**Answer: no second rule found. SAFE with one minor note.**

One owner `plan_checkout_availability` (`billing.py:753`) is read by the billing page,
the subscription page (`dashboard_test.py:1729-1738`), the checkout review page
(`dashboard.py:4017-4027`), the resume route (`dashboard.py:3812-3821`), and both
checkout routes (`billing.py:1707-1713` and `1949-1957`). The two button builders
(`_switch_offer` in `plan_changes.py`, `_plan_card` in `dashboard_test.py`) read the same
answer or its inputs (`plan_is_on_sale` + `replacement_refusal`). The browser scripts
(`hilalmarkets-billing.js`, `hm-subscription-test.js`) read `availability.*` from the
server and re-derive nothing. The resume route re-checks the account and refuses
(`dashboard.py:3822-3823`), so an old "Finish paying" link cannot charge a plan the
account may not buy.

**Finding 5 (minor).** `_switch_offer` gates on `plan_is_on_sale(..., monthly)` and
`_billing_history_rows` uses `plan_is_on_sale`, while `plan_checkout_availability`
also counts `card_annual`. In an annual-only configuration, a card would read
"coming soon" or block a retry while the checkout review page would allow the sale. This
fails closed (a missed sale, not an offer the server refuses). No customer harm.

---

## Question 6 — Does any customer-visible sentence lie?

**Answer: mostly accurate. Two small copy issues and one dead pair of messages.**

- `CONSENT_PLAN_REPLACEMENT` (`plan_replacements.py:79-83`) and the billing-page note
  (`billing.html:190`) match the owner's rule and the 48-hour window.
- `billing_result.html:15` correctly says the plan stays unchanged until confirmation.

**Finding 6 (minor).** The consent says "I pay the full price of the new plan today." A
crypto discount code makes the actual charge less than the full price, so the sentence
is not exact for a code user.

**Finding 7 (minor, no customer impact).** `plan_upgraded` and `plan_downgraded`
messages still exist (`base_dashboard.html:161-162`) and describe the old prorated
switch flow ("the payment company already has your card", "the new price starts on your
renewal day"). They are unreachable: the switch route always raises
`plan_change_needs_payment` (`plan_changes.py:506-510`), so `_finish_plan_change` with
those messages never runs. Stale copy, not a live lie.

---

## Summary table

| # | Severity | Question | Finding | Proof |
|---|---|---|---|---|
| 1 | serious (uncertain) | Q1 | Lapsed recurring card plan is invisible; not frozen, not cancelled → silent double charge | `plan_replacements.py:168-169`, `billing.py:743-744` |
| 2 | minor (coverage) | Q1 | No test for crypto or annual cycles | `test_paid_plan_replacement.py` uses creem/monthly only |
| 3 | serious | Q2 | Cancel-fail rolls back new plan; customer charged for it and old plan keeps charging | `plan_replacements.py:451-460`, `billing.py:2422-2431, 2637` |
| 4 | minor (uncertain) | Q4 | Renewals reuse the first purchase amount; a discounted first period under-refunds | `plan_replacements.py:108-129` |
| 5 | minor | Q5 | `plan_is_on_sale(monthly)` vs `card_annual` divergence (fails closed) | `plan_changes.py:391-397`, `dashboard.py:457-491` |
| 6 | minor | Q6 | Consent says "full price" even when a crypto code lowers it | `plan_replacements.py:79-83` |
| 7 | minor (dead) | Q6 | `plan_upgraded`/`plan_downgraded` copy unreachable | `base_dashboard.html:161-162`, `plan_changes.py:506-510` |

## Verdict per question

| Question | Verdict |
|---|---|
| 1 (double charge) | UNCERTAIN |
| 2 (pay and no access) | UNCERTAIN |
| 3 (money owed twice / too much) | SAFE |
| 4 (amount / plan / period) | SAFE |
| 5 (one availability owner) | SAFE |
| 6 (lying sentences) | SAFE with minor notes |

## What I verified and what I did not

- Verified: the amount source, the idempotency key, the check constraints, the cancel
  order, and the two named test files passing.
- Not verified: live provider behaviour (webhook timing, renewal grace, cancel semantics).
  Findings 1 and 3 depend on that, which is why they are "serious" but "uncertain".
- Recommendation: if Finding 1 is to be closed, add an integration test where the old
  card subscription has `current_period_end` in the past but status `ACTIVE` and
  `cancel_at_period_end = False`, buy a different plan, and assert the old subscription
  was cancelled. If Finding 3 is to be closed, add a test where the cancel call fails on
  the first move and assert the alert text says the customer is charged for a plan our
  system has not yet given.
