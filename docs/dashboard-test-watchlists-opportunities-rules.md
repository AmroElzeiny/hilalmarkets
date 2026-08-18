# Rules for `/dashboard/watchlists` and `/dashboard/opportunities`

Written **before** any code, from the two requests. Every line is a pass/fail gate.

One file for both pages, because the two requests are the same request twice with a
different page name in it. Two copies would be two things to keep in step, and the
first time they drifted, one page would quietly stop following a rule the other kept.
Where a rule is about only one of the pages it says so.

This file sits under `dashboard-test-rules.md`, which governs the whole path. Every
rule there still applies here — brand, motion, accessibility, readability, and the
product rules that cannot be broken. Only what is new or sharper is written below.

> **Amended 18 August 2026** by the shell redesign
> (`docs/dashboard-shell-redesign-rules.md`). Two things on these pages changed:
>
> * The thing a person builds is called a **monitor** on both pages, because that is what
>   the side menu calls it. A button called one thing that opens a page called another is
>   worse than either name on its own. `/dashboard/watchlists` is still the address.
> * Neither page draws its own top-right button any more. Each **declares** what belongs
>   in the topbar and the shared bar draws it, so the create action is in the same place,
>   at the same size, and is there even when the page below it is empty — which is
>   exactly when it used to be missing.
>
> Everything else below still holds.

## A. Scope

| # | Rule |
|---|---|
| A1 | Two new pages: `/dashboard/watchlists` and `/dashboard/opportunities`. |
| A2 | Each keeps its **popups** — every dialog and drawer the live page has, redesigned, none dropped. |
| A3 | Each keeps its route to the **full Passport page**, which already exists on this path and is reused, not rebuilt. |
| A4 | The live pages `/dashboard/watchlists` and `/dashboard/opportunities` are **not changed**. The problems found in them are fixed **in the new design**, and each fix is named against the problem it answers. |
| A5 | Same data, same server context builders as the live pages. A design path that computed its own numbers would be a second answer to "how many are active". |
| A6 | Both pages carry the path's shared chrome: the Passport popup variant, the hidden create button, and Hilal. |

## B. Score before designing

| # | Rule |
|---|---|
| B1 | Both live pages are **scored first** — UX, UI, user-friendliness — with the score written down and the reason for it. |
| B2 | Every problem is written as a numbered finding, and every finding is answered by something in the new design. A finding with no answer is an unfinished job. |
| B3 | The score is about the page, not about the people who wrote it. |

## C. What the pages are for

| # | Rule |
|---|---|
| C1 | **Watchlists** answers: what am I watching, is it working, and what do I do next? |
| C2 | **Opportunities** answers: what is closest to happening, why is it not there yet, and what did the platform actually see? |
| C3 | Every number on either page can be traced to something a person can open and read. No score without its evidence. |
| C4 | Nothing on either page implies a recommendation to trade. Readiness is "how much of your own rule is met", never "how good this is". |

## D. Design

| # | Rule |
|---|---|
| D1 | Colours, type, spacing, radius and shadow from the tokens already declared. **No new main colour, no new spacing scale, no third typeface.** |
| D2 | Apple green stays the single focal accent. Selected and focused states are near-black, as everywhere else on this path. |
| D3 | **Full of icons** — every state, every action, every category carries one. Icons come from the existing set; new ones are added to that set in its own style, never drawn inline. |
| D4 | Modern, and **not an AI-looking template**: no hero gradient, no glass panels, no purple, no emoji, no generic three-card feature row. |
| D5 | The existing structure may be thrown away. What must survive is every piece of information a person needs — and every piece must earn its place. |
| D6 | New information may be added only if it is **main** information, and it must come from data the platform already holds. |

## E. Motion and interactivity

| # | Rule |
|---|---|
| E1 | Motion comes from **Motion One** through `hm-motion.js`. No second easing scale, no one-off numbers. |
| E2 | There is motion at **every** interaction, hover included. |
| E3 | Every animation **explains something**: what arrived, what changed, what is loading, where a thing went. Nothing loops for decoration. |
| E4 | Motion never delays the thing it describes. |
| E5 | `prefers-reduced-motion` removes it and the page still works completely. |

## F. Accessibility — WCAG 2.2 AA

| # | Rule |
|---|---|
| F1 | Text contrast at least 4.5:1; borders and icons at least 3:1. |
| F2 | Status is never colour alone: colour **plus** words **plus** an icon. |
| F3 | Everything reachable and operable by keyboard, in a sensible order. |
| F4 | Every dialog traps focus, closes on `Escape`, and returns focus where it came from. |
| F5 | Touch targets at least 44×44. |
| F6 | Live regions announce changes once, politely — never one announcement per row. |
| F7 | Any drag has a non-drag equivalent (SC 2.5.7). |
| F8 | Real roles and real names on every control; a list is a list, a table is a table. |

## G. Readability

| # | Rule |
|---|---|
| G1 | **Never a wall of text.** The single most important rule in both requests. |
| G2 | No internal word reaches a person: no "lifecycle", no "bottleneck", no "provider_data_error", no "near_miss", no version hashes. |
| G3 | Every state has a plain-words name and one short line saying what it means. |
| G4 | Numbers carry their unit and their meaning. "3 of 5 conditions met" beats "60%". |
| G5 | Empty states say what to do next and offer the button that does it. |
| G6 | A beginner can read any card aloud and understand it. |

## H. No bugs, nothing unrealistic

| # | Rule |
|---|---|
| H1 | Nothing invented for the sake of the design: no fake sparkline, no made-up percentage, no placeholder person. |
| H2 | Every number is real or the panel says it has none yet. |
| H3 | The browser console stays clean through a full pass of both pages, including every popup. |
| H4 | No horizontal scroll at 1440, 1024, 760 and 390. |
| H5 | Both pages work with no data at all, and with a lot of data. |

## I. Verification gates

| # | Rule |
|---|---|
| I1 | `ruff check src tests scripts` clean. |
| I2 | `mypy src` clean. |
| I3 | The unit, engine, interpreter, services and integration suites pass. |
| I4 | Both pages render through the real app, signed in, with data and without. |
| I5 | The browser suite passes with a clean console, including every popup on both pages. |
| I6 | Keyboard-only pass on both pages and every dialog. |
| I7 | Reduced-motion pass. |
| I8 | Contrast measured, not assumed. |
| I9 | Every finding from section B has a test or a measured check proving it is answered. |

## J. What the two pages share

Added while building, because the second page proved the first one had already grown
copies. Every one of these was written twice before it was written once.

| # | Rule |
|---|---|
| J1 | **One state vocabulary.** "What is this opportunity doing" is stored in two places under two different sets of words. Every reader resolves it through `product_language.opportunity_state`. A page may never show a coin under one vocabulary beside the same coin under the other. |
| J2 | **One card per coin.** The readiness record and the recorded history are joined on the server. Neither may be dropped: a history with no readiness row still gets a card. |
| J3 | **One filter.** Finding a card — the buckets, the search, the announcement, the "nothing matched" way out — is `hm-card-filter.js`, used by both pages. |
| J4 | **One popup.** Opening, closing, returning the keyboard and the backdrop click are `hm-dialog.js`, used by every popup on the path. |
| J5 | **One number format.** A market number is written by `product_language.number_in_words` wherever it is shown. |
| J6 | **One logo address.** The icon catalogue and its version pin live in `core/asset_logos.py`. No Python file writes it out again, and a test fails if any front-end copy names a different version. |
| J7 | **A popup never writes its own sentences.** Evidence markup is rendered by the server into the card and copied into the popup. A popup that built sentences from numbers would be a second opinion about the same evidence. |
