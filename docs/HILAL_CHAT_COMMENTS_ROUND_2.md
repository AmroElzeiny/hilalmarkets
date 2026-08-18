# Your comments on the Hilal chat agent — what changed

Written for a reader who is not an engineer. Short sentences. Plain words.

Your nine comments, one section each. Then the extra problems found on the way, and
the one thing I could not check.

---

## 1. The writing box had a scrollbar when it was empty

**What you saw.** A grey bar down the side of the box before you had typed anything.

**Why.** The words inside the empty box — *"Ask about a coin, a Passport, a standard or
your plan…"* — were longer than one line. The box is one line tall. So it was already
overflowing before anybody touched it.

**Fixed, two ways.** Both were needed.

| | |
|---|---|
| The words are short | Now just **"Ask me anything…"**. It fits on one line even on a phone. |
| The box measures itself | It grows to fit whatever is in it, at start-up and whenever the window changes width. A bar only appears once the box has grown as far as it may. |

The second one matters more than the first. Short words fix today's text. A box that
measures itself fixes every future text, in every language.

While fixing this I found the box was **two pixels too short** at every size. Its own
border was eating them. On one line you cannot see it; on four lines it clipped the
last one. Fixed by measuring the border instead of assuming it is nothing.

---

## 2. The suggestion buttons were broken — the words fell below the button

**What you saw.** Exactly right, and it was worse than it looked.

**Why.** The chat window is a fixed height. Inside it, the conversation refused to get
smaller than its own contents. So the window took the space out of **everything else**
instead. Measured in a real browser: the suggestion buttons should be 33 pixels tall,
and they were squeezed to **19** on a laptop and **14** on a phone. Their words dropped
out of the bottom.

This is why it looked fine when you first opened the chat and broke after an answer
arrived: an empty conversation has nothing to push with.

**Fixed at the cause.** Every row in the window now keeps its own height — the header,
the allowance bar, the suggestions, the writing box, the small print. The conversation
is the one part that scrolls, which is what it was always meant to be.

The buttons themselves were also made to place their own words in the middle, rather
than being placed by whatever height the row happened to have. Two protections, because
this had to stop being possible.

**Same bug, other victims.** The send button could be squeezed below 44 pixels, which is
the size a finger needs. The header could be squeezed too. Both are held now, and a test
measures all five rows after a long conversation.

---

## 3. The highlight was a green that is not in the brand guide

**What you saw.** A thick ring around the writing box when you clicked it.

**What it actually was.** *Two* highlights at once: a **blue** ring (`#2a8fc3`) drawn on
top of an **apple-green** border. Together they read as a teal-green that matches no
colour in the guide, because it is not one — it is two colours overlapping.

**Fixed.**

| Before | Now |
|---|---|
| Blue ring, used in ten places in this window | Gone. Blue is used nowhere in the chat. The brand guide keeps blue for connectors and small details, under 2% of a screen. |
| Apple-green border on the active box | Near-black, which is how **every other** box on these pages shows "this is the one you are using". |
| The chat had its own focus ring | It now uses the product's single focus ring, the same one every page has used since before this work. |

**One thing to decide, and it is yours.** The ring you will still see is the product's
own focus ring, and it is apple green. It is defined once, in the brand file, and every
page on the site uses it. The brand guide names apple green as the primary accent and
lists "focused interface states" as a place to use it, so it is on-brand as written.

If you want that ring to be a different colour, that is a **one-line change that affects
every page at once**. I have not made it, because changing the whole product's focus
colour from inside one chat widget is not my call. Say the word and it is a minute's
work.

---

## 4. The text was grey, so it looked unimportant

**What you saw.** Right again, and the fix I would have made first would not have
worked.

**Why.** Every answer is drawn as paragraphs. Paragraphs on these pages are grey by
default. Setting the bubble to black changed nothing, because the words are not in the
bubble — they are in the paragraphs inside it.

**Fixed.** Every word Hilal says and every word you type is now the strongest black the
product has (`#202329`).

**A test that was measuring the wrong thing.** There was already a test checking the
answer text was dark enough. It measured the **bubble**, which was black, while every
word inside it was grey. It passed the whole time. It now measures the paragraph that
actually holds the words, and it checks the exact colour, not just "dark enough".

**Other grey I found and fixed.** The allowance line, the header line, the small print
and the locked panel all used a grey measuring **3.98:1** against white. The accessible
minimum for small text is 4.5:1. All of it now uses a grey measuring 7.5:1. This was my
own mistake from the first pass, and it was an accessibility failure, not only a
looks one.

