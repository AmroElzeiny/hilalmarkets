# MISSION — hand-back after Claude's takeover: review, one mechanical edit, verify, report

Owner: Claude Code (architect and final judge). Written 2026-09-13.
Branch `cloudflare-access-service-tokens`, commit `75b19580`. Tier: **Deep** (money path).

## 0. What happened — read, do not redo

The work is the follow-up mission in
`.hm-orchestrator/runs/20260913T103127Z-e7a9da76/MISSION.md` (D1–D6). Status:

| ID | Work | Who | Evidence |
|---|---|---|---|
| D1 | A refund of a completed payment is recorded; no money owed back from a refunded payment | OpenCode run `20260913T002503Z-fdd3949e` | `…fdd3949e/WP_D1_EVIDENCE.md`, `tests/unit/test_invariant_refund_is_recorded.py` |
| D2 | One held-plan owner for every checkout decision | OpenCode run e7a9da76 | `D2_*` files in run e7a9da76, `tests/unit/test_invariant_one_held_plan_owner.py` |
| D3 | One owner for money on the wire (`core/money.py`) | OpenCode run e7a9da76 | `D3_*`, `tests/unit/test_invariant_money_on_the_wire.py` |
| D4 | Evidence refs are pointers | OpenCode run e7a9da76, **finished by Claude** | `D4_*`; Claude's change below |
| D5 | Corner assistant never hides a focused control | **Claude** (takeover) | `D5_before.xml` (3/3 fail), `D5_after.xml` (3/3 pass) |

Run e7a9da76 stalled because the OpenCode Go weekly usage limit was reached at 15:28
local (`Weekly usage limit reached` in the OpenCode log); the owner ordered a takeover.

### Claude's changes during the takeover

- `observability/issues.py`: an identifier-shaped evidence ref that the content check
  objects to (an `sk-…` key, a JWT) is now **reduced to a digest pointer** instead of
  being refused. Refusing it threw away the whole issue — a critical billing alert lost
  because one pointer looked like a key. New helper `_carries_sensitive_content`.
  Refusals that remain concern only the prefix, which is written in our own code.
  Test changed: `test_a_credential_inside_an_identifier_shaped_ref_is_refused` (added by
  run e7a9da76 itself, not at HEAD) became `…_is_reduced_not_refused`, asserting the
  secret is not stored, the issue is written, and a repeat collapses into one row.
- `static/hm-hilal-chat.js`: `keepControlsClear()` publishes the widget's footprint as
  `--hm-corner-clearance`; a focused control under the widget is scrolled clear once
  smooth scrolling has settled (`whenScrollSettles`), clearing the whole widget column.
- `static/hm-shell.css`: `.app-content` bottom padding is
  `34px + max(cookie banner, corner clearance)`. The phone rule (≤760px) used to set a
  flat `24px`, which also dropped the cookie-banner room on phones — fixed to
  `24px + max(…)`.
- New browser test `tests/browser/test_hilal_never_hides_a_control_e2e.py`: every
  focusable control on market, create-monitor, opportunities, settings, subscription ×
  1440x900, 1024x768, 390x844. Before: 21 covered controls, all 3 widths fail. After:
  3/3 pass. Neighbours: `tests/browser/test_hilal_chat_e2e.py` 49 passed.

## 1. Limits

Test cadence rule in `ROUTING_POLICY.md` is mandatory. One browser at a time. Never read
`.env*`. No git commit/push/reset/clean. Never weaken, skip or delete a test. Do not touch
`.hm-orchestrator/current/MISSION.md` (it holds a different, pending Telegram mission).

## 2. Work packages

### H1 — One release key (worker, `qwen3.8-flash`)
Two shipped static files changed (`hm-hilal-chat.js`, `hm-shell.css`), so browsers must
fetch them again. Replace **every** `?v=20260911-ask-tag` under
`src/ai_market_monitor/templates/` with `?v=20260913-corner-clear` — all 36 files, one
value everywhere. Prove: zero old values left, one distinct value in all templates, and
`tests/unit/test_dashboard_static_assets.py` passes.

### H2 — Logic review (`hm-reviewer-logic`, minimax-m3)
Review D1–D4 and Claude's D4/D5 changes: the diff of `services/billing.py`,
`services/plan_replacements.py`, `core/money.py`, `observability/issues.py`,
`static/hm-hilal-chat.js`, `static/hm-shell.css` and the new tests. Write
`REVIEW_HANDBACK_LOGIC.md` in this run folder.

### H3 — Adversarial review (`hm-reviewer-adversarial`, deepseek-v4.1-flash)
Attack D1 and D3 only: can money still be paid back twice (refund arriving before,
during and after a plan move; refund replayed; refund for an attempt we cannot match)?
Can an amount still leave the system inexact? Write `REVIEW_HANDBACK_ADVERSARIAL.md`.
A finding is fixed by a worker (qwen3.8-flash) with a failing-first test, or escalated.

### H4 — Vision review (`hm-reviewer-visual`, called **directly** with `--file`)
15 screenshots in `test-results/browser/corner-clearance/*-bottom.png` (5 pages × 3
widths, each at the bottom of the page). Judge against `VISUAL_CONTRACT.md`: the widget
unchanged in look and position, and no control under it. Write `VISUAL_REVIEW_HANDBACK.md`.
Then run the contrast browser checks once:
`tests/browser/test_dashboard_shell_e2e.py` (already passes after D5 if Claude's run
shows it) and `tests/browser/test_dashboard_test_account_e2e.py`.

### H5 — One full verification, then the report
After H1–H4: `ruff check src tests scripts`; `mypy src`; `pytest tests/unit tests/engine
tests/interpreter tests/services tests/integration -q -p no:randomly --junitxml=…`. If
anything fails: compare with `C:\wt-head`, fix, re-run failing files only, then the full
run once more. Remove clutter: `temp_*.py`, `dashboard.py`, `dashboard_test.py` inside
`.hm-orchestrator/runs/20260912T193031Z-4125cfbe/`. Write `SUPERVISOR_REPORT.json` + `.md`
per the contract, plain language, requirement IDs D1–D6 and H1–H5.

## 3. Requirement coverage

| ID | Must end as |
|---|---|
| D1–D5 | PASS with the evidence named above, confirmed by H2/H3/H4 |
| H1 | PASS: one release key, zero old values |
| H2, H3 | written, every finding fixed or escalated |
| H4 | PASS or UNVERIFIED with the exact reason |
| H5 | full run counts from the junit files |

Escalate only for a finding that contradicts `OWNER_DECISION_WP8.md`, a need to weaken a
test, a package install, or two failures on one package.
