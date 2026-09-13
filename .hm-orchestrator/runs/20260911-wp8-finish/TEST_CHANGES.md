# Every test changed in this run, with its old and new expectation

Reason for all of them: the owner's rule of 2026-09-10. Moving to a different paid plan is
now a normal full-price payment. The switch route that re-priced the card refuses every
move. A test that expected the old switch behaviour now expects the new rule.

The four conditions from the brief, checked:

| Condition | Held? | Where |
|---|---|---|
| Still parametrised across every plan pair | yes | `PLAN_PAIRS` (every ordered pair of different paid plans) in the journey file; plan × method × cycle × held in `test_invariant_billing_offers.py` |
| Still fails when a page and the server disagree | yes | `test_every_plan_card_button_matches_the_server[held]`, `test_page_offer_matches_server_acceptance[...]`, new `test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page[pair]` |
| Nothing deleted, loosened or skipped | yes, see the note below | no `skip` or `xfail` added; no assertion widened |
| Every changed test listed | yes | this file |

Note on "deleted": the old checks on the Creem re-price call (`update_behavior` =
`proration-charge-immediately` / `proration-none`, `product_id`) are gone. The product no
longer makes that call; the owner removed it. The tests now check the opposite: the
switch route reaches **no** payment company (`fake_creem == []` / `reached == []`).

## tests/integration/test_plan_change_journey.py

| Old test | New test | Old expectation | New expectation |
|---|---|---|---|
| `test_upgrade_trader_to_pro_records_intent_and_moves_access[timing×monthly/annual]` | `test_every_paid_plan_move_is_a_payment_and_the_switch_route_takes_nothing[pair×timing×monthly/annual]` | trader→pro only. Switch POST → `message=plan_upgraded`; one upgrade row (applied now, or scheduled for period end); Creem re-price payload | **every** plan pair. Page: Pay button for the target, none for the held plan, no switch control, the note, the 48-hour window and the consent sentence. Switch POST → `error=plan_change_needs_payment` + `selected_plan`; error page says "normal payment page"; 0 rows; plan and access unchanged; no payment-company call |
| `test_downgrade_pro_to_trader_is_scheduled_and_applies_at_period_end` | `test_a_change_booked_before_the_rule_still_applies_at_period_end` | booked through the switch POST (`plan_downgraded`, Creem `proration-none`); applied one second after period end | booking seeded as a pre-rule row (nobody can book one now). Page: "You are moving to Plus", "Plus is booked", no Pay for Plus. `apply_due_changes` one second **before** the end → 0 and still Pro (new check); one second after → 1, Plus, row `applied`; no payment-company call |
| `test_downgrade_immediate_is_refused` | same name | `error=downgrade_is_period_end`; page "end of the period you have already paid for" | `error=plan_change_needs_payment`; page "normal payment page". Still: 0 rows, no Creem call |
| `test_every_plan_card_button_matches_the_server` | `test_every_plan_card_button_matches_the_server[held]` | held trader only; buttons = switch triggers; switch POST accepted iff offered (trader, pro, demo) | every held paid plan. Buttons = Pay buttons read from the page; the **real** checkout POST is accepted iff the page offers Pay, for every plan on sale; every switch POST (demo too) refused; no `/subscriptions/` call; 0 rows |
| `test_timeout_on_immediate_upgrade_never_invites_a_retry` | same name | `error=billing_timeout_needs_check`; "did not answer in time"; "do not repeat this request"; a `failed` row | `error=plan_change_needs_payment`; "normal payment page"; still never "try again"; the (timing-out) payment company is never reached; 0 rows |
| `test_crypto_paid_account_is_told_to_buy_not_switch` | same name | page "You paid with crypto"; error page "no card to change" | page "You pay the full Pro price today" and a Pay button for Pro; error page "normal payment page"; plus 0 rows and no Creem call (added) |
| `test_switch_with_already_pending_change_is_refused` | same name | first switch POST booked a change and made 1 Creem call; second refused | first change seeded (pre-rule); second refused `change_already_requested`; 1 row, still `scheduled` (added); Creem calls 1 → **0** |
| `test_upgrade_without_consent_is_refused` | same name | `error=consent_required` | `error=plan_change_needs_payment` (the route refuses before it reads the tick box — there is nothing to agree to there). Still 0 rows, no Creem call |
| `test_switch_to_free_plan_is_not_offered_and_is_refused` | unchanged | | |
| `test_invalid_or_denied_switches_are_refused[...]` | unchanged | | |
| `test_switch_without_paid_plan_is_refused` | unchanged | | |
| — | **new** `test_every_checkout_tick_box_says_what_happens_to_the_plan_held[pair]` | | billing, review and subscription pages all carry `CONSENT_PLAN_REPLACEMENT` |
| — | **new** `test_nobody_without_a_paid_plan_is_promised_money_back[none/admin/trial]` | | none of the three pages carries the sentence or the 48-hour window |
| — | **new** `test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page[pair]` | | no Pay button, the refusal sentence on all three pages, no review form, route 400 `paid_amount_missing`, no payment call, notice shows the sentence not "Paid Amount Missing" |

