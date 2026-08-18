# Watchlists and Opportunities — scores, and the two new pages

Written for a reader who is not an engineer. Short sentences. Plain words.

---

## Where this stands

| Part | State |
|---|---|
| The chat agent's confusion about coins and topics | **Done and tested** |
| Both live pages scored, with every problem written down | **Done** — below |
| `/dashboard-test/watchlists` designed and built | **Done and tested** |
| `/dashboard-test/opportunities` designed and built | **Done and tested** |

---

# Part 1 — The chat agent

You showed me a real conversation where Hilal got it wrong three times in a row. It was
two separate faults, and both are fixed.

## What went wrong

> **"Is litecon halal?"** → *"I don't have a record for 'litecon'. Did you mean Litecoin?"* ✅
> **"yes"** → *"I don't have a review record for Litecoin"* ❌
> **"I see it as LTCUSDT"** → *"your canvas has only the Head and shoulders card…"* ❌

**Fault one: a short answer has no coin in it.** The word "yes" contains no coin name.
Hilal looked only at the newest message, found nothing, and read "nothing found" as
"we do not have it". Telling somebody this platform does not have a coin it *does*
have is exactly as wrong as inventing a status for one it does not.

**Fixed.** The conversation is now read for coin names as well as the newest message.
What you just said comes first, then what is open on your screen, then what you were
already talking about. So "yes" still knows it means Litecoin.

**Fault two: a trading pair is not a coin name.** People read `LTCUSDT` off a chart and
type it whole. Nothing matched it.

**Fixed, from your own data.** No list of currencies is written anywhere. There is a row
saying the market `LTC/USDT` exists, and its plain spellings already include `ltcusdt`.
The platform knew; it only had to be asked. `LTC`, `ltc`, `$LTC`, `Litecoin`,
`LTC/USDT`, `LTCUSDT` and `ltcusdt` are each a test.

**Fault three, which you named exactly: it dragged the canvas into a question about a
coin.** Hilal is now told, in as many words, that this is one long conversation, people
change the subject constantly, and an answer about a coin has nothing about the canvas
in it. It is also told never to explain a "no" by talking about what you were doing
before.

## A deeper fault found underneath

While fixing yours I found that **"this coin is not listed here" could never be said at
all**. The words were made lowercase before anything looked at them, which threw away
the two marks that say "I mean this as a coin": the capitals and the leading `$`. So
Hilal never received a clear "we do not have this one" — only silence, from which it
guessed. That is very likely how the wrong answer got its confident tone.

Fixed, with the sentence-shouting case handled too: `IS BTC HALAL` must not announce
that `IS` and `HALAL` are unlisted coins.

## And one more

A coin the platform has but has **not reviewed yet** now says so in words. "We have it,
the review is not published" and "we have never heard of it" are opposite answers, and
an empty status field cannot tell them apart.

**33 checks**, every one a family rather than a single example.

---

# Part 2 — The scores you asked for

Scored from the pages as they actually render, with a real account and real data — not
from reading the code.

## `/dashboard/watchlists`

| | Score |
|---|---|
| UX | **4.5 / 10** |
| UI | **6 / 10** |
| User-friendliness | **3.5 / 10** |

The UI score is the highest because the page is tidy and on-brand. It is the *words*
and the *decisions* that fail.

**Findings**

| # | Problem |
|---|---|
| 1 | **Two "New Watchlist" buttons on one screen**, one in the top bar and one in the page header, three centimetres apart. |
| 2 | **Four counters, three of them useless.** "Active 1 · Paused 0 · Eligible asset scopes 0 · Total current lists 1". "Total" is just the first two added up, and "Eligible asset scopes" is not English. |
| 3 | **A health score for a list that had never run.** It showed "43/100" for a Watchlist that had never checked the market once. That number is about nothing. |
| 4 | **The same problem said twice, in two different wordings**: "Screening policy needs review" above the name, and "Policy unavailable" as a badge below it. |
| 5 | **"0s scan latency"** — meaningless to a beginner, and the word "scan" is in it twice. |
| 6 | **Nothing says what the list actually watches.** No coins, no conditions, no hint. |
| 7 | **"Top blocker: RVOL above 1.50x \| 100% blocking impact"** — four pieces of jargon in one line, and no explanation of any of them. |
| 8 | **A marketing headline in a working page**: "Keep watch without losing control". |
| 9 | **A 109-character sentence of internal vocabulary**: "Each plan retains its exact rules, approved version, screened universe, delivery policy, and health evidence." |
| 10 | **Putting a list away used the browser's own grey confirm box** — unbranded, and not something a screen reader announces as a choice. |
| 11 | **Four things to press on the whole page.** No search, no filter, no sort. With twenty lists it is an unbounded stack. |
| 12 | **"Historical evidence and approved versions remain immutable when the list is edited"** sits in the row of buttons, where a person is deciding what to press. |
| 13 | **A product decision inside a template**: whether a draft counts as active was worked out in the page's own markup, where nothing can test it. |

