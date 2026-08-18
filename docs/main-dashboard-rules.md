# Rules for the `/main` dashboard redesign

This file turns the redesign request into a checklist. Every rule below is a pass/fail
gate. Nothing ships until each line can be answered "yes, and here is where".

It is written before any code, on purpose. The request asked for the rules first.

> **Amended 18 August 2026.** Two rules below have since been overtaken by the shell
> redesign, whose own rules are `docs/dashboard-shell-redesign-rules.md`:
>
> * **A3** said `/main` was a parallel page "until it is asked to become one". It has
>   been. Today is the front page: the Home page is deleted and `/dashboard` sends a
>   browser here.
> * **A6** removed a topbar "New Watchlist" button that no longer exists anywhere. The
>   topbar now draws whatever action the page it is above declared, and Today declares
>   none.
>
> Everything else on this page still holds.

## A. Scope

| # | Rule |
|---|---|
| A1 | Build a **new page** at the path `/main`. It is a dashboard page: it lives inside the signed-in dashboard shell, on the same data as `/dashboard`. |
| A2 | This pass designs **only** `/main` — the page itself, its popups, its icons, its animation, its contrast and its graphics. The rest of the dashboard is a later pass. |
| A3 | `/dashboard` keeps working. `/main` is a parallel page, not a replacement, until it is asked to become one. |
| A4 | **Audit `/dashboard` first** and score it for UX, UI and user-friendliness. The score has to be measured, not guessed. |
| A5 | **Fix every real defect the audit finds, at its source**, so `/dashboard` gets the fix too — not only `/main`. |
| A6 | Remove the **"New Watchlist"** button from this page. |
| A7 | Anything else found broken while working is fixed in the same pass and listed in the report. |

## B. Brand: never invent

| # | Rule |
|---|---|
| B1 | Use only colours already defined in `hilalmarkets-brand.css`. **No new main colour.** |
| B2 | Fonts stay **Geometria** for headings and figures, **Onest** for body and controls. **No new family.** |
| B3 | Spacing, radius, shadow and easing come from the existing `--hm-*` tokens. **No new spacing scale.** |
| B4 | Apple green is one accent, not decoration: roughly 70–80% white, 15–20% neutral, 5–10% green, under 2% blue. |
| B5 | Rounded product surfaces. The chamfer is a rare brand accent — at most one clearly visible one per composition. |
| B6 | Sentence case headings. Never Title Case, never ALL CAPS. |
| B7 | Name in prose is **Hilal Markets**. Technical usage is **Shariah**. |
| B8 | No forbidden claims: no "100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you". |
| B9 | No AI brains, robots, glowing spheres, crypto clichés, religious decoration, fake partner logos. |
| B10 | Structure, colour, copy and UX come from **what is already shipped** on the landing page and the dashboard. Match it before inventing. |

## C. Contrast: above the standard, not at it

| # | Rule |
|---|---|
| C1 | The request asks for contrast **much higher than standard**. The floor on this page is therefore **WCAG AAA**, not AA. |
| C2 | Normal body text: at least **7:1** against its own background. (AA asks 4.5:1.) |
| C3 | Large text (≥ 24px, or ≥ 18.66px bold): at least **4.5:1**. (AA asks 3:1.) |
| C4 | Interface borders, icon strokes and control outlines: at least **3:1**, and the ones that carry meaning at least 4.5:1. |
| C5 | Every pair is **measured** by a test that reads the real rendered colours, not eyeballed. |
| C6 | Apple green never carries small text on white. A status mark painted in the raw accent is invisible — measure, never assume. |
| C7 | The focus ring is visible against **every** surface it can land on, including the accent-filled ones. |

## D. Motion and interactivity

| # | Rule |
|---|---|
| D1 | Animation comes from a **library already vendored in this repo** (Motion One at `static/vendor/motion.min.js`, through `hm-motion.js`). No new CDN, no runtime third-party request. |
| D2 | Every animation must **explain something**: a state change, a relationship, an arrival, a progress step. |
| D3 | **Every interactive element** has a visible hover, focus, active and disabled state. This is the request's "interactivity everywhere". |
| D4 | All of it must **serve ease of use**. Motion that only decorates is deleted, however good it looks. |
| D5 | No constant looping, no flashing, no glowing, no fast trading-terminal effects. |
| D6 | `prefers-reduced-motion` removes all non-essential motion and the page stays completely usable. |
| D7 | Motion never delays a user action. A click is answered immediately, whatever is animating. |
| D8 | Motion never makes monitoring look like automatic trade execution. |
| D9 | A number that is a **count of things** may animate up. A **price** never does — a price travelling through values it never had is invented market data. |

## E. Accessibility (WCAG 2.2 AA as the floor, AAA on contrast)

