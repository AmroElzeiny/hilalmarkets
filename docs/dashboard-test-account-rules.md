# Rules for the the redesigned dashboard pages account pages

The three requests — Subscription, Settings, Support — are one request written three
times. This file turns them into one checklist. Every line is a pass/fail gate.
Nothing ships until each line can be answered "yes, and here is where".

`docsthe redesigned dashboard pages-rules.md` still applies in full. The rules below are the ones
these three pages add, or make stricter.

## A. Scope

| # | Rule |
|---|---|
| A1 | Three new pages: `/dashboard/subscription`, `/dashboard/settings`, `/dashboard/support`. |
| A2 | Every popup each page opens is part of that page's work, not a follow-up. |
| A3 | The **whole** paid flow belongs to Subscription: the plan choice, the billing form, the payment method, the order summary, and what happens after. |
| A4 | `/dashboard/subscription`, `/dashboard/settings` and `/dashboard/support` keep working, unchanged in behaviour. |
| A5 | A defect found in the live page is fixed **at its source**, so both paths get the fix. |
| A6 | Structure and content may change completely. Keeping the old layout is not a goal. |

## B. Brand: never invent

| # | Rule |
|---|---|
| B1 | Only colours already in `hilalmarkets-brand.css`. No new main colour, no new hex. |
| B2 | **Geometria** for headings and figures, **Onest** for body and controls. No third family. |
| B3 | Spacing, radius and shadow come from the `--t-*` / `--hm-*` tokens. No new scale. |
| B4 | Roughly 70–80% white, 15–20% neutral, 5–10% apple green, under 2% blue. |
| B5 | Rounded surfaces. At most one visible chamfer per page. |
| B6 | Sentence case headings. Never Title Case, never ALL CAPS. |
| B7 | **Hilal Markets** in prose. **Shariah** for the formal mechanism. |
| B8 | No "100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you". |
| B9 | No AI brains, robots, glowing spheres, crypto or religious clichés. |

## C. Motion and interactivity

| # | Rule |
|---|---|
| C1 | Motion comes from `hm-motion.js` (Motion One). No one-off inline animation numbers. |
| C2 | Every animation explains something: a state change, a total changing, a step advancing. |
| C3 | Every interactive element has a visible hover, focus **and** active state. |
| C4 | No looping movement, no flashing, no glowing, no trading-terminal effects. |
| C5 | `prefers-reduced-motion` removes all non-essential motion and the page stays fully usable. |
| C6 | Motion never delays an action. A control responds before its animation finishes. |
| C7 | A price never animates through values it never had. Counts may count; money may not. |

## D. Accessibility (WCAG 2.2 AA)

| # | Rule |
|---|---|
| D1 | Body text at least 4.5:1. Large text, icons and control borders at least 3:1. |
| D2 | State is never colour alone — colour **plus** a word, usually plus an icon. |
| D3 | Every control reachable and operable by keyboard, in a sensible order. |
| D4 | Visible focus ring on every focusable element. |
| D5 | Popups trap focus, close on `Escape`, and return focus to what opened them. |
| D6 | Touch targets at least 44×44 px. |
| D7 | Every save, failure and result is announced once through a live region. |
| D8 | Every icon is labelled or marked decorative. Every image has correct `alt`. |
| D9 | Tables use real table semantics, headers tied to cells. |
| D10 | A form field that can be wrong says what is wrong, in words, beside itself. |

## E. Readability for a beginner

| # | Rule |
|---|---|
| E1 | **Never a wall of text.** Long explanations live behind a disclosure. |
| E2 | Plain language. No jargon, no internal field names, no Latin abbreviations. |
| E3 | One idea per line. Short sentences. |
| E4 | A technical term is said once and explained in ordinary words right after. |
| E5 | The first screen answers "what is this and what do I do next" without scrolling. |
| E6 | Never make somebody read a number to learn a state. Say the state in words first. |

## F. Product rules that cannot be broken

| # | Rule |
|---|---|
| F1 | Shariah status comes only from stored, reviewed evidence. Never from a model or a guess. |
| F2 | No buy/sell advice, no leverage, no guaranteed returns, no automatic trading. |
| F3 | Missing data is shown as missing. Never substituted, never estimated, never clamped. |
| F4 | A price, a plan or a date is never invented for layout. If it is not known, it is not drawn. |
| F5 | Hilal Markets never receives card or wallet details. The page must say so where it matters. |

## G. Subscription

| # | Rule |
|---|---|
| G1 | The first thing on the page is **what you have now** and **what it lets you do** — not a price grid. |
| G2 | A plan that cannot be bought says so on its own card, with no price beside it. |
| G3 | A billing interval nobody can buy is never selectable, and never advertises a saving. |
| G4 | Every figure — price, saving, countdown — is derived from `core/plans.py`. None is typed into a template. |
| G5 | The checkout popup is one flow with visible steps. A person always knows what they will be charged and when. |
| G6 | The order summary shows the exact amount and the exact interval before the button that leaves the site. |
| G7 | Refund and renewal terms are visible before payment, not after. |
| G8 | Payment history says what happened in words, not a status code. An unfinished payment offers the way to finish it. |
| G9 | Nothing implies a subscription buys a trading outcome. |

## H. Settings

| # | Rule |
|---|---|
| H1 | **Every control saves, for real.** A setting with no backend reader does not ship. |
| H2 | One owner writes the preference record. The page form and the API must not each build it. |
| H3 | Each setting says, in one line, what changes for the person when they change it. |
| H4 | A setting with a consequence says the consequence *before* it is applied. |
| H5 | Saving is confirmed visibly and announced. A failure says nothing was saved. |
| H6 | Nothing is silently switched off by saving something unrelated. |
| H7 | A new setting may be added only when the backend already reads it, or is made to read it. |
| H8 | Values that cannot be chosen (a locked channel, an unavailable provider) say why. |

## I. Support

| # | Rule |
|---|---|
| I1 | The page answers "can I fix this myself?" before it asks the person to write a message. |
| I2 | The form asks the fewest questions that make a request answerable. |
| I3 | What the person sends and what happens next are both stated before they send. |
| I4 | A sent request appears in their own list immediately, with a real state. |
| I5 | Uploads state their limits before the person hits them, and each file can be removed. |
| I6 | Never ask for a password, a key, or a wallet secret. Say so on the page. |
| I7 | A request the person can read back is a request they can trust. Their own words are shown to them. |

## J. Verification gates

| # | Rule |
|---|---|
| J1 | `ruff check src tests scripts` clean. |
| J2 | `mypy src` clean. |
| J3 | The unit, engine, interpreter and services suites pass. |
| J4 | Each new page renders through the real app, signed in, with a real database. |
| J5 | Each rule above that can be a test **is** a test, parametrised across the family. |
| J6 | Keyboard-only pass over each page and every popup. |
| J7 | Reduced-motion pass. |
| J8 | Contrast checked on every text and status colour pair that ships. |
| J9 | Every defect found on the way is fixed and listed, or named as blocked. |
