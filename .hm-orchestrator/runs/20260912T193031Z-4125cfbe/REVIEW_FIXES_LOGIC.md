# Review — fix verification (R2 fixes)

Model / family: `minimax-m3`, MiniMax. Baseline `75b19580`, branch
`cloudflare-access-service-tokens`. Read-only. I did not re-run R1/R2 from
scratch; I verified the five named fixes against the code, the new tests and
the failure-before evidence.

## Files seen

- `src/ai_market_monitor/observability/issues.py`
- `src/ai_market_monitor/services/billing.py`
- `src/ai_market_monitor/services/plan_replacements.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/services/plan_changes.py`
- `src/ai_market_monitor/static/hilalmarkets.css`
- `src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css`
- `src/ai_market_monitor/static/hilalmarkets-public.css`
- `src/ai_market_monitor/templates/hilal/base_dashboard.html`
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html`
- `tests/integration/test_paid_plan_replacement.py`
- `tests/integration/test_plan_change_journey.py`
- `tests/unit/test_operational_issue_queue.py`
- `tests/browser/test_checkout_pay_button_e2e.py`
- `tests/browser/test_visual_fixes_e2e.py`
- `tests/unit/test_invariant_annual_only_plan_card.py`
- `tests/unit/test_invariant_public_footer_and_chrome.py`
- `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/REVIEW_LOGIC_1.md`
- `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/REVIEW_LOGIC_2.md`
- `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/fix_billing_r2_*.txt`
- `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/fix_billing_verification.txt`
- `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/fix_visual_*.txt`

## Tests I ran

| Command | Result |
|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_operational_issue_queue.py tests/unit/test_invariant_annual_only_plan_card.py -q -p no:randomly` | 28 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_paid_plan_replacement.py -q -p no:randomly` | 17 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_plan_change_journey.py -q -p no:randomly` | 28 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_billing_offers.py tests/unit/test_billing_entitlements.py -q -p no:randomly` | 57 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_checkout_and_payment_email.py -q -p no:randomly` | 63 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_system_brain_payments.py -q -p no:randomly` | 5 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_held_plan_renewal_words.py -q -p no:randomly` | 96 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_public_footer_and_chrome.py -q -p no:randomly` | 31 passed |
| `.venv\Scripts\python -m ruff check src tests scripts` | All checks passed |

No browser tests run; the visual tests need a live server (per CLAUDE.md).

---

## Fix 1 — R2-1 BLOCKER: critical alert lost on crypto moves (colons in event ids)

**What the fix is.** The queue boundary now sanitises every evidence ref. The
function `_sanitize_evidence_ref` (new at `issues.py:81-111`) splits the ref on
the first colon, validates the prefix with `_EVIDENCE_PREFIX_PATTERN`
(`issues.py:47`, `[a-z_]+`), replaces every disallowed character in the tail
with `-`, truncates to the 120-char tail the evidence pattern allows
(`_MAX_EVIDENCE_REF_LENGTH` = 134, `issues.py:57`), and only then runs the
existing `_EVIDENCE_REF_PATTERN.match` check (`issues.py:46`).

The dedupe key already accepts colons (`_DEDUPE_KEY_PATTERN = ^[a-z0-9][a-z0-9_.:/-]{0,159}$`,
`issues.py:45`), so the alert's `billing:plan-move-failed:{event_id}[:160]` key
is not changed. The truncation in `_plan_move_failure_key`
(`billing.py:2631-2635`) preserves colons in the key.

The complementary change in `_save_replacement_failure` is that the queue
itself no longer raises for the colon-bearing id, and the commit
(`billing.py:2723`) succeeds. `_save_resurrection_alert`
(`billing.py:2725-2759`) gets the same treatment for the resurrection case.

### (a) Does the fix close the WHOLE defect class?

Yes, for the alert-evidence path. Every caller of `record_occurrence` goes
through `_validate_payload` (`issues.py:151, 380-413`), so the sanitiser is
the single boundary, not a billing-only patch.

The defect class is "an evidence ref contains a character the queue pattern
forbids". The fix widens the accepted character set at the owner: any
disallowed character in the tail becomes `-`. The four callers that
historically produced non-conforming tails are:

- NOWPayments: `"id": f"nowpayments:{payment_id}:{status}"` (`billing.py:2372`).
  Two colons in the tail; sanitised to `nowpayments-…-…`.
- Static checkout (test/dev): `"id": f"static_checkout:{static_session}"`
  (`dashboard.py:4411`, unchanged). One colon; sanitised.
- Creem/Stripe event ids are ids without colons; sanitisation is a no-op.
- Operator-typed alerts route through `record_fired_alert`
  (`issues.py:222-247`), whose refs are constructed as
  `f"alert:{rule.name}"`, `f"runbook:{anchor}"`, `f"slo:{name}"`. No
  disallowed characters in any of those.

So the fix is at the right owner and covers all producers.

The new unit test `test_an_evidence_ref_with_provider_colons_is_sanitized_not_lost`
(`test_operational_issue_queue.py:267-292`) reproduces the failure-before shape
(`billing_event:nowpayments:90313222:finished`) and asserts the sanitised ref
plus that the pattern still matches. The companion parametrised test
`test_a_malformed_evidence_ref_is_still_refused` (`:294-323`) covers the four
shapes that genuinely cannot be made safe (empty, no colon, leading digit,
empty tail). The integration test
`test_crypto_paid_move_with_failed_cancel_leaves_alert_and_failed_event`
(`test_paid_plan_replacement.py:863-934`) drives the real shape that originally
broke: a NOWPayments `payment.finished` whose `id` carries two colons, with
cancel failing. It asserts the alert key, the failed event row, and the
sanitised `billing_event:nowpayments-np-finish-111-finished` evidence ref. All
three pass.

The `record_fired_alert` summary-length boundary is also fixed in passing
(`_MAX_SUMMARY_LENGTH = MAX_RECORD_VALUE_LENGTH` at `issues.py:53` and the new
`test_the_queue_and_the_content_check_hold_one_length_limit` test at
`:225-265`), so a 240-char alert can no longer slip past the queue and fail at
the content check.

### (b) Does any fix open a new route?

Checked. The sanitiser is lossless for shape (it only rewrites disallowed
characters) and preserves the prefix. It does not alter the dedupe key, so
two distinct failures with the same `event_id` still collapse into one alert
row as the queue has always done. The truncation can merge two distinct
event_ids at the 134-char boundary, but the same boundary exists for the
dedupe key (`[:160]`), and it is already exercised; the alert row count and
its `severity="critical"` state are unchanged. No new refund/charge/plan-lost
route.

**Verdict: closed.**

---

## Fix 2 — R2-2 SERIOUS: lapsed recurring card plan invisible

**What the fix is.** A 30-day grace window is added to the two replacement
queries:

- `paid_plan_codes_for_replacement_decisions` (`billing.py:758-799`), which is
  the helper `prepare_checkout` now reads at `billing.py:1741-1745`. The query
  OR-s in `(provider IN RECURRING_PROVIDERS) AND (cancel_at_period_end IS
  FALSE) AND (current_period_end >= now - 30d) AND (current_period_end <= now)`.
- `_the_one_live_paid_plan` (`plan_replacements.py:152-207`), which is what
  `source_for_checkout` and `replacement_refusal` read. Same grace clause
  (`plan_replacements.py:165-186`).

The constant `_RECURRING_LAPSED_GRACE_WINDOW` (or its billing-side alias
`RECURRING_LAPSED_GRACE_WINDOW`) is defined once at `billing.py:599` and once
at `plan_replacements.py:35`, both `timedelta(days=30)`. The recurrence
provider set is `RECURRING_PROVIDERS = frozenset({"creem", "stripe"})` at
`billing.py:594`, used identically in both queries.

`paid_subscription` (`plan_changes.py:239-268`) and `active_paid_plan_codes`
(`billing.py:724-755`) deliberately do NOT get the grace window — they
answer "what does the customer currently have access to", and a lapsed card
plan has no access. So the entitlement UI keeps showing "you have nothing"
when a lapse has happened, which is what a real customer sees.

The new integration test `test_lapsed_recurring_card_plan_is_frozen_and_cancelled_on_move`
(`test_paid_plan_replacement.py:798-860`) seeds `current_period_end = now - 1
day`, `cancel_at_period_end = False`, runs `_prepare_new_checkout`, and
asserts both that `attempt.replaces_subscription_id == old.id` (the freeze
that was previously `None`) and that the post-pay state is one live card
subscription, the old one CANCELED. The assertion that failed before the
fix is the `replaces_subscription_id` one; it now passes.

### (a) Does the fix close the WHOLE defect class?

Partly. The defect class is "the system doesn't see a card subscription whose
period just ended". The fix closes it for the two readers that decide
replacement: `prepare_checkout` (which freezes) and `_the_one_live_paid_plan`
(which the route calls when refusing `paid_amount_missing` / `paid_period_missing`
/ `multiple_paid_plans_need_help`, plus `apply_after_payment` which uses the
same one-owner). Both queries now share the grace clause, both go through the
same provider set and the same `current_period_end >= now - 30d AND <= now`
shape.

What it does NOT close is the page UI for a user whose only paid plan is
lapsed:

- `paid_subscription` (`plan_changes.py:239-268`) returns `None` because it
  uses `current_period_end > now` with no grace window.
- `switch_offers` (`plan_changes.py:304-336`) reads
  `current_code = held[1].code if held else None`, then `plan_change_kind` →
  `PLAN_CHANGE_NEW`, then `_switch_offer` (`plan_changes.py:338-429`) returns
  `action="buy"` with a Pay button for every plan.
- The page renders a live Pay button for the plan the user already holds
  (the lapsed one), even though `prepare_checkout` would refuse that click
  with `already_subscribed` (`billing.py:1746-1750`, because
  `paid_plan_codes_for_replacement_decisions` does include the lapsed plan).

This is a "page that disagrees with the server" — same shape as Q5 in the
prior reviews, only for the lapsed case. The original R2-2 finding was about
the silent double-charge; that is closed (the old card is frozen and
cancelled). The new failure is purely UI: a Pay button that, on click,
redirects back with `error=already_subscribed` for the held plan and
silently takes the new payment + cancels the old for a different plan. No
money defect, but a page/server mismatch the prior reviewers called "fails
closed" but only for `card_annual`. I rate it minor.

The fix also keeps the original `paid_amount_missing`/`paid_period_missing`/
`multiple_paid_plans_need_help` rejections working — they are reached via
`_the_one_live_paid_plan`, which is the function that received the grace
window. So a customer with a lapsed plan AND no completed payment is
correctly refused by the route and the page correctly hides Pay (the page
sees `holds_other = True` because the plan is still `ACTIVE` with a future
period in this case, and `_switch_offer`'s `replacement_refusal` branch
disables the button with the refusal sentence — verified by
`test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page` in
`test_plan_change_journey.py:1009-1085`).

### (b) Does any fix open a new route?

Checked. The grace window only widens the set of subscriptions considered for
replacement, never the set given access by entitlements. So an account with
two card subscriptions in the grace window (unlikely but possible) would hit
`multiple_paid_plans_need_help`, refuse, and alert — covered by the
parametrised test `test_a_move_that_cannot_be_finished_safely_changes_nothing_and_alerts`
(`test_paid_plan_replacement.py:432-528`). No new double-charge route:
`apply_after_payment` writes the same idempotency-keyed `PlanMoveMoneyOwed`
row, and the test `test_renewing_the_new_plan_never_ends_or_repays_the_old_one_again`
(`test_paid_plan_replacement.py:208-296`) verifies a 30-day-later renewal of
the new plan keeps one live card sub, exactly one cancel call, exactly one
money record, and one staff notice.

**Verdict: closed for the original silent double charge; small UI gap remains
for a lapsed-only account on the held plan itself (severity: minor).**

---

## Fix 3 — R2-3 SERIOUS: late renewal resurrects a cancelled subscription

**What the fix is.** A guard inside the one owner for upserting subscriptions.
`_upsert_subscription` (`billing.py:3222-3298`) loads the existing
subscription by `(provider, provider_subscription_id)`. If the existing row
is `CANCELED` or `EXPIRED`, the incoming status is `ACTIVE` or `TRIALING`,
and the event type is one of `_PAID_EVENT_TYPES`
(`billing.py:3213-3220`: `checkout.session.completed`,
`invoice.payment_succeeded`, `payment.finished`, `subscription.paid`), the
function raises `BillingError("subscription_resurrected_after_cancel", …)`
before mutating any field. The exception is caught in `process_event`
(`billing.py:2474-2493`) and in `reprocess_failed_event`
(`billing.py:2556-2575`), both of which call `_save_resurrection_alert`
(`billing.py:2725-2759`) — which marks the event `failed`, writes a
critical `subscription-resurrected:{event_id}` alert, and commits.

### (a) Does the fix close the WHOLE defect class?

Mostly. The guard is in the only writer (`_upsert_subscription` is the single
sink for subscription rows from a webhook), and the four paid event types are
exactly the four that move money (matching `_is_completed_payment_event` at
`billing.py:3183-3209`). All four call `_upsert_subscription` from
`_apply_event` (`billing.py:2804-2845`).

The new integration test `test_paid_event_for_replaced_subscription_is_refused_and_alerted`
(`test_paid_plan_replacement.py:728-796`) exercises the whole path end-to-end:
a `subscription.paid` for the OLD trader subscription id after a move. The
test asserts (a) `BillingError` raised with the right code, (b) the old row
stays `CANCELED`, (c) the `BillingEvent` row is recorded as `failed`, and
(d) a critical `OperationalIssue` with the right dedupe key exists. All
four pass.

A second test, `test_a_move_that_cannot_be_finished_safely_changes_nothing_and_alerts`,
asserts the alert is the only thing written when a move is refused for
`multiple_paid_plans_need_help` or `paid_amount_missing`. The plan-replacement
class is now uniform: every PlanMoveFailed subclass writes one alert.

What is NOT covered: `subscription.trialing`, `subscription.created`,
`subscription.updated`, `customer.subscription.created`,
`customer.subscription.updated` against an existing `CANCELED` row. These
event types are not in `_PAID_EVENT_TYPES`, so the guard does not fire. A
late `subscription.trialing` for a `CANCELED` subscription would set status
to `TRIALING` and revive the row. The provider-side likelihood is low
(Creem does not send trial events after a cancellation) and the
TrialLifecycleService is the consumer, but it is a theoretical gap. I rate it
minor; the prior finding was about paid-event resurrection, and that is
closed.

### (b) Does any fix open a new route?

Checked. The guard raises BEFORE `subscription.user_id = user_id` and
`subscription.status = status` are assigned (`billing.py:3286-3297`), so the
loaded `CANCELED` row is left untouched on disk. `process_event` then marks
the event `failed` and the caller returns a non-2xx, which the provider will
retry. On retry, `process_event` sees the existing failed event row at
`billing.py:2424-2436` and dispatches to `reprocess_failed_event`, which
re-runs the same guard — so the row stays `CANCELED` indefinitely, the alert
stays open, and a person sees the same critical issue until they reconcile.
No plan that was paid for becomes invisible, and no plan that the customer
doesn't hold is granted.

`apply_after_payment` (`plan_replacements.py:243-408`) also sees the
CANCELED status and short-circuits to the "earlier" lookup
(`plan_replacements.py:273-300`) — so a resurrection attempt via the move
path is harmless too.

**Verdict: closed for the reported paid-event class. Minor residual for
non-paid event types (subscription.trialing et al.).**

---

## Fix 4 — R2-6 MINOR: cancellation webhook downgrades a completed attempt

**What the fix is.** In `_record_checkout_event` (`billing.py:3141-3181`),
the failed-event branch is narrowed:

```python
elif event_type in {"invoice.payment_failed", "payment.failed",
                     "payment.expired", "payment.partially_paid",
                     "payment.refunded"}:
    if current_status in {"creating", "pending", "processing"}:
        attempt.status = event_type.split(".", 1)[1]
        attempt.last_error = event_type
