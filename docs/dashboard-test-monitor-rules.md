# Rules for the `/dashboard/monitor` canvas redesign

This file turns the request into a checklist, written **before** any code was changed.
Every line is a pass/fail gate. It is the companion to `dashboard-test-rules.md`, which
already governs the whole the redesigned dashboard pages path; every rule there still applies here.

## A. Scope of this pass

| # | Rule |
|---|---|
| A1 | The visual canvas becomes **its own page** at `/dashboard/monitor`. It is not a panel inside another page. |
| A2 | The page appears in the side menu **directly after "Watchlists"**. |
| A3 | The chat assistant does **not** come with it. No chatbot popup, no chat panel. |
| A4 | The **"Hilal Markets Assistant" box is removed** — it does not exist anywhere on the new page. |
| A5 | `/dashboard` and its builder keep working exactly as today. This is a parallel path, as `dashboard-test-rules.md` A1 and A3 already require. |
| A6 | Nothing of the old structure is kept out of habit. Cards, lines, boxes, popups and the canvas itself are redrawn. |

## B. Brand: never invent

Inherited from `dashboard-test-rules.md` section B, repeated here because this page is
where the temptation to invent is highest.

| # | Rule |
|---|---|
| B1 | Only colours already declared in `hilalmarkets-brand.css`. **No new main colour.** |
| B2 | **Geometria** for headings and figures, **Onest** for body and controls. No third family. |
| B3 | Spacing, radius and shadow come from existing tokens. **No new spacing scale.** |
| B4 | Apple green stays an accent: roughly 70–80% white, 15–20% neutral, 5–10% green, under 2% blue. |
| B5 | Rounded surfaces. The chamfer appears **at most once** on the page. |
| B6 | Sentence case headings. Never Title Case, never ALL CAPS. |
| B7 | "Hilal Markets" in prose; "Shariah" for the formal mechanism. |
| B8 | No forbidden claim from `brand guide.md` section 17. |
| B9 | No AI brains, robots, glowing spheres, crypto or religious clichés. |

## C. Motion and interactivity

| # | Rule |
|---|---|
| C1 | Motion comes from a **real animation library** — Motion One, already vendored — through `hm-motion.js`. No one-off inline hacks, no second easing scale. |
| C2 | There is motion at **every** interaction: hover, focus, select, create, move, delete, connect, reconnect, zoom, open, close, mode change. |
| C3 | Every animation **explains something**. No loop that runs for decoration, no flashing, no glow, no trading-terminal effects. |
| C4 | Motion never blocks or delays the action it describes. A card is usable the moment it appears. |
| C5 | `prefers-reduced-motion` removes non-essential motion and the page stays completely usable. |
| C6 | Motion must never make monitoring look like automatic trading. |

## D. Accessibility — WCAG 2.2 AA

| # | Rule |
|---|---|
| D1 | Text contrast at least 4.5:1; large text, icons and control borders at least 3:1. |
| D2 | Status is never colour alone: colour **plus** text, and an icon. |
| D3 | Every action is reachable and operable by keyboard, in a sensible order. |
| D4 | Visible focus ring on every focusable element, including cards on the canvas. |
| D5 | Dialogs trap focus, close on `Escape`, and return focus to whatever opened them. |
| D6 | Touch targets at least 44×44 px. |
| D7 | **SC 2.5.7 Dragging Movements**: every drag has a single-pointer, non-drag equivalent. Reconnecting, moving and deleting must all work without dragging. |
| D8 | **SC 2.1.4 Character Key Shortcuts**: single-key shortcuts are off while typing, and each has a modifier alternative. |
| D9 | The rule tree carries real structure for a screen reader — levels, position, selection — not a pile of divs. |
| D10 | Changes are announced once, in plain words, through a polite live region. Never one message per pixel of a drag. |

## E. Readability for a beginner

| # | Rule |
|---|---|
| E1 | **Never a wall of text.** Every card is a title plus at most one sentence. |
| E2 | Every rule reads as a plain sentence — "RSI is at least 55 on 15m candles" — never as a field name. |
| E3 | Plain language. No jargon, no internal keys, no Latin abbreviations. |
| E4 | Longer explanation lives behind a disclosure the person chooses to open. |
| E5 | The first screen answers "what is this, and what do I do next" with no scrolling. |
| E6 | The whole monitor is always readable as **one sentence**, live, while it is built. |

## F. Product rules that cannot be broken

| # | Rule |
|---|---|
| F1 | Shariah status comes only from stored reviewed evidence. Never from chat, a model or a guess. |
| F2 | Every mechanic, parameter, option, limit and default shown on the canvas comes from the **server's own contract**. The browser invents nothing. |
| F3 | A mechanic the platform cannot run right now is shown with its reason. It is never hidden and never silently swapped for a near match. |
| F4 | Fail closed: an unfinished rule is reported as unfinished. Never defaulted, never clamped, never inverted. |
| F5 | No buy/sell advice, no leverage, no guaranteed returns, no automatic trading. |
| F6 | The page never claims to have started monitoring. Only the application's own approval route does that. |

## G. Explicit instructions from the request

| # | Rule |
|---|---|
| G1 | The canvas has **two modes**: fits inside the page, and full screen. Both are labelled, both are reachable by keyboard. |
| G2 | Creating, moving, removing, connecting and reconnecting cards are all animated, and each animation makes the action easier to follow. |
| G3 | The page is **full of icons**, and every icon carries meaning. |
| G4 | The condition list, the created conditions and the flow between them are held to the same standard as the page. |
| G5 | Structure may be built from scratch. Any information added must be genuinely useful — no filler. |
| G6 | Modern design that does not read as a generic AI template. |
| G7 | No bugs. Nothing unrealistic. Nothing fabricated in the interface. |

## H. Verification gates

| # | Rule |
|---|---|
| H1 | `ruff check src tests scripts` clean. |
| H2 | `mypy src` clean. |
| H3 | The unit, engine, interpreter and services suites pass. |
| H4 | The page renders through the real app, signed in, with real catalogue data. |
| H5 | The browser console is clean — no error, no warning — on load and after a full build-and-delete pass. |
| H6 | Keyboard-only pass: add, edit, reconnect, delete, undo, both canvas modes, every dialog. |
| H7 | Reduced-motion pass: everything still reachable and legible. |
| H8 | Contrast checked on every text and status colour pair that ships. |
| H9 | Every defect found on the way is fixed and listed, or named as blocked. |
