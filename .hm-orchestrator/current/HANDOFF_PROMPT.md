# Handoff prompt - Hilal Markets, remaining work

Copy everything below the line into the other AI. It is written to be self-contained.

---

You are taking over a piece of work on a shipped product. Read this whole brief before
you touch anything. Everything in it was checked in the code on 2026-09-10, not guessed.

## 1. The product, and two rules you cannot break

**Hilal Markets** is a Halal crypto-monitoring product for **beginners and Muslims**.
The repository is at:

```
C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant
```

Branch `cloudflare-access-service-tokens`, base commit `145275f2`. The working tree
carries roughly 208 unrelated modified/untracked files that were there before this work
began. **Do not touch them and do not commit them.**

Two rules override everything else:

1. **Never invent a Sharia / halal / haram status.** It is assigned only by the
   platform's own review process. No buy/sell advice, no leverage, no guaranteed returns.
2. **Write for a beginner.** No internal field names, no "entitlement", "provider",
   "proration", "checkout session", "gte". Every sentence a customer reads must say what
   happened **and** what they can do next.

Read `CLAUDE.md` at the project root before starting. It is the working contract. The
three most important habits it demands:

- **Fix the defect class, not the reported example.** The recurring root cause here is
  two modules deciding the same thing and disagreeing. The fix is always extraction into
  one owner, never a patch at one call site.
- **A problem you find is a problem you fix.** "Pre-existing", "not what I was asked",
  "needs its own pass" are not reasons to leave something broken. Only a true blocker is
  (a decision the owner must make, access you do not have).
- **Tests assert the rule, parametrised across the whole family** - every payment state,
  every plan crossed with every other plan. A test that only covers the reported example
  does not count. **Never weaken, skip, delete or widen a test.**

## 2. Your resources and limits

Verification commands (Windows, from the repository root):

```
.venv\Scripts\python -m ruff check src tests scripts
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly
```

`pytest-timeout` is NOT installed - never pass `--timeout`.

**Memory is the hard limit and it has killed seven runs on this machine.** 15.4 GB
total, about 2.5 GB free. Another project of the owner's is running and must not be
stopped.

- One browser at a time, headless, closed before anything else starts.
- Never run the whole `tests/browser/` suite - only single files by path.
- Batch the test commands; do not run the whole tree at once.
- **Write your evidence to files as you go.** A previous run finished five of nine
  requirements and proved none of them, because it saved the report for the end and was
  then killed.

Paid API calls are allowed but spend minimally - cheapest model that answers the
question, one representative case, reuse recorded responses.

## 3. What is ALREADY DONE - verify if you like, but do not redo

All of this was checked in the code and is green (`ruff` passes, `mypy` passes on 425
source files):

| Done | Where |
|---|---|
| "Ask AI" button on Halal Assets, Create monitor, Opportunities, and all 8 settings groups | one Jinja macro `templates/hilal/macros/ask_ai.html`, one CSS block `static/hm-dashboard-test.css:707+`, one handler `static/hm-hilal-chat.js:136`. Colour tokens `--hm-sky*` in `static/hilalmarkets-brand.css:54-62` |
| The button is genuinely last in the Halal Assets filter bar | proven by a test that failed before the fix |
| Contrast tests check the WCAG floor, not one exact number | `tests/unit/test_invariant_ask_ai_contrast.py` |
| Billing jump bar removed ("What you have / Other plans / Your payments") | gone from `templates/hilal/dashboard_test/subscription.html` and `static/hm-subscription-test.js`. The shared `.a-jump` CSS in `static/hm-account-test.css` was correctly KEPT, because the settings page still uses it |
| Settings page rebuilt so one list drives both the jump bar and the 8 group headings | `templates/hilal/dashboard_test/settings.html` - the previously unreachable `g-data` group is now linked |
| **Unfinished payments: one owner for "what may this person do next"** | `_billing_history_rows` in `api/routers/dashboard.py:~446-509` sets `next_step` / `blocked_reason`. BOTH surfaces read it: `dashboard_test/subscription.html` (8 refs) and `dashboard/partials/billing_history.html` (5 refs). 19 new unit tests in `tests/unit/test_invariant_billing_next_step.py`; 892 tests passed in the focused suites |
| Beginner wording sweep on checkout, billing, billing portal, payment-result page and the billing script | "payment provider", "entitlement", "provider webhook", "duplicate payment" are all gone from customer-visible text. 26 new test cases |

Two earlier fixes from a previous session are also in the tree and must be KEPT:
the checkout review page reads the single owner `plan_checkout_availability`
(`dashboard.py:3948`), and two tests that used to skip themselves on finding a bug were
removed from `tests/browser/test_checkout_pay_button_e2e.py`.

