# MISSION — close the remaining review and visual gaps on WP8 / B / C / D

Owner of this mission: Claude Code (architect and final judge).
Written: 2026-09-12.
Branch: `cloudflare-access-service-tokens`. Baseline commit: `75b19580`.

Supervisor tier requested: **Deep** (money path, cross-file state, billing/entitlement
risk, and a required vision review).

---

## 0. Read before anything

1. `CLAUDE.md` at the project root — the working contract.
2. `.hm-orchestrator/current/OWNER_DECISION_WP8.md` — the owner's money rule. **Not
   open for reinterpretation.**
3. `.hm-orchestrator/current/VISUAL_CONTRACT.md` — the binding visual rules.
4. `.hm-orchestrator/policy/ROUTING_POLICY.md`, `SUPERVISOR_REPORT_CONTRACT.md`,
   `ESCALATION_CONTRACT.md`.
5. `.hm-orchestrator/models/LIVE_MODELS.md` and `LIVE_MODELS_VERBOSE.txt` (refreshed
   2026-09-12T15:16Z). **Never invent a model id or a variant.**

### The state of the work — verified by Claude on 2026-09-12, do NOT redo

Claude re-checked these in the code today. They are **done**. Touch them only if a
reviewer finds a real defect in them.

| Item | Verified how |
|---|---|
| WP8 card + crypto plan replacement | `services/plan_replacements.py` (20 KB) exists and is wired |
| Money maths | `MANUAL_RETURN_HOURS: Final[int] = 48` and `MONEY_QUANTUM = Decimal("0.01")` at `services/plan_replacements.py:29-30`; `ROUND_HALF_UP` used, no float |
| 48 hours in ONE place | single constant, read by the sentence builder (`:70`) and the due date (`:354`) |
| Migration identifier safety | `alembic/versions/e5a72b10c94d_paid_plan_replacement_money.py` — 18 of 18 constraint/index names go through `op.f()`; the only plain `name=` strings are `table_name=` on drops |
| Consent sentence rewritten | `services/plan_changes.py` — "the difference for the days left" is gone; `change_needs_checkout` now always True with the reason recorded |
| Required WP8 tests exist | `tests/integration/test_paid_plan_replacement.py`, `tests/unit/test_invariant_plan_replacement_money.py` |
| **Item B** — resume link checks the attempt's own cycle | `api/routers/dashboard.py` `resume_billing_checkout` passes `billing_cycle=attempt.billing_cycle` and reads `requested_purchasable`, not the aggregate |
| **Item C** — one cache-busting key | 130 `?v=` occurrences in `src/ai_market_monitor/templates`, exactly **1** distinct value: `20260911-ask-tag` |
| **Item D** — screenshots taken | 26 files in `.hm-orchestrator/runs/20260911-wp8-finish/screens/`; checkout S1–S7 retaken in `.hm-orchestrator/runs/20260910T030616Z-24bcf48d/screens/` |
| Lint and types | `ruff check src tests scripts` → all checks passed; `mypy src` → no issues in 426 files (both run by Claude on 2026-09-12) |

### What is actually missing

The previous session did the implementation but **reviewed its own work**. CLAUDE.md
does not allow that. Three files say so in their own words:

- `.hm-orchestrator/runs/20260911-wp8-finish/VISUAL_REVIEW.md` — sections **1a, 2 and
  3 are unfinished**; they still read "Being measured" / "Being retaken". The review
  that does exist says "Done by **Claude** … It was not done by an OpenCode vision
  model, which CLAUDE.md asks for."
- `.hm-orchestrator/runs/20260911-wp8-finish/MONEY_REVIEW.md` — "Written by Claude …
  **Not an outside review.**"
- The run folder `20260911-wp8-finish` has **no `SUPERVISOR_REPORT.json` and no
  `SUPERVISOR_REPORT.md`**. There is no evidence package.

So this mission is **review, verification and evidence** — plus fixing whatever the
reviews find. It is not a re-implementation.

---

## 1. Hard limits on this machine

**Memory is the binding constraint and it has killed runs here before.** 15.4 GB
total, **about 1.2 GB free right now**. Another project of the owner's is running and
must not be stopped.

