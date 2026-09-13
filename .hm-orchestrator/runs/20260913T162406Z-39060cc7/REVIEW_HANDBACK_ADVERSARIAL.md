# Adversarial review (H3) — D1 (refund recorded) and D3 (money on the wire)

Reviewer: `hm-reviewer-adversarial` (deepseek-v4.1-flash). Date: 2026-09-13.
Branch `cloudflare-access-service-tokens`, baseline commit `75b19580`.
The work is uncommitted in the working tree. I did not change any product file or test.
All commands below are read/test-only.

Files I attacked: `services/billing.py`, `services/plan_replacements.py`,
`core/money.py`, `observability/issues.py`, `api/routers/dashboard.py`,
`services/system_brain_payments.py`, `core/plans.py`, `services/affiliate_attribution.py`.

Two findings are **reproduced at runtime** (scripts kept in this run folder). The rest
are ordered after them.

---

## Verdict

| Requirement | Verdict | Why |
|---|---|---|
| **D1** — a refund is recorded, and no money owed is computed from a refunded payment | **FAIL** | Two open high findings. A refund that arrives **after** a plan move leaves the money-owed record alive (reproduced, `pending_manual` $8.50). The billing cancel URL erases the refund word, which also switches off the NOWPayments double-grant guard (reproduced). |
| **D3** — one owner for money on the wire, no float, exact amount | **UNVERIFIED** | Exactness is proven, and `wire_text` is the only owner that sends an amount. But the fix changed the provider body from a JSON **number** to a JSON **string** on the strength of a help-centre page that says **both**, while NOWPayments' own OpenAPI, client libraries and request examples all say **number**. Whether a real crypto checkout still works is not proven anywhere offline. |

Both named test files pass:
`tests/unit/test_invariant_refund_is_recorded.py` (144 passed) and
`tests/unit/test_invariant_money_on_the_wire.py` (132 passed); together 276 passed,
0 failed.

---

## Findings, most severe first

### H-1 (High, OPEN, reproduced) — a refund that arrives AFTER a plan move still leaves the money-owed record

**What the requirement says:** no money owed back may come from a refunded payment.
The first fix closed the two windows "refund before the move" and "refund while the
move is frozen", but not "refund after the move has already written the money-owed row".

**Code path.**
- The move runs in `PaidPlanReplacementService.apply_after_payment`
  (`services/plan_replacements.py:261`). It reads the frozen source payment at line 283,
  checks it once at lines 376-382 (`source.status != SETTLED_PAYMENT_STATUS`), then writes
  `PlanMoveMoneyOwed(... status="pending_manual")` at lines 388-406, enqueues a customer
  email at 411-414, and opens a `billing:money-owed:{id}` ticket at 420-434.
- A refund later only changes the checkout attempt and the subscription. Nothing looks at
  `PlanMoveMoneyOwed`. There is no reader of that table that re-checks the source payment:
  `grep PlanMoveMoneyOwed src/` finds only the model, the writer, and the email outbox.

So the customer is told "we will send you $X for the unused time", the provider also gives
the old payment back, and nothing cancels the manual payout.

**Reproduction** (script `.hm-orchestrator/runs/20260913T162406Z-39060cc7/_adv_repro_move_then_refund.py`):

```
.venv\Scripts\python.exe .hm-orchestrator\runs\20260913T162406Z-39060cc7\_adv_repro_move_then_refund.py
```

Exact sequence: old plan Pro paid 15 days into a 30-day period -> new plan Plus paid
(`subscription.paid`) -> the move writes the money-owed row -> the old Pro payment is then
refunded (`payment.refunded`). Output:

```
AFTER MOVE: money_owed_rows=1 total_owed=8.50 source_status=completed
AFTER LATE REFUND: source_status=refunded money_owed_rows=1 pending_manual:8.50
FINDING REPRODUCED: money-owed row still pending after the source payment was refunded.
```

**Reachable:** yes. The product's own money-back window (`core/plans.py:756`, Pro = 7 days)
means a customer can request a refund of the old plan after they have moved to a new one.
A card chargeback after a move reaches the same state. This is a payment made twice for one
period.

**Related race (same code, Medium):** `apply_after_payment` reads the source payment at
`plan_replacements.py:283` without `with_for_update()`. If a refund transaction commits
between that read and the move's commit, the stale read wins and the owed row is written.
The deterministic "after" case above is the reproducible form of the same hole.

