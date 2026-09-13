# MISSION — money follow-up after hand-back run 20260913T162406Z-39060cc7

Date: 2026-09-14. Written by Claude Code (architect). This file is the mission for this run.
`current/MISSION.md` belongs to a different (Telegram) mission. Do not read it as yours and
never edit it.

## Baseline (measured by Claude)

- HEAD `98f56c3f`. D1–D5 of the earlier mission are committed.
- Hand-back run `39060cc7` fixed H-1 (money-owed row is `voided` after a late refund), H-2
  (cancel link never overwrites `refunded`), H-3 (NOWPayments amount is a JSON number),
  M-2 (source payment row lock), logic-M1 (phantom subscription) and L-2 (refund page words).
  Offline + integration suite green (30,934 passed). `ruff` and `mypy src` clean.
- The same reviews left these findings **open**. Claude re-checked each one in the code on
  2026-09-14. Read `.hm-orchestrator/runs/20260913T162406Z-39060cc7/REVIEW_HANDBACK_ADVERSARIAL.md`
  and `REVIEW_HANDBACK_LOGIC.md` for the full text.

## Concurrency rules (a second session is working in this repository right now)

- Another Claude session holds a takeover for the Telegram mission. Do **not** edit any of:
  `src/ai_market_monitor/telegram/*`, `services/telegram_account_links.py`,
  `api/routers/telegram.py`, `api/routers/dashboard.py`, `api/routers/dashboard_test.py`,
  `worker.py`, `core/auth_pages.py`, `core/dashboard_paths.py`, `services/onboarding.py`,
  `services/notifications.py`, `services/compliance_watch.py`, `services/admin_notifications.py`,
  `observability/alert_delivery.py`, `pyproject.toml`, `.gitignore`, any `tests/**/test_telegram*`.
  If a fix truly needs one of them, write ESCALATION.json instead.
- This machine has little memory. Before starting pytest or a browser, check that no other
  `python -m pytest` or browser process is running; if one is, wait for it to finish. Never run
  two at once.
- Never read `.env` / `.env.production`. Never git commit, push, reset, checkout, clean, stash.

## Test cadence (owner rule, 2026-09-13)

Run focused tests (the new test file + direct neighbours) after each work package. Run the
full verification **once**, at the end (WP5). Do not re-run the full suite after each step.

## Requirements

| ID | Requirement |
|---|---|
| R1 | A partial refund is not treated as a full refund (adversarial M-1). |
| R2 | One owner decides "the money kept from a payment"; every money reader uses it. |
| R3 | No `float` on a money value anywhere in `src/`, including `core/plans.py` (L-3). |
| R4 | One rounding rule for money (`ROUND_HALF_UP`, owner `core/money.py`) (L-4). |
| R5 | A refunded payment is not counted as an "unfinished attempt" for staff (L-1). |
| R6 | A second cancel of an already-cancelled subscription keeps the first `canceled_at` (logic L1). |
| R7 | Reviews, one full verification, SUPERVISOR_REPORT.json + .md. |

## Work packages

### WP1 — Partial refunds (R1, R2). Risk: HIGH (billing, money).

- **Objective:** a refund event's amount is read. Money and access follow the amount really
  returned, not the event name.
- **Authority:** `services/billing.py` (`REFUND_EVENT_TYPES`, `REFUND_ENDS_PLAN_EVENT_TYPES`,
  `_hydrate_checkout_data`, `_record_checkout_event`, `_end_refunded_plan`, `_minor_unit_amount`,
  `_stripe_amount`), `services/plan_replacements.py` (`payment_that_bought`,
  `apply_after_payment`, money-owed void), `services/affiliate_attribution.py`
  (`_amount_charged`), `services/system_brain_payments.py`, `db/models/commercial.py`,
  `tests/unit/test_invariant_refund_is_recorded.py`, `tests/unit/test_invariant_refund_after_plan_move.py`.
  Check the real payload field for the refunded amount of each provider (Stripe
  `charge.refunded` → `amount_refunded` in minor units; Creem; NOWPayments) in the existing
  normaliser and test fixtures. Do not invent a field name; if a provider's field is unknown,
  its amount counts as unknown.
