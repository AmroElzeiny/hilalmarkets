# WP D1 — a refund must be recorded, or it can be refunded twice

Run `20260913T002503Z-fdd3949e` · branch `cloudflare-access-service-tokens` · HEAD `75b19580` · 2026-09-13

## Plain summary

When a payment company gives money back, the record of that payment has to say "refunded".
It did not. A refunded payment still read as money this product kept, so the plan-change
service could promise a customer the same money a second time. This work makes the refund
land on the payment record — for every company, every refund message name, and every state
the payment was in — while keeping the rule that a cancellation message must never undo a
settled payment.

Two extra holes of the same class were found and closed: the frozen payment link used by a
plan move, and a refund event that was refused with an error (which destroyed the record of
money leaving). Nothing was committed or pushed.

---

## 1. Files and symbols changed

### `src/ai_market_monitor/services/billing.py` (authorised: primary)

| Line | Symbol | Change |
|---|---|---|
| 603 | `REFUND_ENDS_PLAN_EVENT_TYPES` | New. `payment.refunded`, `refund.created` — money returned, so the plan it bought ends. |
| 612 | `REFUND_CHARGEBACK_EVENT_TYPES` | New. `charge.refunded`, `charge.dispute.created`, `dispute.created` — the payment is recorded; access is **not** ended (unchanged product rule: a dispute can be reversed). |
| 632 | `REFUND_EVENT_TYPES` | New, **built as the union of the two above**, so a name added to either set cannot be missed by a third list. This is the single owner of "this event gave money back". |
| 645 | `REFUND_RECONCILIATION_ALERTS` | New. The two fixed alert sentences (`payment` / `plan`) at module level, so the queue's own length cap is enforced where the sentences live. |
| 662, 667 | `REFUNDED_ATTEMPT_STATUS`, `SETTLED_ATTEMPT_STATUS` | New names for the two words the attempt row is written and read with. |
| 2897, 2979 | `_apply_event` | The two old refund branches became one refund reading. A refund that cannot name a subscription is no longer refused (it used to raise `user_missing` / `subscription_missing`, which rolled the whole webhook back and lost the refund). Chargeback names keep the old audit-only behaviour exactly. |
| 3023 | `_end_refunded_plan` | New. Ends the refunded plan when the event names a person and a subscription; otherwise records the gap and returns `None`. |
| 3063 | `_refund_alert_key` | New. The provider event id is **hashed** into the issue key (see finding F3). |
| 3079 | `_save_refund_alert` | New. Critical issue, scope `billing.refund_reconciliation`, occurrence-deduped. |
| 3111 | `_attach_refund_to_attempt` | New. First thing `_hydrate_checkout_data` does for a refund: find the payment from whatever the event does name; never raise; drop an unusable reference instead of passing it on. |
| 3163 | `_refund_matches_attempt` | New. Identity check on a supplied reference (same company, same person when named). The plan is deliberately **not** compared — a subscription keeps the metadata it was created with, so after a plan change the refund of a new charge still names the old checkout. |
| 3183 | `_resolve_refund_attempt` | New. Subscription reference → person and plan; else person + plan code; else person alone. A payment is attached only when it is the **one** candidate; two candidates goes to a person rather than a guess. |
| 3239, 3247 | `_hydrate_checkout_data` | Refund-like events take the refund reading and return. The `checkout_reference_missing` raise can no longer throw a refund away. New `event_id` keyword (callers 2509, 2602). **The money-in checks below are untouched.** |
| 3298 | same, NOWPayments guard | `checkout_already_completed` now also fires on `refunded`: without it, a replayed `payment.finished` looks never-settled right after a refund and hands out a second 30-day period for returned money. |
| 3510, 3538, 3545 | `_record_checkout_event` | New first reading: a refund sets the attempt to `refunded` whatever it was before, idempotently. `payment.refunded` removed from the failure set; the R2-6 guard for `invoice.payment_failed` / `payment.failed` / `payment.expired` / `payment.partially_paid` is unchanged. |
| ~3696 | `_uuid_if_readable` (read at 2515, 3001, 3041, 3193) | New. Reads a provider id without raising — correct for a refund and for the stored event row, where `_parse_uuid`'s raise produced a server error and lost the record (finding F5). |