**Smallest correct direction:** when a refund lands on a checkout attempt that has an open
`PlanMoveMoneyOwed` row (`pending_manual`), void that record (and close/repoint the
`billing:money-owed:{id}` ticket) before it is paid by hand, and say so on the row.
There is no `voided` status today, but `status` is a free `String(24)`
(`db/models/commercial.py:304`), so no migration is needed.

---

### H-2 (High, OPEN, reproduced) — the billing cancel URL erases a refund and disables the double-grant guard

**Code path.** `GET /billing/cancel` in `api/routers/dashboard.py:4494`:

```python
4507:  if checkout_attempt.status not in {"completed", "failed", "expired"}:
4508:      checkout_attempt.status = "cancelled"
```

`refunded` is not in that set, so revisiting the cancel link a checkout was created with
(`billing.py:1400` gives NOWPayments this URL) writes `cancelled` over `refunded`.
The refund record is gone; the D1 invariant "a refund must be written down" is undone from
a normal browser URL by the customer themselves.

**It is worse than a lost label.** The one-time crypto re-delivery guard checks the status
word:

```python
3318:  if (
3319:      provider == "nowpayments"
3320:      and event_type == "payment.finished"
3321:      and attempt.status in {SETTLED_ATTEMPT_STATUS, REFUNDED_ATTEMPT_STATUS}
```

