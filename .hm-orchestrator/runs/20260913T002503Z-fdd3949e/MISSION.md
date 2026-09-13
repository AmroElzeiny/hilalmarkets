# MISSION — close the five defects the last run wrote down instead of fixing

Owner: Claude Code (architect and final judge). Written 2026-09-13.
Branch `cloudflare-access-service-tokens`, commit `75b19580`.
Tier: **Deep** (refund records, money owed back, payment requests).

## 0. Context — read, do not redo

The previous run `20260912T193031Z-4125cfbe` finished with verdict
`COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM`. Claude checked it on 2026-09-13:

| Check | Claude's own result |
|---|---|
| ruff `src tests scripts` | all checks passed |
| mypy `src` | no issues in 426 files |
| Money test batch (6 files) | passed |
| Broad offline suites (`broad_junit.xml`) | 28 211 tests, 0 failures, 0 errors, 489 skipped |
| skip/xfail added under `tests/` | none |

Good work. **But five defects were found by its own reviewers and recorded, not fixed.**
CLAUDE.md: "Finding a defect and *describing* it instead of fixing it is an unfinished
task." This mission fixes exactly those five. Nothing else.

Read first: `CLAUDE.md`, `.hm-orchestrator/models/ROLE_MODEL_MAP.md`,
`.hm-orchestrator/policy/ROUTING_POLICY.md`, `SUPERVISOR_REPORT_CONTRACT.md`,
`ESCALATION_CONTRACT.md`, and from the previous run `REVIEW_FIXES_ADVERSARIAL.md`,
`VISUAL_REVIEW.md`, `EVIDENCE_FIX_A_B.md`.

## 1. Limits

- Routing: the agents already carry the owner's models (`ROLE_MODEL_MAP.md`).
  `run-model.ps1` refuses everything except `minimax-m3`, `qwen3.8-flash`,
  `deepseek-v4.1-flash`, `deepseek-v4-flash-vision-exp`.
- The supervisor writes only under `.hm-orchestrator/runs/*`. Code goes to workers.
- Memory: often under 1 GB free. **One browser at a time**, closed after use. Never the
  whole `tests/browser/` suite. One pytest process at a time, output to a file.
- **No evidence file for 30 minutes = stuck.** Write `STALLED_<step>.md` and move on.
- Never weaken, skip, xfail, delete or widen a test. No `git commit/push/reset/clean`.
  Never read `.env*`. Do not touch the ~100 unrelated uncommitted files.
- Every fix: a test that **fails before and passes after**, parametrised over the whole
  family, not the one reported case.

## 2. Work packages

### D1 — A refund must be recorded, or it can be refunded twice  (SERIOUS)

**Proved by Claude in the code:**

- `services/billing.py` `_record_checkout_event` (~3211-3222): `payment.refunded` is in
  the same set as `payment.failed` / `payment.expired`, and the guard added for R2-6 only
  lets those move an attempt that is `creating/pending/processing`. So a refund of a
  **completed** payment is now silently ignored; the attempt stays `completed`.
- `services/plan_replacements.py` `payment_that_bought` (~121-142) chooses the
  payment that bought a plan with `status == "completed"`. That payment's `amount` is
  what the money owed back is valued from.
- Result: a payment that was already refunded can still be the basis for "money owed for
  unused days" on a later plan move. **A double refund.** The previous run's reviewer
  called this "minor, needs a deliberate decision". It is not a product decision: the
  refund happened, and the record must say so.

**Fix the class.** R2-6's real intent was narrow: *our own cancellation's webhook must not
downgrade a settled payment*. Keep that. But a genuine refund (and a chargeback/dispute —
see `billing.py` ~2906 and ~2930) must move a completed attempt to `refunded`. Find
**every** reader of attempt status that decides money or access (`payment_that_bought`,
`dashboard.py` ~451 which treats `completed` and `refunded` alike, renewal-wording
readers, history rows) and make sure each one answers correctly for a refunded payment:
no money owed back is computed from a refunded payment, and no page shows it as a live
paid purchase.

