# What changed, in simple words

Two pieces of work. The first fixes how the setup chat decides which markets a setup
watches. The second puts the prices back on the site and adds the launch offer.

---

# Part 1 — which markets a setup watches

## The main problem

A "Watch Plan" can pick its markets in three ways:

| You choose | What it means |
|---|---|
| Specific coins | Only the coins you named |
| A Favorites list | The coins in that list |
| All halal markets | Every coin that passes the screening you picked |

The third one is supposed to **keep changing** as our review of each coin changes. It did
not. When you approved a setup, the system wrote today's list of coins into the setup
itself. From then on it watched only those coins, forever. A new coin that became halal
never joined.

The Favorites list had the opposite problem. It was supposed to stay fixed at what you
approved. But the running setup read the **live** list, so a coin you added to Favorites
after approving quietly joined a running setup, with no review and no record.

## The fix

Three separate things, which used to be one thing:

| Object | What it holds | Who reads it |
|---|---|---|
| Your policy | The rule you wrote: which mode, which exchange, which coins | The runtime, every cycle |
| The reviewed result | The exact coins you saw on screen | Your approval record |
| The live result | The coins right now | This scan cycle |

Your policy is never overwritten. The screened coins travel beside it.

| Fix | Production function | Saved field |
|---|---|---|
| Your policy is never overwritten | `_apply_screening_policy` | `ScreeningExecutionResult.authored_definition` |
| Each mode has one written rule | `membership_contract` | `ReviewedScreeningEvidence.membership_kind` |
| The resolver reads the right field | `_technical_symbols` | — |
| A changed Favorites list stops the setup | `_apply_universe_mode` | `sharia_policy.approved_watchlist_version` |
| Rules and markets are hashed apart | `SecuredPreviewDefinition` | `secured_preview_hash` |

## Nine more problems fixed on the way

**1. The saved market-data check could go missing.** The system checks it can read price
candles before you approve. It saved the *result* to a fast cache but not the *record of
what was checked*. A cache hit gave you "every market is fine" with no record behind it.
Now the result and the record are saved as one thing. If any part of it is wrong, all of
it is thrown away and the check runs again. Enforced by `PreflightCacheEntry` and
`_read_preflight_cache`.

**2. Nothing kept the market-data promise while running.** The check makes one of two
promises: "I checked every market" or "I checked a sample, and I will check each one when
it runs". The second promise was written down but nobody kept it. Now
`engine/runtime_preflight.skip_reason` decides, one market at a time, inside the worker.
A market with missing, stale or incomplete candles is **skipped and the reason recorded**.
It is never evaluated on guessed or partial data. The promise is saved on the approved
version, in `strategy_versions.approval_evidence`.

**3. The assistant could describe the wrong change.** A message could say "I added ETH"
while pointing at the operation that added BTC — real evidence, wrong sentence. Now every
factual sentence carries a small machine-readable statement: what it is about, what it
says, and the value. The server compares that with its own record. Six checks run, all on
ids and values, so an Arabic message is checked exactly like an English one. Enforced by
`validate_claims`.

**4. The assistant had a way around the check.** The model returned a free message field
*and* the checked claims. It could put every fact in the free field and send no claims,
and nothing was checked. **That field is gone.** The model now returns only friendly
wording plus checked claims, and the server builds the message. There is one place a fact
can live and it is checked. Enforced by `compose_final_reply`.

**5. Two changes of the same kind shared each other's evidence.** In "add BTC and ETH",
both operations claimed both coins. Every operation now carries what it aimed at — the
coin, the rule id, the field — and only changes about that target become its evidence.
When a later change replaced an earlier one, the later one is named. Enforced by
`operation_targets` and `reconcile_turn`.

**6. Changing the screening settings produced no record at all.** There was no comparison
for the Sharia policy, so a turn that switched the methodology or the Favorites list
reported nothing and its operation looked like it did nothing. Added `sharia_policy_changed`
to `diff_drafts`.

**7. A Favorites list was identified by gluing text together.** `"BTC" + "/" + "USDT"` is
a string somebody typed, not an identity we govern. Two different coins sharing a ticker
collided. Identity now comes from the governed asset record and the exchange market
record. A member we cannot identify makes the whole list unusable rather than being
quietly hashed as text. The list is also stored in its own table, so "which coins did this
approval cover" has an answer that does not depend on rows that can still change.
`approved_watchlist_snapshots`, `watchlist_content_hash`.

**8. Missing evidence counted as matching evidence.** A review with nothing recorded and
an approval with nothing recorded agreed with each other, and the approval went through.
Presence is now checked separately from sameness: `ReviewedScreeningEvidence.missing_evidence`.

**9. The approval never said what it promised.** Approving a fixed list and approving a
changing one are different promises. The record now carries the sentence:
`membership_sentence`.

## Production decisions applied

| Decision | Where |
|---|---|
| The old agent coordinator stays off in production | `check_release_invariants.py`, `.env.production.example` |
| "Watchlist" wording corrected to "Watch Plan" in 6 places | dashboard and public pages |

