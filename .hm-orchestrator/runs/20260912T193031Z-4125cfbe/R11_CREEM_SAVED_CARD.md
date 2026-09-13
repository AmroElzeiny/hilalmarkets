# R11 — Can Creem reuse a known customer's saved card on a new checkout?

**Status: BLOCKED. It cannot be answered from this repository or from Creem's public
documentation. A real purchase on the owner's live Creem account is needed.**

Date: 2026-09-13. Run: 20260912T193031Z-4125cfbe.

## What is already known (from the previous run, re-checked here)

- Our code never sends a saved card or a Creem customer id when it opens a checkout.
  Each replacement move opens a fresh Creem payment page with the customer's email locked.
- The exact sentences the owner asked about have not changed: the owner's decision of
  2026-09-10 is untouched.

## What the public documentation says (checked 2026-09-13)

- `docs.creem.io/features/checkout/checkout-api` — the checkout API lets you pre-fill the
  **email** and lock it at checkout, and apply a discount code. It says nothing about
  reusing a saved card or a saved payment method on a new checkout.
- `docs.creem.io/merchant-of-record/finance/payment-methods` — lists supported methods
  (Credit Cards, Apple Pay, Google Pay) and says the methods shown depend on product,
  location, billing address, price and device. It says nothing about saved-card reuse.
- A general web search returns Stripe documentation about saved cards (Stripe is a
  configurable card provider in this repository) but **no Creem statement**.

## The one sentence the report must carry

**Whether Creem reuses a known customer's saved card on a new checkout is unknown from
here; answering it needs one real purchase on the owner's live Creem account.**

## What is not done, on purpose

- No real payment was made.
- The owner's 2026-09-10 decision was not changed because of this.
