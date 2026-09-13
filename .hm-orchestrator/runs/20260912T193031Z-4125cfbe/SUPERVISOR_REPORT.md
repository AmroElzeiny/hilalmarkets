# Supervisor report — WP8 review and evidence

Run: **20260912T193031Z-4125cfbe**
Written: 2026-09-13
Branch: `cloudflare-access-service-tokens`
Starting point: commit `75b19580`
Final answer: **COMPLETE, with one item that is explicitly blocked** (the Creem question).

This run continued a stopped run. The stopped run had already done the reviews R1, R2,
R4, R5 and a first vision pass. This report does not repeat that work. It reviews the
fixes the stopped run made afterwards, fixes what the reviews found, finishes the visual
review, and writes down the evidence.

---

## 1. What this run was, and what it started from

| Item | Value |
|---|---|
| Run id | `20260912T193031Z-4125cfbe` |
| Supervisor model | `opencode-go/deepseek-v4.1-flash` |
| Branch | `cloudflare-access-service-tokens` |
| Starting commit | `75b19580` |
| Work reached this run from | stopped run `20260912T152825Z-d6d10e2a` |
| Files changed by the mission | 65 files under `src/` and `tests/` (3948 added lines, 1347 removed) |
| Files touched by the owner | `CLAUDE.md`, `.opencode/*`, `ROUTING_POLICY.md`, `ROLE_MODEL_MAP.md`, `tools/hm-orchestrator/*` — left alone |
| Nothing was committed | no commit, push, reset, stash or merge was run |

The working tree already carried many unrelated changes before this mission. None were
reverted or reformatted.

## 2. Every requirement, and how it ended

| Id | Requirement | Result | Main evidence |
|---|---|---|---|
| R1 | Independent logic review of the money path (six questions) | **PASS** | `REVIEW_LOGIC_1.md` (stopped run) |
| R2 | Adversarial second review, different model family | **PASS** | `REVIEW_LOGIC_2.md` (stopped run) |
| R3 | Vision review of the screenshots, images attached | **PASS** | `VISUAL_REVIEW.md` + 6 `vision_fresh_*.txt` files |
| R4 | Stale images proved current, retaken, or marked unverified | **PASS** | `R4_STALE_IMAGES.md` + fresh section 1a |
| R5 | The 12 dead checkout screenshots deleted | **PASS** | `R5_DELETION_LISTING.txt` |
| R6 | Every finding fixed, each with a failing-then-passing test | **PASS** | `WORKER_FIX_REPORT.md`, `EVIDENCE_FIX_A_B.md`, `fix*.txt` |
| R7 | `VISUAL_REVIEW.md` finished, no "being…" text | **PASS** | `VISUAL_REVIEW.md` (new, supersedes the old) |
| R8 | ruff, mypy, billing batch, broad suites, browser captured | **PASS** | `*_final.txt` and `*_junit.xml` |
| R9 | Test integrity audit, five questions | **PASS** | `TEST_INTEGRITY.md` |
| R10 | `SUPERVISOR_REPORT.json` and `.md`, schema-valid | **PASS** | this file + the JSON (`VALID`) |
| R11 | The Creem saved-card question reported as blocked | **BLOCKED** | `R11_CREEM_SAVED_CARD.md` |
| R12 | Clean up the leftover files in `C:\wt-head` | **PASS** | shell listing; worktree status clean |
| R13 | Confirm the harness repair | **PASS** | `R13_HARNESS_CONFIRMATION.md` |

## 3. The root causes the reviews found, and what was done

This was the heart of the run. Two independent reviewers looked at the fixes the stopped
run had made **after** its own reviews. They found three real problems. All three are
fixed, each with a test that failed first.

| # | Problem (plain words) | Root cause | Fix | Test that failed first |
|---|---|---|---|---|
| 1 | A staff alert about a failed plan move could be **lost silently** when the payment company's event id has a capital letter or a strange character — the same "most expensive defect" the owner warned about, reached by a second road | The alert key and the alert evidence had **two different format rules**, and the key rule was lower-case only. `issues.py` owned the evidence rule but not the key rule | The key is now cleaned by the **same one owner** in `issues.py`, used on both the write and the read | `fix1_failure_before.txt`: the queue raised `IssueQueueError` before the alert was saved |
| 2 | Pages and the server gave **different answers** about which plan the account holds: the page drew a Pay button the server then refused | The checkout route used the new "lapsed card" grace set; the pages still used the old set. Two rules for one question | All four page surfaces now use the **same set the route uses** | `fix2_failure_before.txt`: 4 tests failed because the page said "buyable" for a plan the route refused |
| 3 | A late renewal event could still **bring a cancelled plan back to life**, giving two live subscriptions | The guard only watched "paid" events; other events (`subscription.updated`, `trialing`, and others) also set a plan active | The guard now covers **every event** that can set a plan active, not just money events | `fix2_failure_before.txt`: 5 of 6 event types failed before |