elif current_status in {"creating", "pending"}:
    attempt.status = "processing"
```

The `else` branch that used to unconditionally set `processing` is gone. A
`subscription.canceled` event for a `completed` attempt now falls through
with no change, because none of the elif branches match.

The new integration test `test_canceled_event_does_not_downgrade_completed_attempt`
(`test_paid_plan_replacement.py:670-726`) drives a `subscription.canceled`
webhook that still carries the old checkout's metadata, and asserts
`attempt.status == "completed"` afterward. The failure-before text
`assert 'processing' == 'completed'` matches the diff, and the test now
passes.

### (a) Does the fix close the WHOLE defect class?

Yes. The fix is in the only writer for `attempt.status` from a webhook
(`_record_checkout_event`); the same function is reached from both
`process_event` and `reprocess_failed_event`. The class of defect — "a
non-payment event demotes a settled attempt" — is closed for the four
failure events AND for `subscription.canceled`, `subscription.deleted`, etc.
(a `completed` attempt hits the final elif but `current_status` is not in
`{"creating", "pending"}`, so no change). The same path is used by
`payment.failed` for an in-flight attempt, and that path still sets
`payment.failed` correctly when the attempt was not yet `completed`.

### (b) Does any fix open a new route?

Checked. The narrowed check is `current_status in {creating, pending,
processing}`, which is the same set that the old code implicitly overwrote —
a `failed` or `completed` attempt is now left alone, but a `failed` attempt
was already `failed` so no change. The `completed` row stays the record of
money that moved, so `payment_that_bought`
(`plan_replacements.py:114-135`) keeps finding it, and the money-owed path
keeps working for the new plan. The `paid_access_can_be_repriced` reading is
unchanged. No double refund, no extra charge.

**Verdict: closed.**

---

## Fix 5 — Visual: footer legal text under the corner button (1024 / 390)

**What the fix is.** Two CSS edits in `hilalmarkets-public.css`:

- `@media (max-width: 1024px) { .hm-jinja-footer .hm-footer-bottom { padding-right: 90px; } }`
  (`:308-312`). The bottom band of the public footer reserves a 90px column on
  the right at every width ≤ 1024px, so the wrapped legal text stops at
  `viewport_width - 90` and cannot collide with the assistant's 56px-wide
  launcher at `right: 22` (which occupies the strip 22..78 from the right).
  At 1440 the original `min(...)` from the prototype grid already produced
  text that cleared the launcher, and the test docstring confirms this was
  the case before.
- `.hm-to-top { bottom: 128px; }` (formerly `90px`, `:338`). The back-to-top
  button now sits above the assistant's `Ask AI` label (label height ≈ 29px
  with 10px of air above the 56px button at `bottom:22`, plus 11px of
  breathing room — total `22 + 56 + 10 + 29 + 11 = 128`), as the comment
  spells out at `:320-330`. `right: 26` keeps it centred over the launcher
  circle.

A second set of CSS edits covers the checkout/billing-side defects:

- `hilalmarkets.css:288` — `.offer-countdown { margin: 10px 0 18px }`
  (was `margin: -8px 0 18px`). Removes the negative top margin that was
  pulling the countdown box up over the launch-price paragraph on the
  review page.
- `hilalmarkets.css:319-326` and `hilalmarkets-dashboard-v2.css:2607-2627,
  2629-2655` — replaces `grid-template-columns: auto 1fr` (and
  `28px minmax(0,1fr)`) with `auto minmax(min-content,1fr)` (and
  `28px minmax(min-content,1fr)`). Adds explicit grid placement
  (`grid-column:2; grid-row:1/2`) for the label and note so auto-placement
  cannot drop the `<small>` note into the 28px icon column. Uses
  `overflow-wrap: break-word` rather than `anywhere` (which would shrink
  the column to one character and break words mid-character).
- `hilalmarkets-dashboard-v2.css:2053-2058, 2660-2670, 2761-2765` — replaces
  `opacity: 0.55` on unavailable cards/intervals with a dashed border +
  `background: var(--hm-surface-soft)` (cards) or just removes the opacity
  (intervals). The `0.55` opacity on a 4.5:1-ratio colour had dropped the
  words to ~2.2:1, below WCAG AA. The new contrast check is
  `_assert_unavailable_words_are_readable` in `test_checkout_pay_button_e2e.py`,
  which uses `tests/support/contrast.py` (the one owner of this sum) to
  measure alpha against the first solid ground and assert ≥ 4.5:1.

### (a) Does the fix close the WHOLE defect class?

Yes for the reported defects. The four reproducers from `fix_visual_new_tests_fail_before.txt`
are covered by:

- `tests/browser/test_visual_fixes_e2e.py:111-140` —
  `test_landing_footer_legal_text_clears_the_ai_launcher`, parametrised over
  the three named viewports (1440 / 1024 / 390). It measures every line of
  every `.hm-footer-bottom p` and asserts no rectangle intersects either the
  launcher or its label, catching the "wrapped line runs underneath" case
  even when the paragraph's overall box is wider than the launcher column.
- `tests/browser/test_checkout_pay_button_e2e.py:920-…` —
  `test_review_page_method_notes_wrap_at_word_boundaries`, parametrised over
  card and crypto notes at all three viewports. Asserts `clientWidth >=
  maxWordWidth` and `lineCount <= wordCount`, so the
  "Han dled by a test pay men t pag e" mid-word breakage is caught.
- `tests/browser/test_checkout_pay_button_e2e.py:…` —
  `test_review_page_launch_price_does_not_overlap_countdown`, asserts
  `price.bottom <= countdown.top + 1` at 1440x900.

I did not run the browser tests (per the operating rule "NEVER run the full
tests/browser suite"), but `_assert_unavailable_words_are_readable` is now
called from `_take_screenshots` (`:212`), which every screenshot test uses,
so the contrast check fires on the full checkout/billing E2E suite
(`test_checkout_pay_button_e2e.py`). The class of "wrong opacity on
unavailable cards" is therefore gated by a measurement, not by eye.

Coverage gap (minor): the visual fix CSS uses `max-width: 1024px`. The test
parametrises 1440/1024/390 only. Viewports between 1024 and 1440 (e.g.
1280) are not asserted; the original CSS already cleared the corner above
1024 in the failing-evidence docstring, so this is regression-only coverage
and the underlying fix is unconditional. Minor.

### (b) Does any fix open a new route?

Checked. The CSS edits are presentational only — they change layout, not
what the server returns. The page-server interface is unchanged. There is
no charge/refund/plan change involved in the visual surface.

**Verdict: closed.**

---

## New-route analysis across all five fixes

I checked every fix for the four routes the prompt named:

1. **Double charge.** No new double-charge route. The grace-window fix
   (`paid_plan_codes_for_replacement_decisions`,
   `_the_one_live_paid_plan`) actively prevents the silent double charge of
   R2-2 by freezing the lapsed card onto the new checkout. The resurrection
   guard prevents a paid-event for a `CANCELED` row from re-creating a live
   subscription. R2-6 leaves the settled attempt intact. R2-1 commits the
   alert before any rollback, so the customer is never charged without
   leaving a visible record on our side.
2. **Paid customer with no plan.** No new route. The resurrection guard
   raises before `subscription.status = status` is assigned, so the row
   stays `CANCELED`. `process_event` marks the event `failed` and the
   webhook answers non-2xx, which the provider retries — but every retry
   ends the same way, and the alert stays open until a person reconciles.
3. **Double or oversized refund.** No new route. The visual fix is
   presentational. R2-1 fixes the alert write path, not the refund path.
   The `_plan_move_failure_key` truncation to 160 chars can collapse two
   different event_ids into one alert row at the truncation boundary, but
   the alert row already did that; no refund is initiated by the alert.
4. **Page that disagrees with the server.** One residual, noted in Fix 2:
   the page offers a Pay button for the lapsed plan itself, and the
   checkout route refuses with `already_subscribed`. No money impact; the
   click refuses and the page displays the refusal via the dashboard error
   notice. Severity: minor.

---

## Findings table

| # | Severity | Where | What | Proof |
|---|---|---|---|---|
| F1 | false alarm | n/a | R2-1 fix is at the one owner (`_sanitize_evidence_ref` in `issues.py`), covers all event-id producers (NOWPayments, static, Creem/Stripe no-op, operator alerts no-op), and the malformed-ref test suite keeps raising for unrecoverable refs | `issues.py:81-111, 47-48, 57`; `test_operational_issue_queue.py:267-323`; `test_paid_plan_replacement.py:863-934` |
| F2 | minor | `src/ai_market_monitor/services/plan_changes.py:239-268`, `:304-336`, `:338-429`; `src/ai_market_monitor/templates/hilal/dashboard/billing.html:158-201` | R2-2 fix closes the silent double charge, but the page offers a Pay button for a lapsed plan the user already holds; clicking it redirects with `error=already_subscribed`. No money impact. | `paid_subscription` excludes lapsed plans (`plan_changes.py:259-260`); `switch_offers` → `current_code=None` → `kind=PLAN_CHANGE_NEW` → `_switch_offer` returns `action="buy"` (`plan_changes.py:399-402`); `prepare_checkout` raises `already_subscribed` (`billing.py:1746-1750`). The `paid_amount_missing` test (`test_plan_change_journey.py:1009-1085`) does not seed a lapsed plan. |
| F3 | false alarm | n/a | R2-3 fix is in the one owner (`_upsert_subscription`), the four paid event types are exactly the four that move money (matching `_is_completed_payment_event`), and a complete end-to-end test reproduces the resurrection attempt | `billing.py:3213-3220, 3264-3285`; `test_paid_plan_replacement.py:728-796` |
| F4 | minor | `billing.py:3213-3220, 3264-3285` | The paid-event guard does not fire for `subscription.trialing`, `subscription.created`, `subscription.updated`, `customer.subscription.created`, `customer.subscription.updated`. A late `subscription.trialing` for a `CANCELED` row would set status to `TRIALING` and resurrect it. Provider likelihood is low; out of scope for the original paid-event finding | `_PAID_EVENT_TYPES` is the only set checked; the test covers only `subscription.paid` (`test_paid_plan_replacement.py:728-796`) |
| F5 | false alarm | n/a | R2-6 fix is in the only writer (`_record_checkout_event`); a `completed` attempt is left alone by both the failure branch (not in the elif) and the fallback branch (`current_status` not in `{"creating", "pending"}`); the integration test reproduces the original failure-before shape | `billing.py:3141-3181`; `test_paid_plan_replacement.py:670-726` |
| F6 | minor | `tests/browser/test_visual_fixes_e2e.py:111-140` | Visual fix is asserted at 1440/1024/390 only; viewports between 1024 and 1440 are not tested by this file (regression-only coverage) | test parametrisation in `test_visual_fixes_e2e.py:21-23`; CSS uses `max-width: 1024px` in `hilalmarkets-public.css:308` |
| F7 | false alarm | n/a | Visual fixes are presentational; no server interface changes; no charge/refund/plan route touched | `hilalmarkets.css:288, 319-326`; `hilalmarkets-dashboard-v2.css:2053-2058, 2607-2670, 2761-2765`; `hilalmarkets-public.css:308-312, 338` |
| F8 | false alarm | n/a | No new route to a double charge, paid-without-plan, refund, or page/server disagreement introduced by any of the five fixes. The one residual UI gap (F2) is the page offering Pay for a lapsed plan, and it refuses correctly. | traced above per fix |

Counts: 0 blocker, 0 serious, 4 minor, 4 false alarm.

---

## Per-fix verdict

| Fix | Verdict |
|---|---|
| 1 (R2-1, colon sanitiser) | **closed** |
| 2 (R2-2, lapsed card grace window) | **partly closed** — money path closed; one minor UI gap remains for a lapsed-only account |
| 3 (R2-3, resurrection guard) | **closed** for the paid-event class; minor residual for non-paid event types |
| 4 (R2-6, attempt downgrade) | **closed** |
| 5 (visual: footer / countdown / method note / contrast) | **closed** |

---

## What I verified and what I did not

- Verified: every named test file passes against the working tree; every
  `file:line` claim above is grounded; the evidence-before files in
  `.hm-orchestrator/runs/20260912T152825Z-d6d10e2a/` are consistent with the
  diff; `ruff check src tests scripts` is clean.
- Not verified: live provider behaviour (NOWPayments metadata shape, Creem
  `subscription.canceled` metadata carrying `checkout_attempt_id`,
  re-subscribe id behaviour). R2-2 and R2-3 carry that uncertainty; F2 and
  F4 are consequences of it.
- Did not run: the browser suite (CLAUDE.md forbids the full `tests/browser`
  suite); the full test tree (also forbidden).
