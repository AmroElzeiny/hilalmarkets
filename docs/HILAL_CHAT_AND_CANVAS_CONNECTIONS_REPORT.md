# Two pieces of work: cancelling a connection, and Hilal

Written for a reader who is not an engineer. Short sentences. Plain words.

---

## Part 1 — You can now cancel a connection on the canvas

### What was wrong

On the canvas, a line joins a card to a group. You could **move** that line from one
group to another. You could not **remove** it.

If you dragged the line and let go over empty space, nothing happened. The line snapped
back. There was no button to cut it, and no key to press. The only way to get rid of a
connection was to delete the whole card — which also deleted all the settings you had
typed into it.

Deleting a card and cancelling a connection are two different things. The product only
offered one of them.

### What it does now

A card can be **set aside**. It stays on the board with everything you typed. It is just
not part of the monitor until you join it back.

There are now **four ways** to cancel a connection, and one way to change your mind:

| Way | How |
|---|---|
| Drag | Take hold of the small circle on the left of a card. Drag it to empty space. Let go. |
| Button on the card | A "cut" button on every joined card. One click. |
| Button on the line | Point at a line, or select a card. A small button appears on the line itself. |
| Settings panel | "Sits inside" now has an option called "Nothing — set this card aside". |
| Change your mind | Press **Esc** while dragging. Nothing changes at all. |

The line tells you what will happen **before** you let go:

- over a group — solid blue, "this card will join here";
- over empty space — faint and dotted, "this connection will be cancelled";
- anywhere else — plain, "nothing will happen".

A set-aside card lands on a **shelf** below the monitor, with a label that says
"Set aside — not part of this monitor". The board follows the card there, so you can see
where it went. Undo brings it straight back.

### Things we also had to get right

| Problem | Why it mattered |
|---|---|
| A set-aside card was reported as an unfinished part of the monitor | It said "this card still needs a value" about a card the monitor never reads. No answer could ever clear it. |
| A set-aside card counted against the card limit | You could be blocked from adding a card because of cards you had deliberately taken out. |
| A screen reader was told the shelf was part of the monitor | It is not. The shelf is now its own list, and says so. |

### Problems found on the way, and fixed

**1. The message strip was blocking a strip of the canvas.**
A small message appears at the bottom of the board ("drop the line on a group…"). It was
solid. That meant a band across the bottom of the canvas could not be clicked, could not
be dragged on, and could not be panned from. It also made a wire dropped there read as
"over something" instead of "over empty space". The strip now lets the pointer through.
The one button inside it still works.

**2. A line could be looked at but never touched.**
A line is two pixels wide. Nobody can reliably point at two pixels. Every line now has an
invisible wide copy of itself that catches the pointer.

**3. Undo left you looking at the wrong part of the board.**
Cancel a connection and the card goes to the shelf, far below. Press undo and it goes
back to the top — but the view stayed where it was, so the card had vanished again. The
board now follows the card by the smallest amount that keeps it on screen.

---

## Part 2 — Hilal, the assistant in the dashboard

### What it is

A round button in the bottom right of every `/dashboard-test` page. Click it and a chat
window opens. The assistant is called **Hilal**.

It is **not** the support chat on the public website. Different assistant, different
knowledge, different limits, different storage.

### What it helps with

Listings, Passports, coins, reports, screening standards, pricing, why a coin holds its
status, and why a status changed.

### What it refuses

Two things, always, in any wording:

| Asked for | What it says |
|---|---|
| A trading strategy | It does not build or judge strategies. It points at the Monitor page, where you build one yourself. |
| Financial advice | No buy, sell, hold, target, prediction, leverage or timing. It says so plainly and offers what it *can* do. |

**The refusal happens before the AI is asked.** It costs nothing and takes no time. A
question like "should I buy bitcoin" never reaches the model at all. The model is *also*
told the same rules, so both have to fail before anything could slip through.

### Where its answers come from

Only from this platform's own records. Before every question the server gathers:

- the coins the question mentions, with their recorded status, the standard used, when
  they were reviewed, and why the status last changed;