## `/dashboard/opportunities`

| | Score |
|---|---|
| UX | **5 / 10** |
| UI | **6.5 / 10** |
| User-friendliness | **3 / 10** |

**Findings**

| # | Problem |
|---|---|
| 1 | **16 of the 25 things you can press are under 44 pixels tall.** Measured. That fails the touch-size standard everywhere. |
| 2 | **The same page has three names**: the sidebar says "Opportunities & Evidence", the heading asks "What is closest right now?", and the browser tab says "Evidence and Activity". |
| 3 | **The same coin appears twice, worded differently.** SOL/USDT is "CONFIRMATION PENDING · 4/5 required rules passed" at the top and "GETTING CLOSER · 80% READY" further down. A person cannot tell whether these are the same thing. |
| 4 | **303 words**, most of them internal: "near miss", "provider data error", "lifecycle events", "blocker", "opportunity journeys", "setup evidence", "RVOL", "EMA 200". |
| 5 | **"Current: 1.27 · Required: 1.5 · Distance: 0.23"** — three numbers, no units, no meaning. |
| 6 | **A failure and a gap look identical.** The card that means "we could not get the data" shows "0/5 required rules passed" with an empty bar, which reads as "this failed" rather than "we could not check". |
| 7 | **Two status systems side by side**, "CONFIRMATION PENDING" and "HEALTHY", with nothing saying how they differ. |
| 8 | **"1 lifecycle events"** — broken English, on screen. |
| 9 | **Two panels folded shut** with no preview of what is inside them. |
| 10 | **"Version 3"** shown to a customer. Our filing system, not their information. |
| 11 | **The progress bar has no accessible value**, so a screen reader reads a bar that says nothing. |
| 12 | **A third-party chart widget** opens inside a brand page. |
| 13 | **The empty state gives an instruction, not a way forward**: "Activate a validated Watchlist and allow its first market evaluation to complete." |

---

# Part 3 — The new Watchlists page

`/dashboard-test/watchlists`. Same data as the live page — its context builder is
called, never copied — so the two can never disagree about how many lists are running.

## The idea

Three questions, in the order somebody actually asks them:

1. **Is anything wrong?** — one line, and only when there is.
2. **What am I watching?** — one card per list, readable aloud.
3. **What do I do next?** — one clear action per card, in reach.

The four counters are gone. They answered none of the three.

## Every finding, answered

| # | Was | Now |
|---|---|---|
| 1 | Two "New Watchlist" buttons | One. The top bar's is hidden on this path. |
| 2 | Four counters, three useless | None. A single sentence, shown only when a list needs a look, with a **Show me** button that filters and points at it. |
| 3 | A health score for a list that never ran | Three honest answers instead of a number: **Working well**, **Working, with a gap**, and **Not looked yet**. The third is the one the old page could not say. |
| 4 | The same problem said twice | Once, in words, with a sentence saying what it means. |
| 5 | "0s scan latency" | "Last looked: **3 hours ago**". The exact time is kept where you hover, for anybody who needs it. |
| 6 | Nothing said what it watches | Three facts on every card: how many coins, which screening standard, when it last looked. |
| 7 | "Top blocker \| 100% blocking impact" | "**Usually waiting on:** RVOL above 1.50x", and a **What does this mean?** button that explains it, shows the share as a bar that fills, and says plainly that it is not advice. |
| 8 | A marketing headline | Gone. |
| 9 | 109 characters of internal vocabulary | Gone. Thirteen internal words are now **named in a test** so they cannot come back. |
| 10 | The browser's grey confirm box | A real dialog that names the list, says what will happen, traps the keyboard, closes on Escape, and gives focus back. |
| 11 | Four things to press, no way to find anything | Four filters that each carry a real count and grey themselves out when empty, plus a search box. |
| 12 | Legal text among the buttons | Gone. |
| 13 | A product decision inside a template | Moved into `product_language.py`, beside every other plain word this product uses, and tested for all eight cases. |

## Interactivity and motion

