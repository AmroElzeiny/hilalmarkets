# Rules for the "Hilal" chat agent on the redesigned dashboard pages

This file turns the request into a checklist, written **before** any code was changed.
Every line is a pass/fail gate. It is the third file in this family:
`dashboard-test-rules.md` governs the whole path, `dashboard-test-monitor-rules.md`
governs the canvas, and every rule in both still applies here.

## A. Scope and placement

| # | Rule |
|---|---|
| A1 | One agent, named **Hilal**, reachable from a circular button fixed to the **bottom right** of the screen. |
| A2 | It appears on **every the redesigned dashboard pages page** and on **no other page**. Not on `/dashboard`, not on the public site. |
| A3 | It is **not** the landing-page support assistant. Separate agent, separate knowledge, separate storage, separate limits. |
| A4 | It is decided **server-side**, from the path's own chrome settings — never by reading the URL in the browser. |
| A5 | The canvas page keeps its rule A3/A4: no assistant *inside* the canvas. The floating button is the shell, not the canvas. |
| A6 | The button carries a **unique icon** of its own. It is not the existing `bot` or `support` glyph. |

## B. What Hilal may and may not do

| # | Rule |
|---|---|
| B1 | It helps with: listings, Passports, coins, reports, methodologies, screening, pricing and plans, why a coin holds its status, and why a status changed. |
| B2 | It **refuses** to *decide* a strategy — to author one, to judge one, or to choose a number to put in a field. See B2a–B2c: this rule was narrowed on 16 August 2026. |
| B3 | It **refuses** financial advice: buy, sell, hold, entry, exit, target, allocation, prediction, leverage, "is it a good time". |
| B4 | A refusal is friendly and useful: it says plainly what it cannot do, and offers the nearest thing it *can* do — which, for anything about a monitor, is walking the person through building it themselves. |

### B2 in detail — where the line is, and why it moved

B2 first said "refuses to build, suggest, adjust **or judge** a trading strategy", and
that was read as "refuses anything with the word strategy or monitor near it". The
result was that "how do I connect these two cards" and "what is my board missing" came
back as safety refusals. Those are questions about **working the product**, with no
money judgement in them, and refusing them was the product turning down its own job.

The line is no longer the subject. It is **who is deciding**:

| # | Question | Answer |
|---|---|---|
| B2a | Which of our cards says the thing you have already decided you want to watch; how to add it, join it, group it, cancel it; what a card means; what your board is still missing; what to do next | **Helped.** The answer is this platform's own catalogue and this page's own checklist, and adding a card decides nothing. |
| B2b | Which number, level, percentage, threshold or timeframe to put in a card | **Refused.** That number is the person's own position. CLAUDE.md names substituting it as the first failure to look for. |
| B2c | Whether a draft is good, correct, profitable or likely to work; what the best strategy or settings are; produce a whole monitor for me | **Refused.** A judgement about money, and nobody here makes one. |

Two sentences carry the whole rule, and both are in the tests:

- *"Build me a monitor"* → refused, and offered guidance instead.
- *"Help me build a monitor"* → helped.

The words that separate them live in one place, `_ASKING_TO_BE_SHOWN` in
`services/hilal_chat_agent.py`. They never soften B2b or B2c: "help me pick the best
level" is still somebody asking Hilal to choose their number, and is still refused.

| # | Rule |
|---|---|
| B9 | Guidance is **one step at a time**: name the button, say where it is, say what happens. Never a finished set of conditions, never a whole monitor. |
| B10 | Guidance may only name a control that is **actually on the person's screen**, spelled the way the screen spells it. The page sends the list; nothing else may be named. |
| B11 | Every guidance answer carries a short, plain reminder that this help is **new and can be wrong**. The window also says "Beta" in its header, permanently, so the caveat does not depend on the model remembering it. |
| B5 | It is an expert on **Hilal Markets only**. No general internet knowledge, no news, no outside price source. |
| B6 | It **never invents**. Every fact comes from server-owned evidence supplied to the turn. Nothing found is reported as not found. |
| B7 | Shariah status is only ever **reported** as the recorded result under a named methodology and version. It is never assigned, inferred, judged or predicted. (CLAUDE.md, non-negotiable.) |
| B8 | It never claims an action happened — no monitor started, no plan changed, no ticket opened. |

## C. Knowledge and awareness

