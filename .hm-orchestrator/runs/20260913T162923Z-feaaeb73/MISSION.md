# MISSION — the Telegram bot: one-tap connect, real links, real words

Owner: Claude Code (architect and final judge). Written 2026-09-13.
Branch `cloudflare-access-service-tokens`, commit `75b19580`.
Tier: **Deep** (account identity linking, idempotency, retry paths).

## 0. What the owner reported (their words, cleaned)

1. "When I connect Telegram, enter the long code given on the platform, it asks me to
   confirm, I confirm — and it keeps sending me the same message to confirm. I want just
   a button to confirm and a button to cancel. Easy."
2. "Update the Telegram bot to reflect real links, real descriptions, and fix any bugs."

Reported case 1 is a **symptom**. The scope is every way the connect step can fail to
finish, repeat itself, or trap a person — plus every link and every description the bot
sends.

## 1. Read first — do not rediscover

`CLAUDE.md`, `AGENTS.md`, `brand guide.md` (sections 4, 16, 17 and tone/voice),
`.hm-orchestrator/policy/ROUTING_POLICY.md` (test cadence rule is mandatory),
`.hm-orchestrator/models/ROLE_MODEL_MAP.md`, `SUPERVISOR_REPORT_CONTRACT.md`,
`ESCALATION_CONTRACT.md`.

### Code map Claude already established

| Piece | File |
|---|---|
| Dashboard mints the link + shows `/start link_<token>` | `api/routers/dashboard_test.py` `connections_page` (~786-821), template `templates/hilal/dashboard_test/connections.html` (~185-220) |
| Link records, ownership checks, completion | `services/telegram_account_links.py` — `create_dashboard_start_link`, `pending_dashboard_start_link`, `complete_dashboard_start_link`, and a **second** completion path `complete()` used by sign-up/sign-in with `telegram_link` (`api/routers/dashboard.py` ~1480-1970) |
| Bot: start link, confirm, cancel | `telegram/service.py` `handle_start` (~216), `handle_message` (~301-313), `handle_callback` (~662-665), `_handle_dashboard_start_link` (~3236), `_telegram_link_confirmation_message` (~3281), `_confirm_dashboard_telegram_link` (~3303), `_cancel_dashboard_telegram_link` (~3349), `_upsert_connection` (~3213), `_upsert_conversation` (~3366) |
| How buttons become Telegram markup | `telegram/adapter.py` `deliver` (~59-122) |
| Webhook + replay receipts | `api/routers/telegram.py` `process_telegram_update` (~83-181), `_outbound_to_dict/_outbound_from_dict` |
| Polling worker (default ON) | `worker.py` `_poll_telegram_updates` (~718-782) |
| Unique rules on the data | `db/models/accounts.py` `TelegramConnection`: `telegram_user_id` unique, **`user_id` unique**, **`chat_id` unique**; `UserIdentity` unique (provider, provider_subject); `db/models/telegram.py` conversation unique by `telegram_user_id` |
| Existing test of the happy path only | `tests/integration/test_telegram_service.py::test_telegram_dashboard_start_link_requires_confirmation_and_connects` (fresh Telegram user, typed "Yes, connect") |
| Page addresses — the one owner | `core/dashboard_paths.py` |

### Defects Claude found by reading (prove each with a failing test before fixing)

- **D-A. Action buttons are never attached to the message.** `adapter.deliver` turns any
  button set with no URL into a *reply keyboard* (text buttons under the typing box). So
  "✅ Yes, connect" / "Cancel" are not inline buttons: tapping sends a text message, the
  keyboard is `is_persistent`, and it **stays on screen after the connect succeeds**
  (the success message uses inline markup, which does not replace a reply keyboard).
- **D-B. The confirm step can trap a person for ever.** In `handle_message`, while
  `flow == "telegram_link" and step == "confirm"`, any text that is not "yes…connect" or
  cancel re-sends the whole confirmation prompt. "Confirm", "Yes", "ok" all loop. Worse:
  when `_confirm_dashboard_telegram_link` fails (`TelegramAccountLinkError`) it returns
  an error **but leaves the step at `confirm`**, so every later message — including the
  still-visible "Yes, connect" key and a bare `/start` — brings the prompt back.
- **D-C. Most likely real cause of the owner's loop: a Telegram-only account blocks the
  link.** Anyone who pressed Start in the bot before (the owner certainly did) already
  has an account created by `handle_start` → `OnboardingService.start`, holding the
  Telegram `UserIdentity` and `TelegramConnection`. `complete_dashboard_start_link` then
  raises `telegram_already_linked` ("already linked to another dashboard user") for what
  is really the same person — and D-B turns that into a loop.