- **One browser at a time**, headless, closed before anything else starts.
- **Never** run the whole `tests/browser/` suite. Single files by path only.
- **Do not retake screenshots that are already good.** Retaking costs a browser.
  Reuse the 26 existing images. Retake only the specific files R4 names, and only if
  R4's check actually fails.
- Batch pytest commands; never run the whole tree in one process.
- **Write every piece of evidence to a file the moment you have it.** A previous run
  finished most of the work and proved none of it, because it saved the report for the
  end and was then killed by the memory limit.
- `pytest-timeout` is NOT installed — never pass `--timeout`.
- Verification commands, from the repository root:
  ```
  .venv\Scripts\python -m ruff check src tests scripts
  .venv\Scripts\python -m mypy src
  .venv\Scripts\python -m pytest <paths> -q -p no:randomly
  ```

### Files you must not touch

The working tree carries ~93 changed and 7 untracked files, most from earlier
unrelated work. **Do not revert, reformat or commit anything.** Do not run
`git checkout`, `git stash`, `git reset` or `git commit` on the repository. Claude
owns committing.

Never print a value from `.env` or `.env.production`. Key names and counts only.

Never invent a Sharia / halal / haram status. Never write buy/sell advice, leverage or
guaranteed returns into any string.

---

## 2. Model routing for this mission

Use the live catalog as authority. Claude's intended routing, which the supervisor may
adjust only for a reason it records:

| Role | Model | Why |
|---|---|---|
| Supervisor | `opencode-go/glm-5.3-flash` (Deep tier) | money path + cross-file state |
| Repository explorer | `opencode-go/deepseek-v4-flash` | read-only discovery, cheap |
| Independent logic reviewer #1 | `opencode-go/deepseek-v4-pro` | different family from the author |
| Adversarial reviewer #2 | `opencode-go/qwen3.8-flash` | third family; keeps cost down |
| Vision reviewer | `opencode-go/deepseek-v4-flash-vision-exp` | live metadata confirms `attachment: true` and `input.image: true`; variants `low`/`high`/`max` exist |
| Fix worker (only if a review finds something) | `opencode-go/kimi-k2.7-code` | multi-file implementation |

This is a **high-risk** task (billing, money owed back, entitlements) **and** a visual
task. CLAUDE.md therefore requires: **two independent reviewers from different model
families, plus one code reviewer and one vision reviewer.** Reviewer #1 and #2 above
satisfy the two families; the vision reviewer is separate and mandatory.

**The vision reviewer must receive each screenshot as an image attachment.** Reading
CSS is not a visual review. If it cannot actually see an image, mark that item
**unverified** and say so — never infer a pass.

---

## 3. Work packages

### R1 — Independent logic review of the money path

**Objective.** Someone who did not write the code tries to break it.

**Authority to inspect.** `services/plan_replacements.py`, `services/billing.py`
(`plan_checkout_availability` ~line 654, `reprocess_failed_event` ~2290),
`services/plan_changes.py`, `services/payment_emails.py`,
`api/routers/dashboard.py` (`resume_billing_checkout`, `_billing_history_rows`),
`observability/issues.py`, `observability/labels.py`,
`alembic/versions/e5a72b10c94d_paid_plan_replacement_money.py`,
`db/models/commercial.py`, and the tests named in R8.

**In scope.** Answer each of these **in writing, with a file:line or a test id**. Do
not accept the previous session's answers — re-derive them.

1. Can a customer be charged twice, or end up with **two live subscriptions** or two
   recurring charges? Check every plan crossed with every other plan, both cycles,
   card and crypto.
2. Can a customer **pay and get no access**? Check: abandoned checkout, declined
   card, webhook that never arrives, webhook that arrives twice, and a cancel call to
   the payment company that fails after the new plan is live.
3. Can the money owed back be written **twice**, or be **larger than what was paid**?
   Check the idempotency key, the database check constraints, and a replay through
   `reprocess_failed_event`.
4. Can the **amount, the plan or the period start** be wrong? The amount must come
   from `BillingCheckoutAttempt.amount` (what the customer really paid), never the
   catalogue price.
5. Can **any page in any state** offer something the server then refuses? There must
   be exactly one owner of "may this account buy this plan":
   `plan_checkout_availability`. Look for a second rule anywhere.
