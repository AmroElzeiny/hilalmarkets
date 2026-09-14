# WP1 DESIGN — partial refunds (R1, R2). Supervisor-authored, binding.

Baseline HEAD `5c24d1fe`. Do not touch files in the mission's "Concurrency rules"
(especially `api/routers/dashboard.py`, `api/routers/dashboard_test.py`, `telegram/*`,
`pyproject.toml`, `.gitignore`). Do not `git commit/push/reset/checkout/clean/stash`.
Never read `.env` / `.env.production`.

## The defect

A refund event is treated as a full refund. The refunded amount is never read.
`_record_checkout_event` (billing.py:3572) sets `attempt.status = "refunded"` for **every**
refund name, `_end_refunded_plan` (billing.py:3059) cancels the plan for
`REFUND_ENDS_PLAN_EVENT_TYPES`, and `_void_manual_payout_for_refund` (billing.py:3640) voids
the whole payout. A $1 refund on a $25 payment erases the whole $25.

## Required behaviour

Read the amount the event carries. Decide full vs partial from it.

- Amount **unknown**, or refunded total **>=** the payment amount → **full refund**: today's
  behaviour must stay byte-for-byte identical (status `refunded`, plan ended for
  `REFUND_ENDS_PLAN_EVENT_TYPES`, payout voided).
- Amount **known and smaller** than the payment → **partial refund**: the attempt **stays
  `completed`**, the refunded total is stored, the plan is **not** ended, one `critical`
  staff alert is written, and the money-owed row (if any) is **re-valued** to the money
  kept (void it instead if money kept is 0).
- A later refund that brings the total to the payment amount becomes a **full** refund.
- `money_kept = max(paid - refunded_total, 0)` is the one owner. Every money reader uses it.

## State contract (decide exactly here; do not invent a second owner)

### Storage
Add one column to `BillingCheckoutAttempt` (`src/ai_market_monitor/db/models/commercial.py`,
class at line 217):

```
refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
```

No existing field fits: the only JSON column is `billing_profile`, which is the customer's
name/address written at checkout; storing money state there would be clobbered by profile
edits and hides a money contract in a profile bag. A dedicated column is a migration.

Migration: new file `alembic/versions/<rev>_record_refunded_amount.py`, `down_revision =
"e5a72b10c94d"` (current head). Use `op.batch_alter_table("billing_checkout_attempts")` and
`batch.add_column(sa.Column("refunded_amount", sa.Numeric(12, 2), nullable=False,
server_default="0"))`. `downgrade()` drops it. Column names need no `op.f()`; any named
constraint/index would.

### The one money-kept owner
Add to `src/ai_market_monitor/core/money.py`:

```
def money_kept(paid_amount: Decimal, refunded_amount: Decimal | None) -> Decimal:
    """The money this payment still holds: paid minus what came back, never below zero."""
    refunded = refunded_amount or Decimal("0")
    if refunded <= 0:
        return paid_amount
    return max(paid_amount - refunded, Decimal("0"))
```

Every reader imports this. Do not re-implement the subtraction.

### The refunded-amount parser (provider knowledge lives in the normaliser)
`_normalize_provider_payload` (billing.py:2305) and friends already own provider field
names. Add a normalized field `refunded_amount` (major units string) and
`refunded_total_is_cumulative` (bool) where the provider's field is known:

- Stripe `charge.refunded`: the Stripe object's `amount_refunded` is in **minor units**.
  Set `refunded_amount = BillingService._minor_unit_amount(stripe_object.get("amount_refunded"))`
  (reuse the existing minor-unit parser; do not write a second one) and
  `refunded_total_is_cumulative = True` (Stripe reports the **cumulative** total refunded on
  the charge; the recorder must not add it to a running total twice).
- Creem and NOWPayments: **no field name is known and no test fixture proves one. Do not
  invent one.** Leave `refunded_amount` unset → amount unknown → full refund, which is
  today's behaviour.
- Stripe dispute names (`charge.dispute.created`, `dispute.created`): leave unset
  (unknown) → full, today's behaviour.

Note in the code that the amount is **already in major units** once normalized; the recorder
parses it with `Decimal(str(...))`.

