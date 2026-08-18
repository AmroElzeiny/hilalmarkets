# The three account pages: what was wrong, and what replaced them

Three pages were looked at, scored, and rebuilt on the `/dashboard-test` path:

| Old page | New page |
|---|---|
| `/dashboard/subscription` | `/dashboard-test/subscription` |
| `/dashboard/settings` | `/dashboard-test/settings` |
| `/dashboard/support` | `/dashboard-test/support` |

The old pages still work. Nothing about them changed except the real faults listed
below, which were fixed **in the shared code**, so both the old page and the new one get
the fix.

The rules the new pages were built against are in
[`dashboard-test-account-rules.md`](dashboard-test-account-rules.md).

---

## 1. Scores for the old pages

Out of 10. These are a judgement, but every point taken off has a named reason below.

| Page | Look (UI) | Use (UX) | Easy for a beginner |
|---|---|---|---|
| Subscription | 6.5 | 5.5 | 4.0 |
| Settings | 5.5 | 4.0 | 3.5 |
| Support | 4.5 | 3.5 | 4.0 |

The lowest score everywhere is "easy for a beginner". That is the same fault three
times: the pages were written in the words the code uses, not the words a person uses.

---

## 2. What was wrong, page by page

### Subscription

| # | What was wrong | Why it matters |
|---|---|---|
| S1 | It said which plan you are on, and never what the plan **lets you do** or how much of it you have used. | You cannot tell whether you need to pay more. That is the only question this page exists for. |
| S2 | "Access source: Trial" and "Renewal: No automatic renewal", in three separate boxes. | "Access source" is a name from inside the database. Three boxes said one thing. |
| S3 | The paying popup was one long scroll. The price was written once at the top, then nine address boxes. | The last thing you read before leaving our site to pay was a postcode. |
| S4 | Payment history printed the stored word: "Provider Unavailable", "Creating". | These are machine words about a person's money. |
| S5 | Some buttons were switched off with no reason beside them. | "You cannot press this" is a state, not an answer. |
| S6 | The comparison table (16 rows, 3 columns) was always open on the page. | A wall of text. |

### Settings

| # | What was wrong | Why it matters |
|---|---|---|
| G1 | **Two settings the product really reads had no control at all.** `lifecycle_enabled` ("tell me when an opportunity ends") and `muted_symbols` ("stop telling me about this coin") were read by the alert system and could not be set from anywhere. | Two useful things existed and nobody could reach them. |
| G2 | **The page and the product disagreed about a brand-new account.** The page worked out its own starting values inside the template. For a new account it drew Telegram *and WhatsApp* as already switched on, while the product believed you were only on the in-app notice and Telegram. | The page made a claim about your account that was not true until you happened to press Save. |
| G3 | Turning off an exchange stops **every Watchlist that uses it**. The page said "Highlighted providers are available to Scanner and Watchlist universe checks". | That sentence describes machinery. It does not warn anybody. |
| G4 | One long form with a Save button at the top. | By the time you finish, the Save button is off the screen. Saving reloaded the whole page. |
| G5 | The hours were 24 identical boxes with no explanation. Choosing none means "any hour", and nothing said so. | Somebody could read the empty grid as "never" and switch their alerts off by accident. |
| G6 | Jargon in nearly every label: "Near-miss threshold", "Alert behavior", "Evidence-change delivery", "Spot market providers", "100% match notifications". | The reader is a beginner. |
| G7 | "Every Day" was a choice inside the list of days, so you could pick "Every Day" and "Monday" together. | Two answers to one question. |

### Support

| # | What was wrong | Why it matters |
|---|---|---|
| H1 | The first box was **"Subject"**. | Summarising a problem in a few words is the hardest question you can ask somebody who is stuck. |
| H2 | Nothing tried to answer the question before asking you to write. | Most people writing in about messages can fix it themselves in two presses. |
| H3 | **Every request was sent as "general", and stored as normal urgency** — even a report that no alert arrived. | The rule that raises urgent kinds already existed in `services/support.py` and this route ignored it. |
| H4 | You could not read back what you had written. The list showed a subject line and a coloured badge. | You could see that "a thing" was open, not what you had asked. |
| H5 | A chosen picture could not be taken back out. | You had to start again. |
| H6 | After sending, the whole page reloaded half a second later. | Whatever you were reading was thrown away. |
| H7 | The result was written into a screen-reader-only box. | A sighted person only got a toast that vanished. |
| H8 | Machine words on screen: "Your tickets", "Pending User", "In Progress". | Same fault as the other two pages. |

---

## 3. What the new pages do instead

### Subscription

- **What you have comes first**, then what it lets you do, with bars showing how much
  of each allowance is gone. A bar is only drawn where both numbers are real.
- How your access ends is **one sentence**, not three boxes.
- Every price and every "not for sale yet" comes from `core/plans.py` — the same file
  the public pricing page reads. Nothing is typed into the page.
- A plan nobody can buy **shows no price at all**, not even in the page source, and says
  why it cannot be chosen.
