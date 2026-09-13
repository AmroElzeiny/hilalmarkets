# Adversarial review of the post-R1/R2 fixes (money path)

- Reviewer model/family: `deepseek-v4.1-flash` (DeepSeek).
- Baseline: `75b19580`, branch `cloudflare-access-service-tokens`.
- Read-only on product code. I wrote only this file.
- Prior reports read first: `REVIEW_LOGIC_1.md`, `REVIEW_LOGIC_2.md`.
- `REVIEW_FIXES_LOGIC.md` does **not** exist in either run directory, so my
  conclusions below are independent, not agreements/disagreements.
- I did not run the browser suite (forbidden). The CSS/template/browser fixes
  from this run are outside the money-path question and are not re-reviewed here.

## Tests I ran (one file per process, no `--timeout`)

| Command | Result |
|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_operational_issue_queue.py -q -p no:randomly` | 23 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_annual_only_plan_card.py -q -p no:randomly` | 5 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_paid_plan_replacement.py -q -p no:randomly` | 17 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_plan_change_journey.py -q -p no:randomly` | 28 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_plan_replacement_money.py tests/unit/test_invariant_plan_checkout_availability.py -q -p no:randomly` | 90 passed |

## Per-fix verdict

### R2-1 (blocker) — evidence ref with `:` is sanitized. PARTIALLY CLOSED.

**What is fixed.** The sanitizer now lives at the one owner of what a ref may
look like: `issues.py:81-111` replaces a disallowed tail character with `-`,
truncates to the pattern length, and `_validate_payload` calls it before the
pattern check (`issues.py:404-413`). Every writer goes through
`record_occurrence`, and the only three call sites are `billing.py:2698`,
`billing.py:2743` and `plan_replacements.py:388` (grep of `record_occurrence`).
So `billing_event:nowpayments:90313222:finished` now becomes
`billing_event:nowpayments-90313222-finished`. The new test
`test_an_evidence_ref_with_provider_colons_is_sanitized_not_lost`
(`tests/unit/test_operational_issue_queue.py:267`) and
`test_crypto_paid_move_with_failed_cancel_leaves_alert_and_failed_event`
(`tests/integration/test_paid_plan_replacement.py:863`) pass. Idempotency:
the dedupe key is unchanged by sanitizing
(`tests/unit/test_operational_issue_queue.py:285`), so a redelivery increments
the one row.

**What is NOT fixed — same defect class, second route.** The critical alert can
still be refused *before the commit* because the **dedupe key** is held to a
different, lowercase-only pattern: `_DEDUPE_KEY_PATTERN`
(`issues.py:45`) is `^[a-z0-9][a-z0-9_.:/-]{0,159}$`. `_plan_move_failure_key`
builds `billing:plan-move-failed:{event_id}` with the provider event id
**verbatim** and no case-folding (`billing.py:2631-2635`). Stripe event ids are
`evt_` plus mixed case, and Stripe is a first-class, configurable card provider
(`settings.billing_card_provider: Literal[..., "stripe", "creem"]`,
`config.py:339`; normalizer keeps the raw id at `billing.py:2251-2255`). I
executed the actual pattern:

```
billing:plan-move-failed:evt_01JABCdefGHI  ->  _DEDUPE_KEY_PATTERN.match False
billing:plan-move-failed:evt_01jabcdefghi  ->  True
```

So for a Stripe-paid replacement move whose old-card cancel fails,
`_validate_payload` raises `IssueQueueError` at `issues.py:387-390`, before the
commit at `billing.py:2723`; the `BillingEvent` row is still rolled back and the
critical alert is lost. That is exactly the R2-1 failure ("no alert, no failed
event, nothing"), just triggered by the key instead of the ref. The same key
shape is used by `_save_resurrection_alert` (`billing.py:2744`). This is a
**blocker while any card provider emits an uppercase or otherwise
pattern-refusing event id**; serious if only Creem/NowPayments (lowercase ids)
are live.

Other producers of the same decision: the queue owner is single; the gap is
that the *key* rule was never brought under the same sanitizing owner.

### R2-2 (serious) — lapsed card plan is frozen and cancelled. SERVER SIDE CLOSED; NEW PAGE/SERVER DIVERGENCE.

**What is fixed.** `_the_one_live_paid_plan` now accepts a recurring plan whose
`current_period_end` is up to 30 days in the past while `cancel_at_period_end`
is false (`plan_replacements.py:35, 176-183`). The checkout route uses the new
`paid_plan_codes_for_replacement_decisions` (`billing.py:758-799`) at
`billing.py:1741-1745`, so the lapsed card is frozen onto the attempt and
cancelled after payment. `test_lapsed_recurring_card_plan_is_frozen_and_cancelled_on_move`
(`test_paid_plan_replacement.py:798`) asserts one live subscription, one
recurring row, old `CANCELED`, and no money-owed row for a fully lapsed period.
Good.

**New second owner of "which plans does this account hold".** The pages and the
route now read two different sets:

- Route (grace, used for `already_subscribed` and for replacement):
  `paid_plan_codes_for_replacement_decisions` (`billing.py:1741-1750`).
- Pages and `plan_checkout_availability` (no grace):
  `active_paid_plan_codes` (`billing.py:724-755`) at `dashboard.py:3432`,
  `dashboard.py:4013`, `dashboard_test.py:1711`; also `PlanChangeService.paid_subscription`
  (`plan_changes.py:247-268`) and `paid_access_can_be_repriced`
  (`billing.py:630-631`).

Consequences in the lapsed window (verified by reading; the existing page/server
test `test_every_plan_card_button_matches_the_server` uses a future-dated plan
and cannot see it):

1. The lapsed plan is absent from `active_paid_plan_codes`, so
   `plan_checkout_availability` returns `holds_this = False`,
   `holds_other = False` (`billing.py:833-842`). `_plan_card` marks it
   not-current and `buyable = True` (`dashboard_test.py:1626-1627`), and the
   billing page draws a live Pay button (`dashboard.py:3460-3477`, template
   `billing.html:158`). Pressing it for the same plan reaches
   `prepare_checkout`, which raises `already_subscribed` because the grace set
   contains it (`billing.py:1746-1750`). The page offers what the server refuses.
2. `replacement_refusal` is computed from the grace-aware
   `_the_one_live_paid_plan` (`plan_replacements.py:209-225`) but is only used
   when `holds_other` is true (`billing.py:841, 870`). With no-grace
   `active_paid_plan_codes` the refusal is non-empty and ignored, so a lapsed
   plan with no completed payment record shows a Pay button while the route
   raises `paid_amount_missing` — the exact drift
   `test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page`
   (`test_plan_change_journey.py:934`) was written to stop, but only for
   future-dated plans.

This fails closed (no charge), but it is the repo's classic defect: two modules
decided one thing and disagree. No double charge found from this.

### R2-3 (serious) — late renewal resurrects a cancelled plan. PARTIALLY CLOSED.

**What is fixed.** `_upsert_subscription` raises
`subscription_resurrected_after_cancel` when a `_PAID_EVENT_TYPES` event would
set an ended row `ACTIVE`/`TRIALING` (`billing.py:3264-3285`), and
`process_event`/`reprocess_failed_event` turn it into a committed critical
alert (`billing.py:2485-2493`, `2567-2575`, `2725-2759`).
`test_paid_event_for_replaced_subscription_is_refused_and_alerted`
(`test_paid_plan_replacement.py:714`) covers `subscription.paid`. Idempotency:
redelivery finds the failed event and re-raises; the alert row is incremented,
not duplicated.

**Class gap.** The guard keys on `event_type in self._PAID_EVENT_TYPES`
(`billing.py:3211-3220, 3275`). But `_apply_event` routes several more events
through `_upsert_subscription` with the event type set
(`billing.py:2807-2822`): `subscription.updated`, `subscription.update`,
`customer.subscription.updated`, `subscription.scheduled_cancel`,
`subscription.trialing`. All of them can map to `ACTIVE`/`TRIALING`
(`_status_from_provider`, `billing.py:3300-3315`), and none is in the paid set.
I executed the membership check:

```
subscription.updated            in _PAID_EVENT_TYPES -> False
subscription.update             in _PAID_EVENT_TYPES -> False
customer.subscription.updated   in _PAID_EVENT_TYPES -> False
subscription.scheduled_cancel   in _PAID_EVENT_TYPES -> False
subscription.trialing           in _PAID_EVENT_TYPES -> False
```

So an in-flight `subscription.updated` (status active) arriving after our cancel
still sets the ended row back to `ACTIVE`, giving two live subscriptions — the
same outcome R2-3 was about. No test covers it. Fix the class, not the
`subscription.paid` instance: guard every event that can set `ACTIVE`/`TRIALING`
on an ended row, not just the money events.

### R2-6 (minor) — cancel webhook downgrades a completed attempt. CLOSED.

`_record_checkout_event` now only downgrades an attempt that is still
`creating`/`pending`/`processing` (`billing.py:3167-3180`), so a
`subscription.canceled` for the old subscription leaves the settled attempt
`completed`. `test_canceled_event_does_not_downgrade_completed_attempt`
(`test_paid_plan_replacement.py:666`) passes; the other status writers guard the
same way (`dashboard.py:4488-4493`). Idempotent.

Minor scope note: the same change also stops `payment.refunded`,
`payment.partially_paid` and `payment.expired` from moving a completed attempt
off `completed` (`billing.py:3167-3178`). The billing-history rows treat
`completed` and `refunded` identically (`dashboard.py:451`), so the visible
impact is small, but it is a breadth change beyond the reported case. Whether a
refunded attempt should still count as "the payment that bought" the plan
(`payment_that_bought`, `plan_replacements.py:124-135`) is now left as
`completed`. I did not find a live path where that loses money, because a refund
also sets the subscription `CANCELED` (`billing.py:2886-2904`), which keeps it
out of `_LIVE_STATUSES`.

## New-route / defect-class analysis (a)-(f)

- **(a) Other producers of the same decision.** R2-1: the queue owner is single
  but the dedupe-key rule is a second gate with no sanitizer (finding 1).
  R2-2: `active_paid_plan_codes` vs `paid_plan_codes_for_replacement_decisions`
  vs `_the_one_live_paid_plan` vs `PlanChangeService.paid_subscription`
  (finding 2). R2-3: `_PAID_EVENT_TYPES` vs the full upsert event list
  (finding 3).
- **(b) New money routes.** No new double charge, oversized refund, or
  paid-with-no-plan found from the four fixes. R2-2 does open a page that
  disagrees with the server (finding 2). R2-3 still leaves a resurrection route
  (finding 3).
- **(c) Idempotency (webhooks redelivered).** R2-1: stable dedupe key, one row
  incremented, but entirely lost if the key fails the pattern. R2-2:
  `prepare_checkout` returns the existing attempt on replay
  (`billing.py:1823-1849`), and `attach_source` is a no-op when already attached
  (`plan_replacements.py:232-241`). R2-3: replay re-raises and re-increments the
  same alert. R2-6: idempotent.
- **(d) Decimal-only.** No new `float()` in the fix paths. The one money float
  R2-7 named still exists (`billing.py:1308`, `"price_amount": float(amount)`);
  it is pre-existing and not reached by these fixes. Owed money stays Decimal
  (`plan_replacements.py:92-111, 346-351`).
- **(e) Owner properties.** Successful card→card monthly move keeps one live and
  one recurring row; the new lapsed-card test keeps the same property and leaves
  the old row `CANCELED`; a failed crypto move rolls the new subscription back
  and leaves the old card `ACTIVE` (`test_paid_plan_replacement.py:853-934`),
  which is the owner's abandoned/failed rule. Crypto old plans are no-ops to
  cancel (`plan_replacements.py:410-413`). **Still untested:** annual cycles and
  any successful crypto→card / card→crypto move. That is the residual R2-4 gap,
  not introduced here.
- **(f) Retry-then-alert path.** The two fixed summaries are 182 and 193
  characters against the 200 cap (`labels.py:181`), so length is safe, and the
  evidence refs are now sanitized. But see finding 1: the alert can still be
  refused and lost when the dedupe key carries a character the key pattern
  forbids (uppercase provider id). The class has now shipped here three times:
  length once, evidence-ref character once, dedupe-key character still open.

## Findings table

| # | Severity | Finding | Proof |
|---|---|---|---|
| 1 | blocker (while card provider emits uppercase event ids, e.g. Stripe) / serious otherwise | R2-1 not closed for the whole class: the lowercase-only `_DEDUPE_KEY_PATTERN` rejects `billing:plan-move-failed:<evt_id>` for Stripe-shaped ids, so `record_occurrence` raises before the commit and the critical alert plus failed `BillingEvent` are lost again | `issues.py:45, 387-390`; `billing.py:2631-2635, 2698-2723, 2744-2759, 2251-2255`; `config.py:339`; executed match `False` |
| 2 | serious (no charge; fails closed) | R2-2 introduced a second owner of "which paid plans this account holds": route uses grace set, pages/`plan_checkout_availability`/`paid_subscription` use no-grace set. In the lapsed window the page offers Pay for a plan the route refuses (`already_subscribed`) and hides the `paid_amount_missing` refusal | `billing.py:724-799, 833-842, 1741-1750`; `plan_replacements.py:152-207, 209-225`; `dashboard.py:3432-3477, 4013-4030`; `dashboard_test.py:1711-1738, 1626-1627`; `plan_changes.py:247-268` |
| 3 | serious (uncertain; provider timing) | R2-3 guard only covers `_PAID_EVENT_TYPES`; `subscription.updated`/`subscription.update`/`customer.subscription.updated`/`subscription.scheduled_cancel`/`subscription.trialing` can still set an ended row `ACTIVE`/`TRIALING` | `billing.py:2807-2822, 3211-3220, 3300-3315`; executed membership all `False`; no test |
| 4 | minor | The new sanitizer accepts a payload-shaped ref with a valid prefix (spaces/braces become `-`), weakening the documented "pointer, never a payload" rule; `assert_no_sensitive_content` is not applied to refs | `issues.py:81-111, 404-413`; executed: `billing_event:the provider returned a 500 body` -> accepted |
| 5 | minor | R2-6 fix broadens to all failure events: a `payment.refunded` on a completed attempt no longer records `refunded`. Low visible impact; needs a deliberate decision | `billing.py:3167-3180`; `dashboard.py:451` |
| 6 | minor (residual, pre-existing) | Owner property (a) is still only asserted for card→card monthly; no annual, no successful crypto crossing | `test_paid_plan_replacement.py:124, 798, 863`; `_prepare_new_checkout` uses `billing_cycle="monthly"` |
| 7 | minor (pre-existing, not introduced) | R2-7 money `float()` remains at the NOWPayments invoice | `billing.py:1308` |

Counts: 1 blocker-conditional, 2 serious, 4 minor, 0 false alarms.

## What I verified and what I did not

Verified: the four fix files and tests pass as above; the sanitizer is the only
path for queue evidence refs; the executed pattern matches for the dedupe key
and the sanitizer; the grace-window filters; the resurrection guard's event set;
the page/route owners. Not verified: real provider behaviour (Stripe event id
casing in this account, whether Creem sends `subscription.updated` after a
cancel, renewal timing). Findings 1 and 3 carry that uncertainty and say so.