### `src/ai_market_monitor/services/plan_replacements.py` (authorised only if a reader is wrong — it was)

| Line | Symbol | Change |
|---|---|---|
| 55 | `SETTLED_PAYMENT_STATUS` | New. One word for "money moved and is still moved", shared by the two readers below so they cannot disagree. |
| 148 | `payment_that_bought` | Same query, written through the constant. Behaviour unchanged. |
| 376 | `apply_after_payment` | **The genuine reader bug.** Money owed was valued from the *frozen* source row with no check on it, so a refund arriving after the customer opened the new payment page produced a record promising money the company had already returned — the customer paid twice for one period. Now nothing is owed; the old plan still ends and the new one stays live. |

### `tests/unit/test_invariant_refund_is_recorded.py` (new, 144 cases)

No existing test was edited, deleted, skipped, or loosened. The two neighbours that cover this
ground — `tests/unit/test_billing_entitlements.py:1215` (refund revokes the plan) and
`tests/integration/test_paid_plan_replacement.py:780` (R2-6: a cancel event must not downgrade
a settled attempt) — still pass unchanged, and both fixtures were reused rather than reinvented.

---

## 2. Before the fix — the reproducer failing

**Baseline note.** The working tree already held other workers' uncommitted edits to these
same two files, so `git HEAD` is not a valid "before". The baseline was built by copying the
working tree to `…\Temp\opencode\d1_pre` and reversing **this package's own hunks** there
(`d1_make_pre_tree.py`, marker-asserted, copied into this folder). The real tree was never
reverted; `git stash list` stayed empty; no probe file was left behind.

```powershell
$env:PYTHONPATH="C:\Users\amroe\AppData\Local\Temp\opencode\d1_pre\src"
.venv\Scripts\python -m pytest tests/unit/test_invariant_refund_is_recorded.py `
  -q -p no:randomly --no-header --tb=line `
  --junitxml=.hm-orchestrator\runs\20260913T002503Z-fdd3949e\D1_newfile_before.xml
```

```
before_exit=1
D1_newfile_before {'tests': '144', 'failures': '101', 'errors': '0', 'skipped': '0'}
```

Representative verbatim failures (`D1_newfile_before.txt`):

```
AssertionError: stripe/payment.refunded on a completed payment left it as 'completed': the refund was never written down
AssertionError: stripe/charge.refunded on a creating payment left it as 'processing': the refund was never written down
AssertionError: nowpayments/dispute.created on a pending payment left it as 'processing': the refund was never written down
BillingError: A successful payment must match a server-created checkout attempt.   <- NOWPayments payment.refunded, no reference: refund thrown away
BillingError: Billing event included an invalid checkout reference.                <- same class, unreadable reference
BillingError: Billing event did not include a user id.                             <- refund refused, nothing recorded
BillingError: Billing event did not include a subscription id.
ValueError: badly formed hexadecimal UUID string                                    <- a 500 that loses the refund (see F5)
AssertionError: [] == ['critical']                                                  <- no alert existed for an unplaceable refund
AssertionError: a refunded payment was still offered as the money held               <- money owed from refunded money
```

Three separate bugs are visible: a settled payment stayed `completed`; a non-settled payment
was moved to `processing` by a refund; and a refund that named no checkout was refused, which
destroyed the record.

---

## 3. After the fix

```
after_exit=0
D1_newfile_after {'tests': '144', 'failures': '0', 'errors': '0', 'skipped': '0'}
```