- **D-D. The dashboard account already holding a Telegram connection** (for example a
  different Telegram account, or an old row) hits `TelegramConnection.user_id` /
  `chat_id` uniqueness → `IntegrityError` at flush, not a clean refusal.
- **D-E. The failure path in `process_telegram_update` cannot survive a database error.**
  The `except Exception` branch writes to `receipt` and calls `session.commit()` without
  `session.rollback()` first. After a failed flush that raises `PendingRollbackError`
  → HTTP 500 → Telegram re-sends the same update; in polling the update is re-fetched.
- **D-F. Two completion paths with different ownership rules** (duplicate decision —
  this repo's recurring root cause): `complete()` silently re-points a Telegram identity
  from *any* account, `complete_dashboard_start_link()` refuses from *any* other account.
- **D-G. A message has three hand-written serialisers** (`_outbound_to_dict`,
  `_outbound_from_dict` in the router, `_outbound_from_payload` in the service). Any new
  field (for example "these buttons are inline") that one of them forgets is lost on a
  replay, and the replayed message renders differently.
- **D-H. Editing a message with a reply keyboard.** `deliver` sends
  `editMessageText` with whatever `reply_markup` it built; Telegram only accepts an
  inline keyboard there. Check and fix.
- **D-I. Links that may not exist.** `service.py` sends `/dashboard/billing`,
  `/dashboard/trial`, `/how-it-works`, `/about`, `/dashboard` as the "home" page,
  and the link record's `target_path="/dashboard/integrations"` while the page is
  `CONNECTIONS_PATH` (`/dashboard/connections`). The front page is `HOME_PATH` (`/home`),
  the subscription page is `SUBSCRIPTION_PATH`. Error texts say "Integrations page".

## 2. Requirements (stable IDs — every one must appear in the report)

| ID | Requirement |
|---|---|
| R1 | After `/start link_<token>`, the bot sends **one** message with exactly two buttons **attached to that message** (inline keyboard): **Confirm** and **Cancel**. Plain beginner words: which account (email), what connecting does. |
| R2 | One tap on **Confirm** connects. The prompt message is **edited** to the result and its buttons are removed. The confirmation prompt is never sent again after a Confirm tap, whatever the outcome. |
| R3 | One tap on **Cancel** ends the step: message edited to "Cancelled — nothing was connected", buttons removed, pending token forgotten. |
| R4 | No trap: **every** outcome of Confirm (connected, already connected to this same account, link expired, link used, link invalid, Telegram belongs to a different real account, unexpected error) leaves the `telegram_link/confirm` step and gives one clear next step with a **real** link to the Connections page. While a prompt is open, a typed reply is read through **one** shared yes/no vocabulary (Confirm/Yes/Connect → confirm; Cancel/No/Back → cancel); anything else gets a short one-line reminder pointing at the two buttons — never the full prompt in a loop. Leftover reply-keyboard keys from before the fix ("✅ Yes, connect", "Cancel") must still work for messages already in people's chats. |
| R5 | Reproduce the owner's loop **through the real update path** (`process_telegram_update` with a fake adapter, the same way the webhook and the poller call it) for this matrix, before any fix: {fresh Telegram user, Telegram user who pressed Start before, dashboard account already holding another Telegram, Telegram already on a different real account} × {tap Confirm, tap Cancel, type "Confirm", type "Yes", send `/start` again, tap Confirm twice, same update delivered twice}. Record which rows loop, error, or 500 in `R5_REPRO_MATRIX.md`. Every row must pass after the fix. |
| R6 | **One ownership rule** for putting a Telegram account on an account, used by **both** `complete()` and `complete_dashboard_start_link()` (extract it; no second copy). Rule: (a) Telegram identity/connection held by an account with **no email or Google sign-in** (a Telegram-only account the bot created) → move the identity, the connection and the conversation to the signed-in account; leave that Telegram-only account's other data untouched and write an audit event naming both ids. (b) Held by a **different account that has an email or Google sign-in** → refuse in plain words ("This Telegram is already connected to another Hilal Markets account. Remove it there first on the Connections page."), no data changed. (c) The signed-in account already has a **different** Telegram connected → the prompt says so up front ("This replaces @old"), and Confirm replaces it cleanly without any uniqueness error. (d) Same Telegram already on this same account → success, no duplicate rows. |
| R7 | Idempotent and race-safe: double tap, the same callback delivered twice, the same update delivered twice, or two Confirm taps processed at the same time produce one connection, one consumed link, one audit event, no exception. Lock or re-check the link row inside the transaction; do not rely on the in-memory check alone. |
| R8 | D-E fixed: any exception while handling an update rolls the session back, then records the receipt and sends the fallback. No `PendingRollbackError`, no endless retry, for both the webhook and the poller. Test by forcing an `IntegrityError` inside a handler. |
| R9 | D-G fixed: one owner for turning `TelegramOutboundMessage` into a stored dict and back (in `telegram/types.py`), used by the router and the service. A test round-trips every field of the dataclass, so a field added later without serialising it fails. |
| R10 | Every link in **every** message the bot or Telegram alert delivery sends (`telegram/service.py`, `telegram/rendering.py`, `api/routers/telegram.py`, and the Telegram parts of `services/notifications.py`, `services/compliance_watch.py`, `observability/alert_delivery.py`, `services/admin_notifications.py` if user-facing) points at a page that exists **now**, taken from `core/dashboard_paths.py` or the public page owner — no hand-written path strings. Prove it: a test that collects every URL/path the Telegram code can produce and resolves each against the real app's route table (not 404, not a redirect to a missing page). Includes D-I. Old callback data already in sent messages keeps working. |
| R11 | Every description the bot sends describes the real product today: **Hilal Markets**, Shariah-screened crypto **monitoring** for beginners and Muslims; no buy/sell advice, no leverage, no guaranteed returns, no automatic trading; product is launched (no pre-launch words); plan names and prices only from their owners (`core/plans.py` and the price functions — never typed numbers); no menu item, screen or feature that no longer exists in the dashboard (check "Near-Miss Radar", "Performance / forward-test analytics", "Why No Alert", "Setup Replay", "Quick Scan", "Describe my setup", templates, `/free` against the shipped dashboard; where the dashboard has no such thing, the bot sends the person to the real page instead of promising it). Plain words a beginner understands; no internal field names ("deterministic", "near-miss threshold", "lifecycle state") without a plain explanation. `core/copy_rules.py` must pass on every bot string — add a test that runs it over them. |
| R12 | The bot's own Telegram profile: a script `scripts/telegram_bot_profile.py` that sets the command list, the description and the short description from **one** place in the code (which the bot's `/start` text and menu also use, so they cannot drift). **Dry-run by default** (prints what it would send); `--apply` actually calls the Bot API. Do **not** run `--apply`. Unit-test the dry run. |
| R13 | The Connections page text for Telegram (`connections.html` steps 1-3 and any bot-link error wording in `core/auth_pages.py`) matches the new flow: open Telegram → press Start → tap **Confirm**. **Text only** — no layout, class, CSS or JS change. If a JS status poll/wording depends on the old flow, fix it and name it. |
| R14 | Anything else wrong found on the way in the Telegram path is fixed and listed (what, fixed, how verified). |

