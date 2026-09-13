# Review — plan replacement money path (R2 adversarial logic review)

Reviewer: qwen3.8-flash (Qwen family). Read-only. Baseline `75b19580`,
branch `cloudflare-access-service-tokens`.

I did not trust R1's report. Every claim below is checked against the code, and
each test run was run by me:

- `.venv/Scripts/python -m pytest tests/unit/test_invariant_plan_replacement_money.py -q -p no:randomly` → 9 passed
- `.venv/Scripts/python -m pytest tests/integration/test_paid_plan_replacement.py -q -p no:randomly` → 13 passed

The two purchasable plans are `trader` and `pro` (`core/plans.py:11`), so the
test matrix `PLAN_PAIRS` (`tests/integration/test_paid_plan_replacement.py:29-34`)
does cover both ordered crossings. R1 is right about that.

---

## Question 1 — can a customer be charged twice, or end with two live subscriptions?

The tested card paths are safe (`test_confirmed_card_move_leaves_one_charge_and_one_money_record`,
`test_two_payment_pages_paid_for_one_move_leave_one_live_plan`, replay dedupe at
`billing.py:2371-2383` plus the unique `provider_event_id` at `commercial.py:208`).

### R2-1 — BLOCKER. A card→crypto replacement move whose cancel fails loses its critical alert entirely, silently, and the webhook then loops.

This is new; R1 never saw it.

A move paid by crypto is real and supported (owner decision line 35-38; crypto
checkouts carry `replaces_subscription_id` like card ones).

When that move's old-card cancel fails, `PlanMoveFailed` reaches
`_save_replacement_failure` (`billing.py:2422-2431`), which builds the critical
alert. The event id for a NOWPayments delivery is built with colons:
`billing.py:2319` — `"id": f"nowpayments:{payment_id}:{status}"`. The alert's
evidence refs are `billing.py:2644-2647`. The issue queue refuses any evidence
ref whose tail is not `[A-Za-z0-9_.#/-]` (`issues.py:46`,
`_EVIDENCE_REF_PATTERN`). A colon is not in that set.

I ran the pattern against a real-shaped id and it does not match:

```
ref: billing_event:nowpayments:90313222:finished → match: False
dedupe key matches: True        (colons are allowed there)
```

So `record_occurrence` raises `IssueQueueError` (`issues.py:365-370`) *before*
the commit at `billing.py:2652`. The rollback at `billing.py:2606` has already
discarded the failed `BillingEvent` row, the commit never runs, and the
`IssueQueueError` replaces the `PlanMoveFailed` — so nothing is recorded at all.
The webhook 500s, the provider redelivers, the event row still does not exist,
and every delivery fails the same way. Result: the customer's card plan was not
cancelled, the new crypto payment is credited, and **no alert, no failed event,
no anything** is on our side. This is exactly the failure the owner's decision
calls "the most expensive defect this mission can ship"
(`OWNER_DECISION_WP8.md:66-69`).

The same shape exists for the development `static` provider
(`dashboard.py:4411`, `"id": f"static_checkout:{static_session}"`), test/development only.

R1's alert-length question (my extra angle 4), answered precisely: the two
fixed summaries are 163 and 174 characters against the 200 cap
(`labels.py:181`) — I measured both; the truncations `[:134]` on evidence refs
and `[:160]` on the dedupe key sit exactly on the pattern's boundaries
(`issues.py:45-46`). **Length is handled. Character set is not.** The comment at
`billing.py:2631-2633` shows this bug class shipped here before for length; the
class was fixed for length only, and the same class of refusal came back
through the format rule.

Severity: **blocker** (money alert path silently broken for one real checkout
route).

### R2-2 — SERIOUS (uncertain). The lapsed-period window is real. I agree with R1's finding 1.

Verified, not trusted:

- `_the_one_live_paid_plan` requires `current_period_end > now` or null
  (`plan_replacements.py:168-169`).
- `active_paid_plan_codes` has the same filter (`billing.py:743-744`), as does
  `paid_access_can_be_repriced` (`billing.py:625-626`).
- With no live frozen plan, `apply_after_payment` returns at
  `plan_replacements.py:244-245` (`replaces_subscription_id is None`) and never
  cancels anything at the provider.
- I searched the whole observability module for any alert rule about billing
  staleness. There is none — the only alert rules are market-data ones
  (`observability/alerts.py:241-250`). So the window is silent, exactly as R1
  said.

