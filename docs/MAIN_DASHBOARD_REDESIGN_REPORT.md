# The `/main` dashboard — audit, redesign and fixes

Written 17 August 2026. Describes HEAD.

Two things happened in this pass:

1. `/dashboard` was **looked at in a real browser and scored**.
2. A new page `/main` was **designed and built**, and every real fault the audit found
   was fixed **at its cause**, so `/dashboard` and the other pages got the fix too.

The rules this work had to follow are in [`main-dashboard-rules.md`](main-dashboard-rules.md).
They were written first, from the request, before any code.

---

## 1. What was wrong with `/dashboard`

Everything below was **measured in a real browser**, not judged by eye.

### Score

| What | Score | Why |
|---|---|---|
| Look (UI) | **5.5 / 10** | Clean type and colour, but no order of importance. Four flat white boxes in a row, a dead column on the right, one button floating in the middle of the page with nothing around it. Card borders measure 1.2:1, so cards do not read as cards. |
| Ease of use (UX) | **4.5 / 10** | The page never answers "what should I do now". Two different buttons do the same job. Three of the four numbers are `0` with no explanation. Time is shown as `2026-08-16 22:35:30 UTC`. Nothing moves, nothing responds. |
| Friendly to a beginner | **4 / 10** | "Only persisted lifecycle evidence from your active Watchlists appears here." Every panel is described in engineer's words. |
| Reachable (accessibility) | **3 / 10** | No page title heading at all. The keyboard highlight measures **1.11:1** — invisible. Nine pieces of text below the legal minimum. |
| **Overall** | **≈ 4.5 / 10** | |

### The faults, one by one

| # | What was wrong | How it was measured |
|---|---|---|
| 1 | **The keyboard highlight was invisible.** The ring around whatever you have selected with the Tab key measured **1.11:1** against the page. The rule asks for 3:1. This was true on *every page in the product*, not only this one. | Read the real painted colour of the ring on eight controls in the browser. |
| 2 | **Nine pieces of text were too faint to read.** All four numbers' captions (3.98:1), all four side-menu group titles (3.73:1), every panel description, every empty-state sentence. The rule asks for 4.5:1. | Measured every visible piece of text on the page. |
| 3 | **The page had no title heading.** Headings started at level two. A screen reader announced the page with no name. | Counted `h1` elements: zero. |
| 4 | **The cookie notice covered the page.** It is stuck to the bottom of the window and nothing left room for it, so the last panel could not be scrolled out from under it. | Compared the notice's position with the last thing on the page. |
| 5 | **A machine timestamp.** "Last evaluated 2026-08-16 22:35:30 UTC". | Read from the page. |
| 6 | **Two buttons, one job.** "New Watchlist" in the top bar and "Create Watchlist" in the middle of the page. | Counted links to the create page: two, both visible. |
| 7 | **A heading in Title Case** — "Asset Passport". The brand rules ask for sentence case. | Scanned the headings. |
| 8 | **Bare zeros.** "0 / Eligible screened assets" says nothing about whether the market is quiet or the platform is broken. | Read from the page. |
| 9 | **Engineer's words.** "persisted lifecycle evidence", "tied to its strategy version and stored proof", "conditions currently complete". | Read from the page. |
| 10 | **One control was too small.** The side menu's "Minimize menu" button is 40px tall; the product's own rule is 44. | Measured every control. |

---

## 2. The new page: `/main`

A new path, in the same signed-in shell, over **exactly the same data** as `/dashboard`.
Nothing is counted twice: every number comes from a read model another page already
owns, so `/main` can never disagree with the page it links to.

### What it says, in the order a person asks

| Part | The question it answers |
|---|---|
| The dark band at the top | *Is anything happening right now?* One sentence, one next action, and when the market was last really checked. |
| Four tiles | *Where do things stand?* Each tile is one question, one number, one place to go, and its own "what does this mean" popup. |
| Closest to ready | *What is nearest, and what is it waiting for?* A ring showing how much of your own list is true, with the missing piece in words. |
| Coins you can watch | *What may I look at?* Real coin pictures. Press one and its Shariah evidence opens. |
| Your lists | *Are my lists working?* The same rows the Watchlists page shows. |
| Your messages | *Will I be told?* Only the ways this deployment can really deliver. |