---

# Part 2 — prices and the launch offer

## What was wrong

The previous pass hid the paid plans whenever checkout was switched off. Checkout **is**
switched off (`BILLING_ENABLED=false`), so the pricing page was left with one free plan,
and the landing page comparison table lost its columns and broke.

That decision is reversed. Prices stay on the page in both modes. What checkout being off
changes is the **button**, not the price.

## The launch offer

| Plan | Monthly | Annual |
|---|---|---|
| Explore | $0, free forever | Soon |
| Monitor | ~~$20~~ **$7** with a countdown | Soon |
| Pro | Soon, no price shown | Soon |

- The three annual plans say **Soon**, with no price and no button.
- Pro's monthly plan says **Soon**, with no price and no button.
- Monitor shows its usual $20 crossed out beside the launch price of $7.
- A countdown shows **days, hours, minutes and seconds** until **1 September 2026,
  00:00 UTC**. It counts live: the seconds fall while the visitor is on the page.
- After that instant the discount, the crossed-out price and the countdown all disappear
  by themselves. The price and the deadline come from one rule, so they cannot disagree.
  On the landing page the price also goes back to $20 in the same moment, without a
  reload.

A price is not just hidden for a plan that is not for sale — it is **not sent to the page
at all**, so it is not in the page source either.

## Where it appears

All three places that show prices, from one definition in `core/plans.py`:

| Surface | File |
|---|---|
| Landing page | `Hilal-Markets-Website/src/components/Pricing.tsx` (rebuilt) |
| Public pricing page | `templates/hilal/public/partials/pricing_cards.html` |
| Dashboard billing | `templates/hilal/dashboard/billing.html` |

## Design

Following the brand rules in `brand guide.md`:

- **Calm, even while counting.** The timer steps once a second, because that is what was
  asked for, but it does nothing else: no flash, no animation, no colour change, no red.
  It says "Launch price ends in" and stops there. The brand rules ask for calm, so the
  movement is the only thing that moves.
- **The numbers do not jump.** Each number sits in a fixed-width box, so a second falling
  from 10 to 9 does not push the words beside it sideways.
- **One focal point per card.** The apple-green accent goes on the countdown label and
  nowhere else. A card that cannot be bought loses the green accent and the "Most
  Popular" badge, because it is not the plan to choose.
- **Rounded surfaces, light borders.** The countdown sits in a rounded box with a
  hairline border on the near-white ground. A "Soon" card gets a dashed border instead of
  a shadow.
- **Old price in the muted neutral, struck through.** It reads as information, not as a
  sales flash.
- **Tabular numerals** on the timer and the prices, so the digits do not jump as they
  count down.
- **Sentence case** everywhere. No ALL CAPS.
- **Never colour alone.** "Soon" is a word on the card, a word on the badge, and a
  disabled button that says "Pro is coming soon" — not just a grey tint.

---

# Part 3 — the AI model, and the fonts

## The AI model

Every place the system asks an AI model a question now names the same model,
`gpt-5.6-luna`, and asks it to think as little as possible (`none`, the lowest setting
the system allows). This is set in `.env`, `.env.production`, and both sanitized example
files, so a new machine starts the same way.

| Changed, in each of the four files | Count |
|---|---|
| Model settings pointed at `gpt-5.6-luna` | 11 |
| Thinking-effort settings set to `none` | 8 |
| Settings added (`TARGET_MODEL`, evaluator price list) | 2 |

One setting was **not** changed: `CAPABILITY_EMBEDDING_MODEL`. That is not a model that
answers questions; it turns text into numbers for search. Pointing it at a chat model
would break search.

**The price list had to change too.** Before a turn runs, the system works out what it
could cost and refuses if that is over the limit. A model with no price in the list is
refused every time, so the setup chat would have stopped answering. `gpt-5.6-luna` is now
in the price list — with **placeholder rates**, marked as such in the file. Replace them
with the published price. They only feed the safety limit, not a bill.

The evaluator (the testing tool) reads the same `.env`. It was still pricing the app as
if it ran the old model, so `TARGET_MODEL` and its own price list were added beside the
others.

**One stale label fixed.** The System Brain page had the model name written into the page
by hand and still said "5.4 nano · low". It now reads the real setting.

## The fonts

The dashboard was drawing prices in the browser's default font — usually Times New Roman
— while everything around them was the brand font. The rule said `font-family: "Manrope"`
and **the dashboard never loads Manrope**, and there was no second choice behind it. So
the browser picked its own.

The same sentence was written in **eleven places** in one stylesheet and more in others,
so fixing the price alone would have left the rest wrong.

| Fix | Where |
|---|---|
| The brand faces are declared in one file | `static/hilalmarkets-fonts.css` |
| Every page that needs them loads that file | 7 page shells |
| Every stylesheet asks for a font by name of the rule, not of the font | `var(--hm-font-display)`, `var(--hm-font-ui)` |
| No stylesheet a page loads names a font nobody self-hosts | 24 declarations changed |