- **Paying is three steps in one popup**, with the exact amount and the exact renewal
  sentence on screen the whole way through. It refuses an unfinished step by marking the
  boxes that are missing, and the final button stays shut until a payment method is
  chosen and the terms are ticked.
- Payment history is in plain words: "Paid", "Did not go through — no money was taken".
- The comparison table is behind a "put the plans side by side" disclosure.

### Settings

- **Every control saves on its own, the moment you use it.** There is no Save button to
  forget. A bar at the top says "Saving…", then "Saved", or "Nothing was saved".
- **Every setting saves for real.** This is proved by test, not by inspection: 16
  settings are each changed and then read back out of the stored record, and the
  product's own reader is asked whether it agrees.
- The two missing settings now exist: **"tell me when an opportunity ends"** and
  **"coins you would rather not hear about"**.
- Silencing "BTC" now silences Bitcoin on every pair. Before, the comparison was against
  the whole market symbol, so you would have had to guess the second half of the pair.
- The exchange setting says its consequence **before** you act: *"Turning one off stops
  every Watchlist that uses it."*
- The hours are grouped into night, morning, afternoon and evening — one press each —
  and the page says in words what your choice means, including "no hour is picked, so we
  can tell you at any time of day".
- "Every day" is now a separate switch, so it cannot fight with a single day.
- You can hear a sound before choosing it, played by **the same code that plays the real
  notice**, so the preview cannot be a different tone from the real thing.
- Switching an exchange off **asks first**, in a real popup, because it is the one
  control here that is not undone by pressing it again. The last exchange cannot be
  switched off at all: with none chosen the server quietly falls back to the first one,
  and a person cannot see a fallback happen.

### Support

- **Four things you can fix yourself come first**, each linking to the page that fixes
  it. Every one of those links is checked by test to lead somewhere that really loads.
- No subject box. You **press what it is about**, and that writes the subject and sets
  the real stored category.
- What happens next is on the page before you send: *"We reply by email, usually within
  one working day."*
- Pictures can be dropped in, are checked before they are sent, and **each one can be
  taken back out**.
- Your message appears in your own list **straight away**, and you can open it and read
  your own words back.
- Nothing reloads. Nothing is thrown away.

---

## 4. Problems found on the way, and fixed

These were not part of the request. They were found while working, so they were fixed.

| What it was | Where | How it was fixed | Proof |
|---|---|---|---|
| **The support endpoint accepted a request with no form token.** It created a record, saved uploaded files and sent two emails on the strength of a session cookie alone. Every other route on that file checks the token. | `api/routers/dashboard_api.py` | It checks the token now. The dashboard already sent one, so nothing that legitimately used it noticed. | A test sends a request with no token and asserts it is refused. |
| **Two places decided how urgent a support request is, and disagreed.** One raised three kinds to high; the other stored everything as normal. | `services/support.py`, `api/routers/dashboard_api.py` | One function, `support_priority`, called by both. | Tested across all five kinds. |
| **The Settings page and the product disagreed about a new account** (G2 above). | `api/routers/dashboard.py`, the live template | Both pages now ask `NotificationPreferenceService`. | Tested on **both** pages at once. |
| **Silencing a coin could never work.** The stored word was compared against the whole market symbol, so "BTC" never matched "BTC/USDT". | `services/notification_preferences.py` | One comparison owner, `symbol_is_muted`, that matches the pair or the coin. | Tested across four pairs, plus the exact-pair case. |
| **The switch control existed twice.** | `hm-connections-test.css` | Moved into the shared sheet as `.t-switch`, used by both pages. | The Connections suite still passes unchanged. |
| **The jump bar was about to exist twice.** Two pages need "mark the link for the section really on screen", and the second one had the markup with no behaviour behind it. | new `static/hm-jump.js` | One owner, `followSections`, used by both. | Tested in a browser by scrolling and reading the marker. |
| **`w-quiet` matched nothing on two pages.** It was declared only for the Watchlists and Opportunities pages, so "Unlink Telegram" on Connections was drawn exactly as loudly as the button beside it. | `hm-watch-test.css` → `hm-dashboard-test.css` | One shared `.t-action.is-quiet`. | — |
| **Four kinds of control were under the 44×44 this path's own rules ask for**: the two navigation controls and the shared round icon button were 40px, and the hour buttons were 32px. | `hm-dashboard-test.css`, `hm-account-test.css` | All 44px. | Measured in a real browser, with the check widened so it cannot miss them again. |
| **Text in one stylesheet was corrupted** by an earlier bulk rewrite on Windows — 16 lines of comment headings turned into Cyrillic. | `hm-dashboard-test.css` | Repaired line by line, and the whole of `static/`, `templates/` and `docs/` scanned to prove nothing else is damaged. | Scan reports zero suspect files. |
| **The rule for saving a setting lived inside a route handler.** A second way to save was about to be added beside it. | new `services/account_settings.py` | One owner. The form handler and the new endpoint both call it. | A test asserts both routers use the same class and the same word lists. |
| **The plan page could have opened a paying popup with no way to pay.** Arriving at `?plan=trader` was checked against "this account does not already hold that plan", which stays true while paid checkout is switched off entirely. | `api/routers/dashboard_test.py` | Checked against the plan card's own button instead. | Tested with checkout switched off, and with a plan nobody named. |

