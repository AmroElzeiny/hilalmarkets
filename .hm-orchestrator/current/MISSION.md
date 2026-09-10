# Mission — "Ask AI" on four dashboard surfaces, the billing clean-up, and paying early

Run ID: assigned by delegate.ps1
Owner: Claude Code
Execution owner: OpenCode Go supervisor
Revision: 4 (2026-09-10) — continues after run `20260910T164746Z-f76e4f88`, applies
the owner's resolved card decision, adds the exact-cycle resume defect, and records
the work already verified in the tree so it is preserved rather than rebuilt.
This mission is now the **only** mission for this repository.

## User outcome

A person on the dashboard can open the Hilal assistant from the bar they are already
looking at, on Halal Assets, Create monitor, Opportunities and in every settings
group; on the billing page the dead jump bar is gone while every payment that did not
complete now offers a way to finish it; and a person who moves to a different plan
before their paid days run out pays the full price of the new plan, starts the new
plan today, and is told by email what money is coming back to them.

## State when this revision was written

Claude read the tree at commit `145275f2` with the usual uncommitted work present.
**Do not redo what is already there. Verify it, then finish the rest.**

| Work package | State | What Claude actually saw |
|---|---|---|
| Ask AI owner and four surfaces | **done — preserve and verify** | one macro, one CSS block, one handler, all required surfaces, and invariant tests are present |
| Subscription jump bar | **done — preserve and verify** | subscription-only markup and JavaScript are gone; shared settings `.a-jump` CSS remains |
| Settings jump bar | **done — preserve and verify** | one list drives all 8 links and all 8 headings, including `g-data` |
| Unfinished-payment next step | **done — preserve and verify** | `_billing_history_rows` owns `next_step` and `blocked_reason`; both billing surfaces read them |
| Beginner billing wording | **done — preserve and verify** | the known customer-visible machine words are gone; the family tests are present |
| Cache key + verification floor | **not done** | all 126 template references still use `20260910-pay-terms` |
| Visual proof | **not done** | no current Ask AI or subscription screenshots have been reviewed |
| Paying early | **not done** | the owner resolved the stopped run; implement card and crypto now |
| Exact-cycle resume guard | **not done** | `resume_billing_checkout` omits the attempt's `billing_cycle` and checks aggregate `purchasable` |
| Independent reviews and final report | **not done** | required after implementation and focused tests |

Judging the existing work is part of the job. If any of it is hollow, wrong, or fails
its own test, fix it and say so. Do not accept it because it is there.

**One warning about WP4.** `.a-jump` is used by **two** pages: `subscription.html:48`
and `settings.html:42` (the settings page's own "Jump to a group of settings" bar,
which **stays**). The `.a-jump` rules in `hm-account-test.css:41-78` are therefore
**not** orphans and must **not** be deleted. Only the subscription page's bar, and the
`data-s-jump` / `data-s-jump-link` JavaScript that exists only for it, are removed.
Deleting that shared CSS would silently break the settings page.

## Runtime/source-of-truth authority

- `CLAUDE.md`, `AGENTS.md`, `.agents/commands.json`
- `.hm-orchestrator/current/VISUAL_CONTRACT.md` — **binding** for every pixel, token
  and accessible name in this mission. Read it before writing any markup or CSS.
- `brand guide.md` (project root) — master brand rules.
- `src/ai_market_monitor/static/hilalmarkets-brand.css` — the only home for `--hm-*`
  colour tokens.
- `src/ai_market_monitor/static/hm-dashboard-test.css` lines 642–705 — the shipped
  `.t-action` family. Loaded on all five surfaces in this mission.
- `src/ai_market_monitor/static/hm-hilal-chat.js` and
  `templates/hilal/dashboard_test/partials/hilal_chat.html` — the assistant.
- `src/ai_market_monitor/templates/hilal/base_dashboard.html` lines 175–192 — where
  the assistant and its script are gated.
- `src/ai_market_monitor/api/routers/dashboard_test.py` `_PATH_CHROME` (line ~179) —
  the one owner of "this page carries the assistant".
- `src/ai_market_monitor/api/routers/dashboard.py` `_billing_history_rows`
  (line ~397) — the one owner of what a person may do with a payment attempt.
- `src/ai_market_monitor/api/routers/dashboard_test.py` `_PAYMENT_WORDS` (line ~1460)
  and `_payment_row` (line ~1570).
- `tests/support/contrast.py` — the only place the contrast sum is written.
- `src/ai_market_monitor/core/copy_rules.py` — "Hilal Markets" in prose, "Shariah" in
  technical usage, forbidden claims.
- `src/ai_market_monitor/core/plans.py` — `plan_is_on_sale` is the one owner of
  whether a plan can still be bought.
- `src/ai_market_monitor/services/billing.py` — `plan_checkout_availability` is the
  only owner of whether this account may buy this exact plan and cycle.
