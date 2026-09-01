# Reading a coin's own pages, and deciding from them

## What this is

The product has a Shariah answer for about 184 coins. An exchange lists many hundreds.
For every other coin the product used to say nothing — and silence reads as "there is no
problem", which is the one thing it does not mean.

This is the machine that closes that gap. For a coin nobody has ruled on it:

1. asks CoinMarketCap where the project publishes,
2. reads those pages and the pages they link to,
3. works out what the project says it does,
4. applies the **Hilal Markets Methodology** to that,
5. writes down the answer, the words behind it, and a Passport.

**It never publishes anything by itself.** Nothing this machine writes is a Shariah
status, and no run of it puts a coin in front of a person as a result.

Publishing is a separate, deliberate act by the owner, and it happens somewhere else:
`services/hilal_methodology.py` reads a committed file of admissions and writes the
standard. A reading becomes an admission only when a person has put it in that file and
committed it. See `HILAL_MARKETS_METHODOLOGY.md`.

## The five parts, and what each one is not allowed to do

| Module | Does | Must never do |
|---|---|---|
| `services/coinmarketcap.py` | Finds where a project publishes | Judge anything |
| `services/coin_evidence_crawler.py` | Reads those pages into text | Judge anything |
| `services/sharia_conditions.py` | Holds every rule and its evidence | Decide anything |
| `sharia_condition_decisions.json` | Records which rules the owner approved | Hold a rule |
| `services/sharia_evidence_vocabulary.py` | Turns words into facts | Hold the rule |
| `services/sharia_automated_screen.py` | Applies the approved rules | Read anything |
| `services/sharia_evidence_screen.py` | Joins them and answers | Publish anything |

Keeping them apart is the point. The rule is small enough to argue about in a review;
the words that trigger it can be corrected without touching the rule.

**Writing a rule down and applying it are two separate acts.** Every condition lives in
the register with its Qur'anic or hadith evidence; whether it is *live* lives in the
decisions file, signed and dated. A condition nobody has approved is read on every coin
and reported — so its effect can be seen before it is turned on — and it can never
change a verdict. See [the Arabic register](SHARIA_SCREENING_CONDITIONS_AR.md), which is
generated from the code and cannot drift from it.

## The three answers

| Answer | What it means |
|---|---|
| **Looks clean** | We read the project's pages and found nothing that breaks the rules we check. |
| **Has a problem** | The project's own pages describe something the rules do not allow. The sentence is shown. |
| **Not enough data** | We could not find enough written about the project to say anything. **This is not a "no".** |

### What it does not attempt

The screen reads **up to 80 of the project's own pages**, and that number is the
definition of its reach rather than a tuning knob. Twelve of the 68 approved rules
cannot be settled from any number of web pages — riba al-fadl needs the mechanism of a
swap, a debt ratio needs a balance sheet — so those are **skipped**, not queued.

The reasoning, kept because it will look like a gap otherwise: a rule nobody can act on
produces a queue nobody can clear, and a flag that appears identically on every coin
tells a reader nothing. What the screen does not look at is said **once**, in the notice
beside every result. Skipping is not passing — a skipped rule counts in neither
direction, and it has no phrases, so no page can ever trigger it.

### When "not enough data" is used

**Only when the folder holds nothing that speaks to the question.** That is either no
readable page at all, or pages that never say what the project does.

It is *not* used because one detail was missing. Once there is something to reason
about, the screen reaches an answer, and where the evidence is thin the answer is the
careful one. A screen that says "not enough data" whenever a field is awkward produces a
queue nobody can clear and a product that says nothing about most of the market.

## Every answer carries its words

A finding is not a fact until it can be shown. Each reason stores the sentence it came
from and the address of the page, so a reader who disagrees can open the page and read
the line. This is also the only defence against the failure the product cannot afford —
confidently refusing a coin over a word nobody can find.

## Eight rules that stop a word meaning more than it says

Each was written after a real coin was judged wrongly. The coin is named in each, because
a rule whose reason is forgotten is a rule somebody deletes.

