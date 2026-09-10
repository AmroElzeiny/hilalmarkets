# Owner decision — WP8, paying early on the card path

Date: 2026-09-10
Decided by: the product owner, in answer to `ESCALATION.json` from run
`20260910T164746Z-f76e4f88`.
Status: **the escalation is resolved. WP8 is unblocked, for card and for crypto.**

## What was blocked, and why the block was real

The owner's rule is: moving to a **different** paid plan charges the **full price** of
the new plan today, starts the new plan **today**, ends the old plan's days, and emails
the money value of those unused days back **within 48 hours**.

Creem cannot do that through its subscription-upgrade endpoint. It offers exactly three
behaviours and every one of them is prorated:

| `update_behavior` | Charged today | Restarts the period? |
|---|---|---|
| `proration-charge-immediately` (default) | the **difference** only | no |
| `proration-charge` (deprecated, identical) | the **difference** only | no |
| `proration-none` | **nothing** | no |

Claude checked this against Creem's own published API reference on 2026-09-10 and
confirms the run's finding. The first two are exactly what the owner ruled out. The
third charges nothing today. **There is no full-price-restart behaviour.**

## The decision

**Card: end the old plan, then take a fresh full-price payment for the new plan.**
The old subscription is cancelled and a normal checkout is used, rather than asking
Creem to re-price the existing subscription. This is the only route that produces the
rule the owner asked for.

**Crypto: build it now.** A crypto holder is already allowed to buy a different plan —
`must_switch_instead` is false for them, because there is no card to re-price. Nothing
about crypto was ever blocked. It ships without waiting for the card work.

## The order of operations is not a detail — it is the whole safety argument

Doing these steps in the wrong order harms a real customer. **This order is mandatory.**

1. The customer chooses the new plan and **completes payment first** — a normal
   checkout, at the **full price** of the new plan.
2. **Only when that payment is confirmed** (the payment company's webhook says paid, and
   the amount and currency have passed the checks that already exist):
   a. cancel the old subscription **at once**, so it can never renew;
   b. end the old plan's period;
   c. work out the money value of its unused days;
   d. write the money-owed record;
   e. queue the email that promises it within 48 hours.
3. All of step 2 happens in **one transaction**. If any part fails, none of it is
   recorded, and the failure is raised loudly.

**Never cancel the old plan before the new payment is confirmed.** If the customer
abandons the checkout, or their card is declined, they would be left with **no plan at
all** and days they had already paid for gone. That is worse than the problem this
mission set out to fix.

### The two ways this can lose money, and what must prove they cannot happen

| Failure | What it costs | What must prove it cannot happen |
|---|---|---|
| Old subscription not cancelled after the new one starts | the customer is charged **twice every month, for ever** | a test asserting that after the move, exactly **one** live subscription and **one** recurring charge remain, for every plan crossed with every other plan |
| Old plan cancelled but the new payment never completes | the customer is left with **nothing**, having paid | a test that abandons and that fails the payment, asserting the old plan is **untouched and still live** in both cases |

If the cancellation call to the payment company fails after the new plan is live, the
system must retry, and if it still fails it must raise an alert a person will see. It
must **never** quietly leave two live subscriptions. A silent failure here is the most
expensive defect this mission can ship.

## What does not change

- Paying again for the **same** plan stays refused.
- Free plans, trials and admin grants owe nothing back — no money was paid.
- Nothing is ever refunded automatically. The amount is worked out, written down, and
  emailed. A person sends it by hand.
- The money owed back is based on **what the customer really paid**
  (`BillingCheckoutAttempt.amount`, the already-discounted figure), never the catalogue
  price.

## One known cost of this decision, to be measured and reported

A fresh checkout may ask the customer to enter their card details again, where the old
switch flow did not. Find out whether Creem reuses a known customer's saved details for
a new checkout, say plainly what you found, and report it. Do **not** change the
decision because of it — report it and let the owner judge.