- `src/ai_market_monitor/db/models/commercial.py` — checkout attempts, subscriptions,
  billing events and payment-email delivery records are durable money evidence.
- `src/ai_market_monitor/services/payment_emails.py` — the only payment email path.
- `.hm-orchestrator/current/OWNER_DECISION_WP8.md` — binding owner decision for card
  and crypto plan moves. Do not revisit the three Creem upgrade behaviours.

## Requirements coverage

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | An **Ask AI** button sits in the filters bar of Halal Assets (`/dashboard/market`), **after** the other filters | rendered-HTML test asserting the button is the last child of `div.t-controls`; screenshot V1/V2/V3 |
| R2 | Clicking any Ask AI button opens the Hilal assistant window | browser test: click → `#hilal-window` no longer `hidden`, focus inside it |
| R3 | The button uses the new sky-blue tokens and our white text, per the visual contract | CSS test asserting the modifier resolves to `--hm-sky` / `--hm-surface`; no raw hex outside `hilalmarkets-brand.css` |
| R4 | The button has a 2D scale in/out animation; paused on hover/focus/active; off under reduced motion | browser test reading the computed `animation-name` and `animation-play-state`; reduced-motion screenshot |
| R5 | The button meets WCAG: ≥4.5:1 text, ≥3:1 against the page, 44×44 target, visible focus, name contains the visible label | parametrised contrast test through `tests/support/contrast.py`; measured box in the browser test |
| R6 | The same button is on Create monitor, in the bar holding **Add group**, after it | rendered-HTML test on `.m-bar .m-bar-group`; screenshot |
| R7 | The same button is on Opportunities, beside the search box | rendered-HTML test on `.w-bar`; screenshot |
| R8 | The same button is in **every** settings group (8), each with a unique accessible name | parametrised test over all 8 `section.g-group` ids; assert 8 distinct accessible names |
| R9 | One owner: one Jinja macro, one CSS block, one JS handler. No page re-implements the button, its styling, or the open call | grep proving no second definition; the macro is the only producer of the markup |
| R10 | The button never renders on a page where the assistant is not loaded | test with the chat gate off: zero Ask AI buttons and zero dead controls |
| R11 | The jump bar (**What you have · Other plans · Your payments**) is removed from the subscription page, together with the CSS and JS that existed only for it | rendered-HTML test asserting no `nav.a-jump`; grep proving no orphan `a-jump` rule or `data-s-jump` handler |
| R12 | Every payment that is **not complete** offers a way forward; payments that are complete or in flight offer none | parametrised test over **every** key of `_PAYMENT_WORDS` plus an unrecognised status |
| R13 | "Try again" is refused when the plan is no longer on sale | test with `plan_is_on_sale` false → no action offered, and the reason is said in plain words |
| R14 | One owner decides the payment next-step, and **both** billing surfaces read it | grep proving no second decision; both `subscription.html` and `partials/billing_history.html` read the same field |
| R15 | The cache-busting key is bumped to one new value in **every** template that carries one | test asserting exactly one distinct `?v=` value across all templates |
| R16 | Verification floor green | ruff, mypy, the named pytest suites |
| R17 | Visual proof at all three viewports, checked by a vision reviewer against the contract | screenshots + vision review notes in the run folder |
| R18 | An account holding an active paid plan may **buy a different paid plan**, by card and by crypto, for every purchasable plan and every cycle that plan sells | parametrised test over plan × method × cycle × held-plan; the page offer and the server answer agree |
| R19 | Buying a different plan charges the **full price of the new plan** today. No prorated "difference" is ever charged | test asserting the amount asked of the provider equals `checkout_amount` for the new plan; a test that fails if a difference is charged |
| R20 | The new plan's period **starts today** | test asserting `current_period_start` is the moment of activation, not the old period's end |
| R21 | The old plan's remaining days **end**; they are never added to the new period | test asserting the new period end is today + the new plan's own length, never extended by the old plan's remainder |
| R22 | The **money value of the unused days** is worked out from what the person really paid for the running period, recorded, and never guessed from the catalogue price | parametrised test over discounted and full-price purchases; a launch-offer purchase refunds the launch-offer share |
| R23 | Exactly **one** money-owed record per ended period. A re-delivered webhook, a retry, or `reprocess_failed_event` never creates a second | idempotency test replaying the same event and asserting one record and one email |
| R24 | Nothing is owed back when nothing was paid: free plans, trials, admin grants, an already-ended period, and a computed value of zero or less | parametrised test over each of those, asserting no record and no email |
| R25 | The customer is **emailed** what is coming back and that it arrives **within 48 hours**, through the existing payment-email owner, not a second email path | test asserting the outbox row and its wording; grep proving one email owner |
| R26 | The money is never sent automatically. No refund call reaches any payment company | grep + test proving no provider refund request is made on this path |
| R27 | Every sentence a customer ticks or reads matches what they are really charged. The consent wording that promises "only the difference" is corrected | test over the consent sentences; `core/copy_rules.py` passes |
| R28 | No page, on any surface, in any state, offers a way to pay or a plan change the server would refuse — re-proved **after** R18 changes the rule | the adversarial reviewer hunts this specifically; parametrised page-vs-server test over every plan × every held plan |
| R29 | The checkout page speaks to a beginner: no "payment provider", no "entitlement", no "duplicate payment" in anything a customer reads | test over the customer-visible strings in `checkout.html`; `core/copy_rules.py` passes |
| R30 | An adversarial reviewer from an independent family has attacked the money path and every finding is closed or escalated | the reviewer's written findings and their closures in the run folder |
| R31 | A vision reviewer has really looked at the screenshots — the 21 that exist plus this mission's own — against the visual contract | per-shot findings; if it cannot see them, marked **unverified** and escalated |
| R32 | The full regression floor is green, and every failure is attributed to this work or shown to pre-date it | the command outputs plus the `C:\wt-head` worktree comparison |
| R33 | This run's own `SUPERVISOR_REPORT.json` passes `validate-report.py`, and the test-integrity questions are answered one by one | the validator output and the answers |
| R34 | A resume link checks the checkout attempt's own plan **and own billing cycle**. A withdrawn annual offer cannot be resumed because monthly remains available | test parametrised over every purchasable plan and both cycles; the route passes `attempt.billing_cycle` and checks that cycle's flag, not aggregate `purchasable` |
| R35 | An abandoned checkout and a failed payment leave the old paid plan and old live subscription untouched | two separate tests, parametrised over every different paid-plan pair, proving no cancellation, no ended period and no money-owed email before confirmed payment |
| R36 | After confirmed payment, old-card cancellation, old-period ending, money record and email queueing are one transaction. Cancellation retries and then raises a visible alert if it still fails | tests for rollback, bounded retry, visible alert, exactly one live subscription and exactly one recurring charge over every different paid-plan pair |
| R37 | The report says whether Creem reuses saved card details for a new normal checkout, based on current official evidence. This finding cannot change the owner's chosen mechanism | a short cited finding, or `unverified` with the exact missing evidence; do not re-investigate proration behaviours |