## 4. The owner's decision you must implement (this is the big one)

The product owner decided this on 2026-09-10. **It is not open for reinterpretation.**
The full text is in `.hm-orchestrator/current/OWNER_DECISION_WP8.md` - read it.

**The rule.** When someone who holds an active paid plan buys a **DIFFERENT** paid plan,
by card or by crypto, on any plan:

| | |
|---|---|
| They pay | the **FULL price** of the new plan today. Never a prorated "difference". |
| The new plan's period | starts **TODAY** |
| The old plan's remaining days | **end**. They are not carried over and not held as credit. |
| The money value of those unused days | worked out, saved as a record, and **emailed to the customer within 48 hours** |
| Who sends the money | a **person**, by hand. Nothing is refunded automatically. |
| Buying the SAME plan again | still **refused** - unchanged |
| Free plans, trials, admin grants | owe **nothing** back, because no money was paid |

The amount must be based on **what the customer really paid** -
`BillingCheckoutAttempt.amount` (`db/models/commercial.py:237`) is documented as the
already-discounted figure. **Never** the catalogue price: a launch offer or discount code
would otherwise refund more than was ever paid.

**Why the card path needs a special mechanism.** Creem (the card company) cannot do this.
Its subscription-upgrade endpoint offers exactly three behaviours and every one is
prorated: `proration-charge-immediately` (charges only the difference - the exact thing
the owner ruled out), `proration-charge` (deprecated, identical), and `proration-none`
(charges nothing today). None restarts the billing period. This was verified against
Creem's own published API reference. **Do not re-investigate it and do not approximate
with any of the three.**

**The owner's chosen card mechanism, and the order is mandatory:**

1. The customer completes a **normal checkout at the full price of the new plan FIRST**.
2. **Only once that payment is confirmed** (the webhook says paid and the existing amount
   and currency checks pass), in **one transaction**:
   a. cancel the old subscription at once so it can never renew;
   b. end the old plan's period;
   c. work out the money owed for the unused days;
   d. write the money-owed record;
   e. queue the email promising it within 48 hours.

**Never cancel the old plan before the new payment is confirmed.** If the customer
abandons the checkout or the card is declined, they would be left with **no plan at all**
and their paid days gone. That is worse than the problem being fixed.

**Crypto was never blocked.** A crypto holder can already buy a different plan
(`must_switch_instead` is false for them, because there is no card to re-price). Build the
crypto half straight away; it needs no new mechanism, only the ending of the old plan plus
the money-owed record and the email.

**The two ways this loses real money. Each needs a test proving it cannot happen:**

| Failure | Cost | Required proof |
|---|---|---|
| Old subscription not cancelled after the new one starts | customer charged **twice every month, for ever** | after the move, exactly ONE live subscription and ONE recurring charge remain - parametrised over every plan crossed with every other plan |
| Old plan cancelled but the new payment never completed | customer left with **nothing**, having paid | abandon the checkout, and separately fail the payment; assert the old plan is untouched and still live in both cases |

If the cancellation call fails after the new plan is live, retry, and if it still fails
raise an alert a person will see. **Never silently leave two live subscriptions.**

Other required properties:

- **Idempotent.** Webhooks are re-delivered and `reprocess_failed_event`
  (`services/billing.py:2290`) replays them deliberately. Exactly one money-owed record
  per ended period. Money that doubles on a retry is the worst possible outcome.
- **Money maths:** `Decimal` only, never `float`. Two decimal places, rounded half up,
  stated in one place. Never negative, never more than was paid. Zero or less means no
  record and no email.
- **The email** goes through the existing `PaymentEmailOutboxService`
  (`services/payment_emails.py:186`) and its `PaymentEmailDelivery` record. Do not write
  a second email path.
- **"Within 48 hours" is one constant in one place**, read by the email, any on-screen
  sentence, and the tests.
- **One owner for "may this account buy this plan"** is `plan_checkout_availability`
  (`services/billing.py:654`). Today `must_switch_instead` at line ~688 refuses a card
  customer buying a different plan. That is the refusal this work opens. Change it there,
  once. Because the checkout review page already reads that one owner, every surface
  follows automatically - do not add a second rule anywhere.
- **The consent sentence at `services/plan_changes.py:158`** currently promises "I am
  charged only the difference for the days left in this month". Once this ships that is a
  **false promise about money**. Rewrite it, and check the surrounding sentences too.
- Any migration you add: write every constraint and index name through `op.f()`.
  PostgreSQL refuses identifiers over 63 characters and SQLite does not, so this class of
  bug only ever appears on a real deployment.