### Things that were deliberately removed

| Removed | Why |
|---|---|
| The "New Watchlist" button | Asked for in the request. There is now **one** way to make a list, in the band, and only when making one is the right next step. |
| The four unexplained counters | Replaced by tiles that say what the number means and where to go. |
| The empty right-hand column | The old page gave a whole column to two rows of text. |
| The floating "Create Watchlist" button | Same job as the top-bar button, no heading, no context. |

### Things kept because they earn their place

The screened-asset count, the forming-opportunity count, the compliance-change count,
the active-list count, the latest opportunity with its progress, and the delivery
channels. All six are things a person acts on. Each was rewritten to say what it means.

### How it is built

| Piece | Comes from |
|---|---|
| Movement | **Motion One**, already in the repository at `static/vendor/motion.min.js`, through the shared `hm-motion.js`. No new library, no outside request. |
| Icons | The existing icon set (`hilalmarkets-icons.js`, Lucide shapes). One icon was missing for the menu, so **Lucide's `gauge` was imported exactly as it ships**. Nothing was drawn by hand. |
| Coin pictures | The one owner every other page uses (`asset_logo` and the `coin_logo` macro). |
| Popups | The shared `hm-dialog.js`, and the existing Shariah Passport popup. |
| Colours, type, spacing | Only tokens that already existed. **A test proves no new colour was invented.** |

### Movement, and what each piece is for

Nothing loops. Nothing flashes. Every movement explains something:

| Movement | What it says |
|---|---|
| The band arrives as one surface | This is one thing, and it is the first thing. |
| The live dot pulses **once** | The page has finished loading; this is fresh. |
| Numbers count up | "This many." Never a price — a price travelling through values it never had would be inventing market data. |
| The ring draws itself | "This much of your list is true." |
| Tiles settle in one after another | Read them in this order. |
| Hover: the card lifts, its icon fills green, its arrow slides | "Pressing here goes somewhere." |
| Popups scale open, and close faster than they open | Nobody wants to wait to leave. |

With **reduced motion** switched on, every one of these places the element at its final
state immediately. A test proves nothing is missing when motion is off.

### Contrast — well above the standard, as asked

The floor on this page is **AAA**, not AA. Measured on every visible piece of text.

| Where | Ratio | Standard asks |
|---|---|---|
| Headings and numbers on white | 15.7 : 1 | 4.5 : 1 |
| Body text on white | 7.5 : 1 | 4.5 : 1 |
| White on the dark band | 15.7 : 1 | 4.5 : 1 |
| Quiet text on the dark band | 10.8 : 1 | 4.5 : 1 |
| Apple green on the dark band | 13.0 : 1 | 4.5 : 1 |
| Near-black on the apple-green button | 13.0 : 1 | 4.5 : 1 |
| Status pills | ~14 : 1 | 4.5 : 1 |

The rule that makes this hold: **colour never carries the small words.** A status pill is
a tinted background with near-black words and a coloured icon — three signals at once,
at 14:1, instead of coloured words at 4.9:1. That is also what the brand rules ask for:
never colour on its own.

Bright apple green finally gets to carry a word — on the dark band, where it measures
13:1 instead of the 1.21:1 it measures on white.

---

## 3. Everything fixed, and how it was checked

Every fix is at the **cause**, so it applies everywhere — not only on `/main`.

