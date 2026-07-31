# Production authority and evidence — closure report

Written in simple words. Every claim below names the function that does the work and the
saved field that proves it.

---

## The short version

The setup chat had a gap in the middle. It checked which markets are allowed, then threw
that answer away and carried on with the markets the user had typed. Everything after
that point — the data check, the preview on screen, and the approval — worked on a
different list of markets from the one that was checked.

That gap is closed. The same list now travels from screening, through the data check, to
the screen the user reads, to the approval. And approval now compares what the user read
with what is true at that moment, and refuses if they differ.

---

## What was broken, and what it means for a user

| # | What was wrong | What a user would have seen |
|---|---|---|
| 1 | Screening picked the allowed markets, then returned the **unchecked** list | A setup could run on a market screening had said no to |
| 2 | The data check tested **one** market and reported the whole list as ready | "Every market is ready" when only one was looked at |
| 3 | Approval never compared the markets shown with the markets at that moment | You approve eight markets, the system saves eleven |
| 4 | A Favorites list was identified by a **date**, not by what is in it | Add a coin to the list and the system may not notice |
| 5 | A rule added and then removed in one message was reported as added | "I added your rule" for a rule that is not there |
| 6 | Editing any rule quietly deleted "we cannot do this" warnings | A warning disappears with nothing having fixed it |
| 7 | A question could only be asked if the user wrote "maybe" or "not sure" | "Alert me on a strong move" was refused instead of asking how strong |
| 8 | Two numbers of the same kind could swap places | "RSI period 14, confirm for 3 candles" could be saved backwards |
| 9 | Reply checking used English word patterns | A wrong claim in Arabic was never checked at all |
| 10 | A repeated message re-wrote its answer each time | The same message could get two different answers |
| 11 | User data and generated files were in the public repository | 21 test users, watchlists and chat history were shipped |

---

## Each fix, and where it lives

### 1. The checked markets are the markets that run

`SetupChatLaunchService._apply_screening_policy` used to return the definition it was
given. It now returns a `ScreeningExecutionResult` whose `secured_definition` holds
**exactly** the permitted markets.

- New file: `src/ai_market_monitor/schemas/screening_execution.py`
- Saved fields: `secured_definition`, `resolution_snapshot_id`, `resolution_snapshot_hash`,
  `policy_hash`, `resolved_at`, `considered_symbols`, `included_symbols`,
  `excluded_symbols`, `methodology_id`, `methodology_version`, `watchlist_snapshot_hash`,
  `dynamic_membership`
- The class refuses to exist if the two lists disagree
  (`ScreeningExecutionResult.validate_universe`)
- `apply_setup_turn` sets `definition = screening_result.secured_definition`, so the data
  check, the preview, approval eligibility and the reply all see the same list.

### 2. The data check says what it actually promises

`SetupChatLaunchService._runtime_preflight` used to fall back to
`sorted(listed)[:1]` — one market, picked alphabetically, possibly not even in the user's
list. It now follows **one** written contract, chosen by size:

| Contract | When | What is promised |
|---|---|---|
| `verified_all` | list is at or under `setup_preflight_symbol_cap` (25) | every market × every timeframe was checked |
| `policy_verified_runtime_fail_closed` | bigger list, or a list that can change on its own | the timeframes and features were checked on a sample; each market is checked again when it runs, and one without data is skipped, never guessed |

- Saved as `PreflightManifest`: `contract`, `verified_pairs`, `unverified_symbols`,
  `required_timeframes`, `symbol_cap`, `checked_at`, plus `manifest_hash`
- New settings: `setup_preflight_symbol_cap`, `setup_preflight_max_concurrency`
- Reaches the reply and the approval as `SetupTurnExecutionResult.preflight_manifest`
  (built by `_preflight_evidence`)
- `PreflightManifest.describe()` is the sentence a user reads, and it matches the promise.

### 3. Approval checks what you actually read

`SetupChatLaunchService.revalidate_for_approval` now runs seven checks in order and
refuses — never repairs — at each one:

1. the draft is the exact version and identity reviewed
2. it still compiles, and the preview still matches what was shown
3. the screening methodology is still active and still the same version
4. the Favorites list still holds exactly the same markets, by content
5. the markets re-resolve, and they are **the same set** the user read
6. every data feed the rules need is still available
7. the data check is fresh and keeps the same promise

Step 5 was missing entirely.

- Saved on the chat as `reviewed_screening_evidence`
  (`_store_reviewed_screening_evidence`), read back by
  `_load_reviewed_screening_evidence`
- Saved on the approval as `ApprovalBindingV2.screening_evidence`
- Compared by `ReviewedScreeningEvidence.differences_from`; the refusal sentence comes
  from `describe_change`, in plain words with no field names
- Bound facts: screening result, screening policy, methodology and its version, the market
  set, the Favorites list, the data check, and what that check promised

### 4. A Favorites list is known by what is in it

The old identity was `watchlist.updated_at.isoformat()`. The markets live in a different
table, so adding one need not move that date, and renaming the list always did.

