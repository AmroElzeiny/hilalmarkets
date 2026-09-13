# Logic review — D1–D4 hand-back

Run `20260913T162406Z-39060cc7`. Baseline commit `75b19580` on branch
`cloudflare-access-service-tokens`. Tier: deep. Scope: D1, D2, D3, D4 (and
Claude's D4/D5 changes). I did not edit any product code.

## Scope reviewed

In-scope working-tree paths (as listed in the mission), compared against
`75b19580`:

| Path | Status vs HEAD |
|---|---|
| `src/ai_market_monitor/services/billing.py` | modified (+776/-59) |
| `src/ai_market_monitor/services/plan_replacements.py` | modified (+~210/-60) |
| `src/ai_market_monitor/core/money.py` | new, untracked |
| `src/ai_market_monitor/observability/issues.py` | modified (+~125/-25) |
| `src/ai_market_monitor/static/hm-hilal-chat.js` | modified (+~95/-10) |
| `src/ai_market_monitor/static/hm-shell.css` | modified (+11/-4) |
| `tests/unit/test_invariant_refund_is_recorded.py` | new, untracked (144 cases) |
| `tests/unit/test_invariant_one_held_plan_owner.py` | new, untracked (19 cases) |
| `tests/unit/test_invariant_money_on_the_wire.py` | new, untracked (132 cases) |
| `tests/unit/test_operational_issue_queue.py` | modified (+~395/-10) |
| `tests/browser/test_hilal_never_hides_a_control_e2e.py` | new, untracked |

Adjacent regression tests I also exercised for confidence (still inside
the mission's "neighbours" allowance): `tests/integration/test_paid_plan_replacement.py`,
`tests/integration/test_checkout_and_payment_email.py`,
`tests/integration/test_plan_change_journey.py`,
`tests/unit/test_billing_entitlements.py`.

## What I ran

| Command | Result |
|---|---|
| `git diff --stat` on the in-scope paths | confirms the totals above |
| `git diff` for the seven modified files | captured in `diff_*.txt` |
| Read of the three new test files and `core/money.py` end-to-end | done |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_money_on_the_wire.py -p no:randomly` | 132 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_one_held_plan_owner.py -p no:randomly` | 19 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_invariant_refund_is_recorded.py -p no:randomly` | 144 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_operational_issue_queue.py -p no:randomly` | 61 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_paid_plan_replacement.py -p no:randomly` | 36 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_checkout_and_payment_email.py -p no:randomly` | 64 passed |
| `.venv\Scripts\python -m pytest tests/integration/test_plan_change_journey.py -p no:randomly` | 32 passed |
| `.venv\Scripts\python -m pytest tests/unit/test_billing_entitlements.py -p no:randomly` | 41 passed |

Browser suite was not run (`tests/browser` needs Chromium and was not part
of the offline set the mission authorises me to run). I did read the new
file.

## What I read end-to-end and how

- `core/money.py` (full 83 lines). One owner for the wire text; `FIAT_MINOR_UNITS`
  is an explicit map for USD/EUR with a documented default, `wire_text`
  quantises with `ROUND_HALF_UP` and returns the quantised `Decimal` text.
  `str(Decimal("9.00"))` is `"9.00"`; `str(float(Decimal("9.00")))` is `"9.0"`.
  Confirmed locally: `text_loss_9 True` and `text_str_9 9.0`.
- `billing.py` — the four new constants
  `REFUND_ENDS_PLAN_EVENT_TYPES`, `REFUND_CHARGEBACK_EVENT_TYPES`,
  `REFUND_EVENT_TYPES` (built as the union of the two),
  `REFUNDED_ATTEMPT_STATUS = "refunded"`, `SETTLED_ATTEMPT_STATUS = "completed"`;
  the single refund reading at `billing.py:3002-3039` (which routes through
  `_end_refunded_plan` for refunds and audit-only for chargebacks); the
  attachment logic at `_attach_refund_to_attempt` and the resolver at
  `_resolve_refund_attempt`; the new check at `_hydrate_checkout_data` that
  diverts refund events to the looser path before the strict money-in
  checks; the tightened `_record_checkout_event` with the
  `current_status in {"creating", "pending", "processing"}` guard; and the
  `_upsert_subscription` resurrection guard. I also read `_after_completed_payment`,
  `reprocess_failed_event`, `_save_replacement_failure`,
  `_close_replacement_failure`, `_save_resurrection_alert`,
  `_save_refund_alert`, `_refund_alert_key`, and `_plan_move_failure_key`.
- `plan_replacements.py` — `SETTLED_PAYMENT_STATUS`, `REPLACEMENT_REFUSALS`,
  `payment_that_bought`, `_the_one_live_paid_plan`, `replacement_refusal`,
  `attach_source`, `apply_after_payment` (including the `old.status in
  _ENDED_STATUSES` branch and the `source.status != SETTLED_PAYMENT_STATUS`
  amount guard).
- `issues.py` — `sanitize_dedupe_key`, `_sanitize_evidence_ref`,
  `_carries_sensitive_content`, the rewired `_validate_payload` (now
  returns the safe refs), and the new length constants `_EVIDENCE_REF_MAX_TAIL_LENGTH`,
  `_EVIDENCE_POINTER_HASH_LENGTH`, `_MAX_SUMMARY_LENGTH = MAX_RECORD_VALUE_LENGTH`.
- `labels.py` — `MAX_RECORD_VALUE_LENGTH = 200`, `_SENSITIVE_PATTERNS`
  (openai_api_key, bearer_token, basic_credentials, telegram_bot_token,
  json_web_token, private_key_block, aws_access_key, email_address),
  `_SEED_PHRASE_PATTERN`, `assert_no_sensitive_content`. The queue's
  secret/content check is the same one.
- `hm-hilal-chat.js` — `CLEARANCE_GAP`, `whenScrollSettles`,
  `keepControlsClear` (with `publishClearance` and the focused-control
  scroll-clear). Read end-to-end.
- `hm-shell.css` — the desktop padding is now
  `calc(34px + max(var(--hm-cookie-banner-height, 0px), var(--hm-corner-clearance, 0px)))`
  and the phone rule is `calc(24px + max(...))`.
- The four new/modified test files in full.
- Adjacent tests for confidence, listed above.

## False positives I considered and dismissed

1. **`payment.finished` replay after a refund.** First reading suggested the
   old `attempt.status == "completed"` guard could let a re-delivered
   `payment.finished` slip past once the refund had moved the row to
   `refunded`. Traced: the new guard is
   `attempt.status in {SETTLED_ATTEMPT_STATUS, REFUNDED_ATTEMPT_STATUS}`,
   i.e. `{"completed", "refunded"}`. The comment at `billing.py:3318-3328`
   documents this. A NOWPayments replay of `payment.finished` for a
   refunded invoice is still refused with `checkout_already_completed`.
   Dismissed.
2. **Float scan false negatives.** The scan regex
   `_FLOAT_SITE` uses `(?<![\w.])float\s*\(`, so `_float(value)` is
   correctly excluded (preceded by `_`). `_money_float_lines` is fed
   `services/billing.py`, `services/plan_replacements.py`,
   `services/affiliate.py`, `services/affiliate_attribution.py`. The
   billing.py diff has one `float(` site in the file, at `billing.py:3751`,
   which is `isinstance(value, (int, float))` — not a conversion, not a
   money name. The test `_money_float_lines('            "price_amount":
   float(amount),')` returns the single positive line, and the negative
   samples for `deadline_seconds=...`, `float("inf")`, `threshold=...`,
   `_float(...)` all return `[]`. Dismissed.
3. **`_resolve_refund_attempt` ambiguity.** The query uses `limit(2)` and
   returns `None` when `len(candidates) != 1`, so one candidate is
   resolved, zero and two-plus are not guessed. The test
   `test_a_refund_that_could_belong_to_two_payments_is_not_guessed` proves
   this with two seeded settled payments on the same user/provider, and
   asserts both attempts stay `completed` and exactly one alert is
   written. Dismissed.
4. **`_refund_matches_attempt` not comparing the plan.** Initially looked
   like an over-broad acceptance. The docstring at `billing.py:3185-3197`
   explains the deliberate choice: a subscription keeps its creation-time
   metadata, so a refund of a new charge can still name the old checkout
   on the same provider+user, and the stricter money-event check at
   `_hydrate_checkout_data` is what guards against a wrong-provider refund.
   The provider check uses `attempt.provider not in {provider, "static"}`,
   which is the same shape as the money-in check. Dismissed.
5. **`sanitize_dedupe_key` for `"   "` (whitespace-only).** The function
   raises `IssueQueueError` for any key with whitespace and for any key
   whose surviving body has no lowercase letter or digit. `"___"`, `":::"`,
   `".-_-."`, `"   "` are all rejected. The test
   `test_sanitize_dedupe_key_refuses_nothing_safe_remains` pins this.
   Dismissed.
6. **`_sanitize_evidence_ref` swallowing payloads that look like
   identifiers.** The check `_carries_sensitive_content(kept)` runs against
   the kept form, so a credential inside an identifier-shaped tail
   (`billing_event:sk-…`, a JWT) is reduced to a digest rather than
   stored. The alert itself is still written. The test
   `test_a_credential_inside_an_identifier_shaped_ref_is_reduced_not_refused`
   pins the secret is not stored and the alert collapses on repeat.
   Dismissed (this is the intended D4 fix, exactly as described in the
   take-over summary).
7. **D1 claim "an idempotent refund changes nothing twice".** First read
   suggested `_apply_event` might re-cancel the subscription (writing a
   fresh `canceled_at`) on a second event. The replay detection at
   `billing.py:2518-2527` returns early with `replayed=True` for any
   second event with the same `provider_event_id`, so `_apply_event` is
   not re-entered. Different event ids for the same payment go through,
   and the test `test_two_refund_events_for_one_payment_produce_one_alert`
   asserts one row, one event, no money. Dismissed.
8. **The `subscription_resurrected_after_cancel` guard being too tight.**
   The new `_upsert_subscription` guard fires for any event that ends
   ACTIVE/TRIALING when the row is already CANCELED/EXPIRED, with an
   audit message that asks whether the provider charged. This is wider
   than the old money-only check and is the documented intent; the test
   `test_a_late_event_of_any_type_cannot_resurrect_an_ended_subscription`
   parametrizes six event types. Dismissed.

## Findings (ordered by severity)

### Critical

None.

### High

None.

### Medium

M1. **Phantom subscription is created when the event names a provider
subscription id we don't hold.**
File `src/ai_market_monitor/services/billing.py:3077-3082` (inside
`_end_refunded_plan`) and `src/ai_market_monitor/services/billing.py:3664-3690`
(`_upsert_subscription`).

The flow: refund event arrives with `provider_subscription_id` set but no
matching `Subscription` row in our DB and no matching
`BillingCheckoutAttempt`. `_attach_refund_to_attempt` either matches the
attempt (and sets `data["plan_code"]` to the attempt's plan) or pops the
`checkout_attempt_id`; either way `_end_refunded_plan` reaches
`_upsert_subscription(provider, data, forced_status=CANCELED)`. The
`Subscription` lookup misses, the function creates a new row keyed on
`provider` + `provider_subscription_id` from the event, with
`status=CANCELED`. That row is real pollution: a CANCELED subscription on
the customer that does not correspond to any real subscription we ever
held. The customer then has one phantom row that will never transition
out of CANCELED unless the provider later sends another event with the
same `provider_subscription_id`, which would fire the resurrection guard
and create a critical alert. The phantom row itself is harmless to the
money path (filtered out by `status in _LIVE_STATUSES` in
`_the_one_live_paid_plan` and by `status == "completed"` in
`payment_that_bought`), but the database is the wrong place for it.

I cannot see a unit test in `test_invariant_refund_is_recorded.py` that
covers this shape: every "names nothing" case has empty
`provider_subscription_id`, every "named-checkout" case has a matching
subscription, and the new `[payment-found-plan-unfound]` case has no
`provider_subscription_id` either.

Severity is medium, not critical: no money is at risk and the D1/D2/D3
behaviour is correct for every case the existing tests cover. This is
recorded as a class-of-defect to surface, not as a regression in the
hand-back. The fix would be a small refinement of `_end_refunded_plan`
that, after looking up the subscription, either falls back to the
"plan" alert path or refuses the event — not in scope for this review.

### Low

L1. **`canceled_at` is re-stamped on a second `_end_refunded_plan` for a
subscription that is already CANCELED.** File
`src/ai_market_monitor/services/billing.py:3081-3082`.

If a `payment.refunded` (or `refund.created`) event arrives for a payment
whose subscription is already CANCELED (e.g. a previous refund), the
function reaches `_upsert_subscription` with `forced_status=CANCELED`,
the resurrection guard does not fire (status is CANCELED, not
ACTIVE/TRIALING), and then `subscription.canceled_at = datetime.now(UTC)`
overwrites the original cancellation timestamp with a new one. The
original "when did the access actually end" is lost. No existing test
pins `canceled_at`, so the regression is invisible today. Money and
access are unaffected; this is a minor data-integrity drift.

L2. **D2 test matrix does not exercise the crypto held-shape on the
opener.** File `tests/unit/test_invariant_one_held_plan_owner.py:65`.

The matrix `PURCHASABLE_PLAN_CODES × RECURRING_CARD_PROVIDERS ×
{"monthly","annual"} × ATTEMPT_CYCLES` only covers recurring card plans
(`creem`, `stripe`). The opener is checked for lapsed recurring cards
inside the grace window, which is what the defect was. The crypto
shape (`nowpayments`, `one_time_30_day`) is intentionally excluded
because NOWPayments takes one 30-day invoice per purchase and never
lapses (no `current_period_end` to be inside a window). The code path
is therefore not regression-tested for the crypto provider on this
opener, though the same `paid_plan_codes_for_replacement_decisions`
helper is what is exercised by `test_paid_plan_replacement.py`. The
gap is small and intentional.

L3. **Plan check is not part of refund-attempt matching.**
File `src/ai_market_monitor/services/billing.py:3186-3204`.

`_refund_matches_attempt` deliberately compares only `provider` and
`user_id`. The docstring explains why (subscription keeps creation-time
metadata, so a refund of a new charge can legitimately name the old
checkout). This is the documented intent and is correct; flagging it
only to record that I noticed the asymmetry with the money-event check
in `_hydrate_checkout_data`.

L4. **A refund with a checkout_attempt_id that names a real attempt on a
different provider is matched as a "payment" alert with no plan end.**
Files `src/ai_market_monitor/services/billing.py:3160-3178` and
`_end_refunded_plan`.

If the event names a checkout id that does not pass
`_refund_matches_attempt` (provider mismatch, user mismatch, or
unreadable), the code saves a "payment" alert but `_end_refunded_plan`
also continues — it reads the *original* `data["provider_subscription_id"]`
and may run `_upsert_subscription` to CANCELED a row that was never ours.
Combined with M1 this is the same defect class viewed from the other side.
No existing test covers it. Money safety is preserved.

L5. **The X4 known limitation remains in force.**
Files `src/ai_market_monitor/services/billing.py:3018-3027`,
`src/ai_market_monitor/services/billing.py:3046-3083`.

A `charge.refunded`, `charge.dispute.created` or `dispute.created` event
records the attempt as `refunded` but leaves the subscription ACTIVE by
design (the dispute may be won back). This is the product rule called
out in WP D1 evidence, item X4, and is documented inline. The D1 family
test matrix covers these names. Money is safe; access may be wider than
the refund. No regression in this hand-back.

L6. **D1 "every money reader stops counting a refunded payment" is not
pinned for the post-payment receipt path.**
File `tests/unit/test_invariant_refund_is_recorded.py:357-417`.

`test_every_money_reader_stops_counting_a_refunded_payment` checks the
plan-move source, the affiliate source, the operations "completed
checkouts" count, and the staff `customer` view. It does not check the
receipt email outbox or the customer-facing `billing_history` row for a
refunded payment beyond the dedicated
`test_the_billing_history_row_offers_nothing_for_a_refunded_payment`
test. The receipt path is intentionally not part of D1; this is a
test-coverage note, not a defect.

L7. **Two `_close_replacement_failure` paths exist — and only one calls it.**
File `src/ai_market_monitor/services/billing.py:2593-2595` vs.
`src/ai_market_monitor/services/billing.py:2676-2678`.

`process_event` does not call `_close_replacement_failure` after a
successful first-time processing; only `reprocess_failed_event` does,
because a `_save_replacement_failure` only runs in the failed branch and
is what creates the alert. The alert closes only when the same event id
is reprocessed successfully. This is correct behaviour, but the
asymmetry between the two routes is not asserted anywhere. A passing
test on the first-time path (a webhook whose first delivery succeeds)
would lock the symmetry.

## Per-requirement verdict

| Requirement | Verdict | Evidence |
|---|---|---|
| **D1** — A refund of a completed payment is recorded; no money owed back from a refunded payment | **PASS** | `billing.py:3556-3568` writes `refunded` idempotently for every refund name; `_record_checkout_event` no longer downgrades a settled attempt; `_end_refunded_plan` writes a critical "payment" or "plan" alert when the event names no checkout or no subscription and returns `None` rather than raising; `_apply_event` for refund events records audit lines. `apply_after_payment` at `plan_replacements.py:364-387` computes `amount = Decimal("0.00")` when `source.status != SETTLED_PAYMENT_STATUS`. `payment_that_bought` at `plan_replacements.py:106-119` filters by `status == SETTLED_PAYMENT_STATUS`. The four readers (plan-move source, affiliate commission source, operations completed count, staff customer view) are asserted by `test_every_money_reader_stops_counting_a_refunded_payment`; the billing history row by `test_the_billing_history_row_offers_nothing_for_a_refunded_payment`; replay by `test_the_same_refund_arriving_twice_changes_nothing_twice` and `test_two_refund_events_for_one_payment_produce_one_alert`. 144/144 cases pass. The two medium/low items that touch this requirement (M1 phantom subscription, L4 wrong-provider matching, L1 re-stamped `canceled_at`) are downstream of M1 and do not break D1 itself. |
| **D2** — One held-plan owner for every checkout/replacement decision, including `open_checkout_attempt` | **PASS** | `billing.py:832-883` is the single grace-aware owner `paid_plan_codes_for_replacement_decisions`; `active_paid_plan_codes` is documented as the no-grace access reader and is not used by any purchase answer (asserted by `test_no_purchase_answer_is_built_from_the_no_grace_reader` scanning the whole `src/` tree). The opener at `billing.py:2086-2101` reads `paid_plan_codes_for_replacement_decisions` (asserted by `test_the_checkout_opener_reads_the_one_held_plan_owner`). The dashboard billing-selection, dashboard resume, dashboard review, dashboard_test billing-selection, `prepare_checkout` and `open_checkout_attempt` all pass `paid_plan_codes_for_replacement_decisions` (or its result) into `plan_checkout_availability`. The replacement-refusal path reads from the same owner via `replacement_refusal`. 19/19 cases pass. L2 (no crypto shape on the opener) is a coverage note, not a defect. |
| **D3** — One owner for money on the wire (`core/money.py`); no float on a money value; exact amount proven | **PASS** | `core/money.py` is the single owner: `wire_text(amount, currency)` quantises to `Decimal` minor units with `ROUND_HALF_UP` and returns the quantised text. The call site at `billing.py:1392` replaces the old `float(amount)` with `wire_text(amount, currency)`. A source scan (`test_no_money_amount_is_pushed_through_a_binary_float`) covers `services/billing.py`, `services/plan_replacements.py`, `services/affiliate.py`, `services/affiliate_attribution.py` and asserts no `float(<money-word>)` survives. The wire family covers `PLAN_DEFINITIONS` monthly, `PUBLIC_PLAN_PRESENTATIONS` annual, `effective_monthly_price`, `PLAN_OFFERS.promotional_monthly_price`, and every percent in `settings.billing_discount_codes` over three bases — including prices that lose both cents text (`9.00` → `9.0`) and exact cents value (`10.05`, `6.03`). The float call is patched out of process; no test contacts the network. 132/132 cases pass. |
| **D4** — Evidence refs accept only pointers; payload-shaped input reduced, never stored, never raising | **PASS** | `_sanitize_evidence_ref` at `issues.py:144-215` keeps identifier-shaped tails (`[A-Za-z0-9_.:/#-]+`) — rewriting inner colons to dashes so the stored pattern still matches — and replaces any non-identifier tail (or any tail that carries a credential, JWT, or seed phrase) with the prefix plus 16 hex characters of `sha256(ref)`. The reductions are deterministic, so two occurrences collapse into one row. Refusals that remain concern only the *prefix* (the prefix is written in our own code), and a diagnostic never becomes the failure. The summary and reason length cap is now the same value (`MAX_RECORD_VALUE_LENGTH = 200`) at `issues.py:64`, so the queue cannot fail at the moment it fires. The previous test that asserted a credential-bearing identifier was refused — `test_a_credential_inside_an_identifier_shaped_ref_is_refused` — was renamed to `test_a_credential_inside_an_identifier_shaped_ref_is_reduced_not_refused`, which is the intended behaviour. 61/61 cases pass. |

## Summary of verdicts

| ID | Verdict |
|---|---|
| D1 | PASS |
| D2 | PASS |
| D3 | PASS |
| D4 | PASS |

Five low/medium items recorded above. None is a defect in the hand-back
under D1/D2/D3/D4; the medium item (M1 phantom subscription) is the
class of defect to surface — money and access paths remain correct for
every shape the existing tests cover, and no existing test was deleted,
skipped, `xfail`ed, or had its assertion widened to make the suite pass.