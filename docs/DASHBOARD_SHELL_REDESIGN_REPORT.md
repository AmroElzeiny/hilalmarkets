# The dashboard side menu and topbar — what changed

18 August 2026. The rules this work was held to are in
`docs/dashboard-shell-redesign-rules.md`, written before any code.

The **shell** is the side menu and the bar across the top. They are on every signed-in
page, so every fix here is a fix on nine pages at once.

---

## 1. In one table

| What | Before | Now |
|---|---|---|
| Side menu, open | One flat list, 11 rows, 4 groups | 9 rows in 3 groups, each with its own icon chip, a moving highlight, and an account card that opens a small menu |
| Side menu, minimized | Icons only. **Every link lost its name**, so a screen reader read nine identical "link". No way to sign out. | Same rows, names kept for screen readers, a flyout name for the eye, and sign-out still reachable |
| Topbar | A search box and two buttons. It never said which page you were on. | Says where you are, has search with a keyboard shortcut, and draws whatever action the page below it asked for |
| Who owns the menu | Three files, two saved settings, one class | One file, one saved setting |
| Menu icons | Its own set of 11 files nothing else used | The product's one icon table |

---

## 2. The bug that started it

The minimized menu hid each link's words with `display: none`.

That does not only hide the words from the eye. It removes them from what a screen reader
reads. So a blind person using the minimized menu heard **"link, link, link"** nine times
and could not tell Settings from Support.

The fix is to move the words off the screen instead of removing them. The name is still
there for anybody who cannot see the icon, and a small label now slides out beside the
icon for anybody who can. A real browser test measures the name the browser computed —
reading the stylesheet cannot prove this.

---

## 3. Everything asked for

| Asked | Done |
|---|---|
| Remove **Trading Assistant**; its page not reachable | Menu entry gone. Both of its addresses answer "not found". The one-time scan itself still works from inside the builder, and the Telegram buttons that used to pass through the removed address point straight at it now |
| Remove **Home**; page not reachable and deleted | Template deleted. `/dashboard` sends a person to Today |
| **Monitor** → **Create a monitor** | Done, with a plus icon |
| **Watchlists** → **Monitors** | Done. The page it opens is called Monitors too |
| Relevant menu icons | Every entry uses the shared icon table |
| Today: **Start a list** → **Create a monitor**, going to the canvas | Done |
| Today: **Draw a new list** → **Monitors**, going to the monitors list, new icon | Done. It is hidden when the button beside it already goes there |
| Today: remove the coloured edge on the warning line and the list rows | Done, and for every box of that kind on the page |
| Monitors page: **New Watchlist** moves to the topbar | Done, as **Create a monitor**. It is now there even when the page is empty |
| Monitors page: remove the coloured edge on the "danger" working line | Done, and for every tone, so one page has one look |
| Opportunities: **Your Watchlists** moves to the topbar | Done, as **Monitors** |
| Connections: real app marks | The official Telegram and WhatsApp marks, a real envelope, and a real dashboard mark instead of a bell |
| Settings: the same, across the page | Done |
| Support: a send mark that is not hand-drawn | Replaced with the standard one |
| Assistant reads as **AI**, with a one-line tag that slides for ever | Done. See section 5 |
| `/contact`: remove **Help Center** | Removed, rebuilt, and the built file that visitors download was checked |
| Dashboard served on `app.hilalmarkets.com/` | Done. See section 6 |

---

## 4. What the menu and the bar do now

**Movement, and why each piece is there.** Nothing moves for decoration.

| Movement | What it tells you |
|---|---|
| One soft highlight travels between rows | where you were, and where you are now |
| Rows arrive one after the next, once | the menu has finished loading |
| The icon lifts a little under the pointer | this row can be pressed |
| The flyout name unfolds from the menu edge | this name belongs to that icon |
| The menu widens and narrows smoothly | it moved; it did not jump |

Everything stops for anybody who has asked their computer for less motion.

**Keyboard.** Everything is reachable. `Ctrl K` (or `⌘ K`) jumps to search, and the bar
says so. `Escape` closes the account menu and the phone drawer. The flyout name appears
for the keyboard, not only for a mouse — a keyboard user has no pointer to hover with.

**The topbar carries the page's own actions.** A page no longer draws its own button in
its own heading. It *says* what belongs at the top and the shared bar draws it. That is
why the create button is the same shape and the same size everywhere, and why it is
present on an empty Monitors page — which is exactly the screen where it used to be
missing.

Every action, including the page guide, keeps its word on a wide screen and shrinks to
its mark when the bar runs out of room. The word then comes back as a small label under
the pointer or the keyboard, so nothing ever becomes a circle with no name.

---

## 5. The assistant in the corner

Three problems, one change:

1. It looked like a chat with a person. Now it is a speech bubble with a small spark in
   it — the mark that means "written by software". Not a robot and not a brain: the brand
   rules forbid both by name.
2. Nothing said who it was. A tag above it now reads
   **"Hilal — your AI assistant · sees the page you are on · here to help as you go"**.
3. The line is longer than its box, so it slides from right to left and starts again for
   ever.

The claim "sees the page you are on" is true: the page it is on, the part of it on screen
and the coin being looked at are sent with every message. A test checks that this is still
being sent, so the promise cannot quietly become false.

Moving text has to be stoppable. It stops when a pointer reaches that corner, it stops
when the keyboard does, and it never starts at all for somebody who has asked for less
motion. The whole sentence is also the button's own name, so a screen reader reads it
once, in full, without any of it moving.

---

## 6. `app.hilalmarkets.com`

One deployment answers on two names.