Buttons too: the header buttons and the suggestion buttons are now full-strength black.

---

## 5. The icon should be an imported one, not drawn

**You were right about why it looked wrong.** I had drawn a speech bubble with a
crescent moon turning inside it. At 26 pixels — the only size anybody ever sees it — it
reads as a spiral, or an "@" sign.

**Fixed.** The mark is now **Lucide's `message-circle`**, taken exactly as it ships.
Lucide is the icon set this dashboard's other icons are drawn in the style of, and it is
free to use.

**On the moon.** There is no ready-made icon anywhere that is a chat bubble with a moon
inside it. I checked. You said to use another relevant ready icon in that case, so I
used the one everybody already recognises as "talk to somebody". The crescent stays
where it belongs — in the logo, right beside it.

Lucide's `moon-star` is now also available in the icon set, unchanged, for anywhere the
crescent is wanted later.

A new test checks the icon is not silently missing. Before, deleting it would have shown
an "information" symbol everywhere and every test would still have passed.

---

## 6. Hilal can now see what you are doing

This was the biggest change.

**What it sees now.**

| | |
|---|---|
| Which page you are on | as before |
| **Which part of that page is in front of you** | new — "the canvas", "the list of screened coins", "the four counters that filter the list" |
| The coin or Passport open | as before |
| **The monitor you are drawing** | new, and the important one |

**About the canvas, it is told:**

- the whole monitor as one sentence — the same sentence printed on your screen;
- every card: its name, what it currently says, whether it must be true, which group it
  is in, whether it is set aside, and **which fields you have not filled in yet**;
- the page's own checklist, line by line, with each line's result;
- how far along the page says you are;
- what the monitor is watching and how you have chosen to be told;
- **the names of the buttons actually on your screen**.

**Where the words come from, and why it matters.** The canvas hands over its own words.
Hilal never reads the board itself and works out what a card means. If it did, there
would be two opinions about what a card says, and the day they disagree, a customer sees
it. A test compares the sentence sent to Hilal against the sentence printed on the page;
if they ever differ, the test fails.

**Naming buttons.** Hilal may only name a button that is in the list your page sent, and
must spell it the way your screen spells it. So it can say *"press Add condition"* and be
right. It cannot send you looking for a button that does not exist.

**Naming gestures — and a gap this opened up.** You asked for *"drag the line Y"*
guidance. Hilal may only describe a gesture the page itself documents. When I went to
find where the canvas documents dragging, **it did not.** The "Keys you can use" window
listed keys only. Dragging a wire off a card is how you cancel a connection, and the
only way to discover that was to try it.

So the canvas now documents it. That window is called **"Keys and gestures"** and has a
second list:

| | |
|---|---|
| Drag the board | Move around it. Nothing changes |
| Drag a card | Move that card. Its connection stays |
| Drag the circle on a card's left edge onto a group | Join it to that group instead |
| Drag that same circle onto empty space | Cancel the connection. The card is set aside, keeping everything you typed |
| Point at a line | A small button appears on the line to cancel it |
| Point at a set-aside card | A button appears to join it back |

That helps every person using the canvas, whether or not they ever open the chat. And
Hilal reads those exact sentences, so if one ever changes, its answer changes with it.

**When there is no canvas**, nothing is sent, and Hilal says it cannot see one. It never
talks about an empty board as though it were real.

**It says it is new.** Three places, so it does not depend on the model remembering:

- a permanent **Beta** mark in the header;
- the header line: *"Here to help — I can get things wrong"*;
- a short line under every piece of guidance, in Hilal's own words each time.

---

## 7 and 8. The protection message is gone from canvas questions

**What was wrong.** Ask *"help me build a monitor"* and you got:

> *"I don't build or judge trading strategies — that isn't something I can do safely…"*

That was the product refusing its own job. Asking how to connect two cards has no money
judgement in it at all.

**Where the line is now.** It is no longer about the subject. It is about **who is
deciding**.

| You ask | Hilal |
|---|---|
| Which of our cards says the thing you already decided you want to watch | **helps** |
| How to add a card, join two, cancel a line, group them, remove one | **helps** |
| What a card means, what your board is missing, what to do next | **helps** |
| Where a button is and what pressing it will do | **helps** |
| **Which number** to put in a card — the level, the percentage, the timeframe | **refuses** |
| Whether your monitor is good, correct or likely to make money | **refuses** |
| To produce a whole strategy for you | **refuses** |
| What to buy, sell or hold | **refuses** |

