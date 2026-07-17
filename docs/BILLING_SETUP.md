# Billing Setup

HilalMarkets supports the billing provider abstraction in code, but the active launch
configuration uses NOWPayments hosted crypto invoices.

## NOWPayments

1. Create or open a NOWPayments account.
2. Generate an API key in the NOWPayments dashboard.
3. Set:
   - `BILLING_ENABLED=true`
   - `BILLING_PROVIDER=nowpayments`
   - `NOWPAYMENTS_API_KEY`
   - `NOWPAYMENTS_BASE_URL=https://api.nowpayments.io`
   - `BILLING_WEBHOOK_SECRET` to the NOWPayments IPN secret from the dashboard
4. Configure the NOWPayments IPN callback URL:
   - `/api/v1/billing/webhooks/nowpayments`
5. Checkout uses `POST /v1/invoice` and redirects the user to the returned hosted invoice URL.
6. Payment confirmation must come from the NOWPayments IPN webhook, not from Telegram or a
   dashboard button click.

NOWPayments invoices in this build purchase **30 days of access once**. They do not create an
automatically renewing subscription, do not expose a cancellation portal, and do not imply a
future automatic charge. A new verified invoice extends access for another paid period. The
worker expires `cancel_at_period_end` access after the verified period ends.

NOWPayments separately documents a Recurring Payments API under `/v1/subscriptions`. This
repository does not call that API. The capability flags describe the implemented invoice adapter,
not every product offered by NOWPayments. Enabling recurring billing later requires a separate
adapter, catalog reconciliation, webhook tests, cancellation behavior, and customer-facing review.
See the [official NOWPayments API documentation](https://documenter.getpostman.com/view/7907941/2s93JusNJt).

Before activation, the server matches the signed event to its own checkout attempt and validates
the user, plan, amount, and currency. The default amount tolerance is zero and overpayments enter
manual review unless `BILLING_ALLOW_OVERPAYMENT` is explicitly enabled. Failed, expired,
refunded, duplicate, and mismatched events never create a second entitlement transition or
payment-success email.

NOWPayments signs IPN callbacks using the `x-nowpayments-sig` header. The server verifies it with
HMAC-SHA512 over the alphabetically sorted JSON body using `BILLING_WEBHOOK_SECRET`.

## Stripe Legacy Support

Stripe classes remain in the billing abstraction for compatibility with existing events. When
Stripe is intentionally selected, its subscription period and customer portal support automatic
monthly renewal and cancellation. Do not mix Stripe wording with a NOWPayments deployment.

## Trial Rule

The beta trial is a conditional 14-day monitoring cycle. The user becomes trial-eligible during
onboarding, but the first cycle starts only when the first approved live monitor activates. If no
qualifying live setup alert is successfully delivered during a cycle, the cycle renews automatically
unless the no-alert outcome was caused by user-side ineligibility such as no active monitor or no
verified alert channel.