A recurring card subscription keeps status `ACTIVE` at the payment company
after its period ends until the renewal decision happens. In that window the
customer is treated as holding nothing, buys the other plan as a *fresh*
purchase, and the old card keeps charging. Two live subscriptions, no alert.

### R2-3 — SERIOUS (uncertain). Nothing stops a replaced subscription from being resurrected.

New finding. After a move, the old subscription is `CANCELED` in our DB. But
`_upsert_subscription` (`billing.py:3098-3151`) sets `status`, period dates and
`plan_id` from the event unconditionally — there is no check that reads any
"this subscription was replaced and must never come back" marker. If a
`subscription.paid` for the *old* subscription arrives (a renewal charge the
payment company collected just before our cancel landed, a race no cancel call
can undo), the paid-amount retry passes against the old checkout's own stored
amount (`billing.py:2879-2894`, the old metadata names its own original
purchase), and the old plan comes back `ACTIVE` next to the replacement. Two
live paid plans, silently; the next replacement attempt then refuses with
`multiple_paid_plans_need_help` and a person is needed. The metadata path is
real because the repo itself relies on it for renewals
(`plan_replacements.py:263-265`).

It depends on provider timing, which is why "uncertain". It composes with R2-2:
in the lapsed window the old plan's renewal is due *right now*, so the race is
at its widest exactly when R2-2 is live.

### R2-4 — the owner's property "(a) exactly one live subscription and one recurring charge remains, every plan × plan"

Covered today only for card→card, monthly, both ordered pairs. Crypto old plans,
annual cycles, and card→crypto are not asserted anywhere
(`tests/integration/test_paid_plan_replacement.py` parametrises
`PURCHASABLE_PLAN_CODES` only, monthly `billing_cycle` at 91 and 58). By reading
the code the crypto paths are safe — `_cancel_old_recurring_plan` no-ops for a
non-card provider (`plan_replacements.py:396-399`) and crypto invoices do not
recur — but nothing tests it. I agree with R1's finding 2. Minor.

---

## Question 2 — can a customer pay and get no access?

### R2-5 — R1's finding 3: I agree the window is real, and I disagree with two of R1's claims about it.

Agreed part, verified: the whole event transaction rolls back when the cancel
fails (`billing.py:2422-2431` → `_save_replacement_failure` →
`session.rollback()` at `billing.py:2606`), so the new subscription upsert made
by `_apply_event` is discarded while the payment company did take the money and
did create the new subscription. The old plan also keeps charging, because its
cancel is what failed.

Disagreements:

1. **"Until a person reprocesses" is overstated.** The failure re-raises out of
   `process_event` (`billing.py:2431`), the webhook answers non-2xx, and the
   payment company redelivers. A redelivery hits the failed event row and replays
   through `reprocess_failed_event` (`billing.py:2375-2376`, `2447-2519`). So
   automatic retries happen without any person; only a *persistent* cancel
   failure needs a person. The wrong window is bounded by the provider's retry
   schedule, not by our staffing.
2. **This behaviour is the owner's decision, not a defect.** The owner's rule
   says step 2 must be one transaction, nothing recorded when any part fails,
   failure raised loudly (`OWNER_DECISION_WP8.md:51-52`). The code does exactly
   that. What could still be closed is copy only: the alert sentence "the new
   plan was not given" (`billing.py:2637, 2641`) says nothing about the money
   the company already took and the subscription the company is already
   charging. That is a wording defect, not a money-path defect — severity
   minor, not serious.

And R2-1 above weakens the whole promise: for the crypto-paid route the alert
that this rollback depends on never gets written at all. The rollback is fine;
the visibility is not.

One more thing R1 missed here: the replay path re-derives the *rewritten* data
from the stored redacted payload, and `reprocess_failed_event` re-runs hydrate
and the amount check (`billing.py:2466-2493`). I verified that path is intact.

### R2-6 — MINOR (uncertain). Our own cancellation's webhook can mark a settled payment as "processing".

When we cancel the old subscription, the provider sends `subscription.canceled`
for it. Its metadata still names the original purchaser's checkout attempt
(metadata merging at `billing.py:2224-2231`), so `_record_checkout_event` runs
against that old attempt. `subscription.canceled` is in neither the completed
set (`billing.py:3047-3053`) nor the failure set (`3057-3063`), so the else
branch sets `attempt.status = "processing"` (`billing.py:3066-3067`) — on an
attempt that is `completed`. `payment_that_bought` requires
`status == "completed"` (`plan_replacements.py:118-129`), so the record of a
settled payment becomes unfindable, `held_access_renewal` loses the paid cycle,
and the billing history shows a paid row as "Processing" with no next step
(`dashboard.py:452-454` lists `processing` among statuses offered nothing).