Helpers: `_grant_paid_plan` now also writes the completed payment that bought the plan
(`with_payment_record=True` by default). `_recorded_creem_payload` removed (the call it
read no longer exists).

## tests/browser/test_checkout_pay_button_e2e.py

| Old test | New test | Old expectation | New expectation |
|---|---|---|---|
| `test_a_card_holder_is_sent_to_the_switch_form_not_a_second_checkout` | `test_a_card_holder_is_offered_the_payment_popup_not_a_switch_form` | switch trigger for Pro, **no** Pay button, switch dialog opens | Pay button live with its note; no switch trigger or dialog; the popup opens and its tick box carries `CONSENT_PLAN_REPLACEMENT` |
| `test_the_checkout_page_offers_no_disabled_path_to_a_card_holder` | same name | refusal notice naming "billing page"; no form; no enabled submit; notice above 900px | the form; no error notice; tick box carries the sentence; submit enabled |
| `test_upgrading_trader_to_pro_shows_a_confirmation_the_person_sees` | `test_upgrading_trader_to_pro_says_what_the_payment_does_before_it_is_taken` | walked the switch dialog to `message=plan_upgraded` and a pending notice | Pay + note (full price today, starts only after confirmed, old plan stays if they leave or it fails, the window); nothing booked; popup tick box carries the sentence |
| `test_downgrading_pro_to_trader_names_the_time_it_happens` | same name | upgrade then downgrade through the switch; `plan_downgraded`; pending notice names the day | Pro holder: the Plus Pay note says it starts only after that payment is confirmed, plus the window; nothing booked; no sideways scroll |
| `test_screenshot_s7_pro_refusal_review_page` (state `s7-pro-refusal`) | `test_screenshot_s7_pro_review_page_for_a_card_holder` (state `s7-pro-review-card-holder`) | photographed the refusal | photographs the form with the sentence. Old S7 shots are **stale** |
| `test_screenshot_s5_billing_switch_buttons` (`s5-billing-switch`) | `test_screenshot_s5_billing_pay_buttons_for_a_card_holder` (`s5-billing-pay-for-card-holder`) | switch buttons | Pay button + note, no switch control |
| `test_screenshot_s6_downgrade_confirmation` (`s6-downgrade-scheduled`) | `test_screenshot_s6_downgrade_pay_note` (`s6-downgrade-pay-note`) | a booked downgrade | the Plus Pay note on a Pro holder's page |
| `test_screenshot_s4_trader_refusal` (state `s4-trader-refusal`) | `test_screenshot_s4_billing_pro_card_for_a_crypto_holder` (state `s4-billing-pro-card-crypto-holder`) | a live Pro Pay button with its sentence, saved under a name that said "refusal" | the same state and the same assertions, saved under a name that says what it shows. Renamed in the second pass. Old S4 shots are **stale** (named for a refusal, taken at the S7 address) |
| S1–S3 and the method-selection tests | unchanged | | |

`tests/browser/conftest.py`: `seed_paid_monitor_access` gained `plan_code` (default
`"trader"`, so every existing caller behaves as before).

## tests/unit/test_invariant_billing_offers.py

