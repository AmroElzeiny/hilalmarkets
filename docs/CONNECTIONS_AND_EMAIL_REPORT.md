# Connections, coin logos, and email notifications

What was asked for, what was done, and how it was checked.
The rules this work was held to are in `docs/dashboard-test-connections-and-email-rules.md`.

## In short

| Job | Done | Checked by |
|---|---|---|
| Stack the buttons on the Opportunity cards | Yes | Browser test at four screen widths |
| Every coin shows its logo | Yes | 47 tests over the whole family |
| Score `/dashboard/connections` and redesign it | Yes | 21 page tests + 11 browser tests |
| Email notifications, back end and templates | Yes | 124 tests, and every email rendered and read |

Nothing was left unfinished. Nothing is blocked.

---

## 1. The buttons on the Opportunity cards

**What was wrong.** The buttons sat in a row that wrapped. That is fine only while the
number of buttons never changes — and here it does. One card can have one button and
another can have four, because it depends on what the platform knows about that coin.
So the row wrapped differently on every card, and the last button on a wrapped line
stretched to fill the space left over. Two cards side by side showed the same button at
two different widths, in two different places.

**What was done.** The buttons are now a column: one under another, all the same width.

**Why this is bigger than the one page.** The Watchlists page uses the same code for the
same row and had the same problem. Both are fixed, because it is one rule, not two.

Every button is now at least 44 pixels tall, which is the size a finger needs.

---

## 2. Coin logos

The report said Mubarak had no logo. It was not one coin. It was two separate faults,
and together they explain every missing logo in the product.

### Fault one: eight places each knew a different answer

"Which pictures exist for this coin" was decided in **eight** different places in the
code, and each one knew a different part of the answer.

| Where | What it knew |
|---|---|
| The Watchlist history | The stored picture, and the shared icon catalogue |
| The Passport | Both |
| The home page | Both |
| The screening record | Only the stored picture |
| The live market list | Only the catalogue |
| **The Opportunities page** | **Only the catalogue** |
| Six templates | Each wrote its own version, two with the catalogue address typed in by hand |

A coin has up to two pictures: the one the platform saved when the coin's identity was
checked, and the one in a shared icon library. Small or new coins — like Mubarak — are
**not in the shared library**. The saved picture is the only one they have.

The Opportunities page never looked at the saved picture. It passed "no picture" every
single time. So those coins could never show a logo there, even when the platform was
holding a picture of them.

### Fault two: the browser gave up too early

The browser used one picture *instead of* the other, not *before* the other. If the
saved picture was gone or broken, nothing was drawn at all — no logo, no letters, and no
attempt at the library entry that would have worked. Nothing was even listening for the
picture failing to load.

### What was done

* One place now answers the question: `core/asset_logos.py`. It gives back **every**
  picture a coin has, in the order to try them.
* All eight readers ask it. None of them decides for itself any more.
* One shared piece of markup draws the logo. The six copies are gone, and with them the
  two hand-typed catalogue addresses.
* The browser now tries each picture in turn and only accepts one it has really drawn.
  If none works, the three letters stay — which is what they are for.
* The letters are a designed fallback, not a failure. They are always there from the
  start, so a coin never flickers through an empty box.

**One more thing found and fixed.** A saved picture address that was not `https` was
being put on the page. The browser blocks those, so it showed a broken-image icon —
which looks worse than the letters it replaced. Those are now treated as "no picture".

### How it was checked

47 tests. They cover: a coin in the library, a coin only on its own record (this is the
Mubarak case), a coin with neither, a coin whose name cannot safely go in a web address,
and the coins whose names exchanges write with a number in front (`1000SHIB`). Plus
tests that fail if any file starts writing its own version of the answer again.

---

## 3. The Connections page

### The score

`/dashboard/connections` as it is today:

| | Score | Why |
|---|---|---|
| **UI** | **5.5 / 10** | Clean and on-brand, but says the same thing twice and hides the real state |
| **UX** | **4.5 / 10** | Does not answer "is it working" or "what will I be told" |
| **User-friendliness** | **4 / 10** | Machine words throughout; a beginner cannot act on it |

This is about the page, not about anyone who built it.

### The 14 problems, and what answers each one

| # | The problem | The answer in the new page |
|---|---|---|
| 1 | Three tiles at the top repeat the two cards below. The same status, twice on one screen. | The tiles are gone. One card per channel, said once. |
| 2 | The page has three names: the address says "connections", the title says "Notifications", the heading says "Delivery channels". | One name: **Connections**. |
| 3 | A channel that is not available says "Disabled" and stops. That is a state, not an answer. | Each unavailable channel says **why**, and **what would change it**. |
| 4 | Nothing says what you would actually be told about. | A whole section: "What you will be told about", with all seven kinds in plain words. |
| 5 | There is no email at all — the one way to reach everybody. | Email is a full channel now, beside Telegram. |
| 6 | Machine words: "No delivery recorded", "Last delivery", "Not linked". | "None sent yet", "Last message", "Not linked yet". |
| 7 | Message groups named "Evidence" and "Screening changes". | "Shariah status changed", "Nearly there", "Everything you asked for happened". |
| 8 | Six buttons in one row on the WhatsApp card, one of them "Clear error". | A column of buttons. No engineer's controls. |
| 9 | No way to find out whether a channel really works. | **Send me a test** really sends a real email. |
| 10 | The Telegram fallback prints a raw command and leaves you to select it. | Three numbered steps, and a button that copies the command for you. |
| 11 | The notification settings are on a different page. | The switches are here. The link to timing is one clear button. |
| 12 | Status is a coloured badge and nothing else. | Colour **and** a word **and** an icon, plus one line saying what the state means. |
| 13 | A brand-new account sees three cards and no starting point. | A line at the top when nothing is set up, saying so and what to do. |
| 14 | The WhatsApp form is drawn even when WhatsApp does nothing. | Nothing is drawn that cannot be used. |

### What the new page is

Three questions, in the order a person asks them:

1. **Where will you be told?** One card for each way. In the dashboard, Email, Telegram,
   WhatsApp.
2. **Is it working?** A switch that really saves, and a test that really sends.
3. **What will you be told about?** Seven kinds of message, each in one short line.

**One thing the live page could not say.** "Connected" and "switched on" are two
different facts. You can have Telegram linked and still have its messages turned off.
The old page showed one word for both, so somebody could be certain they would be told
and hear nothing. The new page keeps them apart: *Connected, but switched off*.

### Design

* Every colour, space, shape and typeface comes from tokens that already existed. No new
  main colour. No new spacing. No third typeface.
* Apple green appears only where it means "this is working".
* Motion comes from the shared motion file. The switch knob travels, so you see **which
  way** it moved. Cards lift on hover. Nothing loops, nothing flashes.
* Ask for less motion and it all stops. The page still works completely.
* Every state carries colour **and** words **and** an icon.

---

## 4. Email notifications

### The channel

Email is now a real way to be told, equal to Telegram. Not a copy of one.

* It carries **every** kind of message Telegram carries: a Shariah status changing, a
  setup coming close, a setup completing, an opportunity moving on, a check that could
  not run, and account notices.
* It goes into the same queue, is retried the same way, gives up after the same five
  tries, and records the same failure reasons.
* It is sent from **no-reply@hilalmarkets.com**.
* It only goes to the **confirmed** address on the account. An address somebody typed
  but never confirmed is not one they have been shown to own, and an alert names the
  coins a person watches.
* It is **off until you turn it on**. It never turns itself on because an address exists.
* Email is only offered when the platform can really send it. A channel you can switch on
  that nothing delivers is worse than one that is honestly missing, because the silence
  afterwards looks like "there was nothing to tell you".

### The words in an email

An email never writes its own version of an alert. The facts come from the same place
Telegram reads, so the two can never tell one person two different things about one
event. Only the layout differs.

Three rules that cannot be broken, and are tested:

* A Shariah status is **quoted**, never concluded. What appears is the status that was
  published and stored when the alert was raised, with the date and the methodology
  beside it.
* A missing reading is shown as missing. "We could not read it" — never "We saw 0".
* If you set no entry, stop or target, the email says so. Nothing is filled in for you.

### The templates

Every email now comes from **one design**. The one-time code email and the sign-up email
were rebuilt on it too, so every email a person receives looks like the same product.

| Template | What it is |
|---|---|
| Setup happened / nearly there / forming / moved on / could not check | The market alerts |
| Shariah status changed | The screening change |
| About your account | Trial and plan notices |
| Your sign-in code | Rewritten |
| Confirm your email | Rewritten |
| Your account is ready | New — the sign-up welcome, sent when an account is finished |
| Your email is working | New — the test message |
| Your access is now X | Rewritten |

Built for real email programs, not for a browser:

* Tables and inline styles only. No stylesheet, no scripts, no web font needed.
* **It reads with pictures switched off.** Nothing in it is a picture. A status is a
  colour **and** a word **and** a plain text mark, so switching pictures off costs
  nothing.
* Dark mode cannot swallow the text. Every part paints its own background and its own
  colour.
* One column, 16-pixel text, buttons tall enough to press with a thumb.
* Every email says why it arrived and where to change it.
* Every email says the product does not trade, hold money, or promise returns.

### How the emails were checked

Every template was **rendered and looked at**, at phone width and at desktop width — not
assumed. That found and fixed:

* A long web address pushed the email sideways on a phone. Fixed, and the address is now
  a button that says "Open the Evidence Passport" instead of a line of unreadable text.
* The screening-change email said a status had changed and then showed nothing about it.
  It now shows the coin, the new status, when it was reviewed, and under which
  methodology.
* The title was printed twice, once in the header and again below it.
* The status mark read as a stray letter stuck to the front of the sentence.

---

## Other problems found on the way, and fixed

Each of these was found while doing the work above. All are fixed and tested.

| What was wrong | Why it mattered | Fixed |
|---|---|---|
| The "nothing found yet" panel on the Opportunities page had **no layout at all**. Every rule for it was written for the Watchlists page, and Opportunities uses the same names. | This is what **every new account** sees. It showed bare text with the arrows stacked underneath. A test existed, but it only checked the links were there — not that they were drawn. | Rules now apply to both pages. A new test measures the drawing, not the markup. A second test fails if any shared rule is ever written for one page again. |
| Alert buttons were labelled "🔄 View lifecycle", "📊 Dashboard", "🔕 Mute symbol". | The brand rules exclude these little pictures, and "lifecycle" is a word from inside the machine. These labels became the buttons in the new emails, which is where it showed. | "See what happened", "Open dashboard", "Stop messages about this coin". Written once now, not twice. |
| Buttons across the whole design path were 40 pixels tall. | The path's own rule says 44. Four pixels short, on every page. | 44 everywhere. |
| The sender name on every email was `HilalMarkets`, with no space. | The brand rules say the name in prose is **Hilal Markets**. The inbox is the most visible prose there is. | Corrected in both environment files. |
| Saving the settings page would have silently switched email off. | That form sends the whole list of channels, and anything not ticked is dropped. Email had no tick box. Somebody would find out by not hearing from us. | Email is on the settings page too. |
| A saved logo address that was not `https` was written into the page. | The browser blocks it and shows a broken picture — worse than the letters it replaced. | Treated as "no picture". |
| A dead piece of code carried a hand-typed catalogue address. | Nothing used it, so nothing would notice when it went out of date — and it would come back wrong the day somebody revived it. | It uses the shared markup now. |
| The queue for account emails required an administrator's action behind every message. | So it could only ever hold administrator notices. The welcome a person sends themselves by signing up had nowhere to wait, and would have been a template nothing sends. | The link to an administrator's action is now optional. The welcome goes through the same queue, with the same retries, and can only ever be sent once per account. |

---

## What was measured, and how

| Check | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 365 files |
| Unit, engine, interpreter and services suites | Pass |
| Connections page tests (21) | Pass |
| Email channel tests (12) | Pass |
| Email content tests (112) | Pass |
| Coin logo tests (47) | Pass |
| Connections browser tests (11) | Pass, clean console |
| Opportunities browser tests | Pass |
| Sideways scrolling at 1440, 1024, 760 and 390 | None |
| Colour strength, measured not assumed | Every pair at least 4.5:1 |
| Keyboard only, every control and every popup | Works |
| Reduced motion | Works, page complete |
| Every email rendered and read | Yes, phone and desktop |

### Two things that are stated, not measured

* **Real email sending was not tried.** The tests use the platform's built-in test
  outbox, which records the exact message that would be sent. Sending through the real
  provider would put a real message in a real inbox. Everything up to the provider is
  checked; the provider itself is not.
* **How each mail program draws the email** was checked in a real browser, not in
  Outlook, Gmail and Apple Mail themselves. The design uses only what those programs
  have supported for years, but that is a reasoned choice, not a measurement.

## One thing to know before this ships

The database needs its new migration run (`b3f81c07d5a4`). It does two things: it lets
the database record an email delivery, and it lets an account email exist without an
administrator behind it. Until it runs, sending an alert by email and the sign-up welcome
will both be refused by the database. Nothing else changes.