| # | Fault | Fix | Where | Proof |
|---|---|---|---|---|
| 1 | Keyboard highlight at 1.11:1, product-wide | One ring, declared once, in two parts: a near-black inner ring (15.7:1 on light) and an apple-green halo (13:1 on dark). One indicator that works on every surface. Ten stylesheets that had copied a fainter version now point at it. Two of them had `outline: none` on focus, so there was no indicator at all. | `hilalmarkets-brand.css`, `hilalmarkets.css`, and 8 more | `test_invariant_focus_visibility.py` (179 checks, whole family) + browser measurement of every control |
| 2 | Nine pieces of text below the minimum | The cause was **one token**: the quiet grey measured 3.98:1 and was used as a text colour in about forty rules. The token was raised to 5.55:1, which fixes all forty at once. Three hard-coded copies of the old value were found and fixed too. The smallest text in the product (side-menu group titles) was moved to the strongest quiet colour, 7.5:1. | `hilalmarkets-brand.css`, `hilalmarkets-dashboard-v2.css`, `hilalmarkets-guide.css`, `dashboard.js` | browser measurement of **every** text element on `/dashboard`, plus the palette registry test |
| 3 | No page title heading | Added. | `hilal/dashboard/home.html` | browser test: exactly one `h1` |
| 4 | Cookie notice covered the page | The notice measures itself and publishes its height; the page keeps that much room clear. Zero once answered, zero for anyone without scripting. | `hilalmarkets-consent.js`, `hilalmarkets-dashboard-v2.css`, `hilalmarkets-public.css` | browser test on `/main` **and** `/dashboard` |
| 5 | Machine timestamp | Uses the one owner of "when did this happen" wording. The exact moment stays in the hover title. | `dashboard.py`, `hilal/dashboard/home.html` | browser test: no `0000-00-00 00:00:00` pattern on the page |
| 6 | Two buttons, one job | The page-level duplicate removed; the top-bar button stays. | `hilal/dashboard/home.html` | browser test: at most one visible create link |
| 7 | Title Case headings | "Asset Passport" was the one on `/dashboard`. Looking for the rest of the family found **fourteen more**, plus an ALL CAPS one, across the builder, the strategy pages, System Brain and the public features page. All fixed. | 7 templates + `dashboard.js` | `test_invariant_sentence_case_headings.py` — checks every static heading in every template, with an explicit list of the product's own names so a real name is not mistaken for Title Case |
| 8 | Bare zeros | Every number on `/dashboard` now says what nothing means. On `/main` this is built into the tile. | both pages | browser test: every tile has an explanation |
| 9 | Engineer's words | Rewritten on `/dashboard`; `/main` uses the plain-word helpers throughout. | both pages | browser test: no block of text over 170 characters on `/main` |
| 10 | Controls too small | The side menu's "Minimize menu" button was 40px tall and its brand logo link 31px; both are now 44. The one remaining small target is the "Cookie Policy" link inside a sentence, which WCAG 2.5.8 exempts by name because its height is set by the line around it. | `hilalmarkets-dashboard-v2.css` | browser test measures every control on the page, shell included, taking only the standard's own inline exception |

### Found on the way, and fixed

These were not in the request. They were found while building, and they are fixed.
Faults 11 to 14 and 19 were **already in the product before this work**; they were found
because the new page used the same shared pieces and the same test harness.

