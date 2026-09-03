# Billing Setup

Hilal Markets supports provider-specific checkout by payment method:

- Card subscriptions: Creem.
- Crypto payments: NOWPayments one-time 30-day invoices.
- Stripe remains available for existing deployments.

No checkout redirect grants access. A signed, idempotently recorded provider webhook must match a
server-created checkout attempt before a subscription or receipt email is created.

## Creem Card Subscriptions

1. Create separate Creem products for:
   - `trader_monthly` (customer-facing **Monitor**, monthly)
   - `trader_annual` (customer-facing **Monitor**, annual)
   - `pro_monthly`
   - `pro_annual`
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

The plan costs its normal price. A **code** brings it down — the launch code and its
percentage live in `core/plans.py` (`LAUNCH_DISCOUNT_CODE`), which every pricing card and
every checkout reads.

The two routes apply a code in two different places, and both have to be set up:

| Route | Where the code is typed | Who applies it |
|---|---|---|
| Crypto (NOWPayments) | the box on our own checkout screens | this application, before the invoice is created |
| Card (Creem) | the discount box on Creem's hosted page | Creem |

So **the same code must exist in Creem, at the same percentage**, or a card buyer is shown
an offer they cannot get. Create it in the Creem dashboard as a *percentage* discount named
exactly as `LAUNCH_DISCOUNT_CODE`, applying to the product in `CREEM_PRODUCT_IDS`.

Codes that only apply to crypto are listed per deployment instead:

```env
BILLING_DISCOUNT_CODES=HILAL25=25,TINYTALES=30
```

Codes are looked up in Creem first and in that list second. A code Creem *refuses* —
expired, switched off, used up, fixed-amount, or for another product — is refused here too
and never falls through to the list.

Two rules govern that line:

- **The launch code may be listed, but only at the number it already has.** `core/plans.py`
  owns what `LAUNCH_DISCOUNT_CODE` is worth, because the pricing cards derive the lower
  price from it. A different number here refuses to start, naming the code and both
  figures — a page promising one discount while checkout applies another is the exact
  fault this list is fenced against.
- **Listing it also outlives the launch window.** `core/plans.py` stops offering the launch
  code at `PROMOTION_ENDS_AT`; this list has no end date, so a code written here keeps
  working after the pricing cards stop advertising it. Leave it out of the list if it
  should stop on that date instead.

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