**Tests that assert the OLD card refusal will now be wrong** (the family around
`tests/integration/test_checkout_and_payment_email.py:881` and `:1017`). Changing their
expected value is a **repair, not a weakening**, and is allowed ONLY if: the test still
asserts a rule parametrised across every plan pair; it still fails if any page and the
server disagree about who may buy what; nothing is deleted, loosened or skipped - only the
expected value moves from "refused" to "allowed at full price"; and you list every such
test by name in your report with the old and new expectation. Anything that does not fit
those four conditions is an escalation, not your judgement call.

## 5. The rest of what remains

**B. A real money hole in the resume-payment link - narrow but live.**
`resume_billing_checkout` in `api/routers/dashboard.py:3777` asks
`plan_checkout_availability(...)` **without passing `billing_cycle`**, then accepts the
aggregate `purchasable`, which is true if **any** cycle of that plan can still be sold.
So if the **annual** price is withdrawn while monthly still sells, a stale annual link
still forwards the customer to the payment page and can take money for a cycle that is no
longer on sale. `BillingCheckoutAttempt.billing_cycle` exists
(`db/models/commercial.py:229`) and is simply not consulted. Pass the attempt's own cycle
and check that cycle. Add a test parametrised over every plan and both cycles.
(An independent money reviewer flagged this class; a repair was dispatched and had written
nothing when the run was stopped, so the tree is clean and the bug is untouched.)

**C. The cache-busting key has not been bumped.**
Every `?v=` in every template is still `20260910-pay-terms` (126 places, currently one
distinct value - good). Two JavaScript files changed today
(`hm-subscription-test.js`, `hilalmarkets-billing.js`). Until the key is bumped, browsers
keep the OLD files and will not see the fixes. Bump all 126 to one new value and prove
exactly one distinct value remains.

**D. No screenshots and no visual review have happened.**
Needed at three viewports - 1440x900, 1024x768, 390x844 - for the four "Ask AI" surfaces
and the subscription page, plus one reduced-motion shot. The binding visual rules are in
`.hm-orchestrator/current/VISUAL_CONTRACT.md`. There are also 21 existing screenshots in
`.hm-orchestrator/runs/20260910T030616Z-24bcf48d/` for the checkout pages - reuse them
rather than retaking, and judge those against the V1-V8 table near the end of
`.hm-orchestrator/current/CLAUDE_DECISION.md` (that file is otherwise superseded; only its
visual contract still applies). **Specific thing to look at:** the "Ask AI" button on
Halal Assets now sits on **its own row** in the filter bar after a note was moved. Nobody
has looked at it. Two of the old checkout screenshots (S4, S7) show a refusal that the
owner's new rule removes - once the rule ships those two are out of date and must be
retaken or clearly marked stale, never presented as current proof.
If a reviewer cannot actually open the images, mark visual verification **unverified** and
say so. **Never infer a visual pass from reading CSS.**

**E. No independent review of the money path has happened.**
Someone who did not write the code must try to break it and answer, in writing: can a
customer be charged twice (including two live subscriptions)? can they pay and get no
access? can the money owed back be paid twice, or computed larger than what was paid?
wrong amount, wrong plan, wrong period start? can any page in any state still offer
something the server refuses? does any customer-visible sentence now lie about what they
are charged? "No customer impact" is not an accepted closure unless you show the path is
unreachable and say how you proved it.

**F. Final verification and an honest report.**
Run the three commands in section 2 plus, by path,
`tests/integration/test_plan_change_journey.py`,
`tests/integration/test_dashboard_test_subscription.py`,
`tests/integration/test_checkout_and_payment_email.py`, and your browser file.
The tree already had failures unrelated to this work - before calling anything a
regression, compare the failing test ids against a clean `git worktree` at `HEAD` using a
SHORT path such as `C:\wt-head` (the repository's nested folders overflow Windows' path
limit otherwise), then copy only the files you changed onto it to confirm.

Answer these one at a time, out loud: any test deleted? any skip or xfail added? any
assertion widened? any expected value changed? any test-only hardcode? For the expected
values the owner's decision legitimately moves, name each one.

## 6. How to report

Write for a **non-native English speaker who may not be an engineer**. Short sentences,
one idea each. Everyday words - "the system saved the wrong number", not "the persistence
layer serialised an incorrect value". No Latin, no idioms. Tables beat paragraphs. Say
what it means for the customer, not only what the code does.

State plainly which things are **verified fixed**, which are **unfixed**, and which are
**unverified**. Never present a crash that now completes as a score improvement. Always
include problems you found beyond what was asked, and anything left undone with the
reason.
