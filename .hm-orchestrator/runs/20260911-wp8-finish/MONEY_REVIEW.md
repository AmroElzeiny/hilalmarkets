# Money-path review (item E)

Written by Claude, 2026-09-11, from the code and from tests that ran. Not an outside review.

How to read it: "Yes, fixed" means a problem was found in this run and a test now fails if
it comes back. "Safe" means the code was read and a test covers it.

## The six questions

| # | Question | Answer | Why, in plain words | Proof |
|---|---|---|---|---|
| 1 | Can a customer be charged twice, or have two live paid plans? | **Safe now. Two holes fixed in this run.** | The same plan cannot be bought twice: every page and checkout ask one owner (`plan_checkout_availability`). One checkout request key makes one payment page (the database refuses a second). Hole 1, fixed: when a renewal arrived for the new plan, the move ran again. Hole 2, fixed: if a person paid on two payment pages for one move, two plans stayed live. Now the second payment ends the first one, like any other move. | `test_renewing_the_new_plan_never_ends_or_repays_the_old_one_again[every pair]`, `test_two_payment_pages_paid_for_one_move_leave_one_live_plan[every pair]`, `test_confirmed_card_move_leaves_one_charge_and_one_money_record[every pair]` |
| 2 | Can a customer pay and get nothing? | **Only for a short time, and a person is told at once. Fixed in this run.** | If the old plan cannot be ended safely, the whole move is undone. The customer keeps the old plan and loses nothing they had, but they have paid for the new one. A critical alert is saved. Before this run, it said "Nothing changed", which hid that the customer had paid. It now says "the new plan was not given". When a later try succeeds (Creem sends the event again, or a person reprocesses it), the move finishes and the alert closes by itself. | `test_a_move_that_cannot_be_finished_safely_changes_nothing_and_alerts[2 reasons]`, `test_failed_card_cancellation_is_visible_and_reprocessed` |
| 3 | Can money owed be paid twice, or be more than the customer paid? | **Safe in the code.** | One money record per old plan and paid period (a fixed key; a repeat finds the old record). The database refuses an amount of zero or less, and an amount above what was paid. The maths uses exact decimals, rounded half up, and is capped at the amount paid. A person sends the money by hand. The system gives them one notice per record, so a repeated event cannot create a second notice. | database checks `ck_plan_move_owed_positive`, `ck_plan_move_owed_not_more_paid`; `money_owed_for_unused_time`; the two-pages test (two records, two notices) |
| 4 | Can the amount, the plan or the start date be wrong? | **Safe. One note for the owner.** | The price comes from the server's price list, never from the page. The new period starts on the day the payment is confirmed. **Note:** the money owed is based on the amount of the checkout that bought the old plan (`BillingCheckoutAttempt.amount`), as the owner decided. If that first payment had a discount code, the refund is based on the smaller, discounted amount, even when later renewals cost more. This follows the rule. If the owner wants the renewal price used instead, that is a new decision. | `test_checkout_uses_server_price_and_deduplicates_attempt`, `test_the_review_page_quotes_the_price_it_will_charge[plan]` |
| 5 | Can a page offer something the server then refuses? | **Safe now. Two holes fixed in this run.** | Hole 1, fixed: a paid plan with no payment on record showed a Pay button, but checkout refused it. Now every page shows the same refusal, in words. Hole 2, fixed: checkout let crypto through on a server that could not take it (billing off, no NOWPayments key, or no way to prove a payment). The page never offered it. Now the two always agree. | `test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page[pair]`, `test_page_offer_matches_server_acceptance[...]`, `test_every_plan_card_button_matches_the_server[held]`, new `test_checkout_takes_crypto_exactly_when_the_plan_page_offers_it[plan × 4]` |
| 6 | Does any sentence lie about the charge? | **Safe now. Two holes fixed in this run.** | Hole 1, fixed: the tick box says the full price is taken today, and the old plan ends only after this payment is confirmed. It also says a person will send the unused value within 48 hours. That promise had nothing behind it: the only message went to the customer. Now every money record also opens a staff notice with the amount and the deadline. Nobody without a paid plan sees the promise. Hole 2, fixed: both account pages say whether the plan renews by itself. They asked the payment company this server would use for a new sale, not the plan the person holds. So on a server that sells by card, a customer who paid by crypto read "renews by itself each month". A plan given free read the same. A card plan whose renewal was stopped still read "renews". A yearly plan read "each month". Now the words come from the plan held and the payment that bought it. | Hole 1: `test_every_checkout_tick_box_says_what_happens_to_the_plan_held[pair]`, `test_nobody_without_a_paid_plan_is_promised_money_back[3]`, the notice checks in the card-move test. Hole 2: `test_a_paid_plan_promises_renewal_only_when_it_really_renews[72]`, `test_access_nobody_pays_for_never_promises_a_renewal[24]` |

## Other things checked

| What | Result |
|---|---|
| Free plan chosen by someone who already pays | Harmless. `activate_free_plan` only adds a free plan. It charges nothing and never ends a paid plan. |
| Free plans, trials and admin grants on a move | They owe nothing. No money record is written. |
| A staff alert that is too long | Fixed. The failure alert once put the error code inside its sentence. One code made it longer than a record may be, so the alert itself failed. The code now goes in the evidence list. |

## Not verified

| What | Why |
|---|---|
| Does Creem reuse a customer's saved card on a new checkout? | Our code never sends a saved card or a customer id. Each move opens a new Creem payment page with the email locked. Creem's public docs (checkout and payment-method pages) do not say either way. Only a real purchase on the live Creem account can answer this. |
| How often, and for how long, Creem sends a failed event again | This is set on Creem's side. Our side finishes the move on any later try, and a person can reprocess it by hand. |