| Command (one pytest process at a time, output to a file, no `--timeout`) | Result |
|---|---|
| `pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly` (`offline-suites`) | `offline_exit=0` — `{'tests': '28355', 'failures': '0', 'errors': '0', 'skipped': '489'}` |
| `pytest tests/integration -q -p no:randomly` (`integration`) | `integration_exit=0` — `{'tests': '2295', 'failures': '0', 'errors': '0', 'skipped': '0'}` |
| `pytest tests/unit/test_invariant_refund_is_recorded.py tests/unit/test_billing_entitlements.py tests/integration/test_paid_plan_replacement.py` | `exit=0` — 221 tests, 0 failures (`D1_neighbours.xml`) |
| `ruff check src tests scripts` | `All checks passed!` (`ruff_exit=0`) |
| `mypy src/ai_market_monitor src/hm_chatbot_eval` | `Success: no issues found in 396 source files` (`mypy_exit=0`) |
| `python scripts/check_release_invariants.py` | `PASS: release exposure, route security, provider, and artifact invariants hold.` |
| `python scripts/check_oi_command_catalog.py` | `Command catalog matches the release gate: 38 commands, 25 runnable unattended.` |

The 489 skips are pre-existing network-gated tests. My file contains no skip, xfail or
loosened assertion. `ruff check .` (whole repo, the CI wording) still reports 4 errors in
`tools/hm-orchestrator/validate-report.py` — reproduced inside a clean `HEAD` worktree, so
they exist at `75b19580` and are not from this package.

---

## 4. Required behaviour → where it is proved

| # | Requirement | Test (cases) | Asserted |
|---|---|---|---|
| 1 | A refund/chargeback moves a settled attempt to `refunded`, all providers | `test_a_refund_always_reaches_the_payment_it_refunds` — 5 names × 3 providers × 4 prior states = 60 | `status == "refunded"` |
| 1b | …and it is recorded when the event names no checkout | `test_a_refund_is_recorded_when_the_event_names_no_checkout` (15), `test_a_refund_is_found_from_the_person_when_nothing_else_names_it` | resolved through the subscription / the person, then recorded |
| 2 | Our own cancellation never downgrades a settled payment | `test_our_own_cancellation_never_undoes_a_settled_payment` — 8 names × 3 providers = 24 | attempt stays `completed` |
| 3 | No money owed back from a refunded payment | `test_a_refunded_payment_no_longer_counts_as_money_kept` (15), `test_every_money_reader_stops_counting_a_refunded_payment`, `test_a_plan_move_after_a_refund_owes_nothing_back`, `test_a_refund_that_lands_between_freeze_and_payment_owes_nothing_back` | `payment_that_bought` → `None`; **zero** `PlanMoveMoneyOwed` rows through the real service in both windows; old plan ends, new plan live |
| 4 | Replay is idempotent | `test_the_same_refund_arriving_twice_changes_nothing_twice`, `test_two_refund_events_for_one_payment_produce_one_alert`, `test_every_unplaceable_refund_leaves_exactly_one_alert` (6) | one status change, one `BillingEvent`, no alert for a placed refund, exactly one alert row per unplaced event, no money record |
| 5 | Hydration must not skip a refund, must not raise for a diagnostic, must not weaken money-in checks | `test_a_refund_that_names_nobody_is_accepted_and_alerted` (4), `test_a_refund_naming_a_checkout_we_do_not_have_is_alerted_not_refused` (2), `test_a_refund_that_could_belong_to_two_payments_is_not_guessed`, `test_every_unplaceable_refund_leaves_exactly_one_alert[unreadable-*]` (2), `test_a_payment_still_refuses_a_checkout_it_cannot_see` (9) | refund accepted + critical alert; ambiguity not guessed; payments still refused |
| — | The billing-history reader named in the brief | `test_the_billing_history_row_offers_nothing_for_a_refunded_payment` | refunded row: no next step, no resume — and a failed row still says something (see §5 row 8) |

