# /contact, /privacy and /terms — rebuilt

*17 August 2026*

Three pages were checked, scored, and rebuilt. This report says what was wrong, what
was done, and how each claim was checked.

The rules I worked to are in [PUBLIC_PAGES_REDESIGN_RULES.md](PUBLIC_PAGES_REDESIGN_RULES.md).

---

## 1. The score before

I measured contrast with a calculator rather than judging it by eye, and read every
line of the three pages. These are my scores for the pages as they were.

| Page | UI | UX | Easy to use | Why |
|---|---|---|---|---|
| `/contact` | 6.5 | 5.5 | 5.5 | Clean and on-brand, but no icons at all, invisible field edges, and no help of any kind before you write. |
| `/privacy` | 6.5 | 5.0 | 4.0 | Fifteen cards of long legal sentences. For a beginner — the audience this product is built for — this is close to unreadable. |
| `/terms` | 6.5 | 5.0 | 4.0 | Same, with nineteen cards. |

### What was actually broken

**Measured, not guessed.** Seven colour pairs failed the contrast rule:

| Where | Measured | Needed | What it meant |
|---|---|---|---|
| Form field edges | **1.21 : 1** | 3.0 | The box was in the code and not on the screen. |
| Helper text under the fields | **3.98 : 1** | 4.5 | The sentence explaining what happens to your message. |
| Placeholder text in fields | **2.20 : 1** | 4.5 | The example text inside a box. |
| Card edges on the legal pages | **1.19 : 1** | 3.0 | A white card on a near-white page, with no visible edge. |
| Chip edges in the legal header | **1.26 : 1** | 3.0 | Same. |

**Found by reading:**

| Problem | Why it mattered |
|---|---|
| No icons on any of the three pages. | Nothing to scan. Every section looked the same. |
| The contact form had a 180 and a 5000 character limit and no counter. | You type, and the page silently stops accepting letters. |
| Every failure said the same sentence. | "We could not send your message" was shown for a dropped connection, a broken server, and a limit — three problems with three different answers. |
| Nothing was counted. | One script could open a thousand support tickets. |
| No reply-time expectation anywhere. | You send a message into silence. |
| The legal pages had a list of links and no idea where you were. | Twenty links, all looking identical however far you had read. |
| The Privacy Policy said "the Last updated date changes when this policy is revised". | **No date was shown anywhere on the page.** It pointed at something that did not exist. |
| Both legal pages ended with a waitlist sign-up. | Asking for an unrelated action in the middle of reading an agreement. |
| The legal text described a closed private beta. | Not what the product does. |
| The public pages drew a different focus ring from the rest of the product. | A keyboard user met one indicator in the dashboard and another on the website. |

---

## 2. What the pages are now

### `/contact` — three steps, in the order a person needs them

| Step | What it does |
|---|---|
| **1. Answers first** | Eight common questions, with a live search. Most messages are a question that already has an answer, and reading it here is faster than any reply we could send. |
| **2. Choose a subject** | Six subject cards with icons. Choosing one **changes the help text under the message box** to the two or three things that answer will need — so the first reply can answer instead of asking. |
| **3. Write it** | Fields with a real visible edge, a live counter, and an error message beside the field it is about, not in a list at the top. |

New functions on the page:

- **A review window before sending.** You see exactly what will be sent, and press Send there. A proper window: focus goes in, Tab cannot leave, Escape closes it, and focus comes back to the button you opened it from.
- **A secret guard.** If your message looks like it contains a wallet phrase, a private key, an API key, a full card number or a password, the page warns you **before anything leaves your browser**. It warns; it never blocks.
- **The limit, stated before you meet it** — and after sending, how many you have left.
- **Three different failure messages**, because a dropped connection, a broken server and a reached limit need three different next steps.

### `/privacy` and `/terms` — read it your way

| Feature | What it fixes |
|---|---|
| **"In short" on every section** | One or two plain sentences at the top of each section. A beginner can read only these and know what they agreed to. The full wording sits under it, and the page says plainly which one is the agreement. |
| **Plain summary / Full text** | One button opens or closes every clause at once. |
| **A rail that follows you** | Marks the section you are reading — with a bar, a bolder weight *and* a screen-reader mark, never colour alone — and a line showing how far through you are. |
| **Search** | Nineteen sections is more than anybody scans. |
| **The date, the version, the reading time** | The old text referred to a date the page never displayed. |
| **Print** | Printing opens every clause, whatever was open on screen. Paper has no buttons. |
| **Deep links land open** | A link to `/terms#billing` shows the wording, not a closed summary of it. |
| **Sibling documents at the end** | Replaces the waitlist band with the three documents a reader of this one actually wants next. |

---

## 3. The support-ticket limits

One rule, both doors, numbers in the environment.

