# The visual canvas, rebuilt as its own page

What changed, what it means, and what is still open.
Written on 15 August 2026. Rules followed: `docs/dashboard-test-monitor-rules.md`.

## What you asked for, and where it is

| You asked | Where it is now |
|---|---|
| Move the visual canvas to its own page | `/dashboard-test/monitor` |
| Put it in the side menu right after "Watchlists" | It is the next entry, called **Monitor** |
| No chatbot popup | There is no chat on the page at all |
| Remove the "Hilal Markets Assistant" box | It does not exist on this page |
| Two canvas sizes | "Fit to page" and "Full screen", both in the toolbar |
| Rebuild the canvas, cards, lines, boxes and popups | All new. Nothing was carried over |
| Animation from a known library | Motion One, through the shared motion layer |
| Full of icons | 14 new icons; every card, category, action and check has one |
| Same colours, fonts and spacing | Only existing tokens. A test now fails the build if a colour or font is written by hand |

## Score of the old canvas

The old canvas is the panel inside the Watchlist builder page. These are counted facts
about it, not opinions.

| What was measured | Number |
|---|---|
| Copies of the same board in one page | 2 |
| Copies of the same zoom toolbar | 2 |
| Copies of the same five review tabs | 2 |
| Separate ways to move around the same thing | 3 (a step bar, a side list, a tab row) — 13 buttons |
| Hidden cards shipped to the browser and never shown | 7 |
| "Start monitoring" buttons on one panel | 3 |
| Words of text on the canvas panel | 339 (912 across the whole builder) |
| The word "schema" on screen | 19 |
| The word "universe" on screen | 22 |
| Close buttons that are the letter **x** instead of an icon | 4 |
| Keyboard handlers on the board | 0 |
| Board cards that a screen reader is told anything about | 0 (no role, no level, no focus) |

### The scores

| | Score | Why |
|---|---|---|
| **UI** | **4.5 / 10** | Clean colours, but the same board, toolbar and tabs are drawn twice, and four close buttons are a typed letter. |
| **UX** | **4 / 10** | Three menus for one board, 13 buttons between them. Three "Start monitoring" buttons. Seven hidden cards shipped and never shown. |
| **User-friendliness for a beginner** | **3 / 10** | "ALL OF", "schema", "universe", "raw condition", "deterministic" on the screen. A beginner cannot read it. |

The single worst part: **the board had no keyboard model.** The cards were plain boxes —
no role, no level, no focus, and not one key handler on the board. You could still reach
a card's buttons by pressing Tab through every card before it, but nothing told you
where you were, and nothing announced the shape of the rule you were building. For
someone using a screen reader the board was a blank rectangle.

## What the new page does

The picture is the rule. Three cards are always there — the coins to watch, the group
that decides, and how you hear about it — and you hang conditions off the group.

* **Every card is one sentence.** "Candle moves by a percentage — goes up at least 5%."
  Nothing on a card is a field name.
* **The whole monitor is one sentence, always on screen.** "Watch every eligible coin.
  Tell me when all of these are true: … Send it to Telegram."
* **Nothing is filled in for you.** A comparison nobody chose stays empty and the card
  says so. The old builder let a missing comparison pass; here it is a blocking check.
* **Every rule this platform can run is in the list** — 512 of them, searchable by the
  same words a trader would have typed to the assistant. Nothing needs the assistant.
* **A rule that cannot run today is shown with the reason**, never hidden.
* **Nothing pretends to start monitoring.** The page says plainly that the board is a
  drawing and points at Watchlists.

### Moving things

| Action | With a mouse | Without a mouse |
|---|---|---|
| Add a condition | "Add condition" | `Ctrl` + `Enter`, type, `Enter` |
| Open a card's settings | Click it | Arrow keys, then `Enter` |
| Move a card to another group | Drag the circle on its left edge onto a group | "Sits inside" in its settings |
| Move a card on the board | Drag it | `Alt` + arrow keys |
| Remove a card | The bin button | `Delete` |
| Undo | The toolbar, or "Undo" beside the message | `Ctrl` + `Z` |
| Fit everything on screen | The percentage button | `Ctrl` + `0` |
| Leave full screen | "Fit to page" | `Esc` |