| Old | New | Old expectation | New expectation |
|---|---|---|---|
| `test_the_plan_switch_form_names_no_plan_of_its_own` | `test_the_plan_change_sentences_name_no_plan_of_their_own` | the switch form wrote no plan name by hand | the Pay note and the checkout tick box write no plan name by hand, read `{{ plan.name }}`, `{{ manual_return_window }}` and `{{ consent_plan_replacement }}`; no switch form or `data-plan-switch` in the page or its script |
| `_grant_paid_plan` (helper) | same | subscription only | subscription **and** the completed payment that bought it (input only; every assertion unchanged). Without it the "held-different" cases measured the refusal for a broken record, which now has its own test |

## tests/integration/test_system_brain_payments.py

Input only: the stored consent sentence in the fixture is now the module constant
`BOOKED_DOWNGRADE_CONSENT`, because `CONSENT_DOWNGRADE` was deleted with the switch form.
The assertion compares the same stored sentence, as strongly as before.

## Second pass, 2026-09-11

These have their own reasons, given in each row. None is the owner's plan-move rule.

### tests/integration/test_paid_plan_replacement.py

| Test | Kind of change | Old expectation | New expectation |
|---|---|---|---|
| `test_confirmed_card_move_leaves_one_charge_and_one_money_record[pair]` | assertions **added**, none changed | one live plan, one cancel, one money record, one email | the same, **plus** exactly one staff notice `billing:money-owed:{id}`: open, severity `ticket`, summary carrying the amount and the due time |
| `test_renewing_the_new_plan_never_ends_or_repays_the_old_one_again[pair]` | **new** this pass; one expected value changed while writing it | `OperationalIssue` count `== 0` | exactly `[("billing:money-owed:{id}", 1)]` — the one money notice, seen once. A failure notice, or a second occurrence caused by the renewal, still fails. Changed because the money notice is new |
| `test_two_payment_pages_paid_for_one_move_leave_one_live_plan[pair]` | **new** | — | one live plan; the first bought plan cancelled; two cancel calls in order; two money records; one staff notice per money record |
| `test_a_move_that_cannot_be_finished_safely_changes_nothing_and_alerts[2 refusals]` | **new** | — | payment rolled back, event `failed`, exactly one alert `billing:plan-move-failed:{event}` (critical, open, says "the new plan was not given", evidence `billing_error:{code}`), no money record, the live plans untouched, no payment-company call |
| `test_failed_card_cancellation_is_visible_and_reprocessed` | assertions **added**; one count made exact by name | after the failed cancel: `OperationalIssue` count `== 1`. After the retry: one money record, one live plan | after the failed cancel: the one alert is `billing:plan-move-failed:move-cancel-failed` (still exactly one), critical, open, says the charge "could not be stopped after retries" and "the new plan was not given", evidence carries the code. After the retry: the same, **plus** the failure alert is `resolved` and the money notice is `open` |

### tests/unit/test_operational_issue_queue.py

| Test | Kind | Expectation |
|---|---|---|
| `test_the_queue_and_the_content_check_hold_one_length_limit` | **new** | a summary and a reason of exactly 200 characters are accepted; 201 is refused by the queue's own rule with its own message; an alert rule with 340 characters of text is recorded, cut to 200. Fails at HEAD: the queue said 240, the content check said 200 |

Nothing else in the file changed.

### tests/integration/test_invariant_pages_close_their_tags.py (new file, second pass)

Two corrections made before the file ever passed:

| What | First draft | Now |
|---|---|---|
| `test_the_checker_finds_the_stray_end_tag_it_exists_for` | expected two messages for one stray `</div>`: the stray tag, then a cascade message at the next end tag | expects the one message at the stray tag. The checker sets a stray end tag aside, so one mistake is reported once. A second case was **added**: an end tag left out (`<section><div>` closed by `</section>`) gives three exact messages |
| `PUBLIC_PAGES` | `/`, `/signup`, `/signup/password` | `/`, `/signup`, `/signup/password?email=…&name=…`, `/signup/verify?email=…`, `/signin`, `/signin/code`, `/reset-password`. Opened without an address, `/signup/password` sends the person back to step one (303). The test now opens it the way the journey does, and four sign-in and sign-up pages were added |

Helpers, input only:

| Helper | Old | New |
|---|---|---|
| `_prepare_new_checkout` | request key always `move-{user}-{plan}` | optional `request_key`; the default is the old key |
| `_paid_event` | new subscription id `new-creem-{user.id}` | `new-creem-{attempt.id}`; optional `starts_in_days` (default 0). Two payments by one person are two subscriptions at Creem, and the old id made them one row. Every existing caller makes one payment per person, so their result is the same |