- which screening standards are in force;
- how many coins hold each status;
- which exchanges are covered;
- which categories are recorded (meme coins, stablecoins, and the rest);
- the plans and their prices;
- what page you are looking at, and which coin is open on it.

If something is not in those records, Hilal says so. It never fills the gap from memory.
It has no access to the internet, to prices, to news, or to any other platform.

**Shariah status is only ever repeated, never decided.** Hilal can tell you what a review
recorded, under which named standard and version, and when. It never says a coin is halal
or haram in its own voice, and it never gives a religious ruling.

### Coin names

You can type `btc`, `BTC`, `$BTC`, `bitcoin`, `Bitcoin`, or `the bitcoin coin`. All of
them find the same listing.

The names come **from the listings themselves**, not from a hand-written list. A
hand-written list would be a second opinion about what a coin is called, and it would
drift the first time a listing changed.

### The daily limit

| | Free | Paid |
|---|---|---|
| Per 24 hours | **$0.10** | **$0.50** (five times) |

The cycle resets at **00:00 UTC**.

The window shows how much of the day is left, in words rather than in money — "about half
of today's messages left" is something you can act on; "$0.0731 of $0.10" is not.

When the allowance runs out:

- the message box is disabled;
- the reason is shown in plain words;
- a countdown says how long until it resets;
- a free user is offered a link to the subscription page;
- the moment the day turns over, the box unlocks **on its own**, with no reload.

The status refreshes **every second** while the window is open. It stops the moment the
window is closed or the tab is hidden — nobody is watching it then.

**The limit is enforced on the server.** A browser that ignores the status still gets
refused.

**A refusal costs nothing.** Asking for advice does not use any of your allowance,
because no AI was involved.

### Your conversation is kept

On the server, against your account. Not in your browser.

Close the tab, sign out, use a different computer — the conversation is still there,
oldest message first. That is what "history from all the sessions" has to mean.

### The controls

| Control | What it does |
|---|---|
| Report button in the header | Reports the last answer. Four reasons plus a comment box. |
| Report button on an answer | The same, for any answer, not just the last one. |
| Closing with the X | Asks how it went: five stars and a comment box. Skippable. |
| Suggestions under an answer | Up to three short follow-ups. Buttons — nothing is sent on its own. |

### Design

Everything comes from what already exists: the same colours, the same two typefaces
(Geometria for the name, Onest for everything read), the same spacing steps, the same
corner radii, the same shadows. No new main colour was invented and no new spacing scale
was added.

Motion comes from **Motion One**, the animation library already used on this path,
through one shared file. Every movement explains something:

- the button lifts when pointed at;
- it turns while Hilal is thinking;
- it goes quiet and grey when the day's messages are used up;
- the window grows out of the button, and shrinks back into it;
- each message arrives from below;
- three dots rise while an answer is being written;
- the suggestion buttons appear one after another;
- the stars light up to the one being pointed at.

Somebody who has asked their computer for less movement gets none of it, and the whole
thing still works.

**It never shows JSON, code, a field name or a tag.** That is not left to the AI to
remember. Any answer containing one of them is refused by the server before it is shown,
and the question is asked again.

### Accessibility

- Every control has a name, and works from the keyboard.
- Tab stays inside the open window.
- Escape closes it and gives the keyboard back to the button.
- The conversation is a real list with real roles, so a screen reader can follow who said
  what.
- New answers are announced once, politely.
- A refusal is marked in **words**, not only by colour.
- Text measures better than 4.5:1 against its background; the button is at least 44×44.

### The unique icon

Hilal has its own mark: an open speech shape with a crescent turning inside it. It reads
as a conversation first and the brand second. Deliberately not a robot, not a face, not a
brain, and not a crescent standing alone — a crescent alone would read as a religious
symbol rather than as somebody to talk to.

---

## Problems found on the way and fixed, beyond what was asked