| # | Rule |
|---|---|
| E1 | Full keyboard path: every control reachable and operable, in a sensible order. |
| E2 | Visible focus ring on every focusable element. |
| E3 | Popups trap focus, close on `Escape`, and return focus to the control that opened them. |
| E4 | Touch targets at least 44×44 px. |
| E5 | Status is never colour alone. Always colour **plus** words, and usually an icon. |
| E6 | Every icon is either labelled or marked decorative. Every image has correct `alt`. |
| E7 | Live regions announce summaries, not every tick. |
| E8 | Real semantics: headings in order, lists as lists, tables with headers tied to cells, one `h1`. |
| E9 | The page works at 320px wide and at 200% zoom with no horizontal scrolling. |

## F. Readability for a beginner

| # | Rule |
|---|---|
| F1 | **Never a wall of text.** This is an explicit instruction. Long explanations live behind a disclosure. |
| F2 | Plain language. No jargon, no internal field names, no Latin abbreviations. |
| F3 | One idea per line. Short sentences. |
| F4 | Every number on screen says what it counts, in words a beginner reads. |
| F5 | A screen that shows nothing yet explains **why** and gives the one next action. |

## G. Icons, graphics and assets

| # | Rule |
|---|---|
| G1 | **Import ready items, never draw new ones.** This is an explicit instruction. |
| G2 | Icons come from the existing vendored set (`hilalmarkets-icons.js`, Lucide geometry). If an icon is missing, take the nearest **real Lucide icon** as it ships — do not invent a glyph. |
| G3 | Coin logos come from the one owner (`asset-logos.js` and the `coin_logo` macro). Never a second reader. |
| G4 | Charts come from the vendored TradingView Lightweight Charts build. No hand-drawn chart paths. |
| G5 | The logo is the official asset, unaltered. |
| G6 | Graphics explain a product flow or state. Decoration for its own sake is removed. |

## H. Content and truth

| # | Rule |
|---|---|
| H1 | Only real data from the same context builders `/dashboard` uses. **No invented metric, no placeholder number, no fake trend.** |
| H2 | Shariah status is only ever what the platform's own review assigned. Never inferred, never implied by colour. |
| H3 | New information may be added only if it is **main** — something a person acts on. No filler tiles. |
| H4 | Anything the page cannot honestly say, it does not say. "Not looked yet" is a real answer and must be available. |
| H5 | Never imply Hilal Markets buys, sells, advises, or guarantees anything. |

## I. Quality bar

| # | Rule |
|---|---|
| I1 | Target **at least 9.9/10** for UX, UI and user-friendliness, scored against the same rubric used on the old page. |
| I2 | **Stay far from AI-looking templates**: no generic three-column feature grid, no gradient hero with a centred sentence, no purple, no emoji as icons, no stock "dashboard" layout. |
| I3 | Modern, but committed to the shipped design system — new *composition*, not a new visual language. |
| I4 | Keep every element from the old page that is genuinely useful. Remove the rest. Both decisions get a reason in the report. |
| I5 | **No bugs and nothing unrealistic.** Verified by a browser test that drives the real page. |

## J. Verification — nothing ships unverified

| # | Rule |
|---|---|
| J1 | `ruff check src tests scripts` clean. |
| J2 | `mypy src` clean. |
| J3 | The offline suites pass: `tests/unit tests/engine tests/interpreter tests/services`. |
| J4 | A browser test drives `/main` end to end: it opens, every control works, every popup opens and closes, and **zero console errors**. |
| J5 | A contrast test measures the real rendered colours against the C-series floors above. |
| J6 | A test asserts no horizontal overflow at 320px and at 200% zoom. |
| J7 | A test asserts the reduced-motion path still shows every element. |
| J8 | A test asserts the "New Watchlist" button is **absent** from `/main`. |
| J9 | Every defect the audit found has a **named test** that fails without the fix — across the whole family, not just the reported instance. |
| J10 | The report states, for every claim, what was measured and how. "Verified", "unfixed" and "unverified" are kept apart. |

## K. How it turned out

Filled in after the work, so the checklist and the result sit together. The full write-up
is [`MAIN_DASHBOARD_REDESIGN_REPORT.md`](MAIN_DASHBOARD_REDESIGN_REPORT.md).

| Rule | Result |
|---|---|
| A1–A3 | `/main` exists as a parallel page over the same read models. `/dashboard` still works. |
| A4 | `/dashboard` scored **≈4.5/10**, measured in a browser. |
| A5, A7 | Sixteen faults fixed at their cause. Ten found by the audit, six found while building. |
| A6 | The "New Watchlist" button is gone from `/main`, and a test asserts it. |
| B1 | A test holds `hm-main.css` to the approved palette: **no new colour**. |
| C1–C7 | AAA measured on every visible word. The focus ring went from 1.11:1 to a two-part ring that clears 3:1 on every surface. |
| D1 | Motion One, already vendored. No new library, no outside request. |
| G2 | One icon was missing; **Lucide `gauge` imported as it ships**. Nothing hand-drawn. |
| G4 | **No chart was needed.** The honest summary is a count and a progress ring. The charting library is untouched. This is the one rule that turned out not to apply. |
| I1 | Judged against the same rubric: the faults that cost `/dashboard` its score are all measured and all closed on `/main`. |
| J1–J10 | All green. Test counts are in the report. |