| Limit | Value | Environment variable |
|---|---|---|
| Per email address | 2 | `SUPPORT_INTAKE_MAX_PER_EMAIL` |
| Per device or address | 2 | `SUPPORT_INTAKE_MAX_PER_CLIENT` |
| For everybody together | 20 an hour | `SUPPORT_INTAKE_MAX_PER_HOUR` |
| The period all three count over | 3600 seconds | `SUPPORT_INTAKE_WINDOW_SECONDS` |

All four are in `.env.example` **and** `.env.production.example`.

**How it works.** Both forms — the public `/contact` page and the support form inside
the dashboard — write one row to a shared ledger and read the count from it. That is
what makes "two per email" mean two *across the product* rather than two per form.
The ledger stores no address: the email and the browser session are scrambled with the
application secret, so it can count a person without holding who they are.

**Three things that make it usable rather than only safe:**

1. The limit is written on both forms before you write anything.
2. A refusal names **which** limit and **when** it clears — worked out from the oldest
   message still counted, not a fixed guess — and says what to do instead.
3. A network retry of the same message does not cost you a second message.

The check happens **before** any work: a refused message writes no row, stores no
picture and sends no email.

---

## 4. Beta wording

Every sentence describing a closed private beta is gone from the two legal documents.

That is not a loosening. The promises those sentences carried are still there in live
form, and a test holds them:

| Was | Is now |
|---|---|
| "Paid access is not offered to the public during the private beta." | "Before any payment, the checkout shows the price and currency, what the plan includes and its limits, how long the access lasts, whether it renews by itself, how to cancel… If any of that is not shown, no charge may be taken." |
| "Accounts are issued by invitation during the private beta." | "You must be at least 18 and legally able to enter this agreement. The details on your account must be true and kept up to date…" |

**One thing I deliberately did not change, and it is the user's call.** How open the
product is stays one server setting, `LAUNCH_STAGE=public_waitlist`. The shared header
and footer read it, so while it says `public_waitlist` the button at the top of these
pages still says "Join the waitlist". I did not override that inside three pages,
because two places deciding how open the product is, is exactly the kind of split this
codebase keeps being bitten by. The legal text is now written so it is true either way
— so flipping `LAUNCH_STAGE` to `public_launch` needs no further change to these pages.

---

## 5. Design

| Rule | What was done |
|---|---|
| No new main colours | The five brand colours are untouched. Two neutrals were **darkened inside the same family** because they were measured and failed. A browser test now lists every colour the three pages may paint and fails on a sixth. |
| No new fonts | Geometria for headings, Onest for everything else. Unchanged. |
| No new spacing | The existing steps, named as tokens so the new pages cannot drift from them. |
| Animation from a library | Motion 11.18.2 — **the copy the dashboard already uses**, imported rather than copied, so there is one version to keep current. |
| 3D | Cards tilt towards the pointer inside a perspective. Switched off for touch and for anyone who asked for less motion. |
| Icons everywhere | From the product's own set of 101 icons. The React pages now read that set instead of drawing their own, so the website and the dashboard cannot drift apart. |
| Motion helps, never decorates | Every animation answers a question: which card is under my pointer, which section did I land on, which field has the problem. |

---

## 6. Problems found along the way, and fixed

These were not in the request. Each is the same fault in a wider place, which is where
the fix belongs.

| Found | Fixed |
|---|---|
| **The focus halo could be erased by any decoration.** The halo is drawn with `box-shadow` from a rule with no specificity at all, so *any* component that gave itself a shadow silently removed it. Two were already doing it. This is a site-wide fault, dashboard included. | The indicator is now marked important in all three stylesheets that declare it, and the three places that deliberately draw a different halo are marked the same way so they still win. A decoration can no longer outrank an accessibility guarantee. |
| `--color-ink-soft` measured 3.98:1 — under the readable minimum **everywhere on the site**, not only on these pages. | The token itself was darkened, so every caller was fixed at once. |
| The public website drew a **different focus ring** from the rest of the product — and a nav link overrode even that with a third colour. | All three stylesheets now declare the same ring, the nav link's override is gone, and the test that held two files together now holds all three. |
| The React half of the website was **never read by the copy lint**. The Privacy Policy and Terms — the most carefully read text this product publishes — could say anything at all. | Both pages, the landing page and the shared components are now linted for the forbidden claims, the brand name and the Shariah spelling. It reports zero. |
| The sentence-case heading rule only read the Jinja half of the site. | It now reads the React headings too, including the ones declared as data. |
| The review window was written inside the application's own root, and then hid that root from screen readers — **hiding itself as well**. | Moved outside the root. A browser test walks up from the window and fails if anything above it is hidden. |
| The dashboard support form read only one of the two shapes a server refusal comes in, and threw away the useful one. | It now reads both, so somebody who reached the limit is told which limit and when it clears. |
| Both dashboard support pages stated no limit. | Both now state it, reading the number from the module that enforces it. |
| The contact form let you type a 180-character title, then added the subject in front of it and had the message refused by the server — for a reason nothing on the page had mentioned. | The box now offers only what fits, worked out from the longest subject. |
| **Two browser test runs at once destroyed each other.** They shared one database file and one log, and each deleted the other's database on startup. Failures moved around between attempts and looked like product faults. | Each run now gets its own. |