### Recording the total and deciding full/partial
`_attach_refund_to_attempt` (billing.py:3173) runs from `_hydrate_checkout_data` **before**
`_apply_event`, and is the one place a refund is pointed at an attempt. Extend it:

1. Load the matched attempt **with `with_for_update()`** (a running money total must not lose
   a concurrent update; SQLite no-ops the lock, PostgreSQL serialises). Use
   `select(BillingCheckoutAttempt).where(id==...).with_for_update().execution_options(populate_existing=True)`.
2. Parse `data.get("refunded_amount")`; on unreadable/missing → `None`.
3. Compute the new total:
   - unknown → total stays as stored; `data["refund_is_full"] = True`.
   - known and `refunded_total_is_cumulative` → `new_total = max(stored, amount)`.
   - known otherwise → `new_total = stored + amount`.
4. `data["refund_is_full"] = new_total >= attempt.amount or amount is None`
   (use a tolerant compare: any total **>=** the payment amount is full).
   `data["refund_is_partial"] = not refund_is_full`.
5. Persist `attempt.refunded_amount = new_total`, `await self.session.flush()`.
6. A replayed **same event id** is already stopped by `process_event`'s early return, so it
   never reaches here. Two **distinct** event ids for one payment are two refunds and add.
   Do not add a second dedupe mechanism.

### Access and plan
- `_apply_event` (billing.py:3015 refund branch): if `data.get("refund_is_partial")` is True,
  write the partial staff alert and return `None` **without** calling `_end_refunded_plan`.
  Full/unknown keeps the exact current path.
- `_end_refunded_plan` (billing.py:3059): return `None` early when
  `data.get("refund_is_full") is False`. (Belt-and-braces; the caller also guards.)

### Attempt status
`_record_checkout_event` (billing.py:3572) refund branch:
- Full: `attempt.status = REFUNDED_ATTEMPT_STATUS` when not already refunded (unchanged).
- Partial: **do not** change `attempt.status`; it stays `completed`.
- In **both** cases call the payout settlement (below) so a partial refund re-values it.

### Payout settlement (extend the existing void owner)
`_void_manual_payout_for_refund` (billing.py:3640) is the one owner. Extend it to take the
new money-kept figure into account:
- Load the attempt's `pending_manual` `PlanMoveMoneyOwed` rows (unchanged).
- `kept = money_kept(attempt.amount, attempt.refunded_amount)`.
- If the refund is full (`kept == 0`): set `status = MONEY_OWED_VOID_STATUS` and resolve the
  ticket (unchanged).
- If partial (`kept > 0`): **re-value in place** —
  `new_amount = money_owed_for_unused_time(paid_amount=kept, period_start=row.period_start,
  period_end=row.original_period_end, ended_at=row.ended_at)`; set
  `row.paid_amount = kept`, `row.amount_owed = new_amount`; keep `pending_manual`.
  If `new_amount <= 0`, void instead. Import `money_owed_for_unused_time` from
  `services.plan_replacements` (it is the one unused-time owner; do not re-derive).
  Leave the ticket open (the payout still has to be sent); do not re-report it.
- Keep the existing idempotence: only `pending_manual` rows are touched; a second delivery
  changes nothing further.

### The partial staff alert
Add a module constant sentence and a `_save_partial_refund_alert(self, *, event_id,
event_type, attempt)` method next to `_save_refund_alert`. `severity="critical"`,
`category="billing"`, `affected_scope="billing.partial_refund"`,
`dedupe_key = sanitize_dedupe_key(f"billing:partial-refund:{attempt.id}")` (one alert per
payment, repeats increment the count — do not hash the event id here), and evidence refs to
the attempt and event. Keep the summary within the queue's 200-char cap; include the money
kept and the payment amount in plain words.

### Money readers (R2)
1. `services/plan_replacements.py` `apply_after_payment` (line 272): value the payout from
   `money_kept(source.amount, source.refunded_amount)`; set `paid_amount=` to the same
   figure so `amount_owed <= paid_amount` still holds. The existing
   `source.status != SETTLED_PAYMENT_STATUS` guard stays (a full refund still writes zero).