### Animation

Every movement says something and none of it repeats:

* a new card grows into place; a removed card shrinks away;
* a new wire draws itself from the parent towards the child, so you see what joined what;
* the wires from a card back to the coins light up when you point at it;
* cards travel to their new places when you press "Tidy up";
* a wire follows the pointer while you re-attach a card, and the cards that would accept
  it say so before you let go;
* "Show me" on a problem moves the board to that card and pulses it once.

When a person asks for less motion, all of it stops and the page still works. A test
proves it.

## Problems found on the way, and fixed

Every one of these was found while building, and every one is fixed and covered by a test.

| # | What was wrong | Where | What it meant | Fixed |
|---|---|---|---|---|
| 1 | The Builder's contract — 2 MB of JSON — was sent uncompressed on every builder page | `main.py` | Every page that draws a rule downloaded 2 MB. It is 64 KB compressed | Compression turned on for the whole application. Test: `test_the_big_contract_is_compressed_on_the_way_out` |
| 2 | Six entrance animations kept applying after they ended, which quietly made their element a boundary for anything full screen | `hilalmarkets-dashboard-v2.css` | **No page inside the dashboard could ever show anything full screen.** It silently became the size of its own section | The animations now stop applying when they finish. Test: `test_the_canvas_has_two_sizes` |
| 3 | A new menu entry with an unknown icon drew a blank green square | side menu | The "Monitor" entry had no icon and nothing failed | Icon added, plus a test over **every** menu entry: `test_every_side_menu_entry_has_an_icon_that_exists` |
| 4 | Dragging the board captured the pointer, so the click that followed went to the board instead of the button under it | `hm-monitor-board.js` | The "Undo" offered after removing a card did nothing | Panning now only starts on the empty board. |
| 5 | A search box with text in it eats the `Escape` key | `hm-monitor-test.js` | The condition list could not be closed with the keyboard once you had typed | Closing is handled first. Test: `test_escape_closes_the_list_even_with_something_typed` |
| 6 | The browser drew its own clear button on top of the page's | condition list | Two crosses on top of each other | The browser's is hidden |
| 7 | Dragging across the board swept a text selection over every card | canvas | The whole board turned green | Text selection turned off on the board |
| 8 | An unused, broken easing constant sat in the shared motion layer | `hm-motion.js` | Anyone using it would have got no movement and no error | Removed, with the reason written down |
| 9 | Fitting the board could shrink it to 54% | canvas | A person arrived at a board they could not read | Fitting never goes below a readable size |
| 10 | **Not one connector line was visible on the board** | `hm-monitor-test.css` | The product-wide `img, svg { max-width: 100% }` collapsed the drawing surface to **zero width**, because the layer it sits in has no width of its own. The lines were in the document, correctly placed and coloured, and painted nowhere. The board read as loose cards with no relationships at all | `max-width: none` and a real size on the layer. Tests: `test_the_connector_lines_are_actually_painted` |
| 11 | The lines were drawn in the hairline grey used for card borders | canvas | About **1.3:1** against the board — below the 3:1 a meaningful graphic needs, so even once painted they could barely be seen | Redrawn in `--t-copy` (7:1), 2px, with an arrow head showing which way the rule flows. Test: `test_a_connector_can_be_told_apart_from_the_board_behind_it` |
| 12 | The wire handles were 13px dots in that same invisible grey | canvas | Nothing findable to take hold of, which is why the lines "could not be dragged" | A 30px target around a visible ringed dot, blue on hover, and every card that would accept the wire lights up. Test: `test_the_drag_handles_are_big_enough_to_take_hold_of` |
| 13 | Both actions on a screened-coin card shared one row | `hm-dashboard-test.css` | Each had half a card for "See the evidence" and "Full Passport", so the wording wrapped or was cut on narrow cards | One full-width row each, at every width. Tests: `tests/browser/test_market_card_actions_e2e.py` |

## The yes/no rules that had become "always true" — found, traced and fixed

This was reported in an earlier pass as blocked. It is now fixed at the source, and the
earlier report of it was wrong in one number: **149 rules, not 397.** The first count
read the wrong key out of the compiled rule and so counted every rule in the registry.

