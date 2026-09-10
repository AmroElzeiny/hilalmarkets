# Mission — "Ask AI" on four dashboard surfaces, the billing clean-up, and paying early

Run ID: assigned by delegate.ps1
Owner: Claude Code
Execution owner: OpenCode Go supervisor
Revision: 2 (2026-09-10) — adds WP8, the early plan change and the money owed back.

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
| WP1 one owner | **appears done — verify** | `--hm-sky` / `--hm-sky-strong` / `--hm-sky-soft` at `hilalmarkets-brand.css:54-62`; macro `templates/hilal/macros/ask_ai.html`; CSS block `hm-dashboard-test.css:707-745`; handler `hm-hilal-chat.js:136` |
| WP2 three bars | **appears done — verify** | `market.html:136`, `monitor.html:134`, `opportunities.html:84` |
| WP3 settings groups | **appears done — verify** | 8 calls in `settings.html` (lines 62, 102, 194, 236, 315, 393, 478, 514) |
| tests for the above | **present — judge them** | `tests/unit/test_invariant_ask_ai_{contrast,css,single_owner}.py`, `tests/integration/test_ask_ai_button.py`, `tests/browser/test_ask_ai_button_e2e.py` |
| WP4 jump bar | **NOT done** | `nav.a-jump` still at `subscription.html:48-51`; `data-s-jump-link` still at `hm-subscription-test.js:55` |
| WP5 unfinished payments | **NOT done** | `can_resume` at `dashboard.py:410` is still the narrow pending+url+session+unexpired rule |
| WP6 cache key + floor | **not verified** | — |
| WP7 visual proof | **not verified** | — |
| WP8 paying early | **NEW — nothing exists** | see below |

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
- Never modify unrelated uncommitted work in the tree.
- Never read or print a value from `.env` or `.env.production`.
- No new runtime dependency, and no motion library. CSS keyframes only.
- Colour tokens live in `hilalmarkets-brand.css` and nowhere else.
- Do not write a reduced-motion or state rule inside a bare `:where()` — it counts
  zero for specificity here and has already painted a state wrongly once.

## Work packages

### WP1 — One Ask AI button, owned in one place
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

### WP2 — Halal Assets, Create monitor, Opportunities
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

### WP3 — Every settings group
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

### WP4 — Remove the billing jump bar
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

### WP5 — Every unfinished payment offers a way forward
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

### WP6 — Cache key, and the whole verification floor
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
  ```
  `pytest-timeout` is not installed — do not pass `--timeout`.
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

### WP7 — Visual proof
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

### WP8 — Paying early: full price, a new period today, money back by email

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

Risk: **high — this is money, and it opens a path that is closed today.**

**Escalation triggers — stop and write `ESCALATION.json` rather than guess:**

- Creem's `update_behavior` values known to this code are only
  `proration-charge-immediately` and `proration-none` (`plan_changes.py:171-177`).
  **Neither means "charge the full new price today and restart the period."** Confirm
  against Creem's real API what does. If nothing does, say so and stop — do **not**
  approximate with `proration-charge-immediately` (that charges the difference, the
  exact thing the owner ruled out), and do **not** cancel and re-create a live
  subscription on your own judgement. That is a decision for the owner.
- If the amount really paid for a running period cannot be found for some account,
  stop. Do not fall back to the catalogue price.
- If the crypto path and the card path cannot be made to follow one rule without a
  second decision site, stop and say which two places disagree.

## Defects Claude found while writing revision 2 — fix these too

1. **The settings jump bar is missing a group.** `settings.html` has **eight** groups
   (`g-where`, `g-when`, `g-howmuch`, `g-about`, `g-screen`, `g-evidence`, `g-market`,
   `g-data`) but its jump bar at line 42 lists only **seven** links — `g-data` ("Your
   data", line 508) has none. A bar that says "Jump to a group of settings" and cannot
   reach one of them is wrong. Do not hand-write an eighth link: the bar and the
   groups must be built from **one list**, so the next group added cannot be forgotten
   the same way. Add a test that fails when a group has no link.
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

This Windows machine has 15.4 GB of memory and roughly **2.9 GB free** when this run
starts. Another project of the owner's is running and using about 1 GB; it must not be
stopped. Six previous runs on this repository were killed by running out of memory.

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

## Another Claude session shares this repository

A second session is working on the checkout **review page** under
`.hm-orchestrator/current/CLAUDE_DECISION.md`. Its run was still live when this
revision was written. Treat these as contested and read them immediately before you
edit them, because they may have moved since this mission was written:

- `src/ai_market_monitor/api/routers/dashboard.py` — `billing_checkout_review` (~3831)
- `src/ai_market_monitor/templates/hilal/dashboard/checkout.html`
- `src/ai_market_monitor/services/billing.py` — `plan_checkout_availability` (654)

WP8 changes `plan_checkout_availability` and the other session reads it. Do not
revert their work, and do not leave the review page disagreeing with the popup about
who may buy what — that disagreement is the exact defect they were sent to close.
If their change and R18 cannot both be true, stop and escalate rather than pick one.

## Completion condition

Do not report complete until every R# has PASS evidence or a named blocker.
`SUPERVISOR_REPORT.json` and `SUPERVISOR_REPORT.md` must both be present and must
state, per requirement: the evidence, the command that produced it, and whether it
passed. "Done", "looks good", or one green test is not evidence.