## Non-negotiable invariants

- **The button is one thing.** One Jinja macro, one CSS block, one JS handler. This
  repository's recurring root cause is two modules deciding the same thing and
  disagreeing; four copies of a button across four templates is that failure by
  construction. Extraction, not repetition.
- **Never invent a Sharia/halal/haram status**, and never let a seeded question imply
  one. No buy/sell advice, no guaranteed returns, no leverage.
- **Beginner language everywhere a person can read it.** No field names, no `gte`, no
  internal words in any label, seeded question, or empty state.
- **The AI never approves and never acts.** The seeded question is text in a box. It
  is never sent on the person's behalf.
- Never weaken, skip, delete or widen a test to fit the implementation.
- Tests that only move from the old cross-plan card refusal to the owner's new
  full-price permission must remain parametrised across every different plan pair,
  keep page/server agreement checks, and be named in the final report with old and new
  expectations. No other expected-value change is authorised without escalation.
- Never modify unrelated uncommitted work in the tree.
- Never read or print a value from `.env` or `.env.production`.
- No new runtime dependency, and no motion library. CSS keyframes only.
- Colour tokens live in `hilalmarkets-brand.css` and nowhere else.
- Do not write a reduced-motion or state rule inside a bare `:where()` — it counts
  zero for specificity here and has already painted a state wrongly once.
- Never cancel or end the old plan before the new payment is confirmed by a verified
  webhook whose existing amount and currency checks passed.
- Every migration constraint and index name must use `op.f()`. PostgreSQL's 63-byte
  identifier limit is part of acceptance even when SQLite accepts the name.

## Work packages

The next five sections describe landed work. Preserve it and run its existing focused
checks. They are not implementation work packages for this continuation.

### Landed area A — One Ask AI button, owned in one place
Objective: the token family, the macro, the stylesheet block and the open handler
exist once, and a single call renders a correct button anywhere on the dashboard.

Scope:
- Add `--hm-sky`, `--hm-sky-strong`, `--hm-sky-soft` to `hilalmarkets-brand.css` with
  the exact values in the visual contract, each with a one-line comment saying what
  it is for and what it measured.
- New macro `templates/hilal/macros/ask_ai.html`, taking the accessible name and an
  optional seeded topic, emitting the whole button. This is the **only** producer of
  Ask AI markup.
- New modifier block in `hm-dashboard-test.css`, beside the other `.t-action`
  modifiers, following the file's existing `body.hilal-dashboard :is(...)` prefix.
  Include the compact settings variant and the reduced-motion rule.
- Delegated handler in `hm-hilal-chat.js` for `[data-hilal-ask]`, calling the
  assistant's own open path. No page script may open the window.