## 3. Work packages (supervisor owns the smaller steps)

### WP1 — Reproduce (R5), then the one ownership rule (R6, R7, D-C, D-D, D-F)
- Objective: prove why the loop happens, then make linking correct for every ownership case.
- Authority: `services/telegram_account_links.py`, `db/models/accounts.py`, `db/models/telegram.py`, `api/routers/dashboard.py` callers of `complete()`, `services/onboarding.py` (how the Telegram-only account is created).
- In scope: the matrix test file (new, e.g. `tests/integration/test_telegram_connect_flow.py`), extraction of the ownership rule, locking/re-check.
- Forbidden: schema/migration changes (escalate if truly needed), deleting user data, merging strategies/trials/subscriptions between accounts, changing sign-in/sign-up behaviour beyond the Telegram ownership decision.
- Acceptance: matrix file fails on unfixed code for the looping/erroring rows, passes after; `tests/integration/test_dashboard_web.py` telegram-link tests still pass (if one encoded the old "steal from any account" behaviour, escalate — do not edit it to fit).
- Escalate if: the rule in R6(b)/(c) conflicts with an existing test or with an auth invariant.

### WP2 — Inline Confirm/Cancel and the step that always ends (R1-R4, R9, D-A, D-B, D-G, D-H)
- Objective: two attached buttons, one tap, message edited, no loop.
- Authority: `telegram/adapter.py`, `telegram/types.py`, `telegram/service.py` link methods, `api/routers/telegram.py` serialisers, `engine/active_question.py` / any existing yes-no vocabulary (reuse if one exists; do not write a third word list).
- Design constraint: add an explicit way for a message to ask for an **inline** keyboard (for example a field on `TelegramOutboundMessage`) rather than changing how every other bot screen renders today. Do not change other screens' keyboards in this WP. The edit uses `callback.message_id`; when the prompt came from a typed reply there is nothing to edit — send a new message and remove the reply keyboard (`remove_keyboard`) or restore the primary menu.
- Acceptance: adapter-level tests assert the exact Bot API payload (`inline_keyboard` with `callback_data` `telegram_link:confirm` / `telegram_link:cancel`; `editMessageText` with an inline markup or none); service tests for every R4 outcome; serialiser round-trip test.