### Four found by looking at the finished pages, not by reading the code

Screenshots of the real pages were taken and read. Every one of these passed all the
tests and still looked wrong.

| What it looked like | Why | Fixed |
|---|---|---|
| Two allowance tiles showed a large **"0"** — "Market checks a month 0", "Days of history kept 0" — on a plan whose own card promises "1 quick scan per week". | The free plan stores those two limits as `0`, meaning "not in this plan". A big zero reads as something that has run out. | A limit of zero is not drawn at all. The plan card already says in words what is and is not included. |
| "Messages a day: **2**" on a plan whose real cap is two a *week*. | Two caps are stored for the same thing and the page showed the first one. Saying "2 a day" tells somebody they can have fourteen. | Both caps are compared over the same length of time and only the one that really bites is shown. |
| The included-features list **inside the checkout popup** fell back to browser bullets, with the tick stacked above every line. | A `<dialog>` sits outside the page wrapper, so a style scoped to the page misses it. | Scoped to the popup as well. Now tested by reading the computed layout. |
| "Morning" was as wide as all six of its hour buttons. | The label is also a shared `.t-action`, and that rule spreads buttons evenly across a row. It reaches the element through an `:is()` that includes a tag name, which quietly outranked the plainer override. | Named `.t-action` in the override so it wins. Tested by measuring the label's share of the row. |
| A **192-pixel empty hole** under the hours, inside a card. | Turning a row's main axis vertical without turning off its `flex-wrap` let the browser hand the last item far more height than its content. | `flex-wrap` and `justify-content` are both turned off with the direction. Tested by measuring every row for a hole. |

### One thing worth naming that is not a defect

The support page's template is a single 2,748-character line, and the settings page has a
1,565-character line. That is hard to work on but it is not something a user sees, and
both files keep working. The new pages are written normally.

---

## 5. How it was checked

| Gate | Result |
|---|---|
| `ruff check src tests scripts` | Clean |
| `mypy src` | Clean, 366 files |
| Unit, engine, interpreter and services suites | Pass |
| New page tests — subscription 35, settings 52, support 35 | 122 pass |
| New rule tests (`test_invariant_account_settings.py`) | 36 pass |
| Real browser, real server, real database | 41 pass |
| Existing browser suites, after the shared changes | Pass |
| Old pages still render and still save | Tested, pass |

The browser tests are the ones that matter most for "no bugs". The harness fails a test
on **any** console error, **any** page error and **any** failed request, so a clean run
means a clean console. They check, in a real Chromium:

- every page reads on a phone with no sideways scrolling;
- every text colour is at least 4.5:1 against what is behind it;
- every control is at least 44×44 pixels;
- a setting really saves and is **still there after a reload**;
- a message really sends and is **still there after a reload**;
- the paying popup really opens, really walks through its steps, really refuses an
  unfinished one, and gives the keyboard back on `Escape`;
- with "reduce motion" switched on, everything still arrives and everything still works.

The paying popup needed a second test server, because the main one runs with paid
checkout switched off. That server is started by the test harness with the in-repository
stand-in payment provider, which is refused on any real deployment.

---

## 6. How good are the new pages

The request set a bar of 9.9 out of 10. A design score is a judgement, and I will not
dress one up as a measurement. What *can* be measured was measured, in a real browser,
and every gate passed:

| Gate | Result |
|---|---|
| Text contrast | Every pair checked, 4.5:1 or better |
| Control size | Every control 44×44 or larger |
| Keyboard | Every control reachable and operable |
| Popups | Real dialogs — focus trapped, `Escape` closes, focus returns to what opened them |
| Reduced motion | Everything arrives, nothing moves, everything still works |
| Phone (390px) | No sideways scrolling on any of the three |
| Browser console | Clean. The harness fails a test on any error at all |
| Settings that really save | 16 out of 16, checked against the stored record **and** against the product's own reader |
| Made-up content | None. Every price, plan, limit and state comes from a real record |

Two honest marks against them:

1. **The comparison table still uses product names** — "Condition proof", "Opportunity
   Journeys", "Why wasn't I alerted?". These come from `core/plans.py` and the public
   pricing page shows the same words, so rewriting them here would make the two pages
   disagree. Changing them is a product-naming decision, not a page decision.
2. **The allowance bars only cover what can really be counted.** Today that is
   Watchlists running. The others show what the plan allows and say so, rather than
   drawing a bar against a number nobody measured.

## 7. What is not done

| What | Why |
|---|---|
| Only these three pages are redesigned. | That was the request. The rest of `/dashboard-test` was built in earlier passes. |
| A real card payment is never made. | Real provider credentials are not available here, and taking a real payment is not something to do in a test. Everything up to the moment the browser leaves for the payment company is driven and checked. |
| The old pages' very long template lines are left as they are. | Rewriting them is a formatting change to shipped pages with no user-visible benefit, and it would make the change harder to review. |