I could not prove Creem puts full metadata on canceled events — that is the
uncertain part — but the code deliberately relies on the same persistence for
renewals (`plan_replacements.py:263-265`), so refusing to believe it here would
be inconsistent. Impact is display truth and cycle words today, not money. But
it also feeds R2-2: the proposed fix for the lapsed window depends on
`payment_that_bought` finding the old payment, and this downgrade can block
exactly that.

---

## Question 3 — can the money owed be written twice, or be more than what was paid?

SAFE. Verified independently of R1:

- idempotency key + unique constraint (`plan_replacements.py:311-318`,
  `commercial.py:269`, migration `e5a72b10c94d:112-114`);
- check constraints in both the model (`commercial.py:272-274`) and the
  migration (`e5a72b10c94d:65-74`);
- `money_owed_for_unused_time` clamps inside paid and uses Decimal end to end
  (`plan_replacements.py:86-105`, `_microseconds` at 100-105);
- the two-page replay resolves through the `earlier` lookup and then ends the
  *other* live plan, so no second row for the same period
  (`plan_replacements.py:277-297`), covered by the two-pages test.

---

## Question 4 — can the amount, the plan, or the period start be wrong?

The amount is `source.amount` from the frozen checkout attempt
(`plan_replacements.py:332-337, 355`), the period is captured before the old
row is truncated (`:303-304` before `:324`). SAFE, with the notes below.

### R2-7 — MINOR. One `float()` in a money path: the NOWPayments invoice amount.

`billing.py:1259` — `"price_amount": float(amount)`. The invoice creation sends
a Decimal money figure as a JSON float. For two-decimal catalogue prices the
harm is theoretical (Python's json emits the shortest round-tripping repr, so
"29.99" is sent as 29.99), and every return path re-parses with
`Decimal(str(...))` (`billing.py:2969, 3005-3006, 3245-3253`). But the owner's
field is money and this is the one float in it; it should be called out, not
carried.

Besides that one line, I searched every money path this change reaches —
`plan_replacements.py`, `payment_emails.py`, `plan_changes.py`, the webhook
normalizers, and the renderers. No other `float()` near money. The owed amount
travels as `str(expected_amount)` (`billing.py:2928`), `Numeric(12,2)` columns
(`commercial.py:238, 301-302`), and is rendered with Decimal formatting
`f"{amount:.2f}"` (`payment_emails.py:90, 205`) which never leaves Decimal.

### R2-8 — R1's finding 4: I disagree with R1's mechanism and can say why it is mostly ruled out

R1 feared renewals reuse the first (possibly discounted) purchase amount
because `payment_that_bought` returns the first attempt. But every confirmed
`subscription.paid` **overwrites** `attempt.amount` with the confirmed figure
of that renewal (`billing.py:2906-2916`: `attempt.amount = expected_amount`).
There is one attempt row per subscription, refreshed at each renewal, so the
valuation uses the *latest* confirmed amount, not the first. R1's
under-refund story is therefore mostly closed by code, not open.

A narrower edge survives: a Creem-page discount taken on the *first* payment
only updates `attempt.amount` to the discounted figure; a later full-price
renewal is then compared against the discounted figure
(`billing.py:2888-2895`). With `billing_allow_overpayment` off, that renewal is
**refused** as overpaid (`billing.py:2996-3000`) — the renewal never lands and
the event fails. Whether Creem keeps applying a page discount on renewals is
provider behaviour I cannot see from here. Uncertain, minor.

---

## Question 5 — can a page offer something the server then refuses?

SAFE. Verified: `plan_checkout_availability` is the one owner (`billing.py:753`),
read by the checkout route (`billing.py:1707-1719`), reopen (`1949-1963`),
checkout review page (`dashboard.py:4017-4027`, including
`replacement_refusal`), resume route (`dashboard.py:3812-3837`, which re-checks
and `attach_source`s rather than trusting the old row), and the page payloads
(`dashboard.py:3444-3453`).

### R2-9 — R1's finding 5: agree, minor.