- One owner for "the assistant is on this page": today `base_dashboard.html`
  computes `hilal_chat_on` inline at line 175, where a page template cannot read it.
  Give the boolean a single server-side owner that the base template, the chat
  partial and every macro call all read. Do not recompute the expression anywhere.

Do not change: the `.t-action` base rule, any existing token's value, the assistant's
own open/close/focus behaviour, the orb.

Acceptance criteria: R3, R4, R5, R9, R10.
Evidence required: the contrast test output for each pair; a grep showing exactly one
definition of the macro, the CSS block and the handler; the reduced-motion rule shown
with its full selector.
Risk: medium. Escalate if the gate cannot be given one owner without changing how the
assistant is included.

### Landed area B — Halal Assets, Create monitor, Opportunities
Objective: the button appears in the three bars, in the exact positions the visual
contract names, and opens the assistant.

Scope: `market.html`, `monitor.html`, `opportunities.html`. Markup only — every
visual property comes from WP1. Each call passes its accessible name from the table
in the contract.

Do not change: any existing filter, tile, segment, search box or action on these
pages; nothing is moved or restyled to make room.

Acceptance criteria: R1, R2, R6, R7.
Evidence required: rendered-HTML tests asserting each position by its parent element;
a browser test clicking each one and proving the window opened and focus moved.
Risk: low.

### Landed area C — Every settings group
Objective: all 8 groups on `/dashboard/settings` carry the button, each with its own
accessible name and its own seeded question.

Scope: `settings.html`, the 8 `section.g-group` blocks. The accessible name and the
seeded question are built from the heading already in the markup — do not retype a
heading, and do not hand-write eight different phrasings that will drift from the
headings the next time one is reworded.

Do not change: any setting, control, dialog or saved value on this page.

Acceptance criteria: R8, plus R2/R5 on this surface.
Evidence required: a parametrised test over all 8 group ids; an assertion that the 8
accessible names are distinct and that each contains the visible label; proof that
every seeded question passes `core/copy_rules.py`.
Risk: low-medium — eight instances is where a per-page copy is most tempting. It must
still be the WP1 macro.

### Landed area D — Remove the billing jump bar
Objective: the bar reading **What you have · Other plans · Your payments** is gone,
and nothing is left behind that only existed for it.

Scope: `subscription.html` (`nav.a-jump`, lines ~48–52), the `.a-jump` rules in
whichever stylesheet holds them, and the `data-s-jump` / `data-s-jump-link` handling
in `hm-subscription-test.js`.

Before deleting: search the whole repository for `a-jump`, `s-jump`, `#s-now`,
`#s-plans` and `#s-payments`. The three section ids **stay** — other places may link
straight to them. Only the navigation bar goes. Report every reference found and what
happened to it.

Do not change: the sections themselves, their ids, their order, or their headings.

Acceptance criteria: R11.
Evidence required: the rendered page without the bar; a grep proving no orphan rule,
handler or selector remains; a screenshot showing no gap where it was.
Risk: medium — a deletion that leaves a dead rule behind is the failure here.

### Landed area E — Every unfinished payment offers a way forward
Objective: a person who started paying and did not finish can always act, whatever
went wrong; and a person whose payment is done or still in flight is never offered a
second charge.

This is a **defect class**, not one missing button. `_billing_history_rows` in
`dashboard.py` sets `can_resume` only for `pending` **and** a live checkout URL
**and** a session id **and** an unexpired session. Of the nine states in
`_PAYMENT_WORDS`, six are "not complete and nothing was charged" and five of those
six show the person no way forward at all — a dead end on their own money.

Scope:
- Extend the existing single owner (`_billing_history_rows`) so it answers, for each
  attempt, *what a person may do next*. Do not add a second decision anywhere, and do
  not decide it in a template.
- The rule, which the tests must assert as a rule:

  | Attempt state | Complete? | Offer |
  |---|---|---|
  | `completed` | yes | nothing |
  | `refunded` | settled | nothing |
  | `processing` | in flight | nothing — a second attempt could take the money twice |
  | `creating` | in flight | nothing |
  | `pending`, live URL, session id, not expired | no | **Finish paying** — resume that session |
  | `pending`, expired or no URL or no session id | no | **Try again** — a fresh checkout for the same plan |
  | `failed` | no | **Try again** |
  | `expired` | no | **Try again** |
  | `cancelled` | no | **Try again** |
  | `provider_unavailable` | no | **Try again** |
  | any state we do not recognise | unknown | nothing — keep the existing "ask us and we will look it up" wording. Never guess a nearest state. |

- **Try again must never be offered for a plan that can no longer be bought.**
  `plan_is_on_sale` in `core/plans.py` is the one owner of that answer; ask it. When
  it says no, offer nothing and say why in plain words a beginner can act on.
- Starting again must **not** create a charge by itself. It opens the checkout the
  page already has, with that plan chosen.
- Both billing surfaces read the same field: `dashboard_test/subscription.html` and
  `dashboard/partials/billing_history.html`. Neither may re-derive the decision.