After the cancel visit the status is `cancelled`, so the guard no longer fires. NOWPayments
does re-deliver `payment.finished` (the guard's own comment says so), and the payment is
put back to `completed`. `payment_that_bought` (`plan_replacements.py:142`, requires
`status == "completed"` and `completed_at` set) then counts the returned money as money kept
again, and the money-owed path can value it.

**Reproduction** (script `.../_adv_repro_cancel_erases_refund.py`): a refund lands with no
subscription named (the shape the D1 tests pin: attempt `refunded`, plan stays live) -> the
route's own one-line status write is applied -> `payment.finished` is replayed. Output:

```
AFTER REFUND: attempt=refunded subscription=active
AFTER CANCEL ROUTE EQUIVALENT: attempt=cancelled
AFTER REPLAY: attempt=completed completed_at=2026-09-13 16:34:39.868661 subscription=active
FINDING REPRODUCED: the cancel route erased 'refunded', the double-grant guard was
bypassed, and the returned payment is counted as money kept again.
```

**Reachable:** yes. The condition is a normal sequence: a refund whose payload we could not
tie to a subscription (or a Stripe `charge.refunded`/dispute, which by design leaves the plan
live), then the customer opens the old cancel URL, then the provider re-delivers its success
event. This was already named as scope-expansion X1 in the prior D1 evidence; it is still
unfixed.

**Smallest correct fix:** add `"refunded"` to the protected set at `dashboard.py:4507`
(and give the post-payment page honest words for `refunded`, see L-3). One line, no
migration, and no existing test asserts the current behaviour.

---

### H-3 (High, UNVERIFIED) — the NOWPayments amount now leaves as a JSON string; the provider's own spec says number

**What changed.** `services/billing.py:1392` used to send `float(amount)`; it now sends
`wire_text(amount, currency)` (`core/money.py:75`), a **quoted JSON string** such as
`"9.00"`. The D3 test pins that shape.

**The problem.** The cited source — NOWPayments Help Centre, "API and endpoint description",
updated 2026-03-03 — contains **two** `POST /invoice` tables:
- the first says `price_amount | Number | Amount in fiat`;
- a later expanded block says `price_amount | String | Amount in fiat currency`.

`core/money.py:20-23` calls the String row "the current one". The same page therefore does
not settle the question. Three other authoritative sources say number:
- NOWPayments' published OpenAPI: `price_amount: type: number, format: double`;
- the official NOWPayments PHP library: `price_amount (int|float)`;
- NOWPayments' own request examples: `"price_amount": 3999.5`, `"price_amount": 1000`.

A change from number to string is not "costs nothing". If the API does not coerce the
string, **every crypto checkout fails** at invoice creation, for every customer. Nothing in
the test suite can catch this: the D3 test patches `provider_request` and inspects bytes
(`tests/unit/test_invariant_money_on_the_wire.py:149-182`), so it proves exactness only,
never acceptance.

**Reachable:** every `POST /v1/invoice`. Effect is unverified offline.

**Smallest correct direction:** keep the wire a JSON **number** and still avoid float — for
example build the JSON body with the quantised decimal text inserted as a number
(`"price_amount": 9.00`), or confirm with NOWPayments which row is correct. Exactness and a
number are not in conflict; only `float` was. If a string is truly accepted, a live probe
should say so and the value recorded next to the code should be the probe, not a
self-contradicting help page.

---

### M-1 (Medium, OPEN) — a partial refund is treated as a full refund; the refund amount is never read

The refund route never parses the refund amount (`_hydrate_checkout_data` returns early for
`REFUND_EVENT_TYPES`, `billing.py:3270-3284`; `_record_checkout_event` only sets the word,
`billing.py:3561-3568`). So:
- any refund event moves the whole payment off "money kept" (`payment_that_bought`,
  `affiliate_attribution.py:606`, `system_brain_payments.py` PAID list);
- `payment.refunded` / `refund.created` also end the plan
  (`REFUND_ENDS_PLAN_EVENT_TYPES`, `billing.py:604`);
- Stripe's `charge.refunded` fires on **partial** refunds, so a $1 refund on a $25 payment
  erases the whole $25 from the money-owed reading and revokes access for the rest.

This is conservative for "do not pay twice", so it does not create the D1 double-refund; it
is the opposite error (money wrongly treated as returned, access wrongly ended). It is the
same defect class: a money decision taken without reading the money the event carries.
**Reachable** through Stripe partial refunds and any provider partial refund.
Minimum: at least store the refunded amount, and only end the plan / drop the whole payment
when the refund covers it.

---

### M-2 (Medium, OPEN) — the money-owed row is written from a read that is not row-locked

Covered inside H-1. `plan_replacements.py:283` reads the frozen source payment without
`with_for_update()`, while the refund writer at `billing.py:3555` and the search at
`billing.py:3166` also read without a lock. Two transactions can both see `completed`.
`apply_after_payment` does lock the new attempt and the old subscription (`:271-282`) but
not the source payment, which is the row whose status decides whether money is owed.

---

### L-1 (Low, OPEN) — refunded payments are counted as "unfinished attempts" for staff

`services/system_brain_payments.py:359-364`:

```python
BillingCheckoutAttempt.status.not_in(PAID_STATUSES)   # PAID_STATUSES = {completed, succeeded, paid}
```

A `refunded` row is not in `PAID_STATUSES`, so it is counted as an unfinished attempt.
A returned payment is not an abandoned attempt. Same for `cancelled` after H-2.
Cosmetic; money and access are untouched. (The prior D1 evidence listed this as X2.)

### L-2 (Low, OPEN) — the post-payment page shows "Payment confirmation pending" for a refund

`api/routers/dashboard.py:4454-4466` has no `refunded` entry in `state_content`, so a
refunded checkout falls back to "Payment confirmation pending". The honest words already
exist at `api/routers/dashboard_test.py:1483` ("Refunded" / "The money went back to you").
Wording only. (Prior D1 evidence X3.)

### L-3 (Low, OPEN) — a second owner still puts money through `float`, and the D3 source scan cannot see it

`core/plans.py:330,332,336,340`:

```python
"monthlyPrice": float(charged) ...
"annualPrice": float(PUBLIC_PLAN_PRESENTATIONS[code].annual_price) ...
"originalMonthlyPrice": float(original) ...
"fullMonthlyPrice": float(charged) ...
```

The D3 source scan (`test_invariant_money_on_the_wire.py:286-291`) only checks four
modules (`services/billing.py`, `services/plan_replacements.py`, `services/affiliate.py`,
`services/affiliate_attribution.py`), so `core/plans.py` is invisible to it. This is a JSON
display payload, not the provider amount, and for the current whole-dollar prices no cent
is lost; but the requirement says "no `float` on a money value", and this is a second
serialization owner. To make it safe for a future odd-cent price, the same `wire_text`
question applies (or the scan should cover the module and the values should be serialized as
strings).

### L-4 (Low, OPEN) — money rounding mode is not one owner

`core/money.quantise` and `core/plans.price_after_percent` and
`plan_replacements.money_owed_for_unused_time` use `ROUND_HALF_UP` deliberately.
Three money sites use the default (`ROUND_HALF_EVEN`):
`billing.py:2495` and `billing.py:2504` (`_stripe_amount`, `_minor_unit_amount`),
`affiliate_attribution.py:528` and `:535`, and `services/affiliate.py:366,772-777`.
For whole-cent inputs the mode does not move the result; at an exact half-cent it can move
commission by one cent (`1.00 * 12.5% = 0.125` -> `0.12` here, `0.13` under the owner's
rule). The D3 test bans `float`, not the second rounding policy.

### L-5 (Low, informational) — test coverage gaps in the D1 family

- No test drives a refund **after** a `PlanMoveMoneyOwed` row exists (H-1). The current
  tests stop at "before" and "during".
- No test replays `payment.finished` after a refund (the `refunded` arm of the guard at
  `billing.py:3321` is untested; the only `checkout_already_completed` test uses a completed
  attempt, `tests/unit/test_billing_entitlements.py:727`). The H-2 sequence also bypasses it.
- No test covers a **partial** refund (M-1).
- No test can show the provider **accepts** the invoice body (H-3); that needs one cheap
  live probe, which this review cannot run.

These are coverage gaps, not weakened tests. I found no test that was skipped, deleted, or
loosened for D1/D3. The test files D1/D3 add are new; the neighbouring
`test_billing_entitlements.py` cancel/settled cases still pass.

---

## Scenarios I attacked and eliminated (with how)

### D1

| Scenario | Disposition and evidence |
|---|---|
| Refund **before** a plan move | Closed. `payment_that_bought` filters `status == "completed"` (`plan_replacements.py:148`), so `_the_one_live_paid_plan` raises `paid_amount_missing` (`:220-224`). Executed: `test_a_plan_move_after_a_refund_owes_nothing_back`, `test_a_refunded_payment_no_longer_counts_as_money_kept`. |
| Refund **during** a plan move (frozen, before the new payment) | Closed. `apply_after_payment` checks the frozen `source.status != SETTLED_PAYMENT_STATUS` and writes `Decimal("0.00")` (`plan_replacements.py:375-382`). Executed: `test_a_refund_that_lands_between_freeze_and_payment_owes_nothing_back`. Residual race: H-1/M-2. |
| Refund **after** a plan move | **OPEN — H-1, reproduced.** |
| Refund replayed, same provider event id | Closed. `process_event` returns `replayed=True` on the existing `BillingEvent` (`billing.py:2515-2527`). Executed: `test_the_same_refund_arriving_twice_changes_nothing_twice`. |
| Refund replayed, different event id, same payment | Closed for the payment word: `_resolve_refund_attempt` includes already-`refunded` rows (`billing.py:3241-3260`) and `_record_checkout_event` is a no-op when already `refunded` (`:3567`). Executed: `test_two_refund_events_for_one_payment_produce_one_alert`. |
| Refund out of order (`payment.finished` after a refund) | Closed only while the attempt still says `refunded`: the guard at `billing.py:3318-3332` refuses. **But H-2 shows the cancel URL removes that word.** No direct test. |
| Refund for a checkout we cannot match | Accepted by design, never refused, one critical alert (`billing.py:3134-3178`, `_save_refund_alert`). Executed: `test_a_refund_that_cannot_be_matched_is_an_alert_not_a_crash`, `test_a_refund_naming_a_checkout_we_do_not_have_is_alerted_not_refused`, `test_a_refund_that_names_nobody_is_accepted_and_alerted`. The money reader still sees the payment as kept until a person acts — that is the documented cost of not guessing. |
| Two settled payments, refund names neither | Not guessed; alert (`_resolve_refund_attempt` returns None for 2 candidates). Executed: `test_a_refund_that_could_belong_to_two_payments_is_not_guessed`. |
| Our own **webhook** cancel downgrades a settled payment | Closed. The failure set only downgrades `creating`/`pending`/`processing` (`billing.py:3584-3589`), and the generic fallthrough only touches `creating`/`pending` (`:3590`). Executed: `test_our_own_cancellation_never_undoes_a_settled_payment` (8 names × 3 providers). |
| Our own **browser** cancel link touches a refunded payment | **OPEN — H-2, reproduced.** It cannot downgrade `completed`, but it destroys `refunded`. |
| Is money owed derived from anything a refunded payment can still feed? | The four named readers all filter `completed`: `payment_that_bought` (`plan_replacements.py:148`), affiliate `_amount_charged` (`affiliate_attribution.py:606`), `PAID_STATUSES` (`system_brain_payments.py:74`), completed counter (`system_brain_tools.py:732`). Executed: `test_every_money_reader_stops_counting_a_refunded_payment`. The gap is the row already written before the refund (H-1). |
| Idempotency key stable across retries | Closed. The money-owed key is `sha256("plan-move:{old.id}:{period_start}:{period_end}")` (`plan_replacements.py:343-350`) — no event id, no amount. The refund alert key is a hash of the provider event id (`billing.py:3086-3100`), so a strange id cannot break it. |
| Currency / rounding of the refund amount | No effect: the refund amount is never read (see M-1). Money-owed uses the original `source.amount`/`currency` (`plan_replacements.py:401-403`). |
| Partial vs full refund | **OPEN — M-1.** |
| Refund arrives before the paid event for a crypto invoice | Closed: the refund word makes the re-delivered `payment.finished` fail the guard (`billing.py:3318`). |

### D3

| Scenario | Disposition and evidence |
|---|---|
| Remaining `float(` on a money value | One open Low (L-3, `core/plans.py`); no `float(` on a provider amount. |
| `Decimal` built from a `float` | None found. Every parse is `Decimal(str(...))`: `billing.py:2495,2504,3430,3465,3506-3507,3820`, `discount_codes.py:385`, `affiliate*.py:653,354`. |
| Rounding mode | Inconsistent at four non-owner sites (L-4); the owner `core/money.py:72` is `ROUND_HALF_UP`. |
| Serialization round trip loses a cent | Provider body: exact text, trailing zeros kept (`wire_text`, `core/money.quantise`). DB: `Numeric(12, 2)`, exact on PostgreSQL. Browser JSON: float (L-3), no cent loss for today's whole-dollar prices. |
| `==` / `!=` on money across types | Only three money comparisons, all `Decimal`: `billing.py:1942` (`discount.full != full_amount`), `plan_replacements.py:118` (`paid == 0`). No float comparison. |
| Provider's expected wire format | **OPEN, High — H-3.** |
| The owner is used by every caller | Yes. `grep wire_text` finds one production caller, `billing.py:1392`. Stripe and Creem `del` the amount and use `price_id`/`product_id`; `StaticBillingProvider` never uses it. |
| A second implementation of "amount to send" | None found. The amount is decided server-side once (`BillingService.checkout_amount`, `billing.py:2034`; discount via `core/plans.price_after_percent`), stored on the attempt, and only NOWPayments puts it on a wire. |
| `quantize` before/after arithmetic | Consistent: `price_after_percent` quantises the product once (`plans.py:235`), `money_owed_for_unused_time` quantises the final share (`plan_replacements.py:121`), `wire_text` quantises again only at the boundary. |

---

## What I verified vs what I believe

**Verified (commands run):**
- `pytest tests/unit/test_invariant_refund_is_recorded.py tests/unit/test_invariant_money_on_the_wire.py -q -p no:randomly` — 276 passed, 0 failed.
- H-1 and H-2 reproduced by the two scripts in this folder, with the exact outputs quoted.
- The provider-doc contradiction is quoted from the fetched live page; the OpenAPI and
  library `number` claims come from the search results (apis.io OpenAPI, the official PHP
  library, the official examples). I did not call the real provider.

**Believe, not verified offline:**
- H-3's practical effect. I cannot prove NOWPayments rejects a JSON string. It may coerce.
  That is why H-3 is High/UNVERIFIED, not Critical.
- M-1's reachability at each provider depends on their real partial-refund payloads.
  Stripe's `charge.refunded` for a partial refund is the strongest case.

**Uncertainty:** the provider payload shapes used in my repros are the ones the existing
tests already use (normalised bodies), not live captures.

## Artifacts in this folder
- `_adv_repro_move_then_refund.py` — H-1 reproducer (read-only).
- `_adv_repro_cancel_erases_refund.py` — H-2 reproducer (read-only).