6. Does **any customer-visible sentence** now lie about what the customer is charged,
   what happens to the plan they hold, or when the money comes back?

**Forbidden.** Do not change product code in R1. R1 reports only.

**Acceptance.** `.hm-orchestrator/runs/<run>/REVIEW_LOGIC_1.md` exists, answers all
six questions, and lists every finding with a severity (blocker / serious / minor /
false alarm) and a file:line. "No customer impact" is only accepted if the review
**shows the path is unreachable and says how it proved that**.

**Escalation triggers.** A blocker that needs the owner to choose between two money
behaviours; a contradiction with `OWNER_DECISION_WP8.md`.

---

### R2 — Adversarial second review, different model family

**Objective.** A second family of model, told to disagree, re-checks R1's six answers
and hunts for what R1 missed.

**In scope.** The same authority as R1, plus:

- the **idempotency** story end to end — webhooks are re-delivered on purpose;
- `Decimal` only, never `float`, in every money path it can reach;
- the two required safety properties from `OWNER_DECISION_WP8.md`: after a move
  exactly **one** live subscription and **one** recurring charge remain; and an
  abandoned or failed payment leaves the old plan **untouched and still live**;
- whether the retry-then-alert path for a failed cancellation really raises something
  **a person will see**, and whether that alert can itself be refused for being too
  long (this class of bug has shipped here twice).

**Acceptance.** `REVIEW_LOGIC_2.md` exists, names its model and family, states where
it **agrees and where it disagrees** with R1, and closes or escalates every
disagreement. Two reviewers agreeing because the second one only read the first one's
report is a failure — R2 must cite the code itself.

---

### R3 — Vision review of every screenshot against the visual contract

**Objective.** A vision-capable model looks at the pictures and judges them against
`.hm-orchestrator/current/VISUAL_CONTRACT.md` and the V1–V8 table near the end of
`.hm-orchestrator/current/CLAUDE_DECISION.md`. **Note:** `CLAUDE_DECISION.md` is
otherwise superseded — only its visual table still applies, and its **V4** ("S4, S7:
the refusal sentence is visible") is **dead**: the owner's rule of 2026-09-10 removed
that refusal, so S4 and S7 must now show a live Pay button and the payment form.

**Images to judge** — all already exist, do not retake here:

`.hm-orchestrator/runs/20260911-wp8-finish/screens/`

| Group | Files |
|---|---|
| S1 Halal Assets | `market-1440x900.png`, `market-1024x768.png`, `market-390x844.png` |
| S1 reduced motion | `market-reduced-motion-1440x900.png` |
| S2 Create monitor | `create-monitor-{1440x900,1024x768,390x844}.png` |
| S3 Opportunities | `opportunities-{1440x900,1024x768,390x844}.png` |
| S4 Settings (8 groups) | `settings-{1440x900,1024x768,390x844}.png` |
| S5 Subscription | `subscription-{1440x900,1024x768,390x844}.png` |
| Corner label, public | `landing-corner-{1440x900,1024x768,390x844}.png` |
| Corner label, reduced motion | `landing-corner-reduced-motion-1440x900.png` |
| Corner label, signed in | `dashboard-corner-{1440x900,1024x768,390x844}.png` |
| Corner at the page bottom | `landing-corner-with-back-to-top-{1440x900,1024x768,390x844}.png` |

`.hm-orchestrator/runs/20260910T030616Z-24bcf48d/screens/` — the **current** checkout
set only:

`s1-pro-none-*`, `s2-pro-card-*`, `s3-pro-crypto-*`,
`s4-billing-pro-card-crypto-holder-*`, `s5-billing-pay-for-card-holder-*`,
`s6-downgrade-pay-note-*`, `s7-pro-review-card-holder-*`.

**Specific things to look at, named because nobody has looked at them yet:**

1. On **S1 Halal Assets**, the "Ask AI" button now sits on **its own row** in the
   filter bar, because a note was moved. Is that acceptable against the contract, or
   does it read as a mistake? Say which, at all three widths.
2. On the **corner at the page bottom** images: is the "Ask AI" label **and** the
   round button **wholly on the screen**? Is "back to top" above the label without
   touching it? Is the header at the very top of the page, with **no empty band above
   it**? The earlier Claude review failed this item in the picture. A measurement
   file, `.hm-orchestrator/runs/20260911-wp8-finish/landing_corner_after_scroll.json`,
   now says every corner item is inside the viewport at all three sizes. **The
   pictures decide, not the JSON.** If the pictures disagree with the JSON, that is a
   finding, and R4 handles it.
3. On **S4 Settings**, all 8 group headings must carry the button:
   `g-where`, `g-when`, `g-howmuch`, `g-about`, `g-screen`, `g-evidence`,
   `g-market`, `g-data`.
4. On **S5 Subscription**, there must be **no** Ask AI button, and the old jump bar
   ("What you have / Other plans / Your payments") must be **gone**.
5. On **S4/S7 checkout**, a **live Pay button and the payment form** — not a refusal.

**Forbidden.** Judging colour contrast by eye. Contrast (V7) is decided by the
browser tests, which use the single owner `tests/support/contrast.py`. Quote their
numbers; do not guess.

**Acceptance.** `VISION_REVIEW.md` records: the vision model id and variant, every
image it actually opened, a pass/fail per contract row, and every mismatch with the
exact pixels or tokens involved. Any image it could **not** open is listed as
**unverified**, by name.

---

### R4 — Retake only what is genuinely stale, and prove it

**Objective.** Some pictures may predate the fix they are supposed to show.

**In scope.**

1. Work out, from the run's own evidence files (`fix_run_*.txt`,
   `browser_corner_fourth.txt`, `landing_corner_after_scroll.json`, file timestamps),
   whether `landing-corner-with-back-to-top-*.png` were taken **before or after** the
   corner-at-the-bottom fix. Write the answer down with the evidence.