Verified the exact divergence: plan cards gate on
`plan_is_on_sale(settings, code)` — monthly by default
(`dashboard.py:3460-3461`, `billing.py:535-566`) — while availability also
counts `card_annual` (`billing.py:798-804`). `_billing_history_rows` gates
`Finish paying`/`Try again` on `plan_is_on_sale` with the attempt's cycle
(`dashboard.py:457, 469, 481`). Annual-only sale: history rows and plan cards
say "coming soon"/blocked while the review page would sell. Fails closed
(a missed sale, never a refused offer). Agree.

---

## Question 6 — does any customer-visible sentence lie?

### R2-10 — R1's finding 6: agree, minor.

`CONSENT_PLAN_REPLACEMENT` says "I pay the full price of the new plan today"
(`plan_replacements.py:79-83`). A crypto discount code really does lower the
charge — the confirmed discounted figure is written into `attempt.amount`
(`billing.py:2913-2916`). The sentence is inexact for exactly that buyer.

### R2-11 — R1's finding 7: agree, minor.

Verified unreachable: `request_switch` unconditionally raises at
`plan_changes.py:506-510`, the switch route's only success path is the call at
`dashboard.py:4354-4376`, so the `plan_upgraded` / `plan_downgraded` messages
never reach a person. Stale copy in `base_dashboard.html`, not a live lie.

---

## AGREES / DISAGREES table against R1's findings 1–7

| R1 # | R2 verdict | Evidence |
|---|---|---|
| 1 (lapsed card window, serious) | **AGREES** — verified in code, real, silent | `plan_replacements.py:168-169, 244-245`; `billing.py:743-744`; no billing staleness alert rule exists (`observability/alerts.py` has market-data rules only) |
| 2 (no crypto/annual test coverage, minor) | **AGREES** | test file parametrises monthly card only (`test_paid_plan_replacement.py:29-34, 91`) |
| 3 (cancel-fail rollback, serious) | **AGREES in part — two claims wrong** | Rollback real (`billing.py:2422-2431, 2606`). Wrong 1: "The old plan also keeps charging" is the premise, fine — but wrong 2: "until a person reprocesses" — provider redelivery auto-retries (`billing.py:2431` raise → non-2xx → replay at `2371-2383, 2447`), and the one-transaction rollback is what `OWNER_DECISION_WP8.md:51-52` mandates. Residual real defect is alert copy only (minor) — plus R2-1, which breaks the alert itself on the crypto route |
| 4 (renewal reuses first amount, minor) | **DISAGREES, reframed** | `attempt.amount` is overwritten at every confirmed `subscription.paid` (`billing.py:2906-2916`); the surviving edge runs the other way — a full-price renewal after a provider-page first-period discount is refused as overpaid (`billing.py:2888-2895, 2996-3000`). Uncertain, minor |
| 5 (monthly-only gate vs card_annual, minor) | **AGREES** | `dashboard.py:3460-3461` vs `billing.py:798-804` |
| 6 (consent says "full price" under a code, minor) | **AGREES** | `plan_replacements.py:79-83`; `billing.py:2913-2916` |
| 7 (dead plan_upgraded/downgraded copy, minor) | **AGREES** | `plan_changes.py:506-510` always raises; `dashboard.py:4354-4376` is the only success path |

---

## Findings list (this review, R2)

| # | Severity | Finding | Proof |
|---|---|---|---|
| R2-1 | **blocker** | Critical alert for a failed cancel is lost entirely on crypto-paid moves: NOWPayments event ids contain `:`, the evidence-ref pattern forbids `:`, `IssueQueueError` fires before commit; every provider redelivery repeats it. No alert, no failed-event row | `billing.py:2319, 2606, 2644-2647, 2652`; `issues.py:46, 365-370`; verified by run: pattern match `False` |
| R2-2 | serious (uncertain) | Lapsed-period card plan is invisible; not frozen, not cancelled → silent double charge; no staleness alert exists | `plan_replacements.py:168-169, 244-245`; `billing.py:743-744` |
| R2-3 | serious (uncertain) | No guard stops a replaced (CANCELED) subscription being resurrected `ACTIVE` by its own late `subscription.paid` | `billing.py:3098-3151` overwrites status unconditionally; old checkout metadata passes the checks (`billing.py:2859-2895`) |
| R2-4 | minor (coverage) | Owner property (a) asserted only for card→card monthly; no crypto, no annual | test file, R1 agreed |
| R2-5 | minor | Alert copy "the new plan was not given" hides that the company already took the money and opened the new subscription | `billing.py:2634-2642` |
| R2-6 | minor (uncertain) | Our own cancellation's webhook downgrades the old settled checkout attempt `completed → processing` | `billing.py:3046-3068`; `plan_replacements.py:118-129` |
| R2-7 | minor | `float()` on a money figure in the NOWPayments invoice payload | `billing.py:1259` |
| R2-8 | minor (uncertain) | Full-price renewal after a provider-page first-period discount can be refused as overpaid (or recorded at the smaller figure) | `billing.py:2888-2916, 2980-3001` |
| R2-9 | minor | Plan cards / history rows gate monthly-only while availability also sells annual — fails closed | `dashboard.py:3460-3461, 457-491`; `billing.py:798-804` |
| R2-10 | minor | Consent sentence inexact for a discounted crypto buyer | `plan_replacements.py:79-83` |
| R2-11 | minor | Dead upgrade/downgrade confirmation copy | `plan_changes.py:506-510`; `dashboard.py:4368-4376` |

