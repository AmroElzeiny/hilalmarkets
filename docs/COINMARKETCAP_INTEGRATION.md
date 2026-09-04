# CoinMarketCap — built in

Added 30 August 2026. CoinMarketCap is a **provider record**, exactly like CoinGecko: it
answers what a coin is, where its project publishes, and where it ranks. It is **never**
a Shariah authority. No status, ruling or eligibility is ever read from it — not from a
tag, not from a category, not from a rank.

## Why it was added when CoinGecko already exists

CoinGecko's record usually has no whitepaper. CoinMarketCap's `urls.technical_doc` is
the whitepaper, published per coin, for nearly every listed asset — including small ones.

Before this, the product looked for whitepapers and official pages on the open web:
searching, guessing conventional paths like `/blog`, and finally **paying a model** to
recall an address. A provider that already holds the answer replaces the weakest three
of those layers for most coins.

### The guessing layer is gone — 4 September 2026

"Replaces for most coins" was implemented as **ordering**, not removal. The provider ran
earlier, but when its record held no news page the sweep still fell through to guessing,
which built `<host>/blog`, `<host>/news`, `<host>/announcements`, `<host>/updates`,
`<host>/newsroom`, `<host>/press` and two subdomains, and offered them as candidates.

It was the only layer with **no gate at all**, so it ran for every coin that was short of
a category. HTX DAO's review case then read *"10 address(es) have been tried and none
worked yet"* and listed five of those paths. Every one was a 404. Nobody had ever
published any of them — the product invented them — and a reviewer reading that list
could not tell an invented address from a real one.

An official source is the project **saying** where it publishes. A guess is this product
assuming, and proving the guess afterwards does not turn one into the other. So the layer
was removed. Addresses now come only from:

| Layer | Who states the address |
|---|---|
| `CURATED` | a person typed it |
| `PROVIDER` | the CoinMarketCap record for the coin |
| `IDENTITY` | the official site a reviewer approved |
| `SOCIAL` | a link on the project's own website |
| `SEARCH` | a search result, kept only if provably the project's own |
| `ASSISTED` | a model, filtered the same way as a search result |

Rows already stored under the `convention` layer are **kept**. A guessed page that was
fetched and proved is still a working page, and withdrawing it would delete evidence that
answers. Nothing writes that layer any more.

What HTX DAO shows is that removing it costs nothing that was real. CoinMarketCap does
hold links for that coin — its Telegram channel and its X account, both of which count as
news. X refuses automated readers in its `robots.txt`, and the Telegram channel view
returns 108 characters of readable text, below the 200 the product requires. So the coin
genuinely has no news page anyone can read, and it belongs with a person. The eight
guesses added nothing but noise on top of that answer.

Measured on a live key:

| Coin | Website | Whitepaper | Source code | Community |
|---|---|---|---|---|
| BTC | ✅ | ✅ | ✅ | ✅ |
| SOL | ✅ | ✅ | ✅ | ✅ (all four categories) |
| TAO | ✅ | ✅ | ✅ | ✅ |
| QUBIC (tiny cap) | ✅ | ✅ | ✅ | ✅ |

## Settings

Four keys, added to **all four** env files (`.env.example`, `.env.production.example`,
`.env`, `.env.production`):

`COINMARKETCAP_API_BASE` · `COINMARKETCAP_API_KEY` · `COINMARKETCAP_PLAN` ·
`COINMARKETCAP_ENABLED`

Plus tuning in `core/config.py`: metadata cache hours, quote cache seconds, timeout,
batch size, the unscreened-research controls, and the automated-screen controls
(`AUTOMATED_SCREEN_BATCH_LIMIT`, `AUTOMATED_SCREEN_INTERVAL_HOURS`,
`MARKET_NUMBERS_INTERVAL_HOURS`).

## What the provider record is used for

| Use | Where | Cost |
|---|---|---|
| Where a project publishes | `sharia_source_resolution`, layer `PROVIDER` | 1 credit per 100 coins |
| The whole reading pipeline's seeds | [Automated coin research](AUTOMATED_COIN_RESEARCH.md) | shared with the above |
| A coin's logo | `core/asset_logos.py`, under its own key | free, comes with the record |
| Size, rank, 7/30/90-day movement | The Market page's table and its sorting | 2 credits daily for the whole list |
| Market mood, Bitcoin's share, market size | Nothing shows these today — see "New market-wide indicators" | nothing, the page stopped asking |

### The logo has its own key