| Rule | What it stops | The coin that taught it |
|---|---|---|
| Whole words only | A phrase matching inside a longer word | **Tezos** — "raffle" inside "**Raffles** Avenue" on an events page refused it as a casino |
| A denial is not an admission | "We are not a lending protocol" counting as one | **Dogecoin** — its FAQ heading "Dogecoin has no utility!" exists to answer that charge |
| Refusals need the project's own page | A newsroom refusing the chain it reports on | a chain's blog covers the whole market |
| Not an ecosystem page | A showcase of other people's work | **Cardano** — "Lenfi – Lending protocol – Interview"; **Avalanche** — a fund manager's money market fund |
| Not somebody else's page | An encyclopedia or an exchange listing read as the project | **Ethereum** — Wikipedia's futures paragraph; **Gemini Dollar** — `gate.com/trade/GUSD_USDT` |
| Refusals need corroboration | One passing mention refusing a coin | **Ethereum** — one news line, "Lending protocol Moonwell suffered a loss" |
| Not a company this project merely names | Credit for a partner's business landing on the host | **Algorand** — "Folks Finance is ready. From lending and borrowing to swaps…" on its own homepage |
| Not a directory of other people's software | A tools or wallets index read as self-description | **Ethereum** — `ethereum.org/developers/tools`: "Seamless Protocol is the largest native lending and borrowing DeFi platform on Base" |
| One page has one address | The same page counted twice, so it corroborates itself | **Ethereum** — `ethereum.org/developers/tools` and `www.ethereum.org/developers/tools` were two pages to the counter |
| Not what somebody else *could* build | A platform refused for its own list of use cases | **Ethereum** — its whitepaper: "…can be implemented on the Ethereum blockchain. The simplest gambling protocol is actually simply a contract for difference…" |

Corroboration means: said on a **share** of the project's own pages, or three times on
one. A business repeats itself; a passing reference does not.

**The share is the point, and it was learned twice.** The rule began as a flat "two
pages", which worked while the crawler read twelve. When the budget rose to 80 on
31 August 2026 the same words silently meant "two of forty-three" — four per cent, which
is what a passing mention looks like — and it refused Ethereum on one line of a news
digest, the exact sentence the rule had been written to stop. The bar is now
`max(2, 12% of the project's own pages)`: unchanged for a small folder, and rising with
however far the crawler goes.

The last two need the project's own name, which is why `read_documents` is told what it
is reading. A sentence naming a company that is not this project is about that company;
a sentence naming *this* project is the project describing itself, and still counts.

**"This project" is more than the ticker.** Plenty of tokens are issued under a different
name — GHO is Aave's, EIGEN is EigenLayer's — so a sentence reading "Aave Labs operates
the lending protocol" on GHO's own page would name a company the rule did not recognise,
and the refusal would be dropped. That is a **missed** refusal, the direction of error
that matters most, so `project_terms_for` is given every name the project uses: the
ticker, the coin name, the provider's slug, and the words in its own domain. Extra names
can only ever *keep* a refusal, never invent one.

### The cost of the corroboration threshold

It is a trade-off, not a free win, and it should be read as one. Jupiter's front page says
"the everything exchange on Solana — swap tokens, lend and borrow crypto, trade perps" —
once. Under the threshold that single mention no longer refuses JUP, which brings the
screen into agreement with Fasset, **and** it means a project that advertises a blocking
activity only once will pass. The threshold was set to match the authority being
reproduced; moving it moves both error kinds at the same time.

## Being paid, and being paid *riba*

The hardest question, and the one that produced the worst measured failure. Three
vocabularies, resolved in a stated order:

1. The project names the source as lending → **riba**.
2. The project names the source as work → **not riba**.
3. The project says holders are paid but never says by what → **riba** (fail closed).
4. Nothing says holders are paid → the question does not arise.

Steps 1 and 3 are the only ones that can refuse, so both are read from the project's own
descriptive pages only. Collapsing "pays a return" and "pays riba" into one question once
refused Chainlink, Polygon, Hedera, NEAR and every liquid-staking token for paying
validators.

## Where it runs

| Task | Beat | What it does |
|---|---|---|
| `research_unscreened_coins` | daily | Gathers links for tradeable, unruled coins |
| `screen_researched_coins` | daily | Reads those coins' pages and records the reading |
| `refresh_market_numbers` | daily | Size, rank, 7/30/90-day movement for screened coins |

All three start themselves after a deployment. Nobody has to run anything.

To fill the page immediately rather than waiting for the next beat:

```bash
# Gather links, then read pages and decide, then fill the market filters.
.venv/Scripts/python -c "from ai_market_monitor.worker import research_unscreened_coins as t; print(t())"
.venv/Scripts/python -c "from ai_market_monitor.worker import screen_researched_coins as t; print(t())"
.venv/Scripts/python -c "from ai_market_monitor.worker import refresh_market_numbers as t; print(t())"
```

