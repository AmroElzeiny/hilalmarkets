# Rules for `/dashboard/connections` and for email notifications

Written **before** any code, from the request. Every line is a pass/fail gate. Nothing
ships until each line can be answered "yes, and here is where".

This file sits under `dashboard-test-rules.md`, which governs the whole path, and beside
`dashboard-test-watchlists-opportunities-rules.md`. Every rule in those still applies —
brand, motion, accessibility, readability, and the product rules that cannot be broken.
Only what is new or sharper is written below.

The request carried four separate jobs. They are kept apart here so none of them can be
quietly dropped into "mostly done".

| Job | Sections |
|---|---|
| 1. Stack the action buttons on the Opportunities cards | A |
| 2. Every coin shows its logo | B |
| 3. Score `/dashboard/connections`, then design `/dashboard/connections` | C–H |
| 4. Email notifications: the channel, the backend, and every email template | I–L |

## A. The action buttons on a card

| # | Rule |
|---|---|
| A1 | The buttons inside `.w-actions.o-actions` stack **vertically**, one under another. |
| A2 | The reason is that a row breaks when the number of buttons changes with the data. So the fix is not for Opportunities alone: **every card action row on this path** stacks, because `.w-actions` is one class shared with Watchlists and the same overflow is already there. |
| A3 | A stacked button is full width, so the column reads as one column and not as ragged text. |
| A4 | Order is meaning, not length: the main action first, the quiet ones last. |
| A5 | Touch target stays at least 44px tall after stacking. |

## B. Every coin shows its logo

| # | Rule |
|---|---|
| B1 | The reported symptom is one coin (Mubarak). The scope of the fix is **every coin on every page**, and every reason a logo can fail to appear. |
| B2 | "Where does a coin's picture come from" gets **one owner** in Python and **one owner** in the browser. Today it is answered in eight places with eight different subsets of the sources. |
| B3 | Every source the platform already holds is tried, in order, before giving up: the picture stored on the asset record, then the icon catalogue, then the letter monogram. A page that knows only one source is the defect. |
| B4 | A card that never looks up the asset record cannot pass `logo_url = None` and call that an answer. Every card path resolves from the same place. |
| B5 | The monogram is a **designed fallback**, not a failure: it is what a coin with no picture anywhere is supposed to look like, and it must look deliberate. |
| B6 | The catalogue version pin stays in `core/asset_logos.py` only. No new hand-typed copy. |
| B7 | A test proves the family: for a coin in the catalogue, for a coin only on the asset record, for a coin with neither, and for a ticker the exchange prefixed with a supply multiplier. |

## C. Score the live page first

| # | Rule |
|---|---|
| C1 | `/dashboard/connections` is **scored first** — UX, UI, user-friendliness — with the number written down and the reason for it. |
| C2 | Every problem becomes a numbered finding. Every finding is answered by something in the new design. A finding with no answer is an unfinished job. |
| C3 | The score is about the page, not about the people who wrote it. |
| C4 | The findings that are real defects — not taste — are **fixed at their source**, so `/dashboard/connections` gets the fix too. |

## D. Scope of the new page

| # | Rule |
|---|---|
| D1 | A new page at `/dashboard/connections`. `/dashboard/connections` keeps working exactly as it does today. |
| D2 | Every popup and every function the live page has is carried over, redesigned, none dropped: connect Telegram, remove Telegram, the Telegram Web fallback, WhatsApp connect, WhatsApp categories, test, pause, resume, clear error, disconnect. |
| D3 | Same data, same server truth as the live page. A design path that decided for itself whether a channel is connected would be a second answer to one question. |
| D4 | The page carries the path's shared chrome: Hilal, the hidden create button, no Passport popup (there is no coin on this page). |
| D5 | Which channels exist is `offered_channels`, never a hand-written list. A channel that cannot deliver is never offered. |

## E. What the page is for

| # | Rule |
|---|---|
| E1 | The page answers three questions in this order: **where will you be told, is it working, and what will you be told about.** |
| E2 | Every channel says its real state in words, and what to do next if that state is not "working". |
| E3 | A channel that is unavailable says **why** it is unavailable and what would change it. "Disabled" on its own is not an answer. |
| E4 | Nothing on this page implies a recommendation to trade, and no notification setting can change a Shariah status. |

## F. Design

| # | Rule |
|---|---|
| F1 | Colours, type, spacing, radius and shadow from the tokens already declared. **No new main colour, no new spacing scale, no third typeface.** |
| F2 | Apple green stays the single focal accent. Selected and focused states are near-black, as everywhere else on this path. |
| F3 | **Full of icons** — every channel, every state, every action, every notification kind carries one. Icons come from the existing set; a new one is added to that set in its own style, never drawn inline. |
| F4 | Modern, and **not an AI-looking template**: no hero gradient, no glass panels, no purple, no emoji, no generic three-card feature row. |
| F5 | The live page's structure may be thrown away. What must survive is every piece of information and every action. |
| F6 | New information may be added only if it is **main** information and comes from data the platform already holds. |
| F7 | Aim: 9.9/10 on UX, UI and user-friendliness, measured against the findings in section C, not against taste. |

## G. Motion, interactivity, accessibility

