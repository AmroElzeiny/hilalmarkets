# Mission — Pro plan cannot be paid for; upgrade and downgrade must work in practice

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, entitlements, payment providers, money)
Date: 2026-09-10

## User outcome

A customer on Hilal Markets can buy the Pro plan with a card **and** with crypto, and the
payment really completes as intended. Upgrading and downgrading between plans works in
practice, not only in theory. No payment control anywhere may sit dead and silent.

The reported words, kept exactly: *"The payment for card and crypto in the Pro plan do not
work at all, no matter how many time I click to pay, it doesn't respond."*

## Runtime/source-of-truth authority

Inspect these before deciding anything. They are the authority; a model's memory is not.

**Payment surfaces (there are two — both are in scope):**
- `src/ai_market_monitor/templates/hilal/dashboard/checkout.html` — the checkout page, a plain form POST
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html` — the billing page and its dialogs
- `src/ai_market_monitor/static/hilalmarkets-billing.js` — drives the billing dialog
- `src/ai_market_monitor/static/hm-plan-change.js` — drives plan switching
- `src/ai_market_monitor/static/hm-discount-code.js`
- `src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css`, `hilalmarkets.css`
- `src/ai_market_monitor/templates/hilal/macros/payment_provider_badge.html`
- `src/ai_market_monitor/templates/hilal/dashboard_test/subscription.html`, `static/hm-subscription-test.js`

**Server side:**
- `src/ai_market_monitor/api/routers/billing.py`
- `src/ai_market_monitor/api/routers/dashboard.py`, `dashboard_api.py`, `dashboard_test.py`
- `src/ai_market_monitor/services/billing.py`
- `src/ai_market_monitor/services/plan_changes.py`
- `src/ai_market_monitor/services/entitlements.py`
- `src/ai_market_monitor/services/discount_codes.py`
- `src/ai_market_monitor/core/plans.py` — `plan_is_on_sale` is the one owner of whether a plan is on sale
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/db/models/commercial.py`

**Rules:**
- `CLAUDE.md`, `AGENTS.md`, `.agents/commands.json`
- `docs/BILLING_SETUP.md`
- `scripts/check_creem_prices.py`

**Settings that already exist and are populated in both `.env` and `.env.production`:**
`BILLING_ENABLED`, `BILLING_PROVIDER`, `BILLING_CARD_PROVIDER`, `BILLING_CRYPTO_PROVIDER`,
`CREEM_API_KEY`, `CREEM_PRODUCT_IDS`, `CREEM_WEBHOOK_SECRET`, `NOWPAYMENTS_API_KEY`,
`NOWPAYMENTS_IPN_SECRET`, `STRIPE_PRICE_IDS`.

Claude checked key **names and set/empty state only**, never values. Missing credentials are
**not** the cause: the card and crypto credentials are present in both files. Two keys are
empty in both files — `BILLING_WEBHOOK_SECRET` and `STRIPE_SECRET_KEY`. Decide from the code
whether either is actually required on a live payment path; if one is, that is a finding.

## What Claude already established (read-only, confirm or refute it — do not assume it)

These are leads with file and line references, not conclusions. Reproduce first.

| # | Observation | Where |
|---|---|---|
| L1 | The billing dialog's pay button is rendered `disabled` and only JS can enable it. | `billing.html:300` |
| L2 | It is enabled only when a method is selected: `submit.disabled = submitting \|\| !selectedMethod`. If neither card nor crypto is available, it stays disabled for ever and clicking does nothing at all. | `hilalmarkets-billing.js:240` |
| L3 | Card and crypto availability are both gated by one shared `purchasable` flag, so one wrong value kills both ways of paying at once — which matches "card and crypto do not work". | `hilalmarkets-billing.js:179-180` |
| L4 | On the checkout page the native radios carry `required` but are painted invisible (`opacity:0`, 1×1 px). If no radio ends up checked, the browser refuses to submit and cannot focus the hidden control, so the click does nothing and only the developer console says why. | `checkout.html:105`, `hilalmarkets-dashboard-v2.css:2490` |
| L5 | The checkout page has a correct "there is no way to pay for this plan yet" notice. The billing dialog has no equivalent — it just shows a dead button. | `checkout.html:79-89` |