Provider coverage note (why nothing was silently dropped): the matrix runs **every** refund
name against **all three** companies rather than skipping pairs, because the recording path is
company-independent. In reality `NOWPayments` cannot produce `charge.*`/`refund.created` (its
event type is built as `payment.<status>` at `billing.py:2373`) and `Creem` does not name a
`payment.refunded` event; those rows are extra coverage, not a claim about live traffic. The
provider capability `supports_refunds` (`billing.py:131`, `False` for NOWPayments) describes
whether **we** can push a refund, not whether the company can report one — `payment.refunded`
is reachable today through `_apply_event`, and the R2-6 set already carried that name.

---

## 5. Every reader of attempt status I searched, and what a refund does to it

Searched `src/ai_market_monitor/**` for `BillingCheckoutAttempt.status`, `attempt.status`,
`checkout_attempt.status` (services, routers, templates).

| # | Reader | File:line | What it does with a refunded payment now | Proof |
|---|---|---|---|---|
| 1 | `payment_that_bought` — the money-owed source | `plan_replacements.py:132` | Excluded (`status == "completed"`). A live plan whose only payment was refunded now raises `paid_amount_missing` and the move is **refused**, not priced. | executed (3 tests) |
| 2 | `apply_after_payment` — the frozen link | `plan_replacements.py:261, 376` | **Fixed here** — it read the frozen row without checking it, so it could owe back money already returned. | executed (new test; failed before) |
| 3 | `held_access_renewal` → `held_renewal` | `billing.py:700-721` | Finds no payment → `paid_cycle=None` → "renews by itself" without "each month/year". No crash. Cosmetic only, and only for a dispute that left access live (U2). | reasoned (read `held_renewal`, which takes `None` by design) |
| 4 | Affiliate `_amount_charged` | `affiliate_attribution.py:606` | Filters `completed` → the refunded payment is no longer a source; falls back to an older completed payment, then the catalogue price. **Commission is not clawed back** — no clawback path exists at all (U4). | executed for the filter; the clawback absence is a code read |
| 5 | Operations "paid" list/totals | `system_brain_payments.py:74, 210, 300, 400` | `refunded ∉ PAID_STATUSES` → leaves the paid list, the customer totals and the overall revenue sums. | executed (`customer()` → 0 payments, total 0, empty list) |
| 6 | Operations "unfinished attempts" | `system_brain_payments.py:360-363` | `status.not_in(PAID_STATUSES)` counts the refunded row as an *unfinished* attempt — a wrong label for staff. Money and access are untouched. | executed with a throwaway probe: `status='refunded' payment_count=0 total_paid=0 unfinished_attempts=1` (probe deleted afterwards) |
| 7 | Completed-checkout counter | `system_brain_tools.py:732` | A refund leaves the count — right: it is no longer a kept sale. | executed (same filter) |
| 8 | Billing history rows | `api/routers/dashboard.py:451` | Already treated `refunded` as settled: no "Pay again", no resume. **Verified it does not show a refunded purchase as live paid**: the row is silent like a paid row, and the badge prints the status word itself (`templates/hilal/dashboard/partials/billing_history.html:11-19` → "Refunded", `badge-neutral`, not the `completed` green). | executed |
| 9 | Post-payment result page words | `api/routers/dashboard.py:4453-4466` | No `refunded` entry → falls back to "Payment confirmation pending". It never claims the plan is live or paid. Gap only in wording → X3. | reasoned from the table |
| 10 | Cancel-return route | `api/routers/dashboard.py:4507-4508` | Rewrites any status outside `{completed, failed, expired}` to `cancelled`, so revisiting an old cancel link would **erase the refund record**. Same defect class, file outside my scope → X1. | reasoned from the code (the hole pre-dates this work: `payment.refunded` on a pending attempt already produced `refunded`) |
| 11 | Resume route / checkout reuse | `api/routers/dashboard.py:3857-3861`, `billing.py:1794, 1836, 1993` | Only touch `creating`/`pending`, so a refunded attempt is neither resumed nor silently reused. | reasoned from the queries |
| 12 | Telegram/admin status line | `api/routers/dashboard_api.py:4006-4007` | Prints the raw status → "Refunded". | reasoned |
| 13 | Checkout analytics event | `templates/billing_result.html:31, 48` | Counts `checkout_completed` only on `completed` → a refund is no longer counted as a sale. | reasoned |
| 14 | "already subscribed" guard | `billing.py:1991-1992` | A refunded attempt is not "complete", so a customer can be sent to a payment page for it again; paying again re-settles the row. Left as is → U3. | reasoned |
| 15 | NOWPayments one-time double-grant guard | `billing.py:3298` | Extended to `refunded`, so a replayed `payment.finished` cannot grant a second 30-day period for money already returned. | executed (new matrix cases + existing `checkout_already_completed` test still pass) |

