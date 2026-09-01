# Rules for the /signup, /signin, /signin/code and /reset-password redesign

The prompt, turned into a checklist. Every line is a rule I must satisfy before the work
is finished. Nothing here is optional. Written **before** any code was changed.

## A. Scope

| # | Rule |
|---|---|
| A1 | Four pages named: `/signup`, `/signin`, `/signin/code`, `/reset-password`. |
| A2 | The pages those four hand off to are part of the same journey and are therefore in scope too: `/signup/password` (step 2 of sign-up) and `/signup/verify` (step 3), plus the "enter your code" state of `/signin/code` and `/reset-password`. A person cannot finish sign-up on `/signup` alone. |
| A3 | Score each page now — UX, UI, user-friendliness — **before** changing anything. |
| A4 | Rebuild them. Not a patch: new structure, new sections, new interactions. |
| A5 | Everything inside the pages is in scope: banners, dialogs, error states, the cookie window that renders on them, and the server code behind each. |
| A6 | The current items and the current structure may be dropped. Keeping them is not a goal. |
| A7 | Fix every issue found while scoring, and every issue found while working. |

## B. Design system — what may not change

| # | Rule |
|---|---|
| B1 | No new main colours. Only tokens already shipped in `hilalmarkets-brand.css`: ink `#2b2e35`, ink-strong `#202329`, copy `#50555e`, muted `#63696f`, canvas `#f5f8fb`, surface `#ffffff`, hairline `#e1e5ea`, apple `#cbfa4d`, apple-soft `#f1fadf`, apple-deep `#55712a`, success `#46551b`, danger `#8d3029`, blue `#2a8fc3`. |
| B2 | No new font families. Geometria (headings, Medium 500) and Onest (body and interface) only. |
| B3 | No new spacing rules. Reuse the radius and spacing scale already shipped (`--hm-radius-sm/md/lg/xl`, the 4px-based steps used by the dashboard and the landing page). |
| B4 | Colour balance stays inside `brand guide.md` section 9: 70–80% white/near-white, 15–20% neutral/near-black, 5–10% apple green, under 2% blue. |
| B5 | Apple green is an accent and a focal point. It never carries a status meaning on its own and never sits as small text on white (measured 1.21:1). |
| B6 | Chamfer stays a rare brand signature. Cards, inputs and buttons stay rounded. |
| B7 | Copy follows the brand rules: "Hilal Markets" in prose, "Shariah" for the technical mechanism, none of the forbidden claims. `core/copy_rules.py` enforces it. |
| B8 | Headings are **sentence case**, never Title Case and never ALL CAPS (`brand guide.md` section 11). This includes the browser tab title and every button label. |
| B9 | Structure, colour, text and interaction come from the shipped dashboard and landing page first, `brand guide.md` second. Invent nothing new. |

## C. Accessibility (WCAG 2.2 AA)

| # | Rule |
|---|---|
| C1 | Body text ≥ 4.5:1 against its own background. Large text (≥24px, or ≥18.66px bold) ≥ 3:1. |
| C2 | Every interactive control boundary and every meaningful graphic ≥ 3:1 (1.4.11). |
| C3 | Contrast is **measured with a calculator**, never judged by eye. |
| C4 | Visible focus indicator on every focusable element, using the product's one shared ring. |
| C5 | Interactive targets ≥ 44×44 CSS px (2.5.8). |
| C6 | Status is never colour alone — colour **and** text **and**, where it helps, an icon. |
| C7 | One `h1` per page. One ordered heading outline. |
| C8 | Every input has a real `<label>`, a name, and an `autocomplete` value the browser understands. |
| C9 | Errors: `aria-invalid` on the field, the message joined by `aria-describedby`, focus moved to the first field at fault, and a `role="alert"` summary. (3.3.1, 3.3.3) |
| C10 | Live regions announce every state change: code sent, seconds left, digits entered, sending, failed. |
| C11 | Full keyboard path through every page and every state. Nothing mouse-only. |
| C12 | `prefers-reduced-motion` removes motion everywhere, including anything the animation library starts. |
| C13 | Nothing may scroll sideways on a 320px-wide screen. |
| C14 | Contrast must also look **attractive and professional**, not merely pass. |