### What was wrong

A rule declares two things that describe one fact: the comparison it starts on, and the
comparisons it allows. They were declared separately, so they could disagree — and 149
of them did. Each said *"my comparison is `happens`"* while listing only numeric
comparisons.

Nothing failed. The template builder settled the disagreement by itself: it replaced
`happens` with `at least` and put `0` on the other side. **"This event happened" became
"this number is at least zero", which is true of every number there is.** A rule that
named one event silently matched every candle.

Both defaults were the cause: a rule that never said how it was compared inherited
`happens`, so a deliberate declaration was indistinguishable from silence, and every
reader downstream had to guess.

### What was changed

| Where | Change |
|---|---|
| `schemas/strategy.py` | The two groups of comparison — the yes/no ones and the measured ones — are declared once, beside the list of comparisons itself. Four modules had been reading their own hand-written copy of "which comparisons take no number"; they all read the one declaration now. |
| `engine/capabilities.py` | The comparison and the allowed list are now settled **together**, in one place, so the contradiction cannot be written down. A rule that says nothing about how it is compared is **refused at start-up** instead of being assumed to be yes/no. |
| `engine/capabilities.py` | The 14 hand-written rules that had never said were given an answer, each read from its own wording: 9 are yes/no ("Stop **can** be placed", "**Exclude** stablecoins"), 5 measure a number ("**Minimum** 24h volume", "**Maximum** spread"). Every other rule family already declared correctly. |
| `engine/builder_templates.py` | It no longer picks a replacement comparison. A disagreement now raises, which is what `CLAUDE.md` requires: never substitute a nearest comparator, never fall back to a default. |
| `engine/builder_contract.py` | A yes/no rule no longer shows a "Value" box. All 512 rules used to offer one, including the 377 where nothing typed into it could ever be used. |

### What it means

* 149 rules stopped matching everything and now watch the event they name.
* The canvas offers **"happens" / "does not happen"** for those rules instead of
  "more than" and "at least", which were the only choices it had before.
* The planner no longer asks a trader to state a comparison for an event that has none.
* Adding a rule that forgets to say how it is compared now fails the build, with the
  sentence telling the author what to add.

### How it is held

`tests/unit/test_invariant_capability_comparisons.py` — 8 checks over **every one** of
the 502 rules, not over the one that was noticed: a rule allows the comparison it starts
on; a rule is either yes/no or measured and never both; a yes/no rule never compiles
into a measurement and carries nothing to compare against; a measured rule always has
something to compare against and never a bare true/false; the registry refuses silence;
the registry refuses a contradiction; and the template builder refuses rather than
choosing.

## How it was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 357 files |
| New page tests (server) | 27 pass |
| New invariant tests | 99 for the canvas vocabulary, 3015 for the rule comparisons — all pass |
| New browser tests, driving a real browser | 24 pass (20 canvas, 4 market card) |
| Browser console, page errors, failed requests during those tests | None. The test harness fails the run on any one of them |
| Keyboard-only: add, edit, re-attach, delete, undo, both sizes, both dialogs | Passes |
| Reduced motion | Passes; nothing moves for longer than a blink and nothing repeats |
| No sideways scrolling at 1440, 1024 and 760 pixels wide | Passes |
| Offline suites (`unit`, `engine`, `interpreter`, `services`) | 12,296 tests: **12,196 passed, 0 failed, 0 errors**, 100 skipped |

## Notes

* `/dashboard` is untouched. `/dashboard-test` is the parallel design path, exactly as
  `docs/dashboard-test-rules.md` set it up.
* The cache-busting key on every page moved together, because shared stylesheets
  changed. Leaving it would have served returning users the old, broken full screen.
* The draft you draw is kept in your own browser only. The page says so.

## One thing worth knowing

The 512 conditions carry the wording the rule registry already ships, and a lot of it is
written for engineers: 199 of them begin with the word "Deterministic", and 347 have
Title Case names like "Bearish Fair Value Gap". The new list softens this as far as it
can without inventing words — plain-language rules are shown first, the category is
written in ordinary words beside each one, and search understands a trader's own words.
Rewriting 502 descriptions is a content decision, not a design one, so it is left for
you rather than guessed at.