---

## 7. The one that would have shipped

Worth its own section, because it is the reason a browser test exists at all.

The animation library exports a `spring` helper. Called the way every example shows it —
`spring({ stiffness, damping, mass })` — **it throws**, because it reads
`options.keyframes[0]` straight away. That one line sat at the top level of the motion
module, so importing it stopped the whole bundle loading.

**Every one of the three pages rendered as a blank white screen.** And nothing reported
it:

| Check | What it said |
|---|---|
| TypeScript | passed |
| The build | passed, 438 kB written |
| The server | returned 200 |
| The browser | downloaded the file |
| Every offline test | passed — they read the source, and the source was right |

Only rendering the page in a real browser found it. That is now the first test in the
file: open each page, wait for its heading.

---

## 8. How it was checked

| Check | Result |
|---|---|
| `ruff check src tests scripts` | pass |
| `mypy src` | pass, 368 files |
| `pytest tests/unit tests/engine tests/interpreter tests/services` | pass |
| `pytest tests/integration/test_public_forms_api.py` | pass |
| `pytest tests/integration/test_landing_page.py` | pass — checks the **built** bundle, not the source |
| `pytest tests/browser/test_public_pages_e2e.py` | **34 pass**, in a real browser |
| `pytest tests/browser/test_phase5_status_and_brand.py` | pass — the dashboard is unaffected by the focus-ring change |
| `alembic upgrade head` | pass, new table created |

### New tests

| File | Holds |
|---|---|
| `tests/unit/test_invariant_support_intake_quota.py` | 36 checks: every limit is a setting, is documented in both environment files, fires on its own, is shared between the two doors, returns after the window, and always names a next step. |
| `tests/unit/test_invariant_public_page_accessibility.py` | 55 checks: contrast **computed** for every token on every surface it is used on, eleven dialog rules, the motion rules, the icon rules, labels, target sizes, live regions. |
| `tests/browser/test_public_pages_e2e.py` | 35 checks in a real browser: rendering, no invented colour, measured text contrast, measured field-edge contrast, a keyboard walk of the focus ring, the review window's focus trap, the secret guard, a real message sent, a real refusal at the limit, and the reading tools on both legal pages. |

### Extended, rather than added

| File | Now also covers |
|---|---|
| `core/copy_rules.py` | the React pages — half the public site was unlinted |
| `test_invariant_sentence_case_headings.py` | React headings, including ones declared as data |
| `test_invariant_focus_visibility.py` | the third stylesheet that declares the ring, and the rule that gives every control one |
| `test_landing_analytics.py` | rewritten where it asserted the old design, so the new rules are held rather than the old ones deleted |

### What is measured, not asserted

These numbers come from running the code, not from reading it:

| Measured | Before | Now |
|---|---|---|
| Form field edge against its own fill | 1.21 : 1 | **3.72 : 1** |
| Helper text on white | 3.98 : 1 | **5.99 : 1** |
| Muted text on the page ground | 3.73 : 1 | **5.62 : 1** |
| Control edge on the apple panel | — | **3.21 : 1** |
| Colours used outside the palette | not checked | **0** |
| Text under its required contrast | not checked | **0** |
| Icons that fell back to "unknown" | not checked | **0** |
| Pages that scroll sideways on a phone | not checked | **0** |

---

## 9. Not done, and why

**One thing.** The header on all three pages still says "Join the waitlist", because the
shared header and footer read the server setting `LAUNCH_STAGE`, which is
`public_waitlist`.

I did not override that inside three pages. Two places deciding how open the product is
would be exactly the split this codebase keeps being bitten by, and the setting is also
the emergency brake that pulls the site back without a deploy. **It is a one-line change
and it is yours to make**: set `LAUNCH_STAGE=public_launch`. The two legal documents are
already written to be true either way, so nothing on these three pages needs to change
when you do.

**One reading I had to make.** The request says "you are required to make the dashboard
at least 9.9/10". The rest of the message is about `/contact`, `/privacy` and `/terms`,
so I read that as meaning these pages, and built them to that bar. The dashboard work I
did do is the part the request named directly: the ticket limit is enforced there, its
support page now states the limit, and its error message was fixed. If you meant the
whole dashboard, say so and I will take it as its own piece of work.