| # | Rule |
|---|---|
| G1 | Motion comes from **Motion One** through `hm-motion.js`. No second easing scale, no one-off numbers. |
| G2 | There is motion at **every** interaction, hover included, and every animation **explains something**: what changed, what is loading, what arrived, where a thing went. |
| G3 | Nothing loops for decoration. Nothing flashes or glows. Motion never delays the thing it describes. |
| G4 | `prefers-reduced-motion` removes it and the page still works completely. |
| G5 | WCAG 2.2 AA: text contrast at least 4.5:1, borders and icons at least 3:1, measured and not assumed. |
| G6 | Status is never colour alone: colour **plus** words **plus** an icon. |
| G7 | Everything reachable and operable by keyboard, in a sensible order, with a visible focus ring. |
| G8 | Every dialog traps focus, closes on `Escape`, and returns focus where it came from — through `hm-dialog.js`, not a fourth copy. |
| G9 | Touch targets at least 44×44. |
| G10 | A result of an action is announced once, politely, in words — never only as a colour change. |

## H. Readability

| # | Rule |
|---|---|
| H1 | **Never a wall of text.** The single most repeated instruction in the request. Long explanations live behind a disclosure. |
| H2 | No internal word reaches a person: no "integration", no "opt-in category", no "provider", no error code, no "24-hour service window". |
| H3 | Every state has a plain-words name and one short line saying what it means. |
| H4 | Empty states say what to do next and offer the button that does it. |
| H5 | A beginner can read any card aloud and understand it. |

## I. The email channel — what it must do

| # | Rule |
|---|---|
| I1 | Email becomes a **real delivery channel**, equal to Telegram: the same alerts, chosen the same way, recorded the same way, retried the same way. |
| I2 | It covers every kind Telegram covers: Shariah-status change, near-miss, forming, confirmed setup, lifecycle, account and trial notices. Not a subset. |
| I3 | Which channels can deliver is `offered_channels`. Email joins that list; nothing else re-writes it. |
| I4 | Email is only offered when the platform can actually send it. A channel offered while the sender is not configured is a promise the product cannot keep. |
| I5 | Delivery is recorded as an `AlertDelivery` row like every other channel, with the same retry, the same failure codes, and the same "why was I not told" trail. |
| I6 | Sent from **no-reply@hilalmarkets.com**. |
| I7 | A person can turn email on and off, and it appears on `/dashboard/connections` beside Telegram and the unavailable WhatsApp. |
| I8 | The person's own account email is where it goes. No second address to keep in step, and no new place for a typo to silently swallow alerts. |
| I9 | Every alert email carries a plain-text part as well as the HTML part. |

## J. What an alert email may say

| # | Rule |
|---|---|
| J1 | **The words come from the same owner Telegram uses.** An email that built its own sentences from the proof record would be a second opinion about one alert. |
| J2 | Shariah status in an email is the stored, reviewed status and nothing else. Never inferred, never softened, never coloured green for being green. |
| J3 | No forbidden claim: no "100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you". |
| J4 | Nothing in an email is advice. Conditions are the person's own conditions. |
| J5 | A missing number is shown as missing, never as zero. |
| J6 | Every email says who it is for, why it arrived, and how to stop it. |

## K. Email templates — the design

| # | Rule |
|---|---|
| K1 | **One shell for every email.** The frame, the header, the footer and the legal line are written once. A template that drew its own frame is a second brand. |
| K2 | The one-time code email and the sign-up confirmation are redesigned to that same shell, so every email a person receives looks like the same product. |
| K3 | Brand guide applies to email exactly as it applies to a page: Geometria for headings, Onest for body, apple green as a single accent, sentence case, no ALL CAPS, no Title Case. |
| K4 | Email HTML is table-based and inline-styled, because email clients are not browsers. **No external CSS, no web fonts as a requirement, no JavaScript, no background images carrying meaning.** |
| K5 | It must be readable with images switched off. Every graphic has a text alternative, and no graphic is the only carrier of a status. |
| K6 | Dark mode in the mail client must not make text vanish. |
| K7 | Readable on a phone: one column, at least 16px body text, tap targets at least 44px. |
| K8 | Contrast at least 4.5:1 for body text, measured on the real background colour. |
| K9 | Every template carries the same "hype": the same header, the same status block, the same one clear action, the same footer. A person should not be able to tell which system sent which. |
| K10 | Plain words. A template is a page: **never a wall of text.** |

## L. No bugs, nothing unrealistic

| # | Rule |
|---|---|
| L1 | Nothing invented for the sake of the design: no fake chart, no made-up delivery number, no placeholder person, no fake partner logo. |
| L2 | Every number shown is real, or the panel says it has none yet. |
| L3 | The browser console stays clean through a full pass of the page, including every popup. |
| L4 | No horizontal scroll at 1440, 1024, 760 and 390. |
| L5 | The page works with nothing connected and with everything connected. |

## M. Verification gates

| # | Rule |
|---|---|
| M1 | `ruff check src tests scripts` clean. |
| M2 | `mypy src` clean. |
| M3 | The unit, engine, interpreter, services and integration suites pass. |
| M4 | The new page renders through the real app, signed in, with and without each channel connected. |
| M5 | The browser suite passes with a clean console, including every popup. |
| M6 | Keyboard-only pass on the page and every dialog. |
| M7 | Reduced-motion pass. |
| M8 | Contrast measured, not assumed — on the page **and** in every email template. |
| M9 | Every email template is rendered and read, not assumed: the code email, the sign-up email, and one of every alert kind. |
| M10 | Every finding from section C has a test or a measured check proving it is answered. |
| M11 | Every problem found on the way is fixed and listed, or named as blocked. |