2. `services/affiliate_attribution.py` `_amount_charged` (line 570): select the attempt row
   (not only `.amount`) and return `money_kept(row.amount, row.refunded_amount)` for the
   `"checkout"` source. The event `fallback` path is unchanged (no refund exists yet at
   that moment).
3. `services/system_brain_payments.py`: every money figure the staff reads.
   - `_payments` (line 393): `amount=money_kept(attempt.amount, attempt.refunded_amount)`.
   - `list_customers` totals (line 205): `func.sum(BillingCheckoutAttempt.amount - BillingCheckoutAttempt.refunded_amount)`
     (refunded_total is never above amount on a `completed` row).
   - `overall_totals` (line 297): same SQL expression.
   These stay whole for full refunds because a fully refunded row is not in `PAID_STATUSES`.

## Files you may edit
`core/money.py`, `services/billing.py`, `services/plan_replacements.py`,
`services/affiliate_attribution.py`, `services/system_brain_payments.py`,
`db/models/commercial.py`, one new `alembic/versions/*.py`, one new
`tests/unit/test_invariant_partial_refund.py`.

## Acceptance test (failing first)
New file `tests/unit/test_invariant_partial_refund.py`. Reuse
`tests.unit.test_invariant_refund_is_recorded._seed_paid_plan`, `_refund_payload` and
`tests.support.billing_config.live_billing_overrides` (import them, do not copy). Drive
`BillingService.process_event` exactly as the neighbours do.

Matrix: `REFUND_EVENT_TYPES` (5 names) × `("stripe","creem","nowpayments")` × amounts
`(unknown, partial, exact full, over-full)` × scenario `(single, same-event-replayed,
two-partials-summing-to-full)`. Supply known amounts by putting `"refunded_amount"` (major
units) directly into `data`; that is the normalized contract the normaliser produces.
Assertions:
- partial → `attempt.status == "completed"`, `attempt.refunded_amount` equals the total, the
  plan is not ended (`REFUND_ENDS_PLAN` names), one `billing.partial_refund` critical alert,
  and `money_kept(attempt.amount, attempt.refunded_amount) == amount - refunded`.
- unknown / exact full / over-full → `attempt.status == "refunded"`, plan ended for
  `REFUND_ENDS_PLAN_EVENT_TYPES`, `money_kept == 0`.
- same event id replayed → nothing changes twice (one row, one alert, total unchanged).
- two partials summing to full → first stays `completed`, second becomes `refunded`,
  `refunded_amount == amount`.
- One test seeds a plan move with a standing `pending_manual` payout, then a partial refund:
  the row is re-valued to the money-kept share and stays `pending_manual`; a following refund
  bringing it to full voids it (the existing `test_invariant_refund_after_plan_move` covers
  the full path).
- One test proves the normaliser maps Stripe `amount_refunded` (minor units) to
  `data["refunded_amount"]` in major units and leaves Creem/NOWPayments unset.
- One test proves the three money readers (`apply_after_payment` value/source, affiliate
  `_amount_charged`, staff `_payments`/totals) use money kept after a partial refund.

Prove it fails first: run the new file on the **unfixed** code, save JUnit to
`.hm-orchestrator/runs/20260913T224025Z-95c82040/WP1_BEFORE.xml`. Then implement, run it
again to `.hm-orchestrator/runs/20260913T224025Z-95c82040/WP1_AFTER.xml` (must pass).
Then run the neighbours and save `WP1_NEIGHBOURS.xml`:
`tests/unit/test_invariant_refund_is_recorded.py tests/unit/test_invariant_refund_after_plan_move.py tests/unit/test_billing_entitlements.py tests/integration/test_paid_plan_replacement.py tests/integration/test_checkout_and_payment_email.py`
and the affiliate tests (`tests/unit` files matching `*affiliate*`).
Then `ruff` on the changed files and `mypy src/ai_market_monitor`.

## Forbidden
Changing full-refund results; guessing a refund amount for an unknown provider; ending a
plan on a partial refund; re-implementing `money_kept`, the unused-time formula, the
minor-unit parser, or an open/paid status list; editing concurrency-rule files.