| # | What it was | Why it mattered | Proof |
|---|---|---|---|
| 11 | **Counted numbers never counted.** The shared movement layer asked the animation library to drive a number using a call shape that library does not support. It does not fail — it does nothing. Every counted number in the product was frozen at whatever it was set to first, which was zero. The Screened Market page and the Subscription page both use it. | A number showing `0` when the real answer is `12` is worse than no number. | browser test drives the shared module directly and asserts the number reaches its value; `test_invariant_shared_motion_layer.py` stops the call shape coming back |
| 12 | **The brand movement curve was never applied.** The same file passed the option under its old name, which the current library ignores. Every animation in the product ran on the library's default curve while the file's own comment said this could not happen. | Small, but the file claimed a guarantee it was not keeping. | same invariant test |
| 13 | **"Looked Not yet".** The helper that says when something happened answers a missing time with the words "Not yet" — and a piece of text is always "true", so any page checking that field instead of the time beside it printed "Looked Not yet". One page got it right, the next did not. Fixed at the source so no future page can get it wrong. | A sentence that reads like a bug. | unit test on the read model |
| 14 | **A ring that filled and then emptied itself.** A finished animation stops holding its end state, so the empty value set before it won. | The main graphic on the new page would have been blank. | browser test measures the arc against the real share |
| 15 | **Hover and focus written as one rule.** Five controls shared a rule that removed the outline, so a mouse user got a colour change and a keyboard user got nothing. | Same class as fault 1. | covered by the focus invariant |
| 16 | **A black shape outside the palette.** The new ring's canvas had no fill declared, so it computed to pure black. | The product's palette check exists to catch exactly this. | palette test now also covers the new stylesheet |
| 17 | **Broken dashes and quotes in real on-screen messages.** Six files in the repository had punctuation that had been written back through a Windows codepage. Most of it was in comments, but seven of them were messages a person sees while filling in a rule — `“RSI length” must be one of the choices shown` was on screen with junk characters where its quotes should be. All six files are repaired. | A customer reads it. Nothing else in the build would ever have noticed. | the copy lint now fails on it (`MOJIBAKE_MARKERS` in `core/copy_rules.py`), across every customer-copy source |
| 18 | **The copy lint was not reading the newest copy.** It scanned templates but not the dashboard routers, and `/main` writes every headline and explanation in Python. | The newest words in the product were unchecked for brand name, Shariah spelling and forbidden claims. | the three dashboard routers added to the lint's sources; it reports zero violations |
| 19 | **A half-written rule could disappear while you were writing it.** In the Watchlist Builder, choosing a coin list starts a request that also re-reads the screening options, so it is in flight for a second or two. Opening the "add a rule" form during that window — the natural next thing to do — meant the older request landed and closed the form again, with nothing said. The form now closes only when **it** was the thing that finished, or when the rule it is editing was just deleted. | A person who does not wait loses what they typed. Two browser tests had been failing on this before this work started; both pass now. | `tests/browser/test_dashboard_e2e.py` — 37 tests, including the two that had been failing |

---

## 4. What was checked, and how

| Check | Result |
|---|---|
| `ruff check src tests scripts` | **pass** |
| `mypy src` | **pass**, 367 files |
| `pytest tests/unit tests/engine tests/interpreter tests/services` | **pass** |
| `pytest tests/browser/test_main_dashboard_e2e.py` | **pass**, 28 tests |
| New invariant tests | **pass** — 179 focus-ring checks, 723 heading checks, 11 motion-layer checks |
| `pytest tests/browser/test_dashboard_test_*.py` | **pass**, 96 tests |
| `pytest tests/browser/test_dashboard_e2e.py` | **pass**, 37 tests. Two of them were failing before this work started — confirmed against a clean copy of the repository at `HEAD` — and finding out why turned up a real race in the Builder (fault 19 below). Both pass now. |
| `pytest tests/browser/test_phase5_status_and_brand.py`, `test_market_card_actions_e2e.py` | **pass** |
| Navigation and builder integration tests | **pass** |

### What the new browser tests actually measure

Not samples — the whole page each time.

* **Every** visible piece of text on `/main`, against the AAA floor.
* **Every** visible piece of text on `/dashboard`, against the AA floor.
* **Every** control on the page, for a focus ring of at least 3:1.
* **Every** control on the page, for a 44×44 target.
* **Every** control in the page's content, for a visible focus state.
* **Every** tile, for a number, an explanation and a destination.
* **Every** tile's popup, opened and closed, focus checked in and out.
* Every layout width from 320px up, and 200% zoom, for sideways scrolling.
* Reduced motion: nothing missing, numbers correct, popups still work.
* Zero errors in the browser console, on every one of those tests.

---

## 5. Left unsolved

Nothing from the request is unsolved, and nothing found on the way was left unfixed.

Two notes, for honesty rather than as blockers:

1. **`/main` has no market chart.** The rules file said a chart, if one appeared, had to
   come from the charting library already in the repository. The page ended up not
   needing one: the honest summary of "what is happening" is a count and a progress
   ring, and the brand rules ask for information-dense screens to be quieter than
   marketing ones. The charting library is untouched and still available.
2. **`/main` is a parallel page, not a replacement.** `/dashboard` still works exactly as
   before, with its faults fixed. Turning `/main` into the front page is a decision, not
   a code change, and it was not part of this request.