All of it from Motion One, through the one shared file, and all of it explaining
something: cards settle in one after another on arrival; only *newly matching* cards
animate while you type, so the page does not flicker under a keystroke; **Show me**
scrolls to the card and pulses it once; the share bar fills from nothing to its real
number, so you see "most of the time" rather than reading it; every card lifts on
hover; every filter lifts. `prefers-reduced-motion` removes all of it and the page still
works completely — that is its own test.

## Accessibility

Every filter is a real toggle a screen reader reads as pressed. Status is never colour
alone: the coloured rail always has the word and an icon beside it. Both dialogs trap
focus, close on Escape and return the keyboard. **Every single thing you can press is at
least 44 pixels** — measured in a browser, not assumed. Changes are announced once,
politely, and the search waits before speaking so it does not read out every letter.

## Two things found and fixed while building

- **The shared button stretches.** It carries "grow to fill", which is right in a row of
  equal actions and wrong in a page header — one primary button was four hundred pixels
  wide across empty space.
- **The shared button is 40 pixels tall**, not 44. Both are corrected on these pages, at
  the right weight, after the first attempt silently lost and left half the page passing
  and half failing for a reason nothing on screen could show.

---

# Part 4 — The new Opportunities page

`/dashboard-test/opportunities`. Same data as the live page, from the same two records
the platform already keeps.

## The idea

Three questions, in the order somebody asks them:

1. **What is closest to happening?** — one card per coin, sorted into five plain groups.
2. **Why is it not there yet?** — one line on every card, with its numbers explained.
3. **What did we actually see?** — a popup per card holding every reading we kept.

## The one thing that had to change first

The live page keeps **two records about the same coin** and drew both.

> `SOL/USDT` — "Confirmation pending · 4/5 required rules passed" near the top
> `SOL/USDT` — "Getting closer · 80% ready" further down

Same coin. Same moment. Two cards, two sets of words, and nothing on the page saying
they were one thing. That happened because the two records use **two different sets of
words for the same fact**, and each half of the page read one of them.

The fix is not to hide one. It is one rule that reads both, so the page cannot disagree
with itself. Eight pairs of words now resolve to one answer, and each pair is a test.
The two records are joined on the server, so a coin gets **one** card carrying the count
from one record and the history from the other. Nothing is lost either way: a recorded
opportunity with no readiness row still gets its card, so nothing can vanish because a
short-lived row was tidied away.

## Every finding, answered

| # | Was | Now |
|---|---|---|
| 1 | 16 of 25 things to press were under 44 pixels | **Every one is at least 44** — measured in a browser, not assumed |
| 2 | The page had three names at once | One name. The tab, the heading and the menu all say **Opportunities**, and a test fails if they drift |
| 3 | The same coin shown twice, worded differently | **One card per coin.** Explained above |
| 4 | 303 words, most of them from inside the machine | **24 words are named in a test** and cannot come back. The check runs on a page with real data on it, because that is when most of them appeared |
| 5 | "Current: 1.27 · Required: 1.5 · Distance: 0.23" | "**You asked for 1.5. Right now it is 1.27. That is 0.23 away.**" No word ever says "higher" or "lower" — the distance is stored without a direction, and guessing one would be inventing a fact about your own rule |
| 6 | A failure and a gap looked identical | A coin we could not read has **no progress bar at all** and says "This is not a pass and not a fail." The old page drew "0/5" with an empty bar, which reads as "your rules failed" |
| 7 | Two status systems side by side | One status per card. Old numbers are said as a note, not as a second status |
| 8 | "1 lifecycle events" | "Changed once since we found it". Five plural cases are tested |
| 9 | Two panels folded shut with no preview | Gone. What was in them is on the card, or in a popup opened from the thing it explains |
| 10 | "Version 3" shown to a customer | Gone. Our filing system is not their information |
| 11 | The progress bar said nothing to a screen reader | It is a real progress bar that reads out **"4 of 5 things you asked for are true"** |
| 12 | A third-party chart opened inside a brand page | The popup says the picture is drawn by an outside company's tool and is **not our evidence**. The tool is fetched only when somebody asks for a picture — before, it loaded on every single visit to the page |
| 13 | The empty state gave an instruction, not a way forward | Two different nothings, two different answers. "No lists yet" offers the two ways to make one. "This one list found nothing" offers the way back to everything — the old page would have left you stuck |

## The popups

All three the live page had, redesigned, none dropped.

| Popup | What it does |
|---|---|
| **What did we see?** | Every reading we kept for that coin: each thing you asked for, whether it is true, and what the market number was. |
| **Why was I not told?** | Seven different real reasons, each in plain words with what you can do about it. The platform already worked out which one is true; this only says it in a language a person reads. |
| **Price picture** | The chart, with a plain sentence saying where the picture comes from. When there is no picture it says so in words instead of showing an empty grey box. |