### WP3 — Webhook/poller failure path (R8, D-E)
- Authority: `api/routers/telegram.py`, `worker.py` `_poll_telegram_updates`, `tests/integration/test_telegram_webhook.py`.
- Acceptance: forced `IntegrityError` test for both callers; receipt ends `ready`/`processed` with fallback delivered once; replay of the same `update_id` does not re-run the handler.

### WP4 — Real links (R10, D-I)
- Authority: `core/dashboard_paths.py`, the public router(s), `create_app` route table.
- Acceptance: the link-resolution test described in R10, parametrised per URL; no literal `"/dashboard..."` path string left in the Telegram code (an invariant test enforces it).

### WP5 — Real descriptions + bot profile + Connections page text (R11, R12, R13)
- Authority: `brand guide.md`, `Notion/HilalMarkets_Notion_Workspace/` (positioning + plans folders), `core/copy_rules.py`, `core/plans.py`, shipped dashboard templates for which features exist.
- Acceptance: copy-rules test over all bot strings; plan/price test comparing bot text with the owners; dry-run test for the profile script; connections page integration tests still pass (`tests/integration/test_dashboard_test_connections.py`).
- Forbidden: inventing features, prices, Shariah claims; any visual change to the page.

### WP6 — Final verification and review
- Full verification **once** at the end: `ruff check src tests scripts`, `mypy src`, `pytest tests/unit tests/engine tests/interpreter tests/services tests/integration -q -p no:randomly --junitxml=...` (integration included: account/identity data touched).
- Browser: `tests/browser/test_dashboard_test_connections_e2e.py` only, once at the end, one browser.
- Two independent reviewers from different families (high-risk: identity linking): logic `minimax-m3`, adversarial `deepseek-v4.1-flash`. Adversarial reviewer must specifically try: account takeover through R6(a) (can a person move a Telegram they do **not** control, or onto an account they are **not** signed into?), token replay, a Cancel then Confirm on an old message, a link minted for account A confirmed while the conversation belongs to account B.
- Every reviewer finding is fixed or escalated — never recorded as "found, not fixed".

## 4. Limits

- **A second run is working in this same folder at the same time:** billing hand-back run
  `.hm-orchestrator/runs/20260913T162406Z-39060cc7`. Never edit, format or revert its
  files: `services/billing.py`, `services/plan_replacements.py`, `core/money.py`,
  `observability/issues.py`, `static/hm-hilal-chat.js`, `static/hm-shell.css`, and the
  tests `test_invariant_refund_is_recorded.py`, `test_invariant_one_held_plan_owner.py`,
  `test_invariant_money_on_the_wire.py`, `test_hilal_never_hides_a_control_e2e.py`,
  `test_paid_plan_replacement*`, `test_oi*`. If the final suite fails in a test that
  touches none of the Telegram files you changed, check it against a clean worktree at
  HEAD (`C:\wt-tg`) plus only your changed files before attributing it — it may be the
  other run's work in progress. Report such failures as "not ours", with the proof.
- Do not run the full suite while the other run's `SUPERVISOR_REPORT.json` is missing
  **and** a pytest process is already running — wait for it, one pytest at a time.

- Routing per `ROLE_MODEL_MAP.md`; `run-model.ps1` allows only `minimax-m3`, `qwen3.8-flash`, `deepseek-v4.1-flash`, `deepseek-v4-flash-vision-exp`.
- Supervisor writes only under `.hm-orchestrator/runs/*`. Code goes to workers.
- Memory is tight: one pytest process at a time, output to a file; one browser at a time.
- **No evidence file for 30 minutes = stuck.** Write `STALLED_<step>.md` and change approach.
- Test cadence rule from `ROUTING_POLICY.md` is mandatory: per WP only its own tests + neighbours + ruff on changed files; full suite once at the end.
- Never weaken, skip, xfail, delete or widen a test. No `git commit/push/reset/clean/stash`. Never read or print `.env*`. Do not touch the unrelated uncommitted files (including the billing files of the other mission).
- No real Telegram Bot API calls, no paid model calls — fake adapter/transport only.
- A setting added or changed → all four env files rule in `CLAUDE.md`; the supervisor may not edit the two real files — escalate with the key names instead.
- Report in very simple English (CLAUDE.md "Reporting"): what the person using the bot will now see, per requirement ID, with the test that proves it.