| # | What it was | Fixed |
|---|---|---|
| 1 | The canvas message strip blocked a band across the bottom of the board | Yes — it lets the pointer through now |
| 2 | Canvas lines were 2px and could not be pointed at | Yes — an invisible wide copy catches the pointer |
| 3 | Undo left the view somewhere the changed card was not | Yes — the board follows the card by the smallest pan |
| 4 | "What RSI **settings** should I use" was not refused | Yes — the pattern allowed only one word before "should I". Every one- to three-word form is now covered, and the tests cover them all |
| 5 | "Can you guarantee returns" was not refused | Yes — the pattern only matched the other word order |
| 6 | "What stop level should I use" was not refused | Yes — it needed the words "stop loss" together |
| 7 | "Build me a **trading** strategy" was not refused | Yes — the pattern wanted the noun straight after "a". An adjective or two in front is now allowed, and eight such forms are in the tests |
| 8 | Hilal's limit would have shared one wallet with the setup chat | Yes — the shared budget system gained a per-feature window, so "$0.10 a day with Hilal" is true regardless of what else was used |
| 9 | The canvas offered a **retired** delivery channel | Yes — it built its own list of "ways to be told" from the alert schema alone. The schema still accepts the retired value so old alerts stay readable, so the canvas offered a way to be told that nothing would deliver. There is now one owner for "what we can actually deliver", and both readers import it |
| 10 | A test asserted that retired channel *should* be offered | Yes — my own test from the previous pass had written the defect down as a requirement. It now checks the real rule |
| 11 | Hilal's button was buried under the cookie banner | Yes — the banner is fixed across the bottom of the dashboard on a higher layer. A first-time visitor could see the button and not click it. The widget now sits above the banner, by the banner's own measured height |
| 12 | The whole widget was dead on every page | Yes — the start-up line ran before the class it creates had been initialised, so nothing worked at all: no history, no polling, no clicks. Found by the browser suite; it is why 30 tests failed at once |
| 13 | The message box kept the "come back tomorrow" wording after unlocking | Yes — the placeholder is put back when the allowance returns |
| 14 | Two controls were under the 44-pixel touch size | Yes — the header buttons and the suggestion buttons keep their small drawing and grew a 44px target underneath |
| 15 | Retrying a question whose first attempt failed crashed the server | Yes — the question is written down before the answer is attempted, so a retry hit the "only once" rule on it. The retry now answers the question already on file instead of writing it twice. This was the retry people actually make |
| 16 | Eight tests still expected the old one-word brand name | Yes — the product says "Hilal Markets" as the brand rules require; these expectations had been left behind. Confirmed against a clean checkout first: they failed there too, so they were not caused by this work |

Numbers 4, 5, 6 and 7 are the same defect four times: a refusal that worked for one
phrasing and not for another. Each was found by a test written over a **family** of
phrasings rather than one example, which is why all four surfaced in one pass.

---

## How it was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 363 files |
| Unit, engine, interpreter, services and integration suites | **13,881 tests, all pass** |
| Hilal boundary rules (unit) | 159 checks, pass |
| Hilal through the real app (integration) | 30 checks, pass |
| Hilal in a real browser | 34 checks, pass |
| The canvas in a real browser | 27 checks, pass |
| Market cards in a real browser | 4 checks, pass |
| `scripts/check_release_invariants.py` | Pass |
| `scripts/check_api_route_security.py` | Pass |
| `scripts/check_javascript.py` | Pass, 35 files |
| `scripts/check_jinja_templates.py` | Pass, 78 templates |

The browser suites fail the run on **any** console error, any page error and any failed
request, so "no bugs" is checked by the machine rather than by reading.

### Two things not verified here

1. **Real answers from the AI.** The refusals, the storage, the limits and the whole
   interface are tested end to end, and refusals need no AI at all. Testing what the
   model *says* about a real coin needs a paid call against a real account with real
   review data. Worth doing before launch, with one or two questions — not a suite.
2. **The published price of `gpt-5.6-luna`.** The cost table still carries a placeholder
   rate, as it did before this work. The daily limit is enforced against whatever that
   table says, so the *mechanism* is right; the *number of messages* $0.10 buys will move
   when the real price is filled in.