## Requirements coverage

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | **Reproduce before fixing.** Show the Pro plan failing the way the user describes, from the real product path, before any code changes. | An automated test or captured browser run that fails on current `HEAD`, showing the pay control not responding for Pro. Record which surface was used and the exact state of the method decisions. State plainly whether the checkout page, the billing dialog, or both are affected. |
| R2 | **Root cause named, not guessed.** Explain why both card and crypto resolve to unusable for Pro. | The decision path traced through the server code to the single value that is wrong, with the file and line. "Looks like" is not a root cause. If the leads above are wrong, say so and give the real cause. |
| R3 | **Card payment works for Pro.** A Pro card purchase reaches the card provider and a real checkout session is created. | A test proving the route returns a usable provider checkout URL for Pro + card, plus one real session-creation call against the configured card provider showing the URL it returned. Do **not** complete a real charge — see "Money" below. |
| R4 | **Crypto payment works for Pro.** Same, through the crypto provider. | Same shape of evidence for Pro + crypto. |
| R5 | **Fix the defect class, not the Pro instance.** No payment or plan control anywhere may be dead and silent. Every control that cannot succeed must say why, in words a beginner understands. | Every pay/switch/cancel control audited: checkout page, billing dialog, plan-switch dialog, cancel dialog, the dashboard purchase buttons, and the test-account subscription page. A parametrised test across **every plan × card and crypto × monthly and annual** proves that each combination either works or explains itself. A combination that is silently dead must fail the test. |
| R6 | **The hidden-required-control class is closed.** A form must never be blocked by a control the user cannot see or focus. | A test that submits each payment form with nothing selected and proves the user gets a visible message, not silence. Applies to every form named in R5. |
| R7 | **Upgrade works in practice.** Moving to a higher plan really changes what the customer gets and what they are charged. | End-to-end evidence: entitlements before and after, the money effect (proration or the product's stated rule), the record written, and the customer-visible confirmation. Cover upgrade while on monthly and while on annual. |
| R8 | **Downgrade works in practice.** Same, in the other direction, including what happens to the paid-for period already bought. | Same evidence shape. Must state clearly when the change takes effect and prove the code does that, not something else. Include downgrade to the free plan if the product allows it. |
| R9 | **A page may never offer a way of paying that checkout would refuse.** | A test tying what the page offers to what the server would accept, so the two can never drift apart again. This rule already exists in the product; prove it still holds after the fix. |
| R10 | **One owner for availability.** Whether a plan can be bought, and by which method, is decided in exactly one place and every caller imports it. | Show the owner. Show that no second copy of the decision survives anywhere — page, dialog, route, or JS. If the fix creates a second copy, it is the wrong fix. |
| R11 | **Settings rule honoured.** If any setting is added, renamed, or has its default changed, all four files are edited together. | `.env.example`, `.env.production.example`, `.env`, `.env.production`. Back up the two real files first, prove the key count rose by exactly the number added, and that no existing value changed. **Never print a value.** If no setting changes, say so. |
| R12 | **No test weakened, and the suites are green.** | Section 7 of the report contract answered explicitly, plus lint, types, and the offline suites passing. |

## Non-negotiable invariants

- **Money.** Create real provider checkout sessions to prove the path works. Do **not**
  complete a real charge and do not spend real money. If a completed live payment is the only
  way to prove something, stop and escalate — that decision is the user's, not yours.
- **Secrets.** Never open, print, quote, or log a value from `.env` or `.env.production`.
  Key names only. This includes error messages and test output.
- **Never weaken a test to fit the implementation.** No skip, no xfail, no widened assertion,
  no special case. If a test now fails because behaviour genuinely changed, the product
  behaviour is the authority and the reasoning must be written down.
- **Fail closed.** If a payment method cannot be offered honestly, refuse it and say why.
  Never fall back to a default method, never guess a provider, never offer a method the
  server would reject.
- **No Sharia status is created, inferred, or implied anywhere in this work.**
- Copy rules are enforced by `core/copy_rules.py`: the name in prose is **Hilal Markets**;
  technical usage says **Shariah**; the forbidden-claims list applies to every word shown to
  a customer. Any new message must pass those rules.
- Write for a beginner. A person buying this does not know what "provider", "entitlement",
  "proration" or "capability" mean. Error text must say what happened and what to do next.
- Do not touch unrelated work in the tree. It carries many uncommitted changes.
- Migration constraint and index names go through `op.f()` if any migration is added.

## Work packages

### WP1 — Reproduce and name the root cause (R1, R2)
Objective: Make the failure happen on demand, then find the one value that causes it.
Scope: read the whole payment decision path end to end; write a failing test.
Do not change: product behaviour yet — this package only proves and explains.
Acceptance criteria: a test that fails on current `HEAD` for the Pro plan; the root cause
traced to a file and line; a clear statement of which surfaces are affected.
Evidence required: the failing output, the traced path, the wrong value.
Risk: low.
Escalate if: the cause turns out to be a missing credential or a commercial decision (a price,
a product ID that was never created at the provider). That is the user's call, not yours.

### WP2 — Make Pro payable by card and by crypto (R3, R4, R10)
Objective: Fix the cause so both ways of paying really work for Pro.
Scope: the server-side availability decision and its one owner, plus the callers.
Do not change: unrelated plans' pricing, or anything about what a plan includes.
Acceptance criteria: Pro + card and Pro + crypto each produce a usable provider checkout
session; the availability decision has exactly one owner; no duplicate copy of the rule
remains.
Evidence required: tests, plus one real session-creation call per provider showing the URL
returned. No completed charge.
Risk: high — this is money.
Escalate if: fixing it needs a new product or price to be created at the payment company.

### WP3 — Close the silent-dead-control class (R5, R6, R9)
Objective: No control that cannot succeed may fail silently, anywhere in the product.
Scope: every pay, switch and cancel control on every surface listed in the authority section.
Do not change: the visual language. Match what the shipped pages already do — the checkout
page's existing "there is no way to pay for this plan yet" notice is the pattern to follow.
Acceptance criteria: a parametrised test across every plan × method × billing period; each
combination either works or shows a readable reason; no combination is silently dead. The
hidden-required-control trap is closed on every form.
Evidence required: the parametrised test and its output; before/after screenshots of a
blocked state.
Risk: high — this is the class behind the reported bug.
Escalate if: honouring this needs a product decision about what to tell a customer when a
plan is deliberately not for sale.

### WP4 — Upgrade and downgrade proven in practice (R7, R8)
Objective: Prove both directions really work, including the money and the access effects.
Scope: `services/plan_changes.py`, `entitlements.py`, the switch dialog and its route.
Do not change: the product's stated rule about when a change takes effect — find it, follow
it, and if the code disagrees with the stated rule, that disagreement is a defect to report.
Acceptance criteria: for upgrade and for downgrade, evidence of entitlements before and
after, the charge or credit effect, the record written, the customer-visible confirmation,
and when it takes effect. Monthly and annual both covered. Include downgrade to free if
the product allows it.
Evidence required: end-to-end tests with captured output.
Risk: high — a wrong proration charges a real person the wrong amount.
Escalate if: the product's intended proration rule cannot be found in the code or the docs.
Do not invent one.

### WP5 — Visual proof of the payment states (R5)
Objective: Prove with pictures that a customer always sees why they cannot pay.
Scope: screenshots only; no new visual language.
Do not change: colours, spacing or type. Follow `.hm-orchestrator/current/VISUAL_CONTRACT.md`.
Acceptance criteria: screenshots at the viewports named in the visual contract, for each
state it lists; a vision-capable reviewer checks them against that contract.
Evidence required: the screenshot files and the vision reviewer's findings.
Risk: normal.
Escalate if: screenshots cannot be produced, or no vision-capable reviewer can actually look
at them. Then mark visual verification **unverified** — never infer a visual pass from CSS.

### WP6 — Regression, integrity and review (R11, R12)
Objective: Prove nothing else broke and nothing was quietly weakened.
Scope: lint, types, the offline suites, plus the billing and dashboard suites; the four-file
settings rule if any setting changed.
Acceptance criteria: `.venv/Scripts/python -m ruff check src tests scripts`,
`.venv/Scripts/python -m mypy src`, and
`.venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly`
all pass, plus the billing-related integration tests. Section 7 of the report contract
answered explicitly.
Evidence required: captured output with pass/fail counts.
Risk: normal.
Escalate if: a pre-existing failure is found that is unrelated to this work — fix it if you
can, and say so; only escalate if it needs a decision that is the user's.

## Verification floor

- Reproduce before fixing. A fix with no failing test before it is not proven.
- Parametrise across the family: every plan × card and crypto × monthly and annual. A fix that
  only helps Pro must fail the test.
- Real provider session creation for card and for crypto, one call each, no completed charge.
- Adjacent tests discovered from every touched file.
- Lint, types, offline suites, billing integration tests.
- Final diff review and a requirement coverage audit.

## Reviewer policy

Risk: **high**, and partly visual. Three reviewers, and they must not be the coder:

1. Independent logic reviewer — `opencode-go/deepseek-v4-pro`.
2. Adversarial reviewer from a different family — `opencode-go/glm-5.3-flash`. Its job is to
   attack the money path: wrong amount, wrong plan, double charge, a payment that succeeds at
   the provider but leaves the customer without access, a method offered that would be refused.
3. Vision reviewer — `opencode-go/deepseek-v4-flash-vision-exp`, on the WP5 screenshots
   against the visual contract. It must actually look at the images.

Every finding is closed or formally escalated. None may be left merely described.

## Completion condition

Do not report complete until R1–R12 each carry PASS evidence or a named blocker, and every
reviewer finding is closed or escalated.

Expected verdict: `COMPLETE_VERIFIED`.

Use `COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM` only for something that genuinely cannot be
proven without spending real money, and name it exactly.

If the cause turns out to be a commercial decision — a price, a product that does not exist at
the payment company, or a plan deliberately not for sale — write `ESCALATE_TO_CLAUDE` with the
decision needed. Do not invent a commercial answer.
