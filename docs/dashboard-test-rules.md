# Rules for the the redesigned dashboard pages redesign

This file turns the redesign request into a checklist. Every rule below is a pass/fail
gate. Nothing ships until each line can be answered "yes, and here is where".

## A. Scope

| # | Rule |
|---|---|
| A1 | Build a **new path** the redesigned dashboard pages. Do not change `/dashboard`. |
| A2 | The path is built one page at a time. The first pass covered `/dashboard/market`, its popups, the full passport page and the passport report; the canvas, Watchlists, Opportunities and Connections came after it, each under its own rules file. Every rule below applies to all of them. |
| A3 | The old page keeps working exactly as it does today. The new path is a parallel copy. |
| A4 | Fix the real defects found in the old page at their source, so both paths get the fix. |

## B. Brand: never invent

| # | Rule |
|---|---|
| B1 | Use only the colours already defined in `hilalmarkets-brand.css`. No new main colour. |
| B2 | Fonts stay **Geometria** for headings and figures, **Onest** for body and controls. No new family. |
| B3 | Spacing, radius and shadow come from the existing `--hm-*` tokens. No new scale. |
| B4 | Apple green is an accent, not decoration: roughly 70–80% white, 15–20% neutral, 5–10% green, under 2% blue. |
| B5 | Rounded product surfaces. The chamfer is a rare brand accent, at most one per section. |
| B6 | Sentence case headings. Never Title Case, never ALL CAPS. |
| B7 | Name in prose is **Hilal Markets**. Technical usage is **Shariah**. |
| B8 | No forbidden claims: no "100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you". |
| B9 | No AI brains, robots, glowing spheres, crypto clichés, religious decoration. |

## C. Motion and interactivity

| # | Rule |
|---|---|
| C1 | Animation must **explain something**: a state change, a relationship, a progress step. |
| C2 | Every interactive element has a visible hover, focus and active state. |
| C3 | No constant looping movement, no flashing, no glowing, no trading-terminal effects. |
| C4 | `prefers-reduced-motion` removes all non-essential motion, and the page stays fully usable. |
| C5 | Motion must never make monitoring look like automatic trading. |
| C6 | Animation must never delay a user action. Interaction stays responsive during motion. |

## D. Accessibility (WCAG 2.2 AA)

| # | Rule |
|---|---|
| D1 | Body text contrast at least 4.5:1; large text and UI borders at least 3:1. |
| D2 | Status is never colour alone. Always colour **plus** text, and usually an icon. |
| D3 | Full keyboard path: every control reachable and operable, in a sensible order. |
| D4 | Visible focus ring on every focusable element. |
| D5 | Dialogs trap focus, close on `Escape`, and return focus to the control that opened them. |
| D6 | Touch targets at least 44×44 px. |
| D7 | Live price updates do not shout at a screen reader. Announce summaries, not every tick. |
| D8 | Every icon is either labelled or marked decorative. Every image has correct `alt`. |
| D9 | Tables use real table semantics with headers tied to cells. |

## E. Readability for a beginner

| # | Rule |
|---|---|
| E1 | **Never put a wall of text on screen.** Long explanations live behind a disclosure. |
| E2 | Plain language. No jargon, no internal field names, no Latin abbreviations. |
| E3 | One idea per line. Short sentences. |
| E4 | Any technical term is said once and then explained in ordinary words. |
| E5 | The first screen must answer "what is this and what do I do next" without scrolling. |

## F. Product rules that cannot be broken

| # | Rule |
|---|---|
| F1 | Shariah status comes only from stored, reviewed evidence. Never from a model, chat or guess. |
| F2 | Live price never changes, implies or softens a published status. |
| F3 | Missing data is shown as missing. Never substitute, never estimate, never clamp. |
| F4 | No buy/sell advice, no leverage, no guaranteed returns, no automatic trading. |
| F5 | Evidence, dates, sources and limits stay visible, not hidden to look cleaner. |

## G. Explicit instructions from the request

| # | Rule |
|---|---|
| G1 | Remove **"New watchlist"** from the market page, the popups, the passport page and the report. |
| G2 | Coin logos and exchange logos are always loaded and always visible. |
| G3 | Fix why Binance coins are not active. |
| G4 | Icons throughout, but each icon must carry meaning. |
| G5 | Use a real animation engine, not one-off inline hacks. |
| G6 | Structure and content may change. Any new information must be genuinely useful, not filler. |
| G7 | Modern design that does not read as a generic AI template. |
| G8 | No bugs, nothing unrealistic, nothing fabricated in the interface. |

## H. Verification gates

| # | Rule |
|---|---|
| H1 | `ruff check src tests scripts` clean. |
| H2 | `mypy src` clean. |
| H3 | The unit, engine, interpreter and services suites pass. |
| H4 | Every new page actually renders through the real app, not just in theory. |
| H5 | Keyboard-only pass over the market page, both popups, the passport and the report. |
| H6 | Reduced-motion pass. |
| H7 | Contrast checked on every text and status colour pair that ships. |
| H8 | Every defect found on the way is fixed and listed, or named as blocked. |