- The banner at the top of the subscription page (`unfinished_payment`) must stay
  consistent with the rows beneath it — it picks the first row that can be acted on,
  so it must use the same owner's answer, not a second test.

Do not change: any payment record, any provider call, any amount, any webhook path,
or what a completed payment shows.

Acceptance criteria: R12, R13, R14.
Evidence required:
- a test parametrised over **every** key of `_PAYMENT_WORDS` plus at least one
  unrecognised status, asserting the exact offer from the table;
- a test proving no offer when `plan_is_on_sale` is false;
- a test proving the top banner and the row for the same attempt agree;
- a grep proving one decision site.
Risk: **high** — this is money. Escalate rather than guess if any state's correct
offer is unclear, or if starting again could reach a provider before the person
confirms.

### WP1 — Close the exact-cycle resume money hole

Objective: a saved payment link can continue only when the exact plan and exact period
stored on that checkout attempt are still on sale.

Scope:
- In `resume_billing_checkout`, pass `attempt.billing_cycle` to
  `plan_checkout_availability`.
- Check the returned flag for that exact cycle and method. Do not accept aggregate
  `purchasable`, because another cycle being for sale says nothing about this attempt.
- Add a rule test over every purchasable plan and both `monthly` and `annual`. Each case
  withdraws the attempt's cycle while leaving another cycle available when the catalog
  supports it.
- Keep ownership, expiry, URL and payment-state guards unchanged.

Forbidden changes: no provider call, no price fallback, no new sale rule, no special
case for one plan.

Acceptance criteria: R34.
Evidence required: a reproducing failing test before the fix, the focused test output,
and a source check that the route passes the stored cycle and reads its exact flag.
Risk: high — an incorrect allow can take money for an offer no longer for sale.

### WP2 — Cache key, and the whole verification floor
Objective: the change actually reaches a browser, and nothing else broke.

Scope:
- Bump every `?v=` in every template to one new value. Every `?v=` in the product
  must match; a shipped CSS change with a stale key on one page means that page keeps
  the old stylesheet. Find them all, change them all, and prove one distinct value
  remains.
- Run the verification floor and fix what this work broke:
  ```
  .venv/Scripts/python -m ruff check src tests scripts
  .venv/Scripts/python -m mypy src
  .venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly
  .venv/Scripts/python -m pytest tests/integration/test_plan_change_journey.py -q -p no:randomly
  .venv/Scripts/python -m pytest tests/integration/test_dashboard_test_subscription.py -q -p no:randomly
  .venv/Scripts/python -m pytest tests/integration/test_checkout_and_payment_email.py -q -p no:randomly
  .venv/Scripts/python -m pytest <the single browser test file changed by this run> -q -p no:randomly
  ```
  `pytest-timeout` is not installed — do not pass `--timeout`.
- Never run the whole `tests/browser/` directory. Run one browser file at a time, close
  Chromium, and only then start another test command.
- Run every invariant test the touched files fall under, including the accessibility
  and template invariants.

Attribution rule: the working tree carries unrelated uncommitted changes. Before
calling any failure a regression, diff the failing test ids against a clean
`git worktree` at `HEAD` using a short path (`C:\wt-head`), then copy **only** the
files this mission changed onto that worktree to confirm. State clearly which
failures pre-date this work.

Acceptance criteria: R15, R16.
Evidence required: the four command outputs; the before/after failing-test lists; the
one distinct cache key.
Risk: medium.

### WP3 — Visual proof
Objective: somebody has actually looked at it.

Scope: produce the screenshots the visual contract names, at V1/V2/V3, for all four
Ask AI surfaces and the subscription page, plus one reduced-motion shot. Then a
vision-capable reviewer inspects the images against
`.hm-orchestrator/current/VISUAL_CONTRACT.md`.

Acceptance criteria: R17.
Evidence required: the image files in the run folder, and the vision reviewer's
per-shot findings.
Risk: medium. If a screenshot cannot be produced, or the vision reviewer cannot
actually see the image, mark visual verification **unverified** and escalate. Never
infer a visual pass from reading the CSS.

### WP4 — Paying early: full price, a new period today, money back by email

Objective: a person who moves to a **different** paid plan before their paid days run
out gets the new plan starting today, pays its full price, and is told by email what
money is coming back to them.

**This rule is the product owner's decision, taken on 2026-09-10. It is not open for
reinterpretation by a worker or a reviewer.** Where it contradicts what the code does
today, the code changes.

The rule, which the tests must assert **as a rule**:

| Situation | Allowed? | Charged today | New period | Old plan's days | Money owed back |
|---|---|---|---|---|---|
| Holds paid plan X, buys the **same** plan X | **No** — unchanged | — | — | — | — |
| Holds paid plan X, buys a **different** paid plan Y, **card** | **Yes** | **full price of Y** | starts **today** | end today | value of the unused days on X |
| Holds paid plan X, buys a **different** paid plan Y, **crypto** | **Yes** | **full price of Y** | starts **today** | end today | value of the unused days on X |
| Holds nothing paid, buys Y | Yes — unchanged | full price of Y | starts today | — | none |
| On a free plan, a trial, or an admin grant | unchanged | — | — | — | **none** — no money was paid |

