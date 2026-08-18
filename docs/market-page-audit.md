# The market page: what was wrong, and what the new one does

Two pages, same data:

- `/dashboard/market` — what ships today.
- `/dashboard-test/market` — the redesign.

Everything below was checked in a real browser against the real app. Nothing here is
an estimate.

## Part 1 — What was wrong with the old page

Each row names the file, so the claim can be checked.

| # | Problem | Where | Why it matters |
|---|---|---|---|
| 1 | **Binance coins showed no price at all.** Every Binance row rendered as if the coin were not trading. | `services/market_preview.py` | The main exchange looked broken. Measured: 0 of 490 coins had a price. |
| 2 | Column titles were printed in CAPITALS. | `hilalmarkets.css` `.live-market-head` | The brand rules forbid capitals for headings. |
| 3 | Those titles used a grey that measures 3.98:1 against white. | same rule | Text that small needs 4.5:1. It failed the accessibility rule. |
| 4 | A coin with no price had its **whole row faded to 58%**. | `.live-market-row.is-unavailable` | It faded the Shariah result too. A missing price says nothing about a finished review. All the faded text also fell below the contrast rule. |
| 5 | The table was not a real table. Plain boxes were labelled as one, and the row count was sent as `-1`. | `partials/live_market.html` | A screen reader was told "this is a table of unknown size". |
| 6 | Two green "New watchlist" buttons on one screen. | topbar + `live_market.html` | The brand rules ask for one green focal point per section. |
| 7 | Words a beginner does not know, never explained: "Bid", "Ask", "Methodology". | `live_market.html` | The product is built for beginners. |
| 8 | Nothing told you what the page was, or how many coins passed. | `live_market.html` | You landed straight in a spreadsheet. |
| 9 | When a price was missing it showed `--` and said nothing. | `sharia-market.js` | You could not tell if the coin was the problem or the connection. |
| 10 | With JavaScript off, the page was eight grey boxes forever. | `live_market.html` | The review results are the point, and they need no JavaScript. |
| 11 | Two different searches: one in the top bar, one on the page. | topbar + `live_market.html` | They behaved differently. |
| 12 | The standard's name was cut to "AAOIFI-aligned sc...". | `live_market.html` | You could not read what you had chosen. |
| 13 | Seven fixed columns, 857 px minimum. | `hilalmarkets.css` | Sideways scrolling on every phone. |
| 14 | No sorting, no card view, no way to pause the price updates. | — | |
| 15 | Favorites asked you to "Mark to remove", then Save. | `market.html` | Backwards, and easy to get wrong. |
| 16 | The status badge was a coloured word with no icon. | `sharia-market.js` | The brand rules say pair colour with text **and** an icon. |

### The Binance fault, explained simply

The price service asked the exchange for every coin in one request, naming all 490 of
them in the web address. That address came to 8.3 KB. Binance refused it with an error
that means "your request is too long". The code caught the error, had no smaller plan
to fall back to, and ended up with **no prices at all**.

Bybit hid the fault for months, because the library asks Bybit differently — it never
puts the coin names in the address, so Bybit's request was never too long. Only Binance
looked broken.

**Measured, against the live exchanges:**

| | Coins asked for | Coins that came back with a price |
|---|---|---|
| Binance, before | 490 | **0** |
| Binance, after | 490 | **490** |
| Bybit, before | 409 | 409 |
| Bybit, after | 409 | 409 |

The fix asks in batches of 100. If a batch is still refused, one request that names
nothing covers the rest. If that fails too, coins are asked for one at a time, up to a
limit. A coin no request can answer keeps **no** price — it is never filled in with a
guess.

Because every part of the product reads prices through this one place, the same fix
repairs the Scanner, on-demand scans, the strategy builder and the setup chat.

## Part 2 — Scores

Scored against the checklist in `dashboard-test-rules.md`. This is a judgement against
those rules, checked in a browser. It is not a user study.

| What | Old | New | What changed |
|---|---|---|---|
| Knowing where you are | 3 | 9 | Counts that are also the filter, one plain sentence, live status you can pause |
| Visual design and brand | 6 | 9 | One green focal point, brand tokens only, the chamfer used once, real coin logos |
| Easy for a beginner | 3 | 9 | No jargon; long text hidden behind "show more"; every term explained once |
| Interaction | 4 | 9 | Search, four filters, sorting, cards or table, pause, hover and focus everywhere |
| Accessibility | 3 | 9 | Real table, 44 px targets, focus rings, popups return focus, icon + colour + words, reduced motion |
| Honesty about data | 6 | 10 | Missing stays missing; the 24h bar is drawn only from real numbers; a price never touches a status |
| Phones and failures | 3 | 9 | Cards reflow to one column; works without JavaScript; designed empty, error and stale states |
| **Average** | **4.0** | **9.1** | |

I have not written 9.9. A score above 9 from here needs things I cannot do alone: a
screen-reader pass with a real user, and a designer's review. What I can say is that
every line in the checklist is met and was checked in a browser.

## Part 3 — What the new page does

**One screen, in order:** what this is → how many coins → filter and search → the coins.

- **Four tiles that are also the filter.** All screened · No conditions · With a
  condition · You follow. Tiles 2 and 3 split tile 1 exactly. There is no "needs
  review" tile, because this page only ever lists coins that passed, so that tile could
  only ever read zero.
- **Cards or table.** Cards for reading, table for comparing and sorting. Both show the
  same coins and sort together.
- **A real 24-hour bar** on each card, showing where the price sits between the day's
  low and high. Drawn only when all three numbers are real.
- **Pause.** The live updates can be stopped, which also helps anyone who finds movement
  hard.
- **Honest gaps.** A coin with no price says "No live price right now. The review below
  is unaffected." A coin whose price is not fully checked says so separately.

**Motion** comes from Motion One, kept in the repository at
`static/vendor/motion.min.js`, so the page makes no outside request. Every movement
explains something: cards settle in order as they arrive, a price flashes the way it
moved, a heart beats once when you follow a coin, a popup grows from nothing. All of it
stops when the reader asks for less motion.

**The popups and the Passport** follow the same rules. The quick view answers three
questions in order — what is the result, why, and what it does not cover — and keeps
everything else behind "show more". The full Passport adds rule-by-rule decisions,
sources with their saved copies, and the history. The report is the same record laid
flat for printing.

**"New watchlist" is gone** from the market page, both popups, the Passport and the
report — including the shared top bar, which now hides it on this path.

## Part 4 — Known limits

- The page shows only coins that both passed a review **and** trade on the chosen
  exchange. That is existing behaviour and is correct, but it means the count can drop
  when you switch exchange.
- The 24-hour bar needs a low, a high and a last price. Some coins do not report all
  three, and then the bar is not drawn at all.
- Coin logos come from a public icon catalogue. A coin the catalogue does not know keeps
  its letter badge.