The provider's picture is stored as `provider_ids["coinmarketcap_logo_url"]`, **never**
as `provider_ids["logo_url"]`. Two different jobs write a coin's picture — identity
discovery and the coin researcher — and if both wrote one field, whichever ran last would
silently replace the other's answer. Which picture is *shown* is decided once, in
`core/asset_logos.py`, in this order:

1. the picture stored when a reviewer verified the coin's identity,
2. the provider's picture,
3. the shared icon catalogue, addressed by ticker alone.

Specific before generic: the first two are pictures of *this* coin, while the catalogue is
keyed by ticker, so two coins sharing a ticker share its file.

### Market numbers are read daily, not per page

A ninety-day price change does not need a five-second cache. Reading them on a schedule
and serving them from the database costs about **60 credits a month**; fetching them
while pages load, at the cadence prices refresh at, would cost about **17,000**.

## What this key's plan actually carries

Probed live rather than read from the documentation, because the two disagree in both
directions. `services/coinmarketcap.py` declares every endpoint and the smallest plan
that carries it, so a call the account cannot make is **refused locally** instead of
spending a request to be told no.

| Works on Basic | Needs a higher plan |
|---|---|
| key info, id map, **metadata**, listings, quotes, global metrics, categories, trending, price conversion, exchange map, **fear & greed**, airdrops | gainers/losers, most visited, OHLCV latest + historical, price performance, market pairs |

Budget: **15,000 credits a month, 50 requests a minute.** One metadata call carries 100
coins, so researching 500 coins costs 5 credits.

## Where it plugs into the researcher

`sharia_source_catalog` gained a `PROVIDER` discovery layer, running **second** — after
links a person curated, before everything derived or guessed.

```
CURATED → PROVIDER → IDENTITY → SOCIAL → SEARCH → CONVENTION → ASSISTED (paid)
```

Confidence `0.85`: below a link a person checked (`0.95`), above one inferred from an
approved identity (`0.75`).

**The provider does not get its own judgement.** Fetching lives in
`services/coinmarketcap.py`; deciding what an address *means* stays in
`sharia_source_catalog`, which applies the same "is this provably the project's own
address" rules to a provider, a search engine and a model. Two filters matter:

- **Aggregator pages are refused.** CoinMarketCap returns
  `coinmarketcap.com/community/profile/Solana` as Solana's announcement URL. Published
  as-is it would point a Shariah reviewer at an aggregator instead of the project.
- **The provider's own filing is the weakest signal about category.** It files
  `solana.com/news` under `message_board`. The address itself says more, and
  `_word_category` already owns what those path words mean.

The provider's vocabulary stops at the boundary: the catalog never learns that
CoinMarketCap calls a whitepaper `technical_doc`.

## Coins that are tradeable but unscreened

`services/unscreened_coin_research.py` finds every coin listed for spot on Binance or
Bybit that carries **no Shariah result at all** — not halal here, not haram here,
because no authority has looked at it — and gathers the project's own website,
whitepaper, repository and logo.

Stored in `provider_coin_profiles` (migration `d4b91c07e5a2`). The table has **no status
column, no eligibility flag and no methodology link**, and
`tests/unit/test_invariant_unscreened_coin_research.py` fails the build if one appears.

`plan()` spends nothing and answers "what would this do?". `research()` is the part that
costs credits.

**It runs by itself after a VPS restart.** Celery beat carries
`research-unscreened-tradeable-coins` on a 24-hour schedule
(`UNSCREENED_RESEARCH_INTERVAL_HOURS`), so a deployment picks it up on its next tick with
nobody starting it by hand. **It has not been run.**

## New market-wide indicators

`services/market_sentiment.py` — readings the product did not have, because everything
else it holds answers a question about one coin:

| Reading | Live value at build time |
|---|---|
| Fear & Greed | 78 — "Extreme greed" |
| BTC dominance | 59.53% |
| Total market cap | $2.67T |
| Active cryptocurrencies | 8,084 |

Each carries a plain sentence written for a beginner, kept beside the bands rather than
in a template so every surface says the same thing. None of it is advice, none of it is
a signal, and none of it touches eligibility.

**No page shows these today.** The strip that used to carry them on the Halal Assets page
was removed on 3 September 2026, and the page no longer calls the service, so the reading
costs nothing until some page asks for it again.

## The rules that hold

- No Shariah status is ever read from, or written by, any of this.
- A provider record is a **proposal**. It is fetched and proved before it counts as
  evidence, exactly like a guessed URL.
- The key travels in a header, never a query string, so it cannot land in a log line.
- The provider being off, unreachable, or not entitled all mean the same thing: the
  layer offers nothing and everything below it behaves exactly as it did before.