Schema check: `billing_checkout_attempts.status` is `String(32)` with no `CHECK` constraint
(`db/models/commercial.py:235`; no constraint found in `alembic/versions`), and `refunded` was
already a reachable value, so **no migration is needed**.

---

## 6. Problems found beyond what was asked

* **F1 — a refund of a not-yet-settled attempt wrote a wrong status.** `refund.created` or
  `charge.refunded` on a `pending` attempt fell through to the "processing" reading. Fixed by
  the single refund reading at `billing.py:3538`; covered by 45 of the 60 matrix cases.
* **F2 — a refund that named no subscription was refused outright.** NOWPayments
  `payment.refunded` without a checkout reference raised `checkout_reference_missing`; Creem
  `refund.created` without a subscription raised `user_missing` / `subscription_missing`. A
  refusal rolls the webhook back, so the refund left **no record at all** and the company kept
  retrying. Both paths now record what they can and raise a critical alert.
* **F3 — the alert could destroy itself.** My first issue key embedded the raw provider event
  id; the queue's own `sanitize_dedupe_key` refuses a key containing whitespace, so an odd id
  would have lost the alert about lost money. The key is now a hash of the id
  (`_refund_alert_key`), and the raw id travels only as a truncating evidence pointer. Covered
  by ids with spaces and 200-character ids.
* **F4 — replay under a second event id.** A resolution that looked only at `completed`
  payments would have raised a fresh "unplaceable" alert for the same refund. Settled **or**
  already-refunded rows are candidates, so the second event lands on the row the first one
  changed and nothing further happens.
* **F5 — found in my own first implementation, then fixed.** An unreadable `user_id` on a
  refund still reached `_parse_uuid` and raised `ValueError` → a 500 that loses the record —
  exactly what requirement 5 forbids. Proved with a throwaway probe
  (`PROBE2 raised ValueError: badly formed hexadecimal UUID string`), fixed with
  `_uuid_if_readable`, and pinned by two permanent cases
  (`[unreadable-person-id]`, `[unreadable-everything]`).
* **F6 — pre-existing, not fixed.** `payment.failed` appears in two branches of
  `_apply_event` (`2962` past-due, `3015` audit-only); the first wins, so the second is dead.
  Which one should win changes whether a failed payment pauses the plan — a product decision
  outside this package, reported not patched.

---

## 7. Scope-expansion requests (deliberately not done)

* **X1 — `api/routers/dashboard.py:4507`.** `/dashboard/billing/cancel` rewrites any status
  outside `{completed, failed, expired}` to `cancelled`, so revisiting a stale cancel link
  deletes a recorded refund and puts the payment back on the "money kept" side — the double
  refund, reached from a browser URL. Proposed one-line fix (plus a case in the D1 file):

  ```python
  # from: if checkout_attempt.status not in {"completed", "failed", "expired"}:
  if checkout_attempt.status not in {"completed", "failed", "expired", "refunded"}:
  ```

