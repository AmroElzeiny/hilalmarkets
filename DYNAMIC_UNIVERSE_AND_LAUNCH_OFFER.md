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
| Monitor | ~~$12~~ **$8** with a countdown | Soon |
| Pro | Soon, no price shown | Soon |

- The three annual plans say **Soon**, with no price and no button.
- Pro's monthly plan says **Soon**, with no price and no button.
- Monitor shows its usual $12 crossed out beside the launch price of $8.
- A countdown shows **days, hours and minutes** until **1 September 2026, 00:00 UTC**.
- After that instant the discount, the crossed-out price and the countdown all disappear
  by themselves. The price and the deadline come from one rule, so they cannot disagree.

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

- **Calm, not urgent.** The timer ticks once a minute, not once a second. It does not
  flash or animate. It says "Launch price ends in" and stops there. The brand rules ask
  for calm and forbid urgency and fear of missing out.
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

# Extra problems found and fixed

| Problem | Fix |
|---|---|
| 18 templates and 4 other files had gained an invisible marker character at the top, from earlier edits made with PowerShell. It was being served inside the HTML. | Removed from every file. |
| Two tests demanded one exact cache-busting version string, so any released asset change failed until somebody edited the test. | They now assert the **rule** — all assets share one key — instead of the string. |
| The screening-settings comparison did not exist, so those changes were invisible. | Added, see Part 1 item 6. |
| `DraftChange` used one field for both the display text and the identity. Matching on it was unreliable. | Split into `detail` (for people) and `target` (for matching). |

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

New tests: **116** in `tests/unit/test_invariant_dynamic_universe.py`, plus **6** in
`tests/integration/test_launch_offer_pricing.py`. They assert rules across whole families
— every universe mode, every market-data promise, every operation kind, every claim type,
and the same refused claim in English, Arabic, Arabizi and Chinese — so a fix that only
helps one example fails.

The pricing pages were also rendered and read directly, not only asserted:

```
Explore   $0 free forever          | annual: -                     | Start free
Monitor   $12 struck  $8 /month    | annual: Annual billing: soon. | Try Monitor for 7 days   [timer]
Pro       Soon, not available yet  | annual: Annual billing: soon. | Pro is coming soon
```

# Not done

**One real paid model call.** There is no `OPENAI_API_KEY` in `.env`, in the user
environment, or in the machine environment, so a real call cannot be made. The composer
path is instead covered end to end with only the network call replaced, so the payload
builder, the evidence ledger, the proposition checks and the message assembly are all the
real production code.

**A full staging matrix** (real models, PostgreSQL, Redis, live market data, several
users at once) needs those credentials and a staging environment. It is the one thing
between this and a launch claim.