Four things this rule is **not**, each of which is the failure mode to test against:

1. It is **not** "pay the difference". The customer pays the whole price of the new
   plan. `TIMING_IMMEDIATE: "proration-charge-immediately"` in `plan_changes.py:175`
   is precisely the behaviour being removed for a cross-plan move, and the consent
   sentence at `plan_changes.py:158` ("I am charged only the difference for the days
   left in this month") becomes a false promise the moment this ships. Fix it.
2. It is **not** a credit. The unused days are **not** carried into the new period and
   **not** held as a balance. They end.
3. It is **not** an automatic refund. Nothing calls a payment company to send money.
   The amount is worked out, written down, and emailed. A person pays it by hand.
   NOWPayments reports `supports_refunds=False` (`billing.py:114-119`) — a crypto
   refund is impossible to automate and the product does not hold a wallet address.
4. It is **not** a new rule for the same plan. Paying again for the plan you already
   hold stays refused, with the wording already at `billing.py:707`.

Scope:

- **One owner for "what is owed back".** A single function, given the ending
  subscription and the moment it ends, returns the money. Nothing else computes it.
  It must read **what the person really paid for the running period** —
  `BillingCheckoutAttempt.amount` is documented at `commercial.py:241` as the already
  discounted figure, which is the honest basis. Never the catalogue price: a launch
  offer or a discount code would refund more than was ever paid.
- **One owner for "may this account buy this plan".** That is
  `plan_checkout_availability` (`billing.py:654`). Today `must_switch_instead`
  (line 688) refuses a card customer buying a different plan. That refusal is what
  R18 opens. Change it there, once. Do not add a second rule anywhere, and do not let
  a page decide it.
- **The card double-charge trap.** `paid_access_can_be_repriced` exists because a card
  customer with two live Creem subscriptions is charged twice every month, for ever.
  Opening R18 for card **must not** create a second live subscription. Whatever route
  is chosen, prove by test that exactly one live subscription and exactly one
  recurring charge remain afterwards. This is the single most expensive way this work
  can go wrong.
- **Money maths.** `Decimal` only, never `float`. Two decimal places, rounded half up,
  stated once. Never negative, never more than was paid for that period. Zero or less
  means no record and no email.
- **Idempotency.** Webhooks are re-delivered and `reprocess_failed_event`
  (`billing.py:2290`) replays them on purpose. The money-owed record must be keyed so
  the second delivery finds the first and writes nothing. Money that doubles on a
  retry is the worst outcome in this mission.
- **The email** goes through `PaymentEmailOutboxService` (`payment_emails.py:186`) and
  its `PaymentEmailDelivery` record. Do not write a second email path.
- **"Within 48 hours" is one constant, in one place**, read by the email, by any
  on-screen sentence, and by the tests. Do not type the number twice.
- Beginner wording throughout: "the money for the days you did not use", not
  "prorated credit" or "residual value".

Do not change: what a completed payment shows, any webhook signature check, any
amount already recorded, the cancel flow, or the same-plan refusal.

Acceptance criteria: R18–R27.

Evidence required:
- the parametrised offer test (R18) over plan × method × cycle × held plan;
- a test proving the provider is asked for the **full** price, which fails if a
  difference is charged;
- a test proving the new period starts today and is not extended by the old days;
- the idempotency replay test (R23);
- the "nothing was paid" family test (R24);
- the outbox row and its wording (R25);
- a grep proving no refund call reaches a provider (R26);
- a test proving one live subscription and one recurring charge remain (card).
- separate abandon and failed-payment tests proving the old plan remains live and
  untouched before confirmation (R35);
- a cancellation-failure test proving bounded retry, then a visible alert, and a
  transaction rollback rather than two silently live subscriptions (R36);
- a record of whether a normal Creem checkout reuses saved card details (R37), without
  reopening the settled proration question.

Risk: **high — this is money, and it opens a path that is closed today.**

**The Creem question is ANSWERED. Do not escalate it again.**

Run `20260910T164746Z-f76e4f88` escalated this correctly, Claude verified it against
Creem's published API, and the owner decided on 2026-09-10. The answer is binding and
is written in full in **`.hm-orchestrator/current/OWNER_DECISION_WP8.md`. Read that
file before writing a line of WP8.** In short:

- Creem genuinely cannot charge full price today and restart the period. All three of
  its behaviours are prorated. That is settled fact, not something to re-check.
- **Card:** take the full-price payment through a normal checkout **first**, and only
  once it is confirmed, cancel the old subscription, end its period, work out the money
  owed, record it and queue the email — all in one transaction.
- **Never cancel the old plan before the new payment is confirmed.** A customer who
  abandons the checkout would be left with no plan and their paid days gone.
- **Crypto is not blocked and never was** — `must_switch_instead` is already false for
  a crypto holder. Build it.

The two money-losing failures, both of which need a test that proves they cannot
happen: two live subscriptions after the move (charged twice for ever), and the old
plan cancelled when the new payment never completed (paid for nothing).

**Escalation triggers that remain — stop and write `ESCALATION.json` rather than guess:**
- If the amount really paid for a running period cannot be found for some account,
  stop. Do not fall back to the catalogue price.
- If the crypto path and the card path cannot be made to follow one rule without a
  second decision site, stop and say which two places disagree.

### WP5 — Independent reviews, Phase C2 closure, and final report

Objective: the checkout work the stopped session started is finished, reviewed by
somebody who did not write it, and reported honestly.

Scope:

1. **Preserve and verify the beginner-language sweep that already landed.** Search all
   customer-visible checkout, payment-result, billing, billing-portal and billing-script
   text. The known machine words are already gone. Do not rewrite the finished copy
   unless a test proves a remaining customer-visible sentence is false or unusable.
   `hilalmarkets-billing.js` code comments are not customer copy.

2. **The adversarial review (R30).** An independent family, not whoever wrote the code.
   Give it the whole billing diff and every piece of evidence. Its job is to break the
   money path, and it must answer each of these in writing:
   - can a customer be charged twice — including through R18's new path, and including
     a card customer ending up with two live subscriptions?
   - can a customer pay and get no access?
   - can the money owed back be paid twice, or computed larger than what was paid?
   - wrong amount, wrong plan, wrong period start?
   - **can any page, in any state, still offer something the server refuses** (R28)?
   - does any customer-visible sentence now lie about what they are charged?

   "No customer impact" is not an accepted closure unless the reviewer shows the path
   is unreachable and says how it proved that.

3. **The vision review (R31).** The 21 PNGs already in
   `runs/20260910T030616Z-24bcf48d/` plus this mission's own shots. Copy the existing
   ones into this run's folder rather than taking them again — memory is the reason
   seven runs died. Two contracts, and each shot is judged against the right one:
   - the **checkout** shots (S1–S7) against the **V1–V8 table** in
     `.hm-orchestrator/current/CLAUDE_DECISION.md`, the one section of that superseded
     file still in force;
   - the **Ask AI and subscription** shots against
     `.hm-orchestrator/current/VISUAL_CONTRACT.md`.

   The reviewer must really open the images. If it cannot, mark visual verification
   **unverified** and escalate; never infer a visual pass from CSS.

   One caveat that is yours to catch: the S4 and S7 shots show a **refusal** the review
   page gives a card customer who holds another plan. R18 removes that refusal. If you
   land R18, those two shots no longer show today's product and must be retaken — or,
   if memory will not allow it, recorded plainly as out of date rather than passed off
   as current evidence.

4. **The report (R33).** Your own `SUPERVISOR_REPORT.json` must pass
   `validate-report.py` against `.hm-orchestrator/policy/supervisor-report.schema.json`.
   Answer the test-integrity questions one at a time, out loud: any test deleted? any
   skip or xfail added? any assertion widened? any expected value changed? any test-only
   hardcode? For the expected values R18 legitimately moves, name each one and point at
   the sentence in this mission that authorises it.

Do not change: anything C2 or the takeover brief lists as already landed, unless a
focused test proves a defect in it.

Acceptance criteria: R28, R29, R30, R31, R32, R33, R37.
Risk: medium — except the adversarial review of WP4, which is the highest-value review
in this mission.

## Landed related defects — preserve and verify

1. **The settings jump bar used to miss one group.** One list now drives all eight
   links and headings, including `g-data`. Keep this extraction and its invariant test.
2. **The cache key is currently one value** (`20260910-pay-terms`, 126 places). It is
   already consistent, so R15 is about **keeping** it consistent after this work ships
   CSS and JS changes — bump it once, everywhere, and prove one distinct value again.
   The other session may bump it too; re-read before you change it.

## A problem you find is a problem you fix

Anything discovered on the way is part of this mission. Fix it, do not park it in the
report as "found, not fixed". "It is pre-existing", "it was not asked about" and "it
needs its own pass" are not reasons to leave it. The only exception is a true blocker
— a decision that is the user's to make, access you do not have, or something that
cannot be verified with the tools here. Then say in one sentence what is blocked,
why, and what you need, and fix everything else.

Two places to look first, because this codebase repeats them:
- a second implementation of something this mission touches;
- a rule, handler or selector left behind after a deletion.

## Machine limits — read this, it has killed six runs

This Windows machine has 15.4 GB of memory and roughly **2.5 GB free** when this run
starts. Another project of the owner's is running and using about 1 GB; it must not be
stopped. **Seven** previous runs on this repository were killed by running out of
memory — the most recent one died with all its work done and no report written, which
is why "write evidence as you go" below is not advice.

**Write evidence files as you go.** If this run is killed too, the next one must be
able to continue from what you wrote. Do not save the report for the end. The run that
died on 2026-09-10 had finished five of its nine requirements and proved none of them,
because everything was still in its head.

Work inside that budget:

- **One browser at a time**, and close it before starting anything else. Never run two
  browser sessions, and never leave one open while pytest runs.
- **Batch pytest**, do not run the whole tree at once. The four floor commands are
  already separate; keep them separate and let each finish.
- Do the cheap work first: WP4, WP5 and WP8's logic and tests need no browser at all.
  WP7 is last on purpose.
- Screenshots already exist for the other session's work and are **not** yours. Take
  only the shots this mission's visual contract names.
- If a step dies, say plainly that it was killed and what had already landed. A killed
  run that reports honestly is worth far more than one that claims a pass it never saw.

## Verification floor

- Reproduce before fixing when reproducible.
- Focused tests for every changed behaviour.
- Adjacent tests discovered from the touched code, not guessed.
- Tests assert the **rule**, parametrised across the family — every payment state,
  every settings group, every viewport. A test that only covers the reported example
  does not count.
- ruff, mypy, and the four commands in WP6.
- Final diff review and a requirement-coverage audit.

## Reviewer policy

Risk: **high + visual**.

- Two independent logic reviewers from **different model families**. One must review
  **WP5 and WP8** (money) specifically, and must be told to attack the double-charge
  and the double-refund cases by name.
- One vision reviewer on the screenshots.
- Reviewers must not be the same model that wrote the code under review.

## You own all of it now — the second session was stopped

A second Claude session was working the checkout **review page** under
`.hm-orchestrator/current/CLAUDE_DECISION.md` (Phase C2). The owner stopped it on
2026-09-10. **Nothing is contested any more. Every file in this repository is yours,
and the unfinished half of that mission is now part of this one.**

Its run died without writing a report, but a lot of its work **did land in the tree**.
Claude checked each item directly. **Keep all of it. Do not revert it, and do not redo
it.**

| Phase C2 item | State Claude verified |
|---|---|
| C2-R1 — the review page must read the one owner | **landed.** `dashboard.py:3948` now reads `already_subscribed=checkout_availability["holds_this"]` instead of its own hand-written check |
| C2-R2 — two tests that skipped themselves on finding the bug | **landed.** No `pytest.skip` remains in `tests/browser/test_checkout_pay_button_e2e.py` |
| C2-R3 — the S7 screenshots | **landed.** 21 PNGs in `runs/20260910T030616Z-24bcf48d/`, including `s7-pro-refusal-` at all three viewports |
| C2-R6 — the report contract naming its JSON keys | **landed.** `SUPERVISOR_REPORT_CONTRACT.md` now names them |
| C2-R7 — the Phase B report's shape | **landed.** `validate-report.py` answers `VALID` |
| C2-R4 / C2-R5 — vision review and adversarial review | **NOT done** — the run died first |
| C2-R8 / C2-R9 — regression floor and the test-integrity answers | **NOT done** |
| the beginner-language sentences on the checkout page | **NOT done** — see WP9 |

### The one thing you must think about before touching WP8

C2-R1 and R18 point in **opposite directions**, and getting this wrong charges real
customers wrongly.

- C2-R1 made the review page refuse a **card** customer who holds Trader and opens Pro.
- R18 says that customer must now be **allowed** to buy Pro.

Both are right, at different moments, and the reason they can both be right is the
extraction C2-R1 did: **the page no longer holds a rule of its own, it asks
`plan_checkout_availability`.** So R18 is implemented by changing that **one owner**,
and the review page, the popup and the billing page all follow it for free. That is
the whole point of the extraction — do not undo it by writing a second rule anywhere.

**Tests that assert the old refusal will now be wrong.** This is the single case in
this mission where changing a test's expected value is correct, because the product
owner changed the rule on 2026-09-10. It is a **repair, not a weakening**, and it is
allowed only under all four of these conditions:

1. the test still asserts a **rule**, parametrised across every paid plan crossed with
   every other paid plan — never a single repaired example;
2. it still fails if the page and the server ever disagree about who may buy what;
3. no assertion is deleted, loosened, or turned into a skip — only the expected value
   moves, from "refused" to "allowed, at full price";
4. every such test is **listed by name** in the report, with the old expectation, the
   new one, and the sentence of this mission that authorises it.

Anything you cannot fit inside those four conditions is an escalation, not a judgement
call.

## Completion condition

Do not report complete until every R# has PASS evidence or a named blocker.
`SUPERVISOR_REPORT.json` and `SUPERVISOR_REPORT.md` must both be present and must
state, per requirement: the evidence, the command that produced it, and whether it
passed. "Done", "looks good", or one green test is not evidence.