**A popup never writes its own sentences.** The words inside it are written by the
server into the card and copied across when it opens. A popup that built sentences out
of numbers would be a second opinion about the same evidence, free to disagree with the
card behind it.

## Interactivity and motion

All of it through the one shared motion file, and all of it explaining something: cards
settle in one after another; only *newly matching* cards move while you type; each
progress bar fills from nothing to its real count as its card comes into view, so you
see "nearly all of it" before you read it; every card and every filter lifts on hover.
`prefers-reduced-motion` removes all of it and the page still works completely — the
bars reach their real width without ever moving, and that is its own test.

## Accessibility

Every group is a real toggle a screen reader reads as pressed, and greys itself out when
it has nothing behind it. Status is never colour alone. Every popup traps focus, closes
on Escape and gives the keyboard back. Every single thing you can press is at least 44
pixels, measured. Choosing a Watchlist is a plain form with a plain button on purpose:
sending the page away the moment a name is highlighted would move somebody who was only
reading the choices with the arrow keys.

---

# Part 5 — Problems found on the way, and fixed

None of these were in the request. All are fixed and tested.

| What was wrong | Why it mattered | Now |
|---|---|---|
| **Two vocabularies for one fact.** "What is this opportunity doing" was written in two different sets of words by two different parts of the platform | This is what put the same coin on the screen twice | One rule reads both. Eight pairs, eight tests |
| **The icon catalogue address was written out seven times by hand**, version number included — four times in Python, three more in templates and browser code | Raising the version means finding all seven. Missing one is silent: the coin quietly shows three letters instead of a logo and nobody reports it | One owner. No Python file may write it again, and a test fails if any front-end copy names a different version |
| **How a market number is written was implemented twice** | The same threshold could show four decimals on one screen and two on another | One function, twelve cases tested |
| **Opening and closing a popup was written three times** | Three places for the same bug to be fixed twice and missed once | One shared file, used by every popup on the path |
| **Finding a card — the filter, the search, the announcement — was written twice** | The two copies were already drifting in how they announce a result | One shared file, used by both pages |
| **Choosing a list that had found nothing left you stuck** | The chooser was hidden inside "we have something to show", so the way back to everything disappeared exactly when it was needed | The chooser stays, and the empty state offers the way back by name |
| **A coin the recorded history had never seen got no logo** | One card with a real logo beside three showing letters looks broken | Every coin gets its logo, from the one catalogue address |
| **The status colour bar on every card was too pale to see.** The "working" bar was apple green, which measures **1.21 against white**. The quiet bar was the colour of a card border: **1.46**. A meaningful mark has to reach 3 | This is on the Watchlists page as well — it is one rule shared by both. The fastest way to read a wall of cards was a bar nobody could see | Both use brand colours that reach it: **5.9** and **3.98**. Apple green still carries the page's focal points — the one action a card is really for. Only the bar's role changed |
| **Contrast was never actually measured on these pages** | "It uses the right token" is not the same as "the browser painted it readable" — a later stylesheet can quietly override it | A real browser is asked for the colours it is really painting, on **both** pages, and the check is written once and shared |
| **A "See the Passport" link would have landed on a "not found" page** for any coin with no published record | A visible button that breaks is worse than no button | It opens the Passport popup this path already has, which says "there is no published record for this one" in words — and carries the link to the full Passport once there is one |
| **The chart's colours were typed in as six hex values** | A second copy of the palette, free to drift from the real one | The chart reads the page's own colour tokens |

---

## How it was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 364 files |
| Unit, services, engine, interpreter and integration suites | **Pass** |
| Chat lookup and topic rules | **33 checks, pass** |
| New Watchlists page, through the real app | **37 checks, pass** |
| New Watchlists page, in a real browser | **19 checks, pass**, clean console |
| New Opportunities page, through the real app | **141 checks, pass** |
| New Opportunities page, in a real browser | **24 checks, pass**, clean console |
| One icon catalogue everywhere | **10 checks, pass** |
| `scripts/check_javascript.py` | Pass, 40 files |
| `scripts/check_jinja_templates.py` | Pass, 80 templates |
| `scripts/check_release_invariants.py` | Pass |
| `scripts/check_api_route_security.py` | Pass |

The layout checks are **measured at four screen widths**: nothing under 44 pixels,
nothing scrolling sideways, every filter really filtering, every popup really trapping
the keyboard. Colour is measured in a real browser, not read off a token.