**Tests.** Parametrise over every provider that can refund (Creem, Stripe if wired,
NOWPayments) × every refund-like event type × attempt states. Assert: (a) a refund of a
completed attempt records `refunded`; (b) our own cancellation webhook still never
downgrades a completed attempt; (c) a plan move after a refund writes **no** money-owed
record from the refunded payment; (d) replaying the refund event is idempotent.

### D2 — One owner for "which paid plans does this person hold"  (duplicate reader)

`billing.py` `open_checkout_attempt` (~2005) still calls `active_paid_plan_codes`
(no grace period). `prepare_checkout` (~1742) and every page (`dashboard.py` 3436, 3827,
4027; `dashboard_test.py` 1714) call `paid_plan_codes_for_replacement_decisions`. Two
readers of one question is this codebase's recurring root cause. Make it call the same
owner. Then search all of `src` for any other caller that decides purchase or replacement
from `active_paid_plan_codes`. If `active_paid_plan_codes` has no remaining legitimate
caller, say so; do not delete a function that other code needs.

**Test:** an invariant that fails if any checkout/replacement decision path reads a
different held-plan set than `plan_checkout_availability` is given — for a card plan
inside its lapsed grace window, monthly and annual.

### D3 — Money leaves the system without passing through binary float  (R2-7)

`billing.py` ~1308 sends `"price_amount": float(amount)` to NOWPayments via `json=`.

Fix the class, not the line: search **all of `src`** for `float(` applied to a money value
(amount, price, total, refund, owed, commission). Create **one** owner that turns a money
`Decimal` into what a provider's wire format needs: quantised to the currency's minor
unit, `ROUND_HALF_UP`. If NOWPayments accepts a string (check its API docs with
webfetch and cite the page), send the exact string. If it needs a JSON number, the owner
must prove the emitted text equals the Decimal exactly. Every money-to-provider call goes
through it.

**Test:** parametrised over every catalogue price in both cycles and every discount-code
outcome in the discount tables: the serialised request body's amount text equals the
quantised Decimal exactly. Plus an invariant that fails if `float(` appears on a money
name in `services/billing.py`, `services/plan_replacements.py` or any affiliate/commission
module.

### D4 — The alert "pointer, never a payload" rule must hold  (adversarial finding 4)

`observability/issues.py` `sanitize_dedupe_key` (~85-125) and the evidence-ref sanitiser
(~404-413): characters the pattern forbids become `-`, so a payload-shaped string with a
valid prefix is accepted. The reviewer executed
`billing_event:the provider returned a 500 body` → accepted (via the evidence-ref path).

Make an evidence ref accept only a **pointer**: a known prefix plus an identifier shape.
Anything else must be reduced to a safe pointer (for example the prefix plus a short
hash of the original) — **never raise**. CLAUDE.md: "A diagnostic must never become the
failure." Two HTTP 500 classes and one lost critical billing alert already came from a
check firing while a problem was being reported. Also run `assert_no_sensitive_content`
over refs, as it already runs over the summary.

**Tests:** parametrised over real provider id shapes (Stripe `evt_…` mixed case,
NOWPayments with `:`, Creem, over-length ids) — all still stored and still deduplicate
to the same key on a repeat; and over payload-shaped strings (prose, JSON, stack text,
an email address, a URL with a token) — none stored verbatim, none raise.

### D5 — The corner assistant must never hide a control  (VISUAL)

**What is already settled — do not reopen:** the travelling sentence in the tag
(`static/hm-hilal-chat.css` 68-125) is deliberate; a still picture catches it
mid-travel. "Clipped mid-word" is a false alarm, proven from the CSS and recorded.

**What is a real defect, measured by the previous run (`VISUAL_REVIEW.md` items B/C):**
the fixed corner widget (`.hm-hilal`, `hm-hilal-chat.css:26-46`, `position: fixed`,
bottom-right, `z-index: 210`) sits on top of real, pressable controls:

- Settings at 390: the round button `x318-377` covers the Timing group's own "Ask AI"
  pill `x279-363`; the pill's text is hidden.
- Halal Assets at 1024: the label covers the "What is the Hilal Markets Methodology?"
  pill.
