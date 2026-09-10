# Mission — "Ask AI" on four dashboard surfaces, and the billing clean-up

Run ID: assigned by delegate.ps1
Owner: Claude Code
Execution owner: OpenCode Go supervisor

## User outcome

A person on the dashboard can open the Hilal assistant from the bar they are already
looking at, on Halal Assets, Create monitor, Opportunities and in every settings
group; and on the billing page the dead jump bar is gone while every payment that
did not complete now offers a way to finish it.

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
  WP5 (money) specifically.
- One vision reviewer on the screenshots.
- Reviewers must not be the same model that wrote the code under review.

## Completion condition

Do not report complete until every R# has PASS evidence or a named blocker.
`SUPERVISOR_REPORT.json` and `SUPERVISOR_REPORT.md` must both be present and must
state, per requirement: the evidence, the command that produced it, and whether it
passed. "Done", "looks good", or one green test is not evidence.