Counts: 1 blocker, 3 serious, 7 minor, 0 false alarms.

---

## Do R1's proposed tests actually close R1 findings 1 and 3?

**Finding 1's test** — seed `status ACTIVE`, `current_period_end` in the past,
`cancel_at_period_end False`; buy a different plan; assert the old subscription
was cancelled. It would fail today and pass after a fix, so it *reproduces* the
bug. Two amendments required or it can pass without closing the window:

1. It must also assert `attempt.replaces_subscription_id == old.id`. A fix that
   only cancels at payment time, while `source_for_checkout` still returns
   `None`, leaves the freeze broken and the money-owed path would then raise
   `paid_amount_missing` — the bug would resurface on the next line.
2. The fix must decide what the money record says for a fully lapsed period
   (`money_owed_for_unused_time` returns `0.00` for `unused <= 0`,
   `plan_replacements.py:94-95` — so no email, no owed row, which is right).

**Finding 3's test** (assert the alert text says the customer is charged for a
plan our system has not given): it closes copy only. The behaviour itself is
the owner's mandated one-transaction fail-closed, so a test cannot "fix" it —
what would close the *customer* gap is a test asserting the redelivery replay
path retries the cancel automatically and resolves the alert when it succeeds.
That test half-exists (`test_failed_card_cancellation_is_visible_and_reprocessed`,
which already asserts both sentences R1 objects to). Escalate the copy change as
a wording decision, not a test.

**For R2-1**, the closing test is: a crypto-paid replacement move whose cancel
fails must leave an open critical issue and a failed `BillingEvent` row.
Today that test cannot pass — the alert write itself raises.

---

## Closing the disagreements

- **D1 (R1-3 "serious, only a person reprocesses").** Closed as agree-in-part:
  the rollback behaviour is the owner's decision, automatic retry exists
  through provider redelivery, and the remaining defect is the alert's wording
  (R2-5, minor). Nothing to escalate beyond that wording.
- **D2 (R1-4 renewal amount).** Closed as reframed: the first-purchase-amount
  fear is contradictied by `billing.py:2906-2916`; the surviving edge (R2-8)
  stays open as uncertain pending what Creem does with page discounts across
  renewals — needs one live check, not code.
- **D3 (R1-1 test design).** Closed with amendment as above.
- **D4 (R2-1).** Escalated — this one is for a fix, not a wording decision. The
  smallest correct fix is to cast the event id to a pattern-safe ref (lowercase
  slug or truncated sha256) in `_save_replacement_failure`, or widen
  `_EVIDENCE_REF_PATTERN` to include `:` — one owner, `issues.py`, not a local
  truncation hack at the billing call site.

## What I verified and what I did not

Verified: both named test files pass; the evidence-ref pattern rejection by
direct execution; the two alert summaries fit the 200 cap by measurement; the
filter lines, the early return, the rollback and commit order, the unique
constraint, the check constraints, and every line R1 cited.

Not verified: live provider behaviour — whether Creem puts full metadata on
`subscription.canceled`, whether a renewal charge taken before an immediate
cancel still produces a paid webhook, and Creem's discount behaviour across
renewals. R2-2, R2-3, R2-6, R2-8 carry that uncertainty and say so. Nothing here
was reproduced against a real provider; all of it is code reading plus the two
named suites.