- New file: `src/ai_market_monitor/services/watchlist_snapshot.py`
- Saved as `approved_watchlist_version`, now a `wlv1:<sha256>` content hash written by
  `watchlist_content_hash` at both places that set it
- `watchlist_identity_changed` treats an old date-style value as **changed**, so nothing
  from before is silently accepted

### 5. You are told what really happened, not each step

- New file: `src/ai_market_monitor/engine/operation_reconciliation.py`
- `reconcile_turn(before, after, operation_results)` compares the draft **before** the
  message with the draft **after everything**, and labels each operation
  `effective` / `overwritten` / `cancelled` / `no_net_effect` / `rejected`
- Saved as `SetupTurnExecutionResult.reconciled_operations`
- Only `effective` operations reach `applied_instructions`, the next turn's references
  (`final_condition_ids`) and the reply
- Approval is only invalidated by a real change: `material_change = material and
  reconciliation.executable_changed`

### 6. A warning is only cleared on purpose

`apply_strategy_patch` used to delete every "we cannot do this exactly" item whose turn id
matched the current turn, whenever that turn touched any rule. Nothing there proved the
problem was solved. That block is gone. Such an item can now only be removed by an
explicit `remove_unsupported_key` or by restoring a snapshot.

### 7. A question is asked because something is missing

`_ground_unresolved_operation` used to demand an uncertainty word. "Alert me on a strong
move" has none, so it was refused — and the word list was English, so an Arabic turn that
hedged clearly did not match either.

It now checks the target, in five steps: the segment asks for something; the target is not
already filled by this same message's own operations; it is not already filled in the
draft; the answer type fits the target; and no equal question is already open.

- Helpers: `_turn_determines_target`, `_draft_determines_target`, `_target_is_undetermined`,
  `_answer_schema_mismatch`, `_universe_is_determined`
- Model defaults do not count as answers (`_is_model_default`), and a universe counts as
  chosen only when a past turn really wrote it (`_authored`, using `source_provenance`)
- The word list survives only as an optional signal that lets a user **reopen** a filled
  slot. It can no longer block a question.

### 8. A number has to belong to its parameter

`RSI period 14 and confirm for 3 candles` has two candle counts. Checking values alone
accepted `period=3, confirmation=14` — grounded perfectly, and backwards.

- New file: `src/ai_market_monitor/engine/parameter_roles.py`
- Wired into `capability_contract._parameter_errors`, right after value grounding
- The words come from the registry: `x-semantic-unit`, `x-source-aliases`,
  `x-requires-role-phrase`, emitted by `capabilities._parameter_schema` from the shared
  `_ROLE_ALIASES` table
- A registry default the user never changed is exempt — they did not choose it

### 9. A claim in the reply carries its evidence

- New file: `src/ai_market_monitor/engine/claim_evidence.py`
- The reply now has `conversational_text` (free, asserts nothing) and `factual_claims[]`,
  each with `claim_type`, `text` and `evidence_ids[]`
- The server builds a ledger of citable ids and gives the model only that list
  (`citable_evidence_ids`). An operation the turn undid **has no id**
- `validate_claims` checks the ids. A "ready" claim must cite all four gates.
  `_rebuild_reply_from_validated_claims` drops any claim that fails and replaces it with
  text built from the evidence, so the user still learns the fact
- This reads ids, not sentences, so it behaves the same in every language. The old English
  pattern check is kept only as an extra filter that can reject, never accept.

### 10. A repeated message gets the same answer

The recovery sentence used to be generated when the retry arrived, so two retries could
produce two different answers. It is now written in the same transaction as the state it
describes, under `recovery_reply` in `SetupChatTurn.execution_result_json`, and recovery
(`_recovery_reply`) reads it.

### 11. The repository

| Item | Before | After |
|---|---|---|
| Tracked files | 6287 | 4527 |
| Compiled Python (`.pyc`, `__pycache__`) | 958 | 0 |
| Databases | 8 | 0 |
| Test videos, traces, screenshots | 213 | 0 |
| User data exports | 475 | 0 |
| Reports, logs, playwright output | 105 | 0 |
| Real `.env` files tracked | 0 | 0 |

`test-results/browser/browser-e2e.sqlite` held **21 users**, their web sessions, 11 chat
sessions, 23 chat messages, 2 Favorites lists and 2 Sharia assessments.
`ai_market_monitor.db` held 1 user and 247 assessments. `exports/` held 475 per-user
export files keyed by `user_id`. All are removed from version control and still on disk
locally. `.gitignore` already covered every one of them — the files simply predated it.

Credential scan: every tracked file, eleven provider key patterns. **No real credential
found.** The 14 hits are an SVG icon class starting `sk-`, deliberate fake
secrets inside the leak-detection tests, a `REPLACE_WITH_...` placeholder, and local
compose defaults.

