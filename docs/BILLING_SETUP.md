# Billing Setup

Hilal Markets supports provider-specific checkout by payment method:

- Card subscriptions: Creem.
- Crypto payments: NOWPayments one-time 30-day invoices.
- Stripe remains available for existing deployments.

No checkout redirect grants access. A signed, idempotently recorded provider webhook must match a
server-created checkout attempt before a subscription or receipt email is created.

## Creem Card Subscriptions

1. Create separate Creem products for:
   - `trader_monthly` (customer-facing **Plus**, monthly)
   - `trader_annual` (customer-facing **Plus**, annual)
   - `pro_monthly` (customer-facing **Pro**, monthly)
   - `pro_annual` (customer-facing **Pro**, annual)

   Only the two monthly products are needed today: nothing is sold by the year, so both
   annual keys may be left out. Plan names come from `core/plans.py` — check them there
   rather than trusting this list, which said "Monitor" for months after the rename.

   **Each Creem product's price must equal what `core/plans.py` charges today.** While
   the launch offer runs that is the launch price, not the normal one, because the offer
   no longer needs a code: the lower figure simply *is* the price until
   `PROMOTION_ENDS_AT`. When that date passes, the two Creem products have to be
   re-priced to the normal figures by hand, or the site will show one price and Creem
   will charge another.
2. Optionally configure `trader_trial` in Creem as a seven-day recurring trial. The application
   does not invent or override provider product terms, and leaves the trial CTA unavailable until
   that exact product is configured.
3. Set server-only values:

   ```env
   BILLING_ENABLED=true
   BILLING_CARD_PROVIDER=creem
   CREEM_API_KEY=<rotated server secret>
   CREEM_WEBHOOK_SECRET=<Creem signing secret>
   CREEM_PRODUCT_IDS={"trader_monthly":"prod_...","trader_annual":"prod_...","pro_monthly":"prod_...","pro_annual":"prod_..."}
   CREEM_API_BASE=https://api.creem.io
   ```

   A seven-day trial product is optional. Keep the trial CTA unavailable until
   its exact provider terms have been separately verified and configured.

4. Configure the webhook URL:
   `/api/v1/billing/webhooks/creem`
5. Subscribe to the required checkout, subscription, payment, cancellation, refund, and dispute
   events.
6. Reconcile each product's price, currency, billing interval, and trial setting against the
   application plan catalog before enabling checkout.

The application calls `POST /v1/checkouts` for every checkout attempt. The attempt UUID is sent as
Creem's `request_id`, so every order receives a unique hosted checkout URL while remaining
idempotently bound to the authenticated user, plan, interval, and canonical amount.

Creem webhook signatures use HMAC-SHA256 over the raw body from the `creem-signature` header.
`subscription.trialing` may activate the configured trial. `subscription.paid` is required for a
paid entitlement and payment receipt. A returned success page without that signed state remains
pending.

Official references:

- <https://docs.creem.io/api-reference/endpoint/create-checkout>
- <https://docs.creem.io/code/webhooks>
- <https://docs.creem.io/features/trials>
- <https://docs.creem.io/features/customer-portal>

## Discount codes

**The launch price needs no code.** It used to: a customer typed `HILAL25` to reach the
lower figure. That code is withdrawn — it is listed in `RETIRED_DISCOUNT_CODES` in
`core/plans.py`, and tests refuse to let it appear on any page. The launch price is now
simply the price until `PROMOTION_ENDS_AT`, on the card, at checkout and on the crypto
invoice alike.

Codes still exist for partners and campaigns. They are listed per deployment:

```env
BILLING_DISCOUNT_CODES=TINYTALES=30
```

The two routes apply a code in two different places, and both have to be set up:

| Route | Where the code is typed | Who applies it |
|---|---|---|
| Crypto (NOWPayments) | the box on our own checkout screens | this application, before the invoice is created |
| Card (Creem) | the discount box on Creem's hosted page | Creem |

So **a code offered on a card route must also exist in Creem, at the same percentage**, or
a card buyer is shown an offer they cannot get. Create it in the Creem dashboard as a
*percentage* discount, applying to the products in `CREEM_PRODUCT_IDS`.

Codes are looked up in Creem first and in that list second. A code Creem *refuses* —
expired, switched off, used up, fixed-amount, or for another product — is refused here too
and never falls through to the list.

Two rules govern that line:

- **No withdrawn code may be listed.** Anything in `RETIRED_DISCOUNT_CODES` is refused at
  start-up. A code a page no longer advertises but the list still honours is money given
  away by accident.
- **The list has no end date.** A code written here keeps working until it is removed by
  hand. If a campaign should stop on a date, take it out of the list on that date.

The two example files ship this blank on purpose. A real code belongs in `.env` and
`.env.production`, which are not in git.

Check both sides agree before enabling or changing anything:

```bash
.venv/Scripts/python scripts/check_creem_prices.py --env-file .env.production
```

It compares every product's price with `core/plans.py` and checks that the launch code
exists in Creem at the right percentage. Nothing offline can see Creem, so this command is
the only thing that can catch a disagreement — and a disagreement means a customer pays and
the plan never starts.

## NOWPayments Crypto Invoices

Set:

```env
BILLING_ENABLED=true
BILLING_CRYPTO_PROVIDER=nowpayments
NOWPAYMENTS_API_KEY=<server secret>
NOWPAYMENTS_IPN_SECRET=<IPN signing secret>
NOWPAYMENTS_BASE_URL=https://api.nowpayments.io
```

Configure `/api/v1/billing/webhooks/nowpayments` as the IPN callback. NOWPayments invoices in this
implementation purchase 30 days of access once. They do not create an automatically renewing
subscription or customer cancellation portal.

The server validates checkout ID, user ownership, plan, amount, currency, settlement, and the
HMAC-SHA512 IPN signature before granting access.

## Receipts

One verified payment period creates at most one `PaymentEmailDelivery`. The worker sends the
branded receipt to the user's verified primary email. Provider retries and duplicate webhooks
reuse the same event key and cannot create duplicate logical receipts.

## Deployment Safety

- Keep API and webhook secrets in deployment secrets, never public runtime configuration.
- Rotate any credential pasted into chat, tickets, or terminal history before production use.
- Use `https://test-api.creem.io` for sandbox acceptance and `https://api.creem.io` only in
  production.
- Run the Alembic migration before enabling checkout.
- Test monthly, annual, trial, cancellation, failed payment, refund, dispute, duplicate webhook,
  and delayed webhook paths in provider sandbox.
- Keep `BILLING_ENABLED=false` until product reconciliation and webhook delivery are complete.