### tests/unit/test_billing_entitlements.py

Server settings only, in six tests (nine cases). No assertion was touched:
`test_nowpayments_finished_ipn_creates_subscription`,
`test_nowpayments_partial_payment_never_grants_access`,
`test_nowpayments_settlement_uses_actual_crypto_received[2]`,
`test_checkout_allows_both_paid_plans_monthly_and_nothing_else`,
`test_verified_payment_must_match_checkout_amount_and_currency[3]`,
`test_nowpayments_webhook_sends_single_admin_payment_notification`.

| Old input | New input |
|---|---|
| each wrote its own `Settings(...)`: crypto on, but billing **off** (5 of 6), **no NOWPayments key** (6 of 6), no secret to prove a payment (4 of 6) | `_crypto_only_server()`, built from `live_billing_overrides()` — the one shared description of a server that can take money — with card off and the shared webhook secret the IPN tests sign with |

Why: since HEAD, checkout asks `plan_checkout_availability` — the page's own question —
whether this server can really sell the plan this way. These six servers could not, so all
nine cases failed at HEAD with `plan_not_available` before they reached what they test.
They passed at the baseline only because checkout did not ask.

New: `test_checkout_takes_crypto_exactly_when_the_plan_page_offers_it[plan × 4 server
shapes]` — the page's answer is pinned for each shape, and checkout must agree with it.

### tests/browser/conftest.py (second pass)

| Helper | Old | New |
|---|---|---|
| `seed_paid_monitor_access` | a paid subscription only, with no payment behind it | the subscription **and** the completed payment that bought it: same plan, same payment company, the plan's own price. Input only; no assertion in any browser test was touched |

Why: the first pass made every page refuse a move for a paid plan with no payment record
("We cannot confirm what you paid for your current plan"), the same refusal the checkout
route gives. Every paid-holder browser test seeded exactly that impossible account, so each
would have photographed and asserted the refusal instead of the state it exists for. The
journey file's `_grant_paid_plan` got the same change in the first pass. The one other
caller (`test_setup_observability_desktop_mobile_and_visual_qa`) only needs paid access, and
a payment row does not change what it checks.

### tests/unit/test_invariant_dashboard_guide_registry.py

Unchanged. It failed at HEAD (`settings.html:57 is inside a loop or macro`); the template
was fixed, not the test.

## Third pass, 2026-09-11

These come from the vision review of the checkout screenshots. None is the owner's plan-move
rule, and no existing assertion was changed.

### tests/integration/test_held_plan_renewal_words.py (new file)

| Test | Cases | What it holds |
|---|---|---|
| `test_a_paid_plan_promises_renewal_only_when_it_really_renews[server × plan × company/cycle × running/stopped]` | 72 | On both account pages, the words promise a renewal exactly when the plan held was bought through a company that renews and its renewal was not stopped. They say "month" or "year" from the payment that bought it. Checked on a server that sells by card, one that sells by crypto only, and one that sells nothing |
| `test_access_nobody_pays_for_never_promises_a_renewal[server × shape]` | 24 | The free plan, a trial, and a plan Hilal Markets gave (with an end, with an end and the stop flag, with no end), on both pages and all three servers |

Why: the S4–S6 screenshots showed "Automatic renewal" on the billing page of a customer who
paid by crypto. Both pages asked the payment company this server would use for a new sale.

The two existing checks on these words pass unchanged:
`test_how_the_plan_ends_is_one_sentence_not_three_tiles` (a fresh account is on the free
plan) and the "Free forever" check in `tests/integration/test_dashboard_web.py`.

### tests/browser/conftest.py (third pass)

| Helper | Old | New |
|---|---|---|
| `seed_paid_monitor_access` | the payment's cycle was always `monthly_auto_renewal` | `monthly_auto_renewal` for a company that renews (Creem, Stripe), `one_time_30_day` for any other — the cycle that company really writes. Input only |

### tests/browser/test_checkout_pay_button_e2e.py (third pass)

Assertions **added** only. Nothing removed, and no existing expectation changed.