Two sentences carry the whole rule:

- *"Build me a monitor"* → refused, and immediately offered guidance instead.
- *"Help me build a monitor"* → helped.

The difference is whether you are asking Hilal to **do it** or to **show you**. The words
that separate them are written down in one place. They can never soften the other two
rules: *"help me pick the best level"* is still somebody asking Hilal to choose their
number, and is still refused.

**It guides, it does not author.** Hilal is told, in as many words: one step at a time;
name the button, say where it is, say what happens; then wait. Never a finished set of
conditions. Never a whole monitor. If you ask *"what should I watch?"*, it turns the
question back gently and asks what you already have in mind — because that part is
yours.

**And the numbers stay yours.** Hilal explains what a field means and what its units
are. It never suggests a value, and it may not call one sensible, typical, safe or
common — which is how a suggestion sneaks back in wearing a different coat.

**The refusals that remain now offer something.** The old one closed the door. The new
one reads:

> *"I can't decide a strategy for you, or tell you whether one is any good. That call is
> yours, and the numbers in it are yours — I'd only be guessing, and you deserve better
> than that.*
>
> *What I can do is stay beside you while you build it. Tell me what you want to be
> warned about, and I'll show you which card says it, where the button is, and what your
> board is still missing. You choose every number; I'll make sure nothing is in the
> wrong place."*

---

## 9. Warmer and friendlier

**The instructions Hilal follows now say:** be warm first and correct second, and be
both. Talk like a kind friend who knows the product. When somebody is stuck or worried,
say something human before something useful — *"that one confuses everybody"* costs six
words and changes how the rest lands. Never lecture. Never scold. Use their name once in
a while, not every message. If you must say no, say it kindly and follow it straight away
with what you *can* do.

**The welcome was rewritten.** It now opens with *"I am glad you are here"*, offers to
sit with you on the Monitor page, and ends by admitting it is new and can be wrong.

**The suggested questions now fit the page.** On the canvas you are offered *"What is my
board still missing?"* instead of *"What is a Passport?"*.

**The small marks were softened.** *"Hilal does not help with this"* became *"This one
stays your call"*.

---

## Problems found on the way, and fixed

| # | What it was | Fixed |
|---|---|---|
| 1 | **The "See what a plan adds" button did not exist as a button.** It used a shared button style that only applies inside the page wrapper — and the chat sits beside the page, not inside it. So it rendered as bare text with its icon stacked above it. This is the only way forward offered to somebody who has used up their day. | Yes. The chat is now inside the shared style scope, and a test measures that the link has a shape, padding and its icon on the same line. |
| 2 | **The chat was writing out the spacing scale again.** Because it sat outside the scope, every spacing value was repeated as a fallback — a second copy of the scale, free to drift from the real one. | Yes. Same fix. One scale. |
| 3 | **Four greys under the accessible minimum** (3.98:1 against white, where 4.5:1 is required for small text). Mine, from the first pass. | Yes, all now 7.5:1. |
| 4 | **The contrast test measured the wrong element**, so it passed while every word was grey. | Yes, it measures the words now. |
| 5 | **The writing box was two pixels short**, clipping the last line of anything long. | Yes, the border is measured. |
| 6 | **The send button could be squashed below the 44-pixel touch size.** | Yes, and a test measures it after a long conversation. |
| 7 | **A second list of answer kinds** sat in the service, separate from the real one. Adding "guidance" to one and not the other would have quietly turned every stored guidance answer into a plain one on reload. | Yes. One list, derived from the other. |
| 8 | **"put together a trading system" was let through** — because my own new word list contained "together", which is also the second half of "put together". A vocabulary collision, found by the test. | Yes. |
| 9 | **"the best RSI value" was let through** — the pattern wanted the noun straight after "best", and people put a word in between. This is the same defect this file has now had **four** times. | Yes, and this time the allowance for in-between words is a named thing used by every pattern that needs it, instead of each one remembering separately. |
| 10 | **"what strategy should I use" was let through** while I was narrowing the rules. | Yes, caught by the test suite the same minute. |
| 11 | **Two ways of describing your screen** travelled as loose fields beside the message, so nothing could be added without adding another loose field. | Yes. One shape, one owner. |
| 12 | **The group words were written twice** — once for the card, once for the checks. | Yes, one function both use. |
| 13 | **The canvas never explained its own gestures.** Dragging a wire off a card is the way to cancel a connection, and nothing on screen said so. The help window listed keys only. | Yes. It is now "Keys and gestures" and has a written list of all six pointer actions. Anyone using the canvas benefits, chat or no chat. |
| 14 | **The browser tests could not test a real answer at all.** The stand-in model in the test harness reads the request one way; Hilal sends it another way. So every browser test that needed a real answer got a "provider is down" instead — and the only turns that do not need the model are the refusals. The suite was testing refusals and looking like it tested both. | Yes. The stand-in now understands Hilal, reads the evidence the application supplied, and answers from it. The canvas-guidance test now checks a real answer arrives, is marked as guidance, and carries the "this is new" line. |
| 15 | **The canvas drew its connecting lines on a layer whose colour was pure black** — not a colour this brand has. Nothing looked wrong, because every line is drawn with an outline rather than a fill, but the whole-dashboard brand check reads the colour a page reports, not the colour it shows. | Yes, one line of styling. Found by running a test file that had not been run in this work before. |
| 16 | **The whole browser suite could not be run in one go.** Nearly every test signs a new person up, that is two calls against a limit of two hundred per fifteen minutes, and the suite is 197 tests. Partway through, sign-up started being refused — and what you saw was a sign-up page that never moved, in a test about something else. Fifteen tests failed for a reason that had nothing to do with them. | Yes. The test server's sign-up limit is raised to cover a full run. Nothing here tests that limit; the one test that cares about a refused request produces the refusal in the browser. |
| 17 | **A test typed into a chat box that was not there yet.** The setup chat now opens by asking "Scanner or Monitor?" and holds the writing box back until it has an answer. A test from before that change typed straight into it and then waited a full minute for a message it had never sent. | Yes. It makes the choice the way the product offers it, then types. |