## D. Motion and interactivity

| # | Rule |
|---|---|
| D1 | Animation comes from a real library. Motion 11, already vendored at `static/vendor/motion.min.js` and already owned by `hm-motion.js` — imported, never copied. |
| D2 | 3D is **not** used: `rotateX`, `rotateY` and `transformPerspective` are forbidden in this repository by `test_no_animation_in_this_product_is_three_dimensional`, after a 3D turn shipped a visible bug. The prompt allows "a library **or** 3D"; the library is the half this codebase permits. |
| D3 | Motion is everywhere, hover and focus states included. |
| D4 | Every animation must make the page **easier** to use. Anything that only decorates is removed. |
| D5 | Brand motion rules hold (`brand guide.md` section 15): calm reveals, connectors that explain a process, no looping glow, no flashing, no trading-terminal effects. |
| D6 | The pages respond: things open, check themselves, count down, confirm, and progress. |
| D7 | No component writes its own duration, easing or reduced-motion check. Those belong to `hm-motion.js`. |

## E. Icons

| # | Rule |
|---|---|
| E1 | The pages are full of icons. |
| E2 | They come from the product's own vendored set (`hilalmarkets-icons.js`). No second icon set. |
| E3 | Simple vector icons only. No AI brains, no robots, no glowing spheres, no crypto clichés, no fake logos (`brand guide.md` section 13). |
| E4 | Icons are `aria-hidden` unless they carry meaning, and a meaningful icon always has a text label beside it. |

## F. Content

| # | Rule |
|---|---|
| F1 | **Never a wall of unreadable text.** Short lines, one idea each. |
| F2 | Any new information must be **main** information a person needs at that moment, not filler. |
| F3 | Written for beginners: plain words, no jargon, no internal field names, no error codes shown raw. |
| F4 | Nothing unrealistic: no invented numbers, no fake activity, no promise the product cannot keep. Every number shown (how long a code lasts, how long until resend, how many tries are left) is read from the code that enforces it. |
| F5 | Modern design, far from an AI-template look. |
| F6 | No claim about Shariah status, no trading advice, no guarantee (`core/copy_rules.py`). |

## G. Correctness — the reason this is a rebuild and not a repaint

| # | Rule |
|---|---|
| G1 | Every error state must **look** like an error. |
| G2 | Everything the template includes must be styled on the page that includes it. |
| G3 | A rule the server enforces and the page describes must have **one owner**. The page may not hand-write its own copy of the password rule, the resend wait, or the code lifetime. |
| G4 | A duplicated implementation found on the way is extracted to one owner, not patched twice. |
| G5 | A person must always know which step of the journey they are on and how many are left. |

## H. Quality bar

| # | Rule |
|---|---|
| H1 | ≥ 9.9/10 on UX, UI, user-friendliness and creativity. |
| H2 | No bugs. Every claim measured in a real browser where only a browser can settle it. |
| H3 | Checked with: `ruff`, `mypy`, the offline suites, new invariant tests, and a real browser run. |
| H4 | The existing browser suite must still pass unchanged — roughly two hundred tests sign a person up through these pages. Their handles (`auth-first-name`, `auth-last-name`, `auth-email`, `auth-password`, `auth-repeat-password`, `auth-submit`, `signup-form`, `login-form`, `input[name="code"]`, "Verify and create account") are a contract, not decoration. |
| H5 | Every `?v=` cache key in every template stays in step (one release key for the whole product). |

## I. Working rules from CLAUDE.md that bind this task

| # | Rule |
|---|---|
| I1 | Fix the defect class, not the reported instance. One vocabulary, one owner, every caller importing it. |
| I2 | A problem found while working is a problem I fix — not one I report. |
| I3 | Tests assert the rule across the whole family, not the single case. |
| I4 | Report in very simple words for a non-native English speaker. |
| I5 | Leave nothing in the prompt unsolved; a true blocker is named plainly. |