**Deployment:** `docker-compose.yml` hardcoded `DATABASE_URL` with the password
`market_monitor` in each service's `environment:` block, which overrides `env_file: .env`.
An operator who set a real password in `.env` was deployed with the published one anyway.
Secrets now use `${VAR:?message}`, which stops the deployment with a named error instead of
falling back to something unsafe. `core/startup._weak_database_password` now refuses an
empty password, a well-known one, one equal to the user name, or one under 12 characters.

---

## Problems found and fixed that were not asked for

| Found | Fixed | How it was checked |
|---|---|---|
| Tests read the developer's local `.env`, so a test could pass on one machine and fail on another | Autouse fixture in `tests/conftest.py` isolates `Settings` from the on-disk file for the whole suite | `test_production_runtime_accepts_disabled_integrations_with_safe_core_config` failed at `HEAD` in a clean worktree and passes now |
| `test_agent_kill_switch_keeps_certified_capabilities_bootable` built an incomplete production config, so it failed for a reason unrelated to what it tests | Added `public_forms_enabled=False`; the startup guard was right, the settings were not | Failed at `HEAD`, passes now |
| The documented regression probe could not read **any** current recording and exited with "No matching conversations" | `scripts/replay_recorded_turns.py` now reads the current `canonical_state` shape and compiles it through the real V2 compiler | 108 recorded drafts across five runs: 0 crashes, 0 blocking findings |
| That same probe counted 32 unreadable old recordings as 32 compiler crashes | Pre-V2 recordings are named as unreadable, not counted as failures | Reported separately in the probe output |
| The release gate demanded `SHARIA_TEST_MARKET_ENABLED`, a setting deliberately removed, which another test forbids existing | Removed the expectation | `scripts/check_release_invariants.py` no longer reports it |
| Test database URLs used the password `password` | Replaced with a strong distinct value | `tests/unit/test_reliability_security.py` passes |
| The preflight manifest was appended as a fake "available" status row, which `_provider_status` reads and approval re-checks | Removed; the manifest travels on its own | Two integration tests that count checked pairs pass again |
| A stale manifest from a previous message could be shown next to a new list of markets | Cleared at the start of every preflight | Covered by the manifest tests |

---

## What was checked, and how

| Check | Result |
|---|---|
| `ruff check src tests scripts` | passes |
| `mypy src` | passes, 260 files |
| `pytest tests/unit tests/engine tests/interpreter tests/services tests/integration` | **all pass** |
| New invariant file `tests/unit/test_invariant_production_authority.py` | 95 tests, all pass |
| `scripts/replay_recorded_turns.py` on all five readable recorded runs | 108 drafts, **0 crashes, 0 blocking findings** |
| `scripts/check_release_invariants.py` generated-artifact rule | 794 findings → **0** |
| Credential scan of every tracked file | **0 real credentials** |

The invariant tests assert rules across whole families, not single examples: every claim
type, every net effect, every bound evidence field, every preflight contract, every
universe mode, and the same refused claim written in English, Arabic, Arabizi and Chinese.

---

## Not done, and why

| Item | Status | Reason |
|---|---|---|
| One paid model turn | **Blocked** | There is no `OPENAI_API_KEY` in `.env`, in the user environment, or in the machine environment. I cannot make a paid call without a key. |
| Release gate: `AI_AGENT_CONTROL_ENABLED=true`, `AI_AGENT_ROLLOUT_PERCENT=100` | **Needs your decision** | The gate demands the old coordinator be on in production; `.env.production.example` and the config comments say it is deliberately off and has no authority over Setup Chat. Which one is right is a product choice. |
| Release gate: "disabled billing must expose only the free plan" | **Needs your decision** | `visible_public_plan_codes` was deliberately changed to ignore `billing_enabled`; its docstring says checkout availability is represented separately. Whether the gate or the function is correct is a pricing choice. |
| Release gate: 5 "deprecated Watch Plan terminology" findings | **Needs your decision** | These are user-facing words on the dashboard and public pages. The replacement wording is a brand decision, and `Hilal_Markets_Brand_Rules.md` should set it. |

Instead of the paid turn, the real composer path is covered by an end-to-end test that
runs the actual agent, the actual payload builder, the actual evidence ledger and the
actual validation, with only the single network call faked
(`test_the_real_composer_path_keeps_only_evidence_backed_wording`, four cases). It proves
the composer is given `citable_evidence_ids`, that supported wording survives, and that
unsupported wording is replaced rather than shipped.

---

## The thing to check if you check one thing

The screened list, the data-checked list, the list on screen and the approved list are one
object. You can see it in one place:

```
_apply_screening_policy  -> ScreeningExecutionResult.secured_definition
apply_setup_turn         -> definition = screening_result.secured_definition
_runtime_preflight       -> reads definition.universe.include_symbols
_persist_draft_state     -> chat.draft_schema_json = that same definition
                         -> chat.context_json["reviewed_screening_evidence"]
revalidate_for_approval  -> re-derives it, compares, refuses if it moved
ApprovalBindingV2        -> screening_evidence saved with the approval
```

`ScreeningExecutionResult` will not accept a definition whose markets differ from the
markets screening permitted. That is the join that used to be missing.
