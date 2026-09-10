# Mission — Phase A: make the Pro plan payable, and end the silent dead control

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Deep
Risk: **high** (billing, money)
Date: 2026-09-10

This is **phase A of three**. Deliver **only** what this file asks. Phases B and C follow.

- Phase B: upgrade and downgrade proven in practice.
- Phase C: screenshots against the visual contract, the full regression suites, the
  adversarial reviewer and the vision reviewer.

The earlier full-mission text is preserved at
`.hm-orchestrator/runs/20260909T222439Z-b484f87c/MISSION.md` if you want the whole picture.

## Why this phase is small

Three previous runs were killed because this machine ran out of memory. Nothing was lost —
**no product code was ever changed** — but the work must now arrive in small pieces.

**Memory rules. These are hard limits, not advice:**

- **Do not run the full offline suite.** Targeted test files only, one at a time.
- **Do not start a browser.** No screenshots in this phase; phase C does that.
- **One worker at a time.** No parallel sub-agents.
- **One reviewer**, at the end, not in parallel with the work.
- **Write evidence files as you go**, not only at the end. If this run is killed, the next one
  must continue from what you already wrote.

## What is already done — do not repeat it, it was paid for

Two failing tests already exist in the working tree, written by earlier runs. Both are
untracked. **Keep them and build on them.**

| File | What it proves |
|---|---|
| `tests/unit/test_invariant_billing_offers.py` | The page offers card and crypto as available while the server answers the POST with 400. Also: the billing dialog shows no reason, and a required radio is hidden by the CSS. |
| `tests/integration/test_plan_change_journey.py` | Written for the upgrade and downgrade journey. **Phase B owns it. Do not delete it, do not finish it here.** |

Failure evidence: `.hm-orchestrator/runs/20260909T213031Z-702ebe0d/wp1-failing-tests.txt`.

Start by running **only** `tests/unit/test_invariant_billing_offers.py` and confirming it
still fails. That is your baseline.

## The finding that shapes the fix

Every failure is in the same case: **the account already holds a different plan.**

The page says card and crypto are available. The customer clicks. The server refuses with
400. The screen says nothing. That is the reported bug, in full.

This also means buying Pro while already on Trader **is** the upgrade path. Before writing any
fix, check whether a plan change is being wrongly sent through the new-purchase checkout
instead of the plan-change path. If it is, that is the root cause and the fix belongs there.

Phase A must fix the cause and make the refusal honest and visible. Phase B then proves the
whole upgrade and downgrade journey. Do not design phase A in a way that phase B must undo.

## Runtime/source-of-truth authority

- `src/ai_market_monitor/api/routers/billing.py` — the route that returns 400
- `src/ai_market_monitor/services/billing.py`
- `src/ai_market_monitor/services/plan_changes.py`
- `src/ai_market_monitor/services/entitlements.py`
- `src/ai_market_monitor/core/plans.py` — `plan_is_on_sale` is the one owner of "is this on sale"
- `src/ai_market_monitor/api/routers/dashboard.py` — builds `billing_plan_data`
- `src/ai_market_monitor/templates/hilal/dashboard/billing.html` (dialog), `checkout.html` (page)
- `src/ai_market_monitor/static/hilalmarkets-billing.js` — lines 179-180 and 240 decide whether the pay button can ever enable
- `src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css` — line 2490 hides the required radio
- `CLAUDE.md`, `AGENTS.md`, `docs/BILLING_SETUP.md`

Credentials are present in both `.env` and `.env.production` (`CREEM_API_KEY`,
`CREEM_PRODUCT_IDS`, `NOWPAYMENTS_API_KEY`, `BILLING_ENABLED`, both provider settings).
Claude checked key **names and set/empty state only**. Missing credentials are **not** the
cause. Do not go looking for one.

## Requirements for phase A

IDs match the full mission so coverage tracks across phases.

| ID | Requirement | Acceptance evidence |
|---|---|---|
| A-R2 | **Root cause named.** Say exactly why the page and the server disagree, with file and line. | The traced path to the one wrong value. "Looks like" is not a root cause. If the reading above is wrong, say so and give the real cause. |
| A-R3 | **A customer holding another plan can buy Pro by card**, or is told plainly why not. | The route no longer returns an unexplained 400 for this case. Test evidence. |
| A-R4 | **Same for crypto.** | Test evidence. |
| A-R5 | **No payment control is dead and silent.** Every control either works or shows a readable reason. | The billing dialog gains the same kind of plain explanation the checkout page already has at `checkout.html:79-89`. Reuse the existing `notice notice-error` pattern — invent no new visual language. A parametrised test over every plan × card and crypto × monthly and annual, where a silently dead combination fails. |
| A-R6 | **No form is blocked by a control the user cannot see.** | Submitting with nothing chosen produces a visible message, not silence. Test evidence. |
| A-R10 | **One owner.** Whether a plan can be bought, and by which method, is decided in exactly one place; every caller imports it. | Show the owner. Show no second copy survives in page, dialog, route, or JS. A fix that adds a second copy is the wrong fix. |
| A-R12 | **No test weakened.** | Answer explicitly: any test deleted, skipped, xfail'd, assertion widened, expected value changed, or special case added? If an expected value changed, say why the product behaviour is the authority. |

## Non-negotiable invariants

- **No completed real charge.** Creating a provider checkout session to prove the path is
  allowed. Spending real money is not. If a live charge is the only remaining proof, stop and
  escalate — that is the user's decision.
- **Never print a value** from `.env` or `.env.production`. Key names only.
- **Fail closed.** Never offer a method the server would refuse. Never guess a provider, never
  fall back to a default method.
- **Never weaken a test** to make the code pass.
- Messages must read for a beginner. No "entitlement", "provider", "capability", "proration".
- Copy rules from `core/copy_rules.py` apply: **Hilal Markets** in prose, **Shariah** in
  technical usage, and the forbidden-claims list applies to every customer-visible word.
- Do not touch the many unrelated uncommitted changes in the tree.
- Any migration constraint or index name goes through `op.f()`.

## Verification floor for this phase

- The reproduction test fails before the fix and passes after.
- The parametrised family test covers every plan × method × cycle. A fix that only helps Pro
  must fail it.
- `.venv/Scripts/python -m ruff check src tests` and `.venv/Scripts/python -m mypy src`.
- Targeted tests only. The full suite belongs to phase C.

## Reviewer policy

**One** independent logic reviewer, at the end: `opencode-go/deepseek-v4-pro`.
It must check the money path: right plan, right amount, no double charge, and no method
offered that the server would refuse.

The adversarial and vision reviewers run in phase C. Do not start them here.

## Completion condition

Report complete when A-R2, A-R3, A-R4, A-R5, A-R6, A-R10 and A-R12 each have PASS evidence or
a named blocker, and the reviewer's findings are closed or escalated.

If the cause turns out to be a commercial decision — a price, or a product that does not exist
at the payment company — write `ESCALATE_TO_CLAUDE`. Do not invent a commercial answer.