* **X2 — `services/system_brain_payments.py:362`.** Exclude `refunded` from
  `unfinished_attempts` (or add its own count), so staff do not see a returned payment as an
  abandoned attempt.
* **X3 — `api/routers/dashboard.py:4453`.** Give `refunded` honest words on the post-payment
  page (the wording already exists at `api/routers/dashboard_test.py:1483`: "Refunded" /
  "The money went back to you") instead of "Payment confirmation pending".
* **X4 — a chargeback that is not yet a loss.** `REFUND_CHARGEBACK_EVENT_TYPES` records the
  payment as refunded while leaving the plan live (unchanged rule). If the product wants a
  confirmed `charge.refunded` to end access too, that is a decision, not a webhook detail.

---

## 8. Uncertainties

* **U1 — provider payload shapes come from this codebase, not from live providers.** The five
  names are the ones the webhook code already routes; the tests feed normalised payloads as the
  existing billing tests do. I could not confirm today which companies echo
  `checkout_attempt_id` on a refund, which is why the fallback resolution (subscription →
  person + plan → person alone) exists.
* **U2 — after `charge.refunded`/`dispute.created`, the attempt says `refunded` but the
  subscription stays live** (disputes were, and remain, audit-only for access). Consequences:
  `held_access_renewal` loses its period word, and a later plan move is refused with
  `paid_amount_missing` ("write to us") until a person acts. Fail-closed and money-safe, but
  the customer sees a refusal. X4 asks whether that split is intended.
* **U3 — paying again after a refund.** A refunded attempt is no longer "already complete", so
  the checkout route can hand back a payment page for it, and a success event moves the row
  back to `completed`. That reads as correct (money moved again) and was not asked for; I did
  not test the sequence end to end. The opposite ordering — a late success event arriving
  after a genuine refund for the *same* money — would revive the row. Not protected.
* **U4 — affiliate commission is not reversed** on a refund; no clawback path exists anywhere.
* **U5 — the "before" numbers depend on a reconstructed baseline** (§2), because the tree
  carried other workers' edits to the same files. The reversal script prints what it reversed,
  and both JUnit XML files are in this folder so a reviewer can re-run the comparison.
* **U6 — browser suites were not run** (`tests/browser` needs Chromium and is not part of the
  offline set). Nothing here changes markup or CSS; the template and static-asset invariant
  tests in `tests/unit` did run and pass.
* **U7 — one-time crypto re-delivery.** The widened `checkout_already_completed` guard (§5
  row 15) assumes a NOWPayments replay of `payment.finished` for a refunded invoice should be
  refused. I did not find a case where a *new* crypto payment legitimately reuses a refunded
  attempt id, but I cannot rule one out from here.

---

## 9. Artifacts in this folder

| File | What it is |
|---|---|
| `D1_newfile_before.xml` / `.txt` | The reproducer against the reconstructed pre-fix baseline: 144 tests, 101 failures |
| `D1_newfile_after.xml` / `.txt` | The same file with the fix: 144 tests, 0 failures |
| `D1_final_before.txt`, `D1_final_after.xml`, `D1_before_fix.txt` | Earlier captures of the same comparison (142 and 132 case revisions), including the first run taken before any source edit |
| `D1_offline_final.xml` / `.txt`, `D1_integration_final.xml` / `.txt`, `D1_unit_final.xml` | The adjacent regression runs |
| `D1_mypy.txt`, `D1_mypy2.txt` | Type-check output before and after my own typing fixes |
| `D1_WP_own_diff_billing.txt`, `D1_WP_own_diff_plan_replacements.txt` | This package's own diff, isolated from other workers' uncommitted edits |
| `WP_D1_EVIDENCE.md` | This document |

No commit, push, reset, clean, checkout or stash was performed; `git stash list` is empty and
only the three paths in §1 differ from how I found them.