| Address | What it opens |
|---|---|
| `hilalmarkets.com/` | the marketing site |
| `app.hilalmarkets.com/` | the dashboard |

This is read from `APP_BASE_URL`, which the product already used for its own links. The
root only becomes the dashboard when the two names are really different — a local run and
any single-domain install have both names the same, and there the root stays the landing
page. Without that rule, every local visitor would have been sent to sign-in.

**To turn it on:** point `app.hilalmarkets.com` at this deployment in DNS, give it a
certificate, and set `APP_BASE_URL=https://app.hilalmarkets.com` (already the value in
`.env.production.example`). Nothing else to change.

---

## 7. Problems found on the way, and fixed

None of these were asked about. All of them are fixed.

| Found | Why it mattered | Fixed |
|---|---|---|
| Three files decided whether the menu was minimized, from **two different saved settings** | Pressing the button changed one setting; the other kept its old answer for the next page. The menu looked like it forgot what you chose | One file, one setting |
| `dashboard.js` still ran a menu controller aimed at markup that had not existed for a long time | It did nothing anybody asked for, and one thing nobody asked for: it re-decided the menu state on every page load | Removed |
| The menu had a **second icon system** — 11 files nothing else used | A new entry needed its icon added in two places; getting it wrong drew an empty square and nothing reported it | Removed. The 11 files are deleted |
| **Five menu entries opened the old page, not the redesigned one** | See below. This was the most serious thing found | Those five routes have names of their own now |
| The side menu sent people to the **older** Opportunities and Plan pages, while Today sent them to the newer ones | Two different Opportunities screens depending on how you got there | Every menu entry opens the same page Today links to |
| One shipped colour was not in the approved palette (`#6f8f33`, on the two builder start cards) | It is the first thing a person sees when building | Changed to the approved green |
| Two more off-palette colours on the redesigned Connections page | Nothing was checking that page, because the menu opened the older one | Changed to the approved hairline, and **every** redesigned page's stylesheet is now in the palette test |
| No way to sign out of a minimized menu | The sign-out form was one of the things `display: none` removed | The account button opens a menu in both states |
| The print rules named `.app-sidebar` and `.app-topbar` — **classes that have never existed** | Printing a Shariah evidence report printed the whole side menu and the search box down the first page | Print rules now name the real shell |
| The icon table had the same key twice (`moon`) | The first one was dead code nobody could reach | Removed |
| Six Telegram buttons pointed at a page being deleted | A button inside a chat message lives as long as the message. They would have led to "not found" from months-old chats | Pointed at the builder's own address |

---

### The worst one: five menu entries opened the wrong page

Two files each had a page function with the same name — `screened_market_page`,
`watchlists_page`, `connections_page`, `settings_page`, `support_page`. One is the older
page, one is the redesigned page.

The web framework turns a name into an address by taking **whichever file was loaded
first**, and the older file is loaded first. So the side menu said "Monitors" and opened
the *old* Watchlists page. Halal Assets, Notifications, Settings and Support did the same.

This was found only because a browser test printed the real addresses the menu had drawn.
It matters more than it sounds: the rename to **Monitors**, the create button moved into
the topbar and the removed coloured edge are all on the redesigned page — and nobody using
the menu would ever have seen any of them.

The five redesigned pages have names of their own now, and a test fails the build if any
two routers ever answer to one name again.

## 8. How it was checked

| Check | Result |
|---|---|
| `ruff` over `src`, `tests`, `scripts` | passes |
| `mypy src` | passes, 368 files |
| `pytest tests/unit` | passes |
| `pytest tests/integration` for every page touched | passes |
| `scripts/check_release_invariants.py` | passes |
| `pytest tests/browser/test_dashboard_shell_e2e.py` — 17 tests in a real browser | passes |
| `pytest tests/browser/test_main_dashboard_e2e.py` | passes |
| Landing site type-check and build, then the built file checked for the removed row | passes |

The browser tests are the ones that matter most here, because nothing in this layer
reports its own failure. They measure the name a screen reader would get, the highlight
really moving from one row to another, the tag really sliding and really stopping, the
contrast of every word, that no control is under 44 pixels, that the page never scrolls
sideways, and that the phone drawer opens and closes. The shared browser harness fails a
test on any console error, so "nothing throws" is enforced rather than assumed.

## 9. Three failing tests that are not from this work

`tests/integration/test_dashboard_test_settings.py` (two tests) and
`test_dashboard_test_subscription.py` (one test) fail. They are **not** caused by this
work, and they are not fixed here:

* All five files involved — the two pages, their stylesheet and their two test files —
  are **untracked**. They do not exist in the last commit at all.
* The settings page was **rewritten while this work was in progress**. The version this
  work edited said "Most messages in one hour" and carried a `why_not` explanation for a
  channel that cannot be chosen. The version on disk now says "Maximum messages per hour"
  and has no `why_not` anywhere.
* The three assertions are about the explanation under four settings, the wording for an
  unavailable channel, and one sentence about how a plan ends. This work never touched
  any of them. Its only edits to that page were two icon names, and both are still there.

Writing the missing copy would mean guessing at sentences somebody is in the middle of
writing, into a file that is changing. That is the one case these rules leave alone.

## 10. Left undone, on purpose

The word **Watchlist** still appears in email, in Telegram and WhatsApp messages, and on
the public website. Renaming it there is a separate, deliberate pass: those are a
different audience with their own copy tests, and a half-finished rename across them would
read worse than the old name does. On the redesigned screens — Today and every
`/dashboard-test` page — the word is **monitor** throughout, so a button and the page it
opens always agree.