- **Behaviour (Claude's decision):**
  - One parser reads the refunded amount (reuse the existing minor-unit parser; do not write a
    second one).
  - Amount unknown, or amount ≥ the payment amount → **full refund**: today's behaviour stays
    exactly as it is.
  - Amount known and smaller than the payment → **partial refund**: the attempt stays
    `completed`; the refunded total is stored (sum of distinct refund events, never counted
    twice for a replayed event); the plan is **not** ended; one critical staff alert says a
    partial refund arrived so a person can decide. A later refund that brings the total to the
    full amount becomes a full refund.
  - One owner function returns "money kept" = paid − refunded (never below zero). The plan-move
    money-owed value and the affiliate commission base read it. A money-owed row already
    written from a payment that is later partly refunded is re-valued or voided the same way
    H-1 voids it — one rule, in the existing void owner.
  - Storage: prefer an existing JSON/metadata column on the attempt. A new column needs an
    Alembic migration with `op.f()` names; only do that if no existing field fits, and say why.
- **Forbidden:** changing the full-refund path's results; guessing a refund amount; ending a
  plan on a partial refund; editing the files in "Concurrency rules".
- **Acceptance / evidence:** failing-first test `tests/unit/test_invariant_partial_refund.py`,
  parametrised over every refund event name × every provider × {unknown, partial, exact full,
  over-full} amounts × {one event, same event replayed, two partial events summing to full}.
  Before (fails) and after (passes) JUnit files in the run folder. Neighbours:
  both refund invariant files, `test_billing_entitlements.py`, `tests/integration/test_paid_plan_replacement.py`,
  affiliate tests.
- **Escalate if:** a provider sends no amount at all for any refund; a migration is needed.

### WP2 — One money-kept reader for staff (R5). Risk: low.

- `services/system_brain_payments.py:359-364` counts `refunded` (and `cancelled`) as
  unfinished. An unfinished attempt is one still open (`creating`, `pending`, `processing`).
  Use an allow-list of open states, from one shared constant with the billing module, not a
  new word list. Failing-first test over every attempt status.

### WP3 — Money serialisation and rounding owner (R3, R4). Risk: medium.

- `core/plans.py:330-340` pushes prices through `float`. Route them through one function in
  `core/money.py` that returns a JSON number exactly equal to the 2-decimal text (the browser
  contract stays a number; the React bundle and templates must not need a change). Prove it
  for every plan, both cycles, every offer and every discount percent.
- Money `quantize` calls without `ROUND_HALF_UP`: `services/billing.py` (`_stripe_amount`,
  `_minor_unit_amount`, around lines 2508 and 2517), `services/affiliate_attribution.py:528,535`,
  `services/affiliate.py:366,772-777,813`. All must use the `core/money.py` owner.
  Search the whole `src/` for any other money quantize or `float(` on money.
- Extend the D3 source scan in `tests/unit/test_invariant_money_on_the_wire.py` to all of
  `src/ai_market_monitor` (not four modules), and add a scan that bans a money `quantize`
  outside the owner. Add a half-cent commission case (`1.00 × 12.5 %` → `0.13`).
- In the report, say plainly that stored commission rows are not rewritten; only new ones use
  the one rule.

### WP4 — Keep the first cancellation time (R6). Risk: low.

- `services/billing.py:2984` and `:3121` overwrite `canceled_at` unconditionally. Keep an
  existing value (the rule already used at `:3815`). Failing-first test: a second refund / cancel
  event on an already-cancelled subscription leaves `canceled_at` unchanged.

### WP5 — Reviews and final verification (R7).

- Logic review by `minimax-m3`; adversarial review of WP1 and WP3 by `deepseek-v4.1-flash`
  (billing is high risk → two model families). Each finding is fixed with a failing-first test
  or escalated.
- One full verification at the end, after checking no other pytest is running:
  `ruff check src tests scripts`, `mypy src`,
  `pytest tests/unit tests/engine tests/interpreter tests/services tests/integration -q -p no:randomly --junitxml=<run>/FINAL_offline.xml`.
  If a failure appears in a Telegram test while the other session is editing, record it and
  re-run that file once; do not change Telegram files.
- Write `SUPERVISOR_REPORT.json` and `SUPERVISOR_REPORT.md` (very simple English, tables).
  Delete scratch `_*.py` / `_*.ps1` files you created in the run folder once the report cites
  their results.