| # | Rule |
|---|---|
| C1 | It knows the **coin nicknames** a beginner types ("btc", "bitcoin", "the king", "doge"), and resolves them to real listed assets — from a server-owned vocabulary, not the model's memory. |
| C2 | It knows which **exchanges** the platform actually covers, and says so from stored data. |
| C3 | It knows the **categories** the platform records — meme coins, stablecoins, and the rest — from stored data. |
| C4 | It can **see what the user sees**: the page, the part of it in view, the coin or Passport in front of them, and — on the canvas — the monitor they are drawing. Sent with the turn, in one shape. |
| C5 | Page awareness never becomes a second source of truth **about the platform**. The page says *what is being looked at*; facts about a coin, a standard or a plan still come from the server's own rows. The one exception is the person's **own draft**, which exists nowhere else yet and is therefore the subject itself when they ask about it. |
| C6 | It answers in **any language the person writes in**, and the welcome says so. |
| C7 | The canvas describes itself **in its own words** — the readout sentence, the checklist, the card labels, the clause on each card. Hilal never re-reads the board and forms a second opinion about what a card means. One owner for "what this card says", as everywhere else in this codebase. |
| C8 | When there is no canvas on the page, no board is sent. Hilal says it cannot see one rather than talking about an empty one as if it were real. |
| C9 | It knows **what this product's own words mean** — a condition, a card, a group and its three kinds, connecting a card, cancelling a connection, "set aside", the checklist, a draft, approving, an alert — from `services/hilal_product_words.py`, handed over as evidence on every turn. Before this, the most common beginner question on the canvas ("what is a Group?") had two possible answers and both were wrong: say it did not know, or invent one. |
| C10 | Those definitions are the **words the interface prints**, spelled the way it prints them. A card in a group says "all of these"; explaining "the AND box" sends a person looking for something that is not on their screen. `test_every_product_word_matches_the_interface` checks the spelling against the canvas itself. |
| C11 | The definitions are evidence, and obey every line an answer obeys: **no ruling, no advice, no number.** A definition is the easiest place for a rule to leak, because it is written once and read every turn. |
| C12 | **How** to do something on the board is never defined here. That comes from the canvas's own "Keys and gestures" panel, travels as part of the board, and is quoted rather than restated — see C7. |

### C9 in detail — somebody who is lost

"I don't understand any of this" is one of the most useful messages Hilal will ever get,
and the easiest to answer badly. The order is fixed:

1. **A calm line first.** That it looks like more than it is, that this part confuses
   nearly everybody, that nothing is being watched until they approve it. One line — a
   long reassurance is its own kind of pressure.
2. **One question, and a concrete one.** Which part are they looking at, what did they
   just try, what did they expect. Never two questions. Never "describe the screen" when
   the board is already in front of Hilal — it can see it, so it asks a sharper question.
3. **One step at a time, from where they actually are.** Never a list of steps for
   somebody who has just said they are confused.

Short messages, repeated questions, "it is not working", "I give up" and a question with
no verb in it are all read as somebody stuck, not as somebody being brief.

## D. History and storage

| # | Rule |
|---|---|
| D1 | The conversation is stored **per user**, on the server, and survives closing the tab, signing out and changing device. |
| D2 | Opening the chat shows the earlier messages, oldest first, without a reload. |
| D3 | History has one owner. The browser never holds the authoritative transcript. |
| D4 | Stored messages carry a retention boundary, like every other stored conversation on this platform. |

## E. Spending limit

| # | Rule |
|---|---|
| E1 | A free user may spend **$0.10** of model cost per 24-hour cycle. A paying subscriber gets **5×** that. |
| E2 | The cycle resets at **00:00 UTC**. |
| E3 | There is **one** authority for this number. It reuses `services/ai_budget.py`; it does not add a second ledger. |
| E4 | When the limit is reached the person is **told plainly**, and the message box is **disabled**. |
| E5 | While disabled, Hilal offers to upgrade and links to the subscription section. |
| E6 | Status refreshes **every second** and the chat box reacts to it — including re-enabling itself when the reset time arrives, with no reload. |
| E7 | The limit is enforced on the **server**. A client that ignores the status still gets refused. |
| E8 | A refusal names the right reason: a platform-wide ceiling is never described as the person's fault. |

## F. Controls in the window

| # | Rule |
|---|---|
| F1 | A **report** button in the chat header, for reporting a message. |
| F2 | Closing with **X** opens a rating popup: **5 stars** plus a comment box. |
| F3 | The rating can be skipped. Skipping still closes the chat. |
| F4 | Every control has a visible label or an accessible name, and works by keyboard. |

## G. Brand: never invent

Inherited from `dashboard-test-rules.md` section B.

