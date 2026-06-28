# Billing Setup

TraceEdge supports the billing provider abstraction in code, but the active launch
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

NOWPayments signs IPN callbacks using the `x-nowpayments-sig` header. The server verifies it with
HMAC-SHA512 over the alphabetically sorted JSON body using `BILLING_WEBHOOK_SECRET`.

## Stripe Legacy Support

Stripe classes remain in the billing abstraction for compatibility with existing tests and old
events. Do not use Stripe for the current launch unless the provider is intentionally switched back.

## Trial Rule

The beta trial is a conditional 14-day monitoring cycle. The user becomes trial-eligible during
onboarding, but the first cycle starts only when the first approved live monitor activates. If no
qualifying live setup alert is successfully delivered during a cycle, the cycle renews automatically
unless the no-alert outcome was caused by user-side ineligibility such as no active monitor or no
verified alert channel.