Numbers 8, 9 and 10 are the same kind of mistake as the four in the last pass, and all
three were caught the same way: by testing a **family** of phrasings rather than one
example. Number 9 is the fourth time this exact hole has appeared, which is why it is now
a named thing rather than something to remember.

Numbers 14 to 17 all came from one decision: running **every** browser test file
together rather than only the ones this work touches. Four real problems were sitting in
the ones I had no reason to open. Numbers 15 and 17 are not mine — 15 came in with the
canvas, 17 with the Scanner-or-Monitor choice — but a problem you find is a problem you
fix, so both are fixed and both are named here.

---

## How it was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 363 files |
| Unit, engine, interpreter, services and integration suites | **Pass** |
| Hilal boundary rules (unit) | **245 checks, pass** — was 159 |
| Hilal through the real app (integration) | **30 checks, pass** |
| The whole browser suite, in one run | **195 pass, 2 skipped** — and this is the first time it has been able to run in one go at all |
| Hilal in a real browser | **49 checks, pass** — was 34, clean console |
| The canvas in a real browser | **27 checks, pass** |
| The rest of the dashboard in a real browser | **37 checks, pass** |
| Market cards in a real browser | **4 checks, pass** |
| `scripts/check_javascript.py` | Pass, 36 files |
| `scripts/check_jinja_templates.py` | Pass, 78 templates |
| `scripts/check_release_invariants.py` | Pass |
| `scripts/check_api_route_security.py` | Pass |

Every reported problem now has a test that would fail if it came back. The layout ones
are **measured in a running browser at three screen widths** — where the words sit inside
a button, whether the empty box overflows, whether a row was squashed. Reading the code
would not have found any of them; only running it did.

The two skipped checks are skips the suite declares itself: one needs a cookie banner
that a returning account no longer sees, and one needs a paid plan the test server does
not create.

### One thing I could not check

**What the real model says when it guides you.** The whole path is now tested end to
end — the question, the refusal check, the allowance, the evidence gathered, the model
call, the check that no code reaches you, the storage and what is drawn on screen. But
the model in the tests is a stand-in. It reads the real evidence and answers from it,
which is what makes the path real; it is not the model that will ship.

Worth ten minutes before launch with a handful of real questions on a real board —
*"what is my board missing"*, *"how do I connect these two"*, *"which card tells me when
a price drops"*, and one that must still be refused, such as *"what number should I
put"*. That needs a paid call, not a suite.

The published price of `gpt-5.6-luna` is still a placeholder, as it was before. The daily
limit works against whatever that table says; the number of messages $0.10 buys will move
when the real price is filled in.