Until `refresh_market_numbers` has run once, the new Market filters have nothing to
filter on and every coin shows **Not known** for size and long-range movement. That is
the correct display for a number nobody has read yet — it is not a zero, and the page
never draws one.

### Browser rendering matters more than it looks

`SHARIA_SOURCE_BROWSER_RENDER_ENABLED` is **on** since 1 September 2026, and the Docker
image installs Chromium in its runtime stage. With it off, every project whose site needs
JavaScript is unreadable and lands in *Not enough data* for a reason that is ours rather
than theirs. Measured on the same 20 coins on 30 August 2026, with the rules of that day:
**9/20 without it, 17/20 with it**. Three of the eight coins it recovered had been filed
as unreadable purely because their site needs JavaScript.

That measurement is why it is on. It was off because Chromium costs about 300 MB while it
runs and the production server is a 3.9 GB machine with no swap — a real memory decision,
not a formality. What changed is not the memory but what bounds it: one browser per sweep,
started only when a page needs it and closed in a `finally`; one page open at a time;
images, GPU and extensions off; and a hard page budget,
`SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES` (40). The worker container's 1024 MB ceiling is
sized to hold the Celery parent, one child and one browser at once, and
`test_invariant_container_memory_limits.py` asserts that sum.

If the server ever runs short, `SHARIA_SOURCE_BROWSER_RENDER_ENABLED=false` turns it off
again without a deploy, and the review cases say plainly that it is off rather than
reporting the coins as having no pages.

## What it costs

| Thing | Cost |
|---|---|
| Finding where 100 coins publish | 1 provider credit |
| Reading one coin's pages | 0 credits, up to 80 page fetches |
| Market numbers for the whole screened list | 2 credits, once a day |

## How it was measured

`scripts/blind_automated_screen_probe.py` runs the whole thing for real against coins
whose answer is already known, and it **can fail**.

The coins are drawn at random by seed, never chosen by hand — choosing them would be
choosing the ones expected to pass. The screen sees a ticker and a set of web pages; the
answer key is loaded at the very end, only to score with.

```bash
.venv/Scripts/python scripts/blind_automated_screen_probe.py --stage control --count 25
```

`--stage live` runs the same thing against the busiest coins on Bybit, whose answer
nobody has. It publishes nothing either.

### What it measures, on 30 August 2026 — under the old five-rule screen

> **These numbers describe a rule that is no longer the one running.** On 31 August 2026
> the owner approved 60 further conditions, taking the screen from 5 blocking activities
> to 23. Everything below was measured before that and is kept because the *method* it
> describes is still how the screen is measured — not because the figures still apply.
> The current figure is in the section after it.

Two numbers, both from coins drawn at random by seed and never chosen by hand.

| Run | Result |
|---|---|
| 3 coins (`--count 3`, the release gate) | **3/3** |
| 20 coins, a seed nothing was tuned against | **14/18** — two symbols the provider does not carry |

**Every one of the four misses was inspected, and none was a vocabulary defect.**

| Coin | Why it missed | Kind |
|---|---|---|
| **Synthetix (sUSD)** | its own site: "Perpetual futures that don't make you choose" | the screen is right |
| **PancakeSwap (CAKE)** | its own documentation lists "Lottery" and "Prediction" | the screen is right |
| **USDD** | its own site: "Stake your USDD to earn rewards" | the screen is right |
| **Arkham (ARKM)** | `arkm.com` would not answer; only the whitepaper was read, and it never says what the project does | not enough data |

So the honest reading is: **78% agreement with the authority, and no case where the
screen misread the words in front of it.** The gap is a real disagreement about whether
a project that also runs a lottery, or pays its stablecoin holders, stays eligible.

That number is lower than the 91.6% the rule alone scores in
[the methodology note](HILAL_MARKETS_METHODOLOGY.md), and it should be. That measurement
starts from facts somebody wrote down; this one starts from a ticker and reads the web.
It is the harder measurement and the one that matches what the product actually does.

### Disagreements that are not defects

Tuning the vocabulary until these pass would be fitting the screen to the answer key —
the circularity the blind probe exists to prevent. Each of them is a true statement by
the project about itself, refused by the rule this screen reproduces:

| Coin | What its own site says today |
|---|---|
| **Synthetix (sUSD)** | "Perpetual futures that don't make you choose" |
| **PancakeSwap (CAKE)** | "Lottery", "Prediction" — in its own documentation index |
| **USDD** | "Stake your USDD to earn rewards" |
| **PayPal USD (PYUSD)** | "PayPal lets you earn rewards for holding PYUSD" |