2. Retake **only** the images R3 failed or that this check proves stale. One browser,
   headless, one viewport at a time, closed before the next command.
3. If a retake is impossible on this machine's memory, say so plainly and mark those
   items **unverified**. Do not guess and do not present an old picture as current.

**Acceptance.** Either the images are proved current, or they are retaken and R3's
vision reviewer judges the new ones, or they are explicitly marked unverified with
the reason.

---

### R5 — Remove the stale checkout screenshots

**Objective.** Nobody can ever present a dead refusal screen as current proof.

**The problem.** `.hm-orchestrator/runs/20260910T030616Z-24bcf48d/screens/` still
holds **12 files showing states the product no longer has**:

| Stale file (×3 viewports) | Superseded by |
|---|---|
| `s4-trader-refusal-*` | `s4-billing-pro-card-crypto-holder-*` |
| `s5-billing-switch-*` | `s5-billing-pay-for-card-holder-*` |
| `s6-downgrade-scheduled-*` | `s6-downgrade-pay-note-*` |
| `s7-pro-refusal-*` | `s7-pro-review-card-holder-*` |

`SCREENSHOTS_STALE.md` documents them and says "The tool refuses to delete files
here, so they are kept and listed instead." Documenting a stale artefact is not
removing it.

**In scope.** Delete exactly those 12 files. Confirm each replacement file exists
**before** deleting the file it replaces. Then rewrite
`SCREENSHOTS_STALE.md` to say they are **removed**, listing what replaced each one.

**Forbidden.** Deleting any other file under `.hm-orchestrator/runs/`. Deleting a
stale file whose replacement is missing.

**Acceptance.** A directory listing in the evidence showing the 12 are gone and all
21 current checkout images remain.

---

### R6 — Fix every finding from R1, R2, R3 and R4

**Objective.** A problem found is a problem fixed. CLAUDE.md: "Finding a defect and
*describing* it instead of fixing it is an unfinished task, not a finding."

**In scope.** Every blocker and serious finding. Minor findings too, unless fixing one
would touch a file outside this mission's scope — then say which and why.

**Rules that apply to every fix.**

- **Fix the defect class, not the reported instance.** Before fixing anything, search
  for other places that make the same decision. The recurring root cause in this
  codebase is two modules deciding the same thing and disagreeing; the fix is
  extraction into one owner, never a patch at one call site.
- **Never weaken, skip, delete or widen a test.** No new `skip`, `skipif`, `xfail` or
  `importorskip`.
