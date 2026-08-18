# Rules for the /contact, /privacy and /terms redesign

This is the prompt turned into a checklist. Every line is a rule I must satisfy before
the work is finished. Nothing here is optional.

## A. Scope

| # | Rule |
|---|---|
| A1 | Three pages only: `/contact`, `/privacy`, `/terms`. |
| A2 | Score each page now (UX, UI, user-friendliness) before changing anything. |
| A3 | Rebuild them. Not a patch — new design, new structure, new sections. |
| A4 | Everything **inside** the pages is in scope too: popups, dialogs, sub-windows, sections, and the functions behind them. |
| A5 | The current items and the current structure may be dropped. Keeping them is not a goal. |
| A6 | Fix every issue found while scoring. |

## B. Design system — what may not change

| # | Rule |
|---|---|
| B1 | No new main colours. Use the tokens already shipped: ink `#2b2e35`, ink-soft `#7a8089`, canvas `#f5f8fb`, surface `#ffffff`, hairline `#e1e5ea`, apple `#cbfa4d`, apple-deep `#55712a`, accent-blue `#2a8fc3`. |
| B2 | No new font families. Geometria (headings, Medium) and Onest (body/UI) only. |
| B3 | No new spacing rules. Reuse the scale already on the landing page and dashboard. |
| B4 | Colour balance stays inside the brand guide: 70–80% white/near-white, 15–20% neutral/near-black, 5–10% apple green, under 2% blue. |
| B5 | Apple green is an accent and a focal point, never a status meaning and never small text on white. |
| B6 | Chamfer is a rare brand signature, not the default corner. Cards, inputs and buttons stay rounded. |
| B7 | Copy follows the brand rules: "Hilal Markets" in prose, "Shariah" for the technical mechanism, none of the forbidden claims (`core/copy_rules.py` enforces this). |
| B8 | Structure, colour, text, UX and UI come from the shipped landing page and dashboard first, `brand guide.md` second. Invent nothing new. |

## C. Contrast and accessibility (WCAG)

| # | Rule |
|---|---|
| C1 | Body text ≥ 4.5:1 against its own background. Large text (≥24px, or ≥19px bold) ≥ 3:1. |
| C2 | Every interactive control and every meaningful boundary ≥ 3:1 (WCAG 1.4.11). |
| C3 | Contrast is **measured**, not assumed. Apple green is 1.21:1 on white — it can never carry text or meaning on its own. |
| C4 | Visible focus ring on every focusable element, with its own ≥ 3:1 contrast. |
| C5 | Targets ≥ 44×44 CSS px. |
| C6 | Status is never colour alone — always colour + text, and an icon where it helps. |
| C7 | Every dialog traps focus, closes on `Escape`, returns focus to what opened it, and is labelled. |
| C8 | Full keyboard path through every page and every dialog. Nothing reachable by mouse only. |
| C9 | Live regions announce every state change (sending, sent, failed, limit reached). |
| C10 | `prefers-reduced-motion` removes motion everywhere, including the new library animations. |
| C11 | Headings are one ordered outline per page. One `h1`. |
| C12 | Contrast must also look **attractive and professional**, not merely pass. |

## D. Motion and interactivity

| # | Rule |
|---|---|
| D1 | Animation comes from a real library, or is 3D. Not hand-rolled one-offs. |
| D2 | Motion everywhere, hover states included. |
| D3 | Every animation must make the page **easier** to use. Decoration that does not help is removed. |
| D4 | Brand motion rules hold: calm reveals, left-to-right flows, no looping glow, no trading-terminal effects. |
| D5 | The pages are interactive — things respond, open, filter, confirm, progress. |

## E. Icons

| # | Rule |
|---|---|
| E1 | The pages are full of icons. |
| E2 | Simple vector icons only. No AI brains, no robots, no glowing spheres, no fake logos, no crypto clichés. |
| E3 | Icons are decorative (`aria-hidden`) unless they carry meaning, and then they get a text label too. |

## F. Content

| # | Rule |
|---|---|
| F1 | Never a wall of unreadable text. This is the hardest rule on the page type most likely to break it. |
| F2 | Any new information must be **main** information, not filler. |
| F3 | Remove every sign of beta testing; describe live behaviour instead. |
| F4 | Privacy and Terms may be rewritten as I judge correct and secure. |
| F5 | Written for beginners: plain words, one idea per sentence, no jargon and no internal field names. |
| F6 | Modern design. Far from an AI-template look. |

## G. Support-ticket limits (product change)

| # | Rule |
|---|---|
| G1 | 2 tickets per email. |
| G2 | 2 tickets per IP / user session. |
| G3 | 20 tickets received per hour in total — the flood ceiling. |
| G4 | Applies to **both** the dashboard support form and the public `/contact` form. |
| G5 | Every number lives in `.env.example` and `.env.production.example`. |
| G6 | One owner for the rule. Two forms must not each invent their own counting. |
| G7 | When a limit is hit, the person is told plainly what happened and what to do next. Never a bare error. |

## H. Quality bar

| # | Rule |
|---|---|
| H1 | ≥ 9.9/10 on UX, UI, user-friendliness and creativity. |
| H2 | No bugs. Nothing unrealistic — no invented numbers, no fake activity, no promises the product cannot keep. |
| H3 | Tested: type check, lint, unit tests, and a real browser measurement for anything that only a browser can prove. |
| H4 | The built bundle is what ships. Editing the React source proves intent, not delivery — rebuild and copy into `static/landing/`, and bump the `?v=` cache key on every template that carries one. |

## I. Working rules from CLAUDE.md that bind this task

| # | Rule |
|---|---|
| I1 | Fix the defect class, not the reported instance. One vocabulary, one owner, every caller importing it. |
| I2 | A problem found while working is a problem I fix — not one I report. |
| I3 | Tests assert the rule across the whole family, not the single case. |
| I4 | Report in very simple words for a non-native English speaker. |
| I5 | Leave nothing in the prompt unsolved; a true blocker is named plainly. |