| Test or helper | Added |
|---|---|
| `_take_screenshots` (every screenshot test, S1–S7, at all three sizes) | before each picture, no element inside a plan tile (`.checkout-limit-grid article`), a payment card (`.billing-method-card`) or a plan fact on the billing page (`.billing-current-meta > span`) may run out of its tile or be cut short, measured from the drawn boxes. At 1024px a tile's number and the note "Paying by crypto is switched off just now." ran out of their boxes, and the page-wide sideways-scroll check could not see it. The plan facts ended in "…" when too long |
| `_take_screenshots`, once per state | every word on a choice that cannot be made (an unavailable way of paying or interval) measures at least 4.5:1, computed with `tests/support/contrast.py`. The card was faded to 55%, which took its reason to 2.2:1 |
| `test_screenshot_s4_billing_pro_card_for_a_crypto_holder` | the billing page's Renewal tile reads `Manual 30-day renewal` (a plan not bought by card). It read "Automatic renewal" |
| `test_screenshot_s5_billing_pay_buttons_for_a_card_holder`, `test_screenshot_s6_downgrade_pay_note` | the Renewal tile reads `Automatic renewal, every month` (a monthly card plan that still renews) |

### tests/browser/test_ask_ai_corner_tag_e2e.py (third pass; the file is new in this run)

| Test | Old | New |
|---|---|---|
| `test_screenshots_of_the_ask_ai_surfaces_and_the_subscription_page` | a fresh account; it stopped at Opportunities, whose empty state has no filter bar and so no Ask AI button | the account is given findings first (`seed_setup_observability`), so Opportunities draws its bar and its button. Every assertion unchanged. One assertion **added**: the run has its own database. Without it the seed helper would skip the test when pointed at an outside server, and a missing screenshot would pass as skipped |

## Fourth pass, 2026-09-11

From the same screenshot review. Tests **added** only. Nothing removed, and no existing
expectation changed.

### tests/integration/test_checkout_and_payment_email.py

| Test | Cases | What it holds |
|---|---|---|
| `test_the_checkout_names_each_limit_as_every_other_page_does[trader, pro]` | 2 | The checkout review's limit tiles use the names in `PLAN_LIMIT_WORDS` (`core/plans.py`), the same names the subscription page uses, and no tile says "Watchlist". The first tile read "Active Watchlists" |
| `test_the_receipt_names_each_limit_as_every_other_page_does[trader, pro]` | 2 | The payment receipt really sent after a verified payment lists the same names, in both the text and the HTML part. It read "Active Watchlists" and "Markets per Watchlist" |

The existing check `assert "Create a Watchlist" in payment_messages[0]["body"]` in
`test_verified_static_payment_activates_once_and_emails_once` is unchanged and still passes:
that is a link, not a limit name (see the final report).

### tests/browser/test_checkout_pay_button_e2e.py (fourth pass)

| Helper | Added |
|---|---|
| `_TILES` (used by `_take_screenshots` in S1–S7) | the payment history's name line (`.billing-history-primary`) and its fact boxes (`.billing-history-facts > div`). At 1024px they ended in "…" ("Card via St…", "Monthly Au…") |

### tests/browser/test_ask_ai_corner_tag_e2e.py (fourth pass)

| Test | Added |
|---|---|
| `test_the_landing_page_corner_button_wears_the_ask_ai_label[v1, v2, v3]` | after the scroll to the bottom of the page: a wait until the smooth scroll has stopped (`_wait_for_scroll_to_rest`), a record of where every corner item really is (`landing_corner_after_scroll.json`, written before any check), and two new checks: the label's top is on the screen, and the circle's bottom is on the screen. The pictures showed both below the bottom edge while the test passed. The existing check (back to top sits above the label) is unchanged |

## New test files

- `tests/unit/test_invariant_templates_compile.py` — every template compiles in every
  environment (the release-gate script, now in the offline suite).
- `tests/browser/test_ask_ai_corner_tag_e2e.py` — the corner label, and the D screenshots.
- `tests/integration/test_invariant_pages_close_their_tags.py` and its helper
  `tests/support/html_balance.py` — every public and signed-in page closes each tag where
  it opened it (second pass).
- `tests/integration/test_held_plan_renewal_words.py` — what both account pages say about
  renewal, for every shape of held access (third pass).