- Create monitor at 1024: the orb covers the "Open the list" button.
- Opportunities/Create monitor at 390: the orb covers the "A coin jumps/drops" chips.

"Inherent to a fixed corner assistant" is not an accepted closure. There is no
`scroll-padding` anywhere in `static/` today.

**The rule to implement (WCAG 2.2 SC 2.4.11, Focus Not Obscured):** on every signed-in
page that carries the corner assistant, **every focusable control can be brought fully
into view without the corner widget covering it**, at 1440x900, 1024x768 and 390x844.
Concretely: when a control receives keyboard focus, it must not be covered by `.hm-hilal`;
and the last controls on the page must be able to scroll clear of it. Solve it **once**
in the shared shell (for example a `scroll-padding-block-end` and a page bottom clearance
driven by the widget's own measured footprint, including `--h-lift` for the cookie
banner) — never per page. Do not change the widget's look, colours, size or position:
those are in `VISUAL_CONTRACT.md` and must not move.

**Tests:** one browser file, by path. For each of the 5 surfaces (market,
create-monitor, opportunities, settings, subscription) × 3 viewports: Tab through every
focusable control and assert none is covered by `.hm-hilal`'s rectangle; scroll to the
bottom and assert the last control clears it. The test must fail on the current code
for at least the Settings-390 case.

**Visual proof:** after the fix, retake **only** the affected screenshots and have
`hm-reviewer-visual` judge them, called **directly** with `--file` (a subagent cannot
receive an image). Then run the contrast browser tests once (`tests/support/contrast.py`
is the one owner), because the previous run did not re-measure V7.

## 3. Review, verify, report

- `hm-reviewer-logic` (minimax-m3) reviews D1-D4; `hm-reviewer-adversarial`
  (deepseek-v4.1-flash) attacks D1 and D3 specifically: can money still be refunded
  twice, or leave the system inexact? Each writes its own file under the run folder.
- `hm-reviewer-visual` judges D5's new screenshots.
- After the last fix, capture: ruff `src tests scripts`; mypy `src`; the money batch
  (`test_paid_plan_replacement.py`, `test_invariant_plan_replacement_money.py`,
  `test_plan_change_journey.py`, `test_invariant_plan_checkout_availability.py`,
  `test_invariant_resume_checkout_cycle.py`, `test_operational_issue_queue.py`,
  `test_checkout_and_payment_email.py`); the broad offline suites with `--junitxml`; the
  D5 browser file. Report real counts from the files.
- Before calling a failure a regression, compare against `C:\wt-head` (clean at HEAD).
- Clean the previous run's clutter: `temp_*.py`, and the copies `dashboard.py` and
  `dashboard_test.py` inside `.hm-orchestrator/runs/20260912T193031Z-4125cfbe/` — they
  are not evidence and trip `ruff check .`.
- `SUPERVISOR_REPORT.json` + `.md` per the contract, plain language for a non-native
  English speaker. Test integrity: the five questions, each with what was searched.

## 4. Requirement coverage

| ID | Requirement | Must end as |
|---|---|---|
| D1 | A refund of a completed payment is recorded; no money owed back is ever computed from a refunded payment; our own cancel webhook still cannot downgrade a settled payment | PASS |
| D2 | One held-plan owner for every checkout/replacement decision, including `open_checkout_attempt` | PASS |
| D3 | One owner for money onto the wire; no `float` on a money value; exact amount proven | PASS |
| D4 | Evidence refs accept only pointers; payload-shaped input reduced, never stored, never raising | PASS |
| D5 | No focusable control covered by the corner assistant on 5 surfaces × 3 viewports; solved once; new screenshots vision-judged; contrast re-measured | PASS, or UNVERIFIED with the exact reason |
| D6 | Two logic reviews, one vision review, full verification, clutter removed, schema-valid report | PASS |

Escalate (`ESCALATION.json`) only for: a change that would contradict
`OWNER_DECISION_WP8.md`; a need to change the corner widget's look or position; a test
that could only pass by weakening; a package install; two meaningful failures on one
package.