- Money is `Decimal`, two places, rounded half up, in one place.
- Any new migration writes **every** constraint and index name through `op.f()`.
- If a fix would change an **expected value** in an existing test, that is allowed
  **only** when the owner's decision of 2026-09-10 is what moved it, the test still
  asserts a rule parametrised across every plan pair, nothing is loosened, and you
  **name the test with its old and new expectation** in the report. Anything else is
  an escalation, not a judgement call.
- Two repair loops per work package. After two meaningful failures, change model or
  family, or escalate. No endless loops.

**Acceptance.** Each finding is either fixed with a test that **fails before the fix
and passes after**, or escalated in `ESCALATION.json` with the exact question.

---

### R7 — Finish `VISUAL_REVIEW.md`

**Objective.** No report in the evidence may still say "Being measured" or "Being
retaken".

**In scope.** Rewrite
`.hm-orchestrator/runs/20260911-wp8-finish/VISUAL_REVIEW.md`, or write a fresh
`VISUAL_REVIEW.md` in this run's folder that **supersedes it and says so**. Sections
1a, 2 and 3 must carry real verdicts from R3's vision reviewer.

Section 2 (the four Ask AI surfaces and the subscription page) and section 3
(checkout S1–S7) currently have **no verdict at all**. They must have one per image
per viewport.

Also record the two things the earlier review saw and correctly refused to hide, and
say whether each is now fixed, accepted or still open:

- on the signed-in pages the round assistant button and its moving "Hilal — your AI
  assistant" line float over the page; at 1440 the line crosses the time-zone box on
  Settings, and at 390 the round button covers half of the Timing group's Ask AI
  button;
- the corner at the bottom of the landing page (R3 item 2).

**Acceptance.** No "being …" sentence survives anywhere in the run's reports.

---

### R8 — Re-verify, with captured evidence

Run after R6's last fix. If R6 changed nothing, run it anyway to confirm the tree.

| Command | Evidence file |
|---|---|
| `.venv\Scripts\python -m ruff check src tests scripts` | `ruff_final.txt` |
| `.venv\Scripts\python -m mypy src` | `mypy_final.txt` |
| `pytest tests/integration/test_paid_plan_replacement.py tests/unit/test_invariant_plan_replacement_money.py tests/integration/test_plan_change_journey.py tests/integration/test_dashboard_test_subscription.py tests/integration/test_checkout_and_payment_email.py tests/integration/test_held_plan_renewal_words.py -q -p no:randomly` | `billing_batch_final.txt` |
| `pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly` | `broad_suites_final.txt` |
| your browser file(s), **by path only** | `browser_final.txt` |

Claude's own measurement today, for comparison: ruff passed, mypy found no issues in
426 source files.

**Before calling any failure a regression**, compare the failing test ids against the
clean worktree already on disk at **`C:\wt-head`** (detached at `75b19580`), then copy
**only** the files you changed onto it to confirm attribution. A short path is
required — the repository's nested folders overflow Windows' path limit.
`C:\wt-baseline` (at `145275f2`) also exists. Note: two untracked test files were left
behind in `C:\wt-head` by the previous run
(`tests/integration/test_invariant_pages_close_their_tags.py`,
`tests/support/html_balance.py`) — account for them so they do not confuse the
comparison, and remove them if you can.

**Acceptance.** Every evidence file exists and the report quotes real counts from it.
Never claim a test was run without captured output.

---

### R9 — Test integrity audit

Answer each question **out loud, one at a time**, for every change this run made and
for the working tree as a whole:

- any test deleted? yes/no — which
- any `skip`, `skipif`, `xfail` or `importorskip` added? yes/no — which
- any assertion widened or loosened? yes/no — which
- any expected value changed? yes/no — name each, with old and new, and why the
  **product rule** is the authority rather than what was convenient to implement
- any test-only hardcode or special case? yes/no

**Acceptance.** `TEST_INTEGRITY.md` with all five answered. A "no" with no search
behind it is not an answer — say what you searched.

---

### R10 — The evidence package

**This is the deliverable Claude judges.** Write, in this run's folder:

- `SUPERVISOR_REPORT.json`
- `SUPERVISOR_REPORT.md`