Two smaller money items were also closed:

| Item | What was wrong | What changed |
|---|---|---|
| Consent copy | The tick box said "I pay the new plan price **in full** today", but a discount code can lower the real charge | The sentence now says "the new plan's price today" on the billing page and in the consent constant |
| Renewal after a discount | A first period bought with a discount, then renewed at full price, was **refused as overpaid** and the renewal never landed | A confirmed renewal now accepts either the stored figure or the plan's current price; an amount above the plan price is still refused |

The owner's two safety rules are now tested across the whole family — card and crypto,
monthly and annual, every plan pair:

- after a move, exactly **one** live subscription and **one** recurring charge remain;
- an abandoned or failed payment leaves the old plan **untouched and still live**.

## 4. What the reviews said about the fixes

| Reviewer | Model family | Count | Result |
|---|---|---|---|
| Logic | MiniMax (`minimax-m3`) | 0 blocker, 0 serious, 4 minor, 4 false alarm | Fixes close their findings at the one owner |
| Adversarial | DeepSeek (`deepseek-v4.1-flash`) | 1 blocker (conditional), 2 serious, 4 minor | No new double charge, oversized refund or paid-without-plan; the three real items were fixed |
| Vision | DeepSeek vision (`deepseek-v4-flash-vision-exp`) | — | Contract items pass; floating-overlay overlaps recorded, not hidden |

The adversarial reviewer's blocker was "blocker if any card provider uses capital letters
in its event ids (for example Stripe), serious otherwise". It is fixed either way, so the
answer no longer depends on which provider is live.

## 5. Tests and checks — real numbers

| Check | Result |
|---|---|
| `ruff check src tests scripts` | All checks passed (exit 0) |
| `mypy src` | No issues in 426 files (exit 0) |
| Billing batch (6 files) | **283 tests, 0 failed, 0 skipped** |
| Broad offline suites (`unit`, `engine`, `interpreter`, `services`) | **28,211 tests, 0 failed, 0 errors, 489 skipped** |
| Browser: `test_visual_fixes_e2e.py` | **4 passed, 0 failed, 0 skipped** |
| Browser: `test_checkout_pay_button_e2e.py` | **22 passed, 0 failed, 0 skipped** |

Every number above comes from a file in this run's folder. The full output of each
command is saved there.

## 6. Test integrity (R9) — short answers

| Question | Answer |
|---|---|
| Any test deleted? | No |
| Any `skip` / `skipif` / `xfail` / `importorskip` added? | One was, by the stopped run; **this run removed it** and replaced it with an assertion. The one skip left in the touched files was already at `HEAD`. |
| Any assertion weakened? | No, on the reading of both independent reviewers |
| Any expected value changed? | Yes, three: "price in full today" → "price today" for the deliberate consent fix. Named in `TEST_INTEGRITY.md`. |
| Any test-only hardcode? | No |

## 7. Visual review (R3, R7) — short answer

A vision model looked at **28 current pictures** with the images attached. It measured
pixels, not CSS.

- Corner label "Ask AI": **pass** at all widths — rounded rectangle, right colour, on
  screen, centred, above the button.
- Corner at the bottom of the landing page: **pass** — label and circle wholly on screen,
  "back to top" above with a gap, header at the top with no empty band.
- Settings: **8 of 8** group buttons present.
- Subscription: no group "Ask AI" button, the old jump bar is gone.
- Checkout: a **live** pay button and the full payment form, and the **agreement tick box
  is visible above the button at phone width**. The suspected money defect is not there.
- Still open and written down, not hidden: the floating "Hilal — your AI assistant" line
  and the round corner button sometimes sit over page content. The line is built to
  travel across its box, so a still picture catches it mid-move. This is recorded as a
  residual visual risk.

## 8. What is blocked (R11)

**Whether Creem reuses a saved card on a new checkout cannot be answered from here.**
Creem's public documentation only mentions pre-filling the email; it says nothing about
saved cards. Answering it needs **one real purchase on the owner's live Creem account**.
This does not change the owner's decision of 2026-09-10.

## 9. Things still uncertain

- Real payment-company behaviour (webhook timing, whether a cancel is followed by an
  update event, discount behaviour across renewals) is not visible from this machine.
- An annual **new** checkout and an annual crypto held plan could not be simulated; the
  annual arms of the safety tests cover a held annual plan, not a new annual purchase.
- Contrast (V7) was not re-measured here; it is owned by `tests/support/contrast.py`.
- One server-side helper (`open_checkout_attempt`) still reads the old set; it is always
  preceded by the corrected check, so it cannot by itself reopen the page/server split.
- The floating assistant overlaps are cosmetic, not money or access problems.

## 10. Final answer

**COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM.**

Everything the mission asked for is done and proved with captured evidence, except the
Creem saved-card question, which is blocked on a real purchase and is named here and in
`R11_CREEM_SAVED_CARD.md`. The guard rails in `.opencode/*` were not active before
2026-09-12; they are active now.