Headings and prices are **Geometria Medium**; body text, labels and buttons are
**Onest** — section 11 of `brand guide.md`. The public site also stops loading two fonts
from Google (DM Sans and Manrope): the brand faces are on our own server, so that is one
less outside service on every page.

## The countdown looked different in the dashboard

It did, and here is why. On the public site the Monitor card is dark green, so the
countdown was written white-on-dark. In the dashboard the same card is white. The rule
did not know the difference, so it painted **white numbers on a white card**. The
crossed-out $20 had the same problem: a pale colour meant for a dark card, on a white
one.

Fixed by deleting the special case. There is now **one countdown design**, the landing
page one, on all three surfaces. Anything that has to follow the card takes its colour
from the card's own text colour instead of a second copy written by hand.

## Two scripts were fighting over one element

On the dashboard, switching to annual billing hides the countdown. The countdown script
showed it again on its next tick. At one tick a minute this was rare; at one tick a
second it would have flickered every second.

Now each script states a fact and the stylesheet decides what is visible:

| Fact | Who says it |
|---|---|
| The offer is still running | the countdown script |
| This billing period has no offer | the billing script |
| So: show it or not | the stylesheet |

---

# Extra problems found and fixed

| Problem | Fix |
|---|---|
| 18 templates and 4 other files had gained an invisible marker character at the top, from earlier edits made with PowerShell. It was being served inside the HTML. | Removed from every file. |
| Two tests demanded one exact cache-busting version string, so any released asset change failed until somebody edited the test. | They now assert the **rule** — all assets share one key — instead of the string. |
| The screening-settings comparison did not exist, so those changes were invisible. | Added, see Part 1 item 6. |
| `DraftChange` used one field for both the display text and the identity. Matching on it was unreliable. | Split into `detail` (for people) and `target` (for matching). |
| Some text in the code had been saved through the wrong character table. A dashboard box said "Ask about your setup**вЂ¦**" instead of "…", and the System Brain page showed "**В·**" instead of "·". Two test sentences meant to be Arabic and Chinese had become nonsense. | Repaired, using the repair function the code already had. |
| That repair function could not see this damage. Its list of bad patterns was written by hand and covered only part of the problem. | The list is now worked out from the character tables themselves, so it covers the whole family. A repair that would produce unreadable characters is refused, so real Russian text is left alone. |
| The billing switch promised "Save up to $44" in fixed text. That was the Pro figure; with Monitor at $20 it was wrong. | Computed from the prices beside it, on both the landing page and the dashboard. |
| Three tests demanded exact prices (`$8`, `$12`), so changing a price failed the tests until someone edited them by hand. | They now read the price from `core/plans.py`, the one place it is defined. |
| Pages used **nine different** version stamps on their asset links. That stamp is what makes a browser fetch a changed file instead of its saved copy. A stylesheet used by two pages could be updated on one and left stale on the other. | One stamp for the whole site, and a test that fails the moment a second one appears. |

---

# How this was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | passes |
| `mypy src` | passes, 264 files |
| `pytest tests/unit tests/engine tests/interpreter tests/services tests/integration` | **exit 0** |
| `scripts/check_release_invariants.py` | PASS |
| Compiler replay probe, two recorded runs | 19 drafts each, 0 crashes, 0 blocking findings |
| `tsc --noEmit` on the landing site | passes |
| Landing bundle rebuilt and copied to `static/landing/assets/` | done |

New tests: **116** in `tests/unit/test_invariant_dynamic_universe.py`, **39** in
`tests/unit/test_invariant_offer_presentation.py`, **29** more in
`tests/unit/test_text_normalization.py`, plus **8** in
`tests/integration/test_launch_offer_pricing.py`. They assert rules across whole families
— every universe mode, every market-data promise, every operation kind, every claim type,
and the same refused claim in English, Arabic, Arabizi and Chinese — so a fix that only
helps one example fails.

The pricing pages were also rendered and read directly, not only asserted. Public page
and dashboard, side by side:

```
Explore   $0 free forever          | Start free
Monitor   $20 struck   $7 /month   | Try Monitor for 7 days   [timer, ends 2026-09-01T00:00:00+00:00]
Pro       Soon, not available yet  | Pro is coming soon
```

Both scripts were also syntax-checked with `node --check`.

# Not done

**One real paid model call.** There is no `OPENAI_API_KEY` in `.env`, in the user
environment, or in the machine environment, so a real call cannot be made. The composer
path is instead covered end to end with only the network call replaced, so the payload
builder, the evidence ledger, the proposition checks and the message assembly are all the
real production code.

**Two things about `gpt-5.6-luna` cannot be checked without that key**, and both are one
call away once there is one:

| Not checked | What happens if it is wrong |
|---|---|
| That the provider knows this model name | Every AI answer fails with a provider error |
| That the provider accepts thinking effort `none` | Same; the next lowest setting is `minimal` |
| Its real price | Only the per-turn spending limit is affected, not a bill |

**A full staging matrix** (real models, PostgreSQL, Redis, live market data, several
users at once) needs those credentials and a staging environment. It is the one thing
between this and a launch claim.