Both must follow `.hm-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md` exactly. The
JSON must have these top-level keys and nothing missing: `run_id`, `status`,
`baseline`, `models`, `requirements`, `work_packages`, `changed_files`, `tests`,
`test_integrity`, `hidden_error_audit`, `diff_audit`, `reviews`, `uncertainties`,
`final_verdict`. It is validated by `tools/hm-orchestrator/validate-report.py` against
`.hm-orchestrator/policy/supervisor-report.schema.json` and the delegation **fails**
if it does not pass.

Every requirement id **R1–R12** must appear exactly once with a status.

**Do not create, overwrite, rename or delete** `SUPERVISOR_STDOUT.txt` or
`SUPERVISOR_STDOUT_RAW.txt` — `delegate.ps1` alone owns those.

**Write the report for a non-native English speaker who may not be an engineer.**
Short sentences, one idea each. Everyday words — "the system saved the wrong number",
not "the persistence layer serialised an incorrect value". No Latin, no idioms. Tables
beat paragraphs. Say what it means for the customer, not only what the code does.
Deep technical detail belongs in the other evidence files.

State plainly which items are **verified fixed**, which are **unfixed**, and which are
**unverified**. Never present a crash that now completes as a score improvement.

---

### R11 — The Creem saved-card question

The owner asked one question that may have no answer from here: **does Creem reuse a
known customer's saved card details on a new checkout?** A fresh checkout may ask the
customer to type their card again, where the old switch flow did not.

What is already known, from the previous run: our code never sends a saved card or a
customer id; each move opens a new Creem payment page with the email locked; Creem's
public documentation does not say either way.

**In scope.** Spend at most a few minutes confirming that the public documentation
still does not answer it. Then **report it as blocked**, in one sentence, naming what
is needed: a real purchase on the owner's live Creem account. **Do not change the
owner's decision because of it.** Do not make a real payment.

**Acceptance.** The report carries this as an explicit blocked item with the reason,
not as a quiet omission.

---

### R12 — Cleanup

Low priority. Do it last, and only if it costs nothing.

- Remove the two untracked test files the previous run left in `C:\wt-head`
  (see R8). If the tool refuses, say so.
- Leave both worktrees registered — Claude uses them.
- Leave every other uncommitted file in the repository exactly as it is.

---

## 4. Requirement coverage list

| ID | Requirement | Must end as |
|---|---|---|
| R1 | Independent logic review of the money path, six questions answered | PASS with `REVIEW_LOGIC_1.md` |
| R2 | Adversarial second review, a different model family, cites the code itself | PASS with `REVIEW_LOGIC_2.md` |
| R3 | Vision review of every screenshot against the visual contract, images attached | PASS with `VISION_REVIEW.md`, or named items marked unverified |
| R4 | Stale images proved current, retaken, or marked unverified with the reason | PASS |
| R5 | The 12 dead checkout screenshots deleted, `SCREENSHOTS_STALE.md` rewritten | PASS |
| R6 | Every finding fixed, each with a test that failed before and passes after | PASS or ESCALATED |
| R7 | `VISUAL_REVIEW.md` finished — no "being retaken" left anywhere | PASS |
| R8 | ruff, mypy, the billing batch, the broad suites, the browser file — all captured | PASS |
| R9 | `TEST_INTEGRITY.md` — five questions answered with what was searched | PASS |
| R10 | `SUPERVISOR_REPORT.json` + `.md`, schema-valid, plain language | PASS |
| R11 | The Creem saved-card question reported as blocked, with what is needed | BLOCKED, stated |
| R12 | Cleanup of the leftover files in `C:\wt-head` | PASS or stated |

## 5. When to come back to Claude

Write `ESCALATION.json` per `.hm-orchestrator/policy/ESCALATION_CONTRACT.md` for any
of these, and keep `allowed_files` as narrow as you can:

- a finding that contradicts `OWNER_DECISION_WP8.md`, or needs the owner to choose
  between two money behaviours;
- the two reviewers disagree and the supervisor cannot settle it from the code;
- a test would have to be weakened, skipped or deleted to make something pass;
- a package would have to be installed;
- a visual requirement cannot be proved because the vision model cannot see the
  images, or a browser cannot run in the memory available;
- the same work package fails twice in a meaningful way;
- anything touching Sharia labelling, governed authority, approval or activation.

Nothing in this mission needs a schema migration or a new dependency. If you think it
does, that is an escalation.