| # | Rule |
|---|---|
| G1 | Only colours already declared in `hilalmarkets-brand.css`. **No new main colour.** |
| G2 | **Geometria** for headings and figures, **Onest** for body and controls. No third family. |
| G3 | Spacing, radius and shadow from existing tokens. **No new spacing scale.** |
| G4 | Apple green stays an accent, roughly 5–10% of the surface. |
| G5 | Sentence case. Never Title Case, never ALL CAPS. |
| G6 | "Hilal Markets" in prose; "Shariah" for the formal mechanism (`core/copy_rules.py` enforces both). |
| G7 | No forbidden claim from `brand guide.md` section 17. |
| G8 | No AI brains, robots, glowing spheres, crypto or religious clichés — including in the agent's own avatar. |
| G9 | Islamic greetings are **written by the model as language**, never hard-coded into the interface. |
| G10 | One highlight, and it is the product's. The focus ring is declared once in `hilalmarkets-brand.css` and is not restated here; the widget only adjusts how far out it sits, because the window clips what leaves it. It carried a blue ring of its own, which §9 keeps for connectors and small progress details. |
| G11 | "Selected" and "focused" are shown in **near-black**, the way the rest of the redesigned dashboard pages shows them. Apple green marks the two things that are actions: the button that opens Hilal, and the button that sends. |
| G12 | Words a person reads are **full-strength ink**, never the quiet grey used for secondary detail. `--t-muted` measures 3.98:1 on white and is for large text and non-text only. |
| G13 | Icons are **imported, not drawn here**. The assistant's mark is Lucide's `message-circle`, taken as it ships. A hand-drawn crescent-in-a-bubble was tried and read as a spiral at 26 pixels, which is the only size it is ever seen at. |

## H. Motion and interactivity

| # | Rule |
|---|---|
| H1 | Motion comes from **Motion One**, through `hm-motion.js`. No second easing scale, no one-off inline hacks. |
| H2 | The button has real **states**: closed, hover, focus, open, thinking, limit reached, unread. Each one is visibly different. |
| H3 | There is motion at **every** interaction: hover, focus, open, close, send, arrive, typing, star, report, error. |
| H4 | Every animation **explains something**. Nothing loops for decoration. |
| H5 | Motion never delays the action it describes. |
| H6 | `prefers-reduced-motion` removes non-essential motion and everything still works. |

## I. Accessibility — WCAG 2.2 AA

| # | Rule |
|---|---|
| I1 | Text contrast at least 4.5:1; icons and control borders at least 3:1. |
| I2 | Status is never colour alone: colour **plus** text, and an icon. |
| I3 | Everything reachable and operable by keyboard, in a sensible order. |
| I4 | Visible focus ring on every focusable element. |
| I5 | The chat window traps focus, closes on `Escape`, and returns focus to the button. |
| I6 | Touch targets at least 44×44 px. |
| I7 | New messages are announced once through a polite live region. Never one message per token. |
| I8 | The transcript is a real list with real roles, so a screen reader hears who said what. |

## J. Readability

| # | Rule |
|---|---|
| J1 | **Never a wall of text.** Short answers, short sentences, one idea each. |
| J2 | It **never shows JSON, code, field names or internal keys** — not in any reply, not in an error. |
| J3 | It never reads like a report. It reads like a helpful person. |
| J4 | Plain words. No jargon a beginner would not know. |
| J5 | It is honest about what it cannot do, straight away, rather than after three paragraphs. |
| J6 | **Nothing in the window is squashed.** The window is a fixed-height column; every row that is not the transcript keeps its own height, and the transcript is the one that scrolls. A row that gives up its height puts its own words outside itself. |
| J7 | **No scrollbar on something nobody has typed in.** The writing box measures itself and grows; a bar appears only once it has stopped growing. The invitation inside it is short enough for one line at the narrowest width the window is ever drawn at. |
| J8 | It is **warm before it is correct, and it is both**. Something human before something useful when a person is stuck; a refusal never ends the conversation; the person's name used occasionally, never in every message. |

## K. Verification gates

| # | Rule |
|---|---|
| K1 | `ruff check src tests scripts` clean. |
| K2 | `mypy src` clean. |
| K3 | The unit, engine, interpreter, services and integration suites pass. |
| K4 | The chat opens, sends and answers through the real app, signed in. |
| K5 | The browser console is clean — no error, no warning — through a full open/send/report/rate/close pass. |
| K6 | Keyboard-only pass: open, send, report, rate, close. |
| K7 | Reduced-motion pass. |
| K8 | Contrast checked on every text and status colour pair that ships. |
| K9 | The refusals are tested as rules over a family of prompts, not as one example each. **And so is the help**: the questions that must never be refused are their own family, so narrowing B2 by accident fails the suite. |
| K10 | Layout is **measured in a running browser**, not read. Where the words sit inside a button, whether the writing box overflows, whether a row was squashed — each is a number a test compares, at more than one screen width. |
| K11 | What Hilal is told about the screen is compared against what the screen shows. If the sentence sent to the model differs from the sentence printed on the page, one of them is inventing. |
| K10 | Every defect found on the way is fixed and listed, or named as blocked. |
