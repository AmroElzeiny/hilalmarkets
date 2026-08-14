# Release Readiness Report

**Verdict: NOT READY — INTERNAL ONLY.** The reasons are in section 10.

---

## 1. What was inspected

| | |
|---|---|
| Audit date | 14 August 2026 |
| Starting SHA | `f756ef9e1d416426a4d6f79e2571d665e381ae36` |
| Starting branch | `setup-chat-semantic-closeout` (working tree clean) |
| Audit branch | `phase6-launch-readiness-audit` |
| Ending SHA | `f6c78066` plus the commit adding this report |
| Commits | 8, in reviewable units; documentation-truth commits kept separate from code fixes |

The repository, measured rather than remembered:

| Thing | Count |
|---|---|
| Tracked files | 4,770 |
| Tracked Markdown files | 251 |
| Python lines under `src/` | 187,483 |
| Test files | 235 |
| Alembic migrations | 57 |
| GitHub workflows | 1 (`release-gate.yml`) |

**Read in full:** `README.md`, `CLAUDE.md`, `AGENTS.md`, `brand guide.md`,
`docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`, `docs/LAUNCH_CHECKLIST.md`,
`docs/PRIVATE_BETA_READINESS_REPORT.md`, `.github/workflows/release-gate.yml`,
`scripts/check_release_invariants.py`, `scripts/google_apps_script/waitlist_webhook.gs`,
`src/ai_market_monitor/core/{config,startup,launch_stage,site_content,copy_rules}.py`,
`src/ai_market_monitor/api/routers/{public,dashboard_api}.py`,
`src/ai_market_monitor/services/{ai_setup_chat,setup_chat_launch,setup_chat_agent,public_chat,web_auth,system_brain,waitlist_sheet_contract}.py`,
both environment examples, and the whole migration graph.

**Read in part, by targeted search:** the remainder of `src/ai_market_monitor` (356 files).
Searches were exhaustive for the specific questions asked — every reader of a setting,
every caller of a function, every occurrence of a name — but no claim below rests on a
file being read end to end unless it is listed above.

**Not inspected:** the 158 Notion export files, `VvvebJs/`, `Fixed site/`, and
`HilalMarkets_UI_Prototype/`. None of them is shipped by the application image.

---

## 2. Document ledger

251 tracked Markdown files. Classified by three real signals: the date of the last commit
that touched the file, whether it carries an archival marker, and — for the files listed
individually below — whether its claims were checked against code at HEAD.

| Last touched | Files |
|---|---|
| August 2026 | 38 |
| July 2026 | 158 |
| June 2026 | 55 |

### Current and authoritative (checked against code this audit)

| File | Evidence |
|---|---|
| `README.md` | **Was partly false; corrected.** See contradictions 1.1 and 1.2. |
| `docs/OPERATIONS.md` | **Was actively false; corrected.** See 1.1, 1.5, 1.6. |
| `docs/ARCHITECTURE.md` | **Was partly false; corrected.** Layer map omitted `observability/` and `core/launch_stage.py`; the bounded-agent paragraph described the opposite of the shipped configuration. |
| `docs/LAUNCH_CHECKLIST.md` | **Was partly false; corrected.** Said capability extensions are disabled; the release gate requires them enabled. Now opens with commands runnable from a clean checkout. |
| `CLAUDE.md` | **Was false; corrected.** Pointed at `Hilal_Markets_Brand_Rules.md` at the repository root. No such file exists there; the master is `brand guide.md`. |
| `brand guide.md` | Current. Its own Geometrica/Geometria correction (12 August) is accurate. |
| `docs/PRIVATE_BETA_SOAK_RUNBOOK.md` | Current. Procedure matches `scripts/audit_private_beta_soak.py`. |
| `.github/workflows/release-gate.yml` | Current, with one wrinkle: it sets `AI_AGENT_CONTROL_ENABLED=true`, `AI_AGENT_SHADOW_MODE=false`, `AI_AGENT_ROLLOUT_PERCENT=100` in the CI environment. Those have no effect on what it tests, because `APP_ENV=test` and nothing routes to that coordinator. Harmless, and misleading to read. |

### Historical / archival

| File | Marked | Note |
|---|---|---|
| `docs/PRIVATE_BETA_READINESS_REPORT.md` | **Newly marked** | Verdict still stands. Three facts in it are stale — see 1.6. README linked it as the *current* status until this audit. |
| `TRACEEDGE_CURRENT_RATING.md` | **Newly marked** | June 2026 product rating under an earlier product name. |
| `TRACE_EDGE_LIVE_DEPLOYMENT_REPORT.md` | **Newly marked** | July 2026. Superseded by `docs/PRODUCTION_DEPLOYMENT.md`. |
| `DEPLOY_TRACE_EDGE_LIVE.md` | **Newly marked** | Hostnames, image names and variables are not the ones the product ships. |
| `docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md` | Already marked | |
| `docs/LIFECYCLE_INVESTIGATION_AND_MONITOR_NAMING_REPORT.md` | Already marked | |
| `docs/SETUP_OBSERVABILITY_IMPLEMENTATION_REPORT.md` | Already marked | |
| `docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md` | Already marked | |
| `docs/SHARIA_METHODOLOGY_IMPORT_PACK_IMPLEMENTATION_REPORT.md` | Already marked | |
| `docs/VERIFIED_STRATEGY_MONITORING_IMPLEMENTATION_REPORT.md` | Already marked | |
| `Notion/.../19_Glossary.md` | Already marked | |

### Not individually verified

The remaining **~230** files. They are dated implementation reports, specifications and
Notion exports. Their age is recorded above; their claims were not checked against code
one by one, and this report does not classify them as current. **A committed Markdown
report is not evidence of anything** — that rule is why they were not treated as input.

Nothing was deleted. Every historical record is still in the repository.

---

## 3. Contradiction register

### 1.1 The Setup Chat kill switch — CONFIRMED, both sides were wrong

**Truth at HEAD.** `AISetupChatService.handle_message` hands every authenticated turn to
`SetupChatLaunchService` and returns (`services/ai_setup_chat.py:1313-1340`). The branch
that reads `AI_AGENT_CONTROL_ENABLED` (`:1550`) sits *below* that return and is reachable
only through `SETUP_CHAT_LEGACY_TEST_COMPAT_ENABLED`, which deployed startup refuses
(`core/config.py:733-736`).

- **README was right** that the flag is not a Setup Chat switch.
- **README was wrong** that "there is no Setup Chat feature flag". There are seven.
- **`docs/OPERATIONS.md` was wrong twice**, and one was an incident instruction: it called
  the flag "the Setup Chat kill switch" and told an operator to roll Setup Chat back by
  setting it `false` and restarting. That does nothing. Somebody following it mid-incident
  would have changed a variable, restarted, and watched Setup Chat carry on.

**Resolution.** OPERATIONS.md now has a *Stopping Setup Chat* section: a table of the six
switches that work, narrowest first, and what keeps working under each. README corrected.
`scripts/check_release_invariants.py:143` already required `AI_AGENT_CONTROL_ENABLED=false`
in production, so the code side had already settled this — only the documents had not.

### 1.2 Builder architecture — a deterministic non-AI path DOES exist

**Truth at HEAD.** `POST /api/v1/dashboard/setup-chat/sessions/{id}/builder-actions`
→ `SetupChatLaunchService.handle_builder_action` (`services/setup_chat_launch.py:724`).
Zero model calls. Operations are built by `engine/builder_operations.py` from fields the
server drew, then applied through `_apply_server_owned_operations` — the same authority the
assistant uses. `/setup-chat/builder-contract` supplies every legal mechanic, operator,
timeframe and Boolean limit; `/universe-options` supplies the per-account choices.
`dashboard_api.py:1481` reports `guided_builder_available: True` unconditionally.

**AI availability is therefore not a hard product dependency.** README did not say so; it
does now.

### 1.3 Brand and naming — CONFIRMED, larger than reported

| Reported | Truth at HEAD |
|---|---|
| `docs/OPERATIONS.md` title says TraceEdge | **Already fixed** 12 August 2026. The premise was stale. |
| "Geometrica" vs "Geometria" | `brand guide.md` corrected 12 August. **The copy at `Hilal-Markets-Website/src/imports/Hilal_Markets_Brand_Rules.md:221` still said Geometrica** — and called itself "the master reference", as did the root file. Fixed; the copy now says which one wins. |
| "Hilal Markets" vs "HilalMarkets" | **184 prose occurrences** across 56 files a customer reads: both menus, the emails, Telegram, WhatsApp, and the assistant's replies in five languages. |
| "Sharia" vs "Shariah" | The lint owned this rule but scanned only the public website. Widening the scope found **six more violations** in `auth.html`, the WhatsApp renderer, the WhatsApp service and the builder contract. |
| TraceEdge residue | `contact@trace-edge.com` was the **default value of `SUPPORT_EMAIL`** (`core/config.py:517`), so every deployment that had not overridden it showed a customer an earlier product's inbox. The chat UI's own brand scrub rewrote "TraceEdge" to "HilalMarkets" — replacing one brand-guide violation with another. |

**Resolution.** The rule now lives in `core/copy_rules.py` beside the other three, and the
scanned surface list covers the places the product speaks that are not templates.

**Boundary recorded, nothing renamed:** identifiers, CSS classes, asset filenames, the
`HilalMarkets/1.0` User-Agent, the import-pack directory, database values, enum members and
migration history are untouched. The lint pattern cannot match them by construction. One
further exception is deliberate: the `description=` on a compiled strategy artifact
(`engine/strategy_compiler_v2.py:99`). Compiled artifacts are hashed and an approval binds
to that hash, so editing it is a stored-contract change, not a copy fix.

### 1.4 Public metadata — the reported defect was already fixed; a different one was not

**Truth at HEAD.** `core/site_content.social_image_url` (`:73-93`) forces `https` for every
real host and is the single owner; `api/routers/public.py:159` is its only caller. A
deployment on a real domain publishes an absolute HTTPS `og:image`. `localhost` is a named
exception, because forcing HTTPS on a machine with no certificate breaks the local page and
proves nothing. Deployed startup additionally refuses a non-HTTPS `PUBLIC_OG_IMAGE_URL`
(`core/startup.py:172-175`).

**What was still broken:** the committed landing bundle,
`static/landing/index.html:31,37`, published `og:image="/static/landing/…"` — a **relative**
address, which a scraper resolves against nothing. That file is tracked, is served under
`/static`, and gave one page two answers.

**Resolution.** `vite.config.ts` now resolves the preview image against `VITE_SITE_URL` and
emits **no** tags when no origin is configured, rather than emitting a broken one. The
committed bundle matches. Regression tests assert absolute-and-HTTPS across four real hosts,
the local exception across three, and the absence of relative tags in the bundle.

### 1.5 Waitlist webhook — CONFIRMED, and it was inside the repository

**Truth at HEAD.** The server sends exactly `secret, email, name, source, country, status`
(`services/waitlist_sheet_contract.py:29-36`). The committed receiver authorised on
`payload.webhook_secret` (`waitlist_webhook.gs:68`) and required `payload.event_id`
(`:78`) and `payload.submitted_at` (`:85`) — **none of which the server sends**. Deploying
it would have answered `unauthorized` to every signup, and the rejection would have looked
like an ordinary delivery failure in the retry log.

`tests/unit/test_invariant_waitlist_sheet_payload.py` existed and passed. It only checked
the server's half. That is precisely how the two halves drifted.

**Resolution — both.** The receiver is rewritten against the contract and now dedupes by
email address (the right key for a waitlist, and the only one available without a delivery
id). Regression tests read the committed `.gs` and the Python contract together. **And**
because nothing in this repository can change what is running in Google Apps Script, the
exact redeploy procedure is written into `docs/OPERATIONS.md` and recorded as a blocking
external dependency in section 9.

### 1.6 Launch stage vs repository claims — CONFIRMED, and it was a code defect too

**In documents:** README linked `docs/PRIVATE_BETA_READINESS_REPORT.md` as "the current
private-beta correction status". It is dated 17 July and three of its facts are false at
HEAD — the virtual environment runs, there are 57 migrations not 33, and section 9 names
`AI_AGENT_CONTROL_ENABLED` as the kill switch. Its **verdict still stands**. Marked archival
with a table of exactly what changed.

**In code — the more serious half.** `core/launch_stage.py` declares itself the authority
("no route, template or assistant decides for itself") and holds `hidden_pages` per stage.
Nothing read it. The header, footer, sitemap and public assistant each read a **second,
identical** set, `WAITLIST_HIDDEN_PAGES` in `core/site_content.py:167`, gated on
`settings.waitlist_mode`.

The two sets agreed on contents and disagreed on behaviour, because `waitlist_mode` is
`stage_exposure.shows_waitlist` — true in exactly **one** of four stages. At `internal` and
at `private_beta_invite`:

| Surface | What the stage table says | What actually happened |
|---|---|---|
| Header and footer | `pricing`, `screened_market` hidden | Both shown |
| `sitemap.xml` | Both hidden | Both listed for indexing |
| Public assistant | `assistant_may_offer_account=False` | Could send an anonymous visitor to sign-in |
| schema.org | `advertises_pricing=False` | Published a purchasable `Offer` with a price |

Nobody had to make a mistake. Setting `LAUNCH_STAGE` to the product's honest value was
enough to trigger it.

**Resolution.** Every surface reads `settings.stage_exposure`. The navigation helpers take
the hidden-page set instead of a boolean that cannot express four states. Regression tests
run over **all four stages**.

Also corrected: `docs/LAUNCH_CHECKLIST.md` claimed "Billing, WhatsApp, and capability
extensions are disabled" while the release gate requires `CAPABILITY_EXTENSION_ENABLED=true`.

### 1.7 Repository hygiene — **this is a security finding**

**The premise is stale; the danger is not.** `ai_market_monitor.db.bak-20260803` is **not
tracked at HEAD**. It was added in `f0286e70` and untracked in `565c6e34`, and `.gitignore:72`
(`*.bak-*`) now covers it. `scripts/check_release_invariants.py:31-48` already refuses the
whole family of names.

**It is still in git history**, and it contains real data. The blob
`ea58e3e857bd0c2403385679bfb1e6998912c3ec` is byte-identical to the file on disk (verified
by SHA-256) and is the same blob previously committed as the live `ai_market_monitor.db`.
Read-only inspection of 157 tables:

| Table | Rows | Contents |
|---|---|---|
| `user_identities` | 1 | Real email `amroelzene@gmail.com`, and a **live `pbkdf2_sha256` password hash** |
| `users` | 1 | Status `active`, role `admin` |
| `sharia_governance_role_grants` | 4 | SYSTEM_ADMIN, RESEARCHER, REVIEWER, PUBLISHER for that user |
| `sharia_telegram_notification_attempts` | 234 | A real Telegram chat id, `1261328718` |
| `waitlist_signups`, `ai_setup_chat_*`, `strategies`, `billing_events`, `subscriptions`, `alert_deliveries` | **0** | No customer or third-party data |

**Assessment.** No customer data was exposed. What was exposed is the credential verifier
for an active administrator account with full governance grants, the owner's personal email
address, and an operational Telegram chat id — to anyone who has ever cloned this
repository, and permanently.

**Nothing was deleted.** The on-disk file is untracked and gitignored, and it is the only
convenient copy of that evidence; removing it is a decision for the owner. Remediation is in
section 8.

### 1.8 Further contradictions found

| # | Finding | Severity |
|---|---|---|
| **A** | **`AUTH_TEST_FIXED_CODE` was read without an environment guard by the System Brain login** (`services/system_brain.py:160`). `web_auth.py` checked `APP_ENV=test` *and* the six-digit shape; the governance console's second factor checked neither. Same setting, two readings, and the unguarded one guarded the admin console. | **High** |
| **B** | **41 settings existed in neither environment example**, while the release gate's parity check passed — it checked ten hand-written names. Missing: every Setup Chat surface switch, every AI spending ceiling, every provider pool and circuit bound, and `AUTH_TEST_FIXED_CODE`. An operator looking for a way to stop Setup Chat during an incident could not have found one. | **High** |
| **C** | `SUPPORT_EMAIL` defaulted to `contact@trace-edge.com` (see 1.3). Because the default sat on the field, the property's own fallback was unreachable. | Medium |
| **D** | The chat UI's brand scrub rewrote "TraceEdge" → "HilalMarkets" (`static/ai-setup-chat.js:120`), trading a stale-brand violation for a brand-guide §4 violation, in customer-visible text. | Low |
| **E** | Two documents each called themselves "the master reference" for the brand, and had drifted (see 1.3). | Medium |
| **F** | `.github/workflows/release-gate.yml:26-28` sets three `AI_AGENT_*` variables that have no effect on what CI tests. | Low (misleading only) |
| **G** | `src/ai_market_monitor/discord/` exists on disk with no tracked files — an untracked `__pycache__` shell left by the Discord retirement. Harmless; recorded so it is not mistaken for a live surface. | Informational |
| **H** | **An ordinary English word after "no" became a blocklisted market.** "with no carry-over" excluded `CARRY/USDT`. Found by a test that fails at the audit's own starting commit, so it is pre-existing, not a regression. Full detail in section 7. | **High** |

A–F and H are fixed. G is a leftover directory containing no tracked file.

---

## 4. Invariant table

Marked against real code paths. **"A test exists" was not accepted as evidence, and neither
was a document.** Where the path was not traced in this session it says `NOT VERIFIED`, and
that means *not checked*, not *broken*.

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | Server authoritative; client never decides | **HOLDS** | `dashboard_api.py:1734-1804` serves every legal mechanic, operator, timeframe and Boolean limit from the registry and compiler constants; `:1481` sets `guided_builder_available` server-side. Route-security audit over the live app returns no unprotected route. |
| 2 | One canonical strategy/draft state | **HOLDS** | `StrategyDraftV2` via `load_strategy_draft_v2`; a draft marked `strategy_state_authority == "v2"` is refused by the legacy writer (`ai_setup_chat.py:1349-1354`). |
| 3 | One canonical mutation path | **HOLDS** | `_apply_server_owned_operations` (`setup_chat_launch.py:1371`) is the only writer. Three callers: chat turn `:1318`, Builder `:824`, draft actions `:1159`. |
| 4 | Shariah screening fails closed | **NOT VERIFIED** | Deployed startup requires `SHARIA_SCREENING_ENFORCED` for live scanning and refuses the legacy-unscreened escape (`startup.py:228-233`); a `no_active_passport` refusal path and its alert exist. The screening decision itself was not traced. |
| 5 | Compiler authority fails closed | **NOT VERIFIED** | Not re-derived in this audit. Existing `tests/unit/test_invariant_*` cover it; per the hard rules that is not evidence here. |
| 6 | Provider requirements fail closed | **HOLDS (offer surface)** | `dashboard_api.py:1751-1767` decides availability against the feeds the configured adapter implements, and lists an unavailable mechanic with its reason instead of hiding it. Runtime refusal not traced. |
| 7 | Ownership and entitlement on every authenticated route | **PARTLY HOLDS** | *Authentication*: holds structurally. `audit_versioned_api_routes(app)` walks the live application's routes and requires each `/api/v1` route either to carry one of six authenticated dependencies or to be explicitly annotated `public_api`/`signed_webhook` **with a written reason** (`api/route_security.py:15-24, 39-44`). It returned zero unprotected routes, and the gate also fails if it discovers no routes at all, so it cannot pass by scanning nothing. *Per-object ownership*: **NOT VERIFIED across all handlers.** The setup-chat routes look the object up through `service.owned_session(...)` before touching it (`dashboard_api.py:1699, 1827, 1901`); the other handlers were not checked one by one. |
| 8 | Approval binds to the exact executable state | **HOLDS** | Approval takes `expected_schema_hash`, `expected_executable_version` and `expected_executable_hash` (`dashboard_api.py:1902-1908`); `setup_chat_launch.py:2722` refuses on hash mismatch; re-using a stale approval after an edit is refused by name (`ai_setup_chat.py:1514-1549`). |
| 9 | No silent nearest-capability substitution | **NOT VERIFIED** | The refusal wording exists (`agent_tools.py:206-207`) and the compiler invariants are documented in `CLAUDE.md`. Not re-derived here. |
| 10 | Idempotent operations cannot double-apply | **HOLDS (setup turns)** | `request_fingerprint` computed once before any routing (`setup_chat_launch.py:384`), replay returns the stored turn without re-executing (`:391-409`), and a replay records its own latency rather than overwriting the original's measured telemetry. Billing double-charge **NOT VERIFIED**. |
| 11 | Stale messages cannot mutate newer state | **HOLDS** | While a question is open, a message with no `question_id`/`step_revision` is refused before any routing decision (`setup_chat_launch.py:445-458`), and the compatibility escape is forbidden when deployed (`config.py:737-740`). |
| 12 | No hidden blockers; nothing stranded | **NOT VERIFIED** | Not traced. |
| 13 | AI/provider failure never renders as a Shariah or compiler failure | **NOT VERIFIED** | The runbook forbids it and degradation banners exist in `observability/banners.py`. The rendering path was not traced. |
| 14 | The model receives no approval, activation, network, SQL, filesystem or trade tool | **HOLDS** | Setup Chat calls the provider through `structured_call(... schema_model=PlannerIntentEnvelope ...)` (`setup_chat_agent.py:3254`) — a JSON-schema structured output with **no tools argument at all**. The model returns a typed plan; the server executes `apply_setup_turn` (`engine/setup_turn_execution.py:405`) itself. No `tools=` payload exists anywhere in the Setup Chat path. |
| 15 | No secret, prompt, model output or customer text in logs/metrics/issues | **NOT VERIFIED** | `observability/labels.py` owns the allowed labels and the rule keeping secrets out; `SecurityReviewService.redact` covers both waitlist secret names. Not exhaustively verified. |
| 16 | Observability is read-only with respect to product state | **NOT VERIFIED** | `hm_oi/builder_permissions.py:127-134` refuses launch-stage and feature-flag changes and production writes. Not traced end to end. |
| 17 | Every config key in both examples, production holding the fail-closed value | **WAS: DOES NOT HOLD → NOW HOLDS** | 41 settings were in neither file (finding B). Both now carry all 435 keys; the check derives from `Settings.model_fields` and fails on drift in either direction. |
| 18 | Exactly one Alembic head; upgrade works from the actual previous revision | **HOLDS** | One head, `9d21c4e75f80`. Verified on SQLite: empty → head; empty → `3ba17c6d40f2` (the actual previous revision) → head; and `downgrade -1` → `upgrade head`. `alembic check`: *No new upgrade operations detected*. **PostgreSQL not verified** — no Docker on this machine. |
| 19 | Startup fails closed on incoherent configuration | **HOLDS** | `core/startup.py:179-546` refuses ~60 unsafe combinations, including SQLite in production, well-known database passwords, placeholder secrets, mock providers, non-HTTPS base URLs, an objective nothing emits, and an alert routed through the subsystem it watches. This audit added `AUTH_TEST_FIXED_CODE`. |
| 20 | Public metadata, sitemap, robots and navigation match the true stage; admin and System Brain absent | **WAS: DOES NOT HOLD → NOW HOLDS** | See 1.6 for the failure and its blast radius. `robots.txt` disallows `/dashboard`, `/system-brain` and `/api/` (`public.py:700-713`); neither appears in `PUBLIC_PAGES` or the sitemap. **Not browser-verified in this session.** |

**Blast radius of the two that did not hold**

- **Invariant 20 / finding 1.6.** Search engines could index a Pricing page for a product
  nobody could buy, and an anonymous visitor could be sent to a sign-in page during a stage
  whose entire purpose is that there is no sign-in. No customer data at risk; the damage is
  a public promise the product could not keep, and it would have been made silently, by
  configuration alone.
- **Invariant 17 / finding B.** Nothing breaks. The cost is discovered during an incident:
  the switch an operator needs is not written down anywhere they will look.
- **Finding A (not one of the twenty).** Highest severity found. Had `AUTH_TEST_FIXED_CODE`
  been set in a deployed environment, the System Brain second factor would have become a
  known constant. It required an operator mistake to become live, and nothing in the system
  would have reported that mistake.

---

## 5. Capability truth table

| Capability | Status | Surface that enforces it |
|---|---|---|
| Describe a Watchlist in free text and have it compiled | Supported | `setup_chat_launch.handle` → `apply_setup_turn` |
| Author the same Watchlist with no AI at all | Supported | `handle_builder_action`, zero model calls |
| Approve an exact reviewed version | Supported | `/setup-chat/sessions/{id}/approve`, hash-bound |
| Scanner (one-time sweep) and Monitor (persistent) | Supported | `setup_scanner_enabled`, `setup_monitor_enabled` |
| In-app and Telegram alerts | Supported in code; **delivery never proven** | `STAGE_EXPOSURE[*].offered_channels` |
| Shariah screening from published Passports | Supported | `ShariaUniverseResolver`; fails closed |
| Certified user-scoped OHLCV capability extension | Supported | `CAPABILITY_EXTENSION_ENABLED=true` |
| Public waitlist signup | Supported (database); **Sheet projection blocked** | `waitlist_signups`; see section 9 |
| Public support assistant | Supported, read-only | `services/public_chat.py` |
| Paid checkout | **Not yet** | `BILLING_ENABLED=false`; mutations fail closed |
| WhatsApp delivery | **Not yet** | `WHATSAPP_ENABLED=false`; not mounted |
| Discord | **Retired** | New activation refused as `delivery_channel_retired` |
| Trade execution, brokerage, buy/sell advice, financial advice | **Never** | `core/product_boundaries.py`, permanent |

---

## 6. Evidence table

Columns are not merged. `impl` = code exists. The rest are separate claims.

`unit` means it was exercised in the 8,398-case run reported in section 7. `integration`
and `browser` say **not run in this session** — those suites exist and were not executed
here, which is a different statement from "they fail".

| Subsystem | impl | unit | integration | browser | CI | staging | prod-config |
|---|---|---|---|---|---|---|---|
| Setup Chat (agent path) | yes | **yes** | not run | not run | never green | no | example only |
| Guided Builder (no AI) | yes | **yes** | not run | not run | never green | no | example only |
| Launch-stage exposure | yes | **yes (new)** | not run | not run | never green | no | example only |
| Public site and metadata | yes | **yes** | not run | not run | never green | no | example only |
| Waitlist capture | yes | **yes** | not run | not run | never green | no | example only |
| Waitlist → Google Sheet | yes | **yes (new)** | not run | n/a | never green | no | **blocked** |
| Shariah screening/Passports | yes | **yes** | not run | not run | never green | no | example only |
| Alerts and delivery | yes | **yes** | not run | not run | never green | no | **no credentials** |
| Email outbox | yes | **yes** | not run | n/a | never green | no | **no SMTP proof** |
| Observability / paging | yes | **yes** | not run | n/a | never green | no | **destinations unset** |
| Billing | yes | **yes** | not run | not run | never green | no | disabled on purpose |
| Migrations | yes | n/a | **SQLite only** | n/a | never green | no | PostgreSQL unverified |

"CI: never green" is not a euphemism. `release-gate.yml` exists and has never been observed
completing successfully; no GitHub Actions run was inspected or triggered by this audit.

---

## 7. Exact test results

Commands were run from the repository root with `.venv\Scripts\python.exe` (Python 3.12.0).

| Command | Result |
|---|---|
| `python -m ruff check src tests scripts` | **All checks passed!** (exit 0) |
| `python -m mypy src/ai_market_monitor` | **Success: no issues found in 299 source files** (exit 0) |
| `python scripts/check_release_invariants.py` | **PASS: release exposure, route security, provider, and artifact invariants hold.** (exit 0) |
| `python -m alembic heads` | `9d21c4e75f80 (head)` — exactly one |
| `python -m alembic upgrade head` (empty SQLite) | exit 0 |
| `python -m alembic upgrade 3ba17c6d40f2` then `upgrade head` | exit 0 — upgrade from the actual previous revision works |
| `python -m alembic downgrade -1` then `upgrade head` | exit 0 |
| `python -m alembic check` | **No new upgrade operations detected** (exit 0) |
| `python scripts/audit_private_beta_soak.py` | `"status": "pass"`, all six duplicate-group counts `0` — **but every total is also 0**. See the caveat below. |
| `python -m pytest tests/unit/test_invariant_phase6_launch_audit.py` | **105 passed** |
| `python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly` | **8,398 passed, 362 skipped, 0 failed, 0 errors** (exit 0) |
| `python -m pytest` (whole suite, incl. browser) | **Did not finish.** See below. |

**`alembic check` and `alembic upgrade` failed first, and why.** Against the repository's own
`.env`, both raise `socket.gaierror: [Errno 11001] getaddrinfo failed` — `DATABASE_URL`
points at a PostgreSQL host that does not resolve from this machine. They were re-run with
`DATABASE_URL` pointed at a disposable SQLite file. **PostgreSQL migration behaviour is
therefore unverified**, and that is a real gap: SQLite does not exercise the constraint and
type behaviour the production database will.

**The soak audit passed over an empty database.** Zero duplicate groups out of zero rows
proves the query runs, not that the product does not duplicate work. It is not evidence.

**The whole-tree `pytest` run did not finish, and no full-suite number is claimed.** Two
attempts were made. The first ran against the clean starting tree for 65 minutes without
finishing, by which time edits had landed underneath it, so its result would have described
neither tree. The second reached 1% (about 144 of roughly 14,000 collected cases) at a rate
that projected to several hours. Both were stopped. The suite that **is** reported above is
the one `CLAUDE.md` prescribes for verification — `tests/unit tests/engine tests/interpreter
tests/services` — which excludes `tests/integration` and `tests/browser`.

**Seven failures were found, and attributed before being touched.** The first run of the
prescribed suite failed seven tests. Following the rule in `CLAUDE.md`, a clean
`git worktree` was created at the starting SHA `f756ef9e` and the suspect cases were run
there before anything was changed:

| Test | Attribution |
|---|---|
| `test_invariant_public_waitlist_surface.py` ×3 | **Mine.** `public_navigation()` changed signature (finding 1.6). Tests updated to pass the hidden-page set. |
| `test_waitlist_google_apps_script.py` ×2 | **Mine.** Asserted the delivery-id column the rewrite removed (finding 1.5). Updated to assert email deduplication and the new layout. |
| `test_capability_resolver.py` ×1 | **Mine.** Asserted the brand name without its space (finding 1.3). Expected string updated. |
| `test_strategy_state.py::test_approval_metadata_and_rejected_parameter_example_do_not_change_draft` | **NOT mine — fails at `f756ef9e` too.** A pre-existing defect this audit found. Fixed; see finding H below. |

No assertion was weakened and no case deleted. Each updated test now asserts the new
behaviour, which is the behaviour the finding above it required.

### Finding H — an ordinary English word after "no" became a blocklisted market

`engine/strategy_state._explicit_bare_asset_exclusions` ran its patterns under
`re.IGNORECASE`, which defeated the `[A-Z]` the pattern itself declared. Every ordinary word
following an exclusion word therefore became a market to exclude. The failing test's input
was *"Bind approval to the exact reviewed hash with no carry-over"*, and it put
**`CARRY/USDT` on the exclusion list** — a market the trader never mentioned, from a sentence
about approval policy.

The only thing preventing it was `reserved`: **24 hand-written English words**. That is the
hand-written-subset failure this repository keeps repeating, and a blocklist can only ever be
extended one incident at a time — `no problem`, `no changes`, `no rush`, `no worries` all did
the same thing.

**Fix:** the ticker group is matched case-sensitively while the keywords stay
case-insensitive, so capitalisation is the proof that a bare word is a ticker — the same rule
`turn_fragments._CONCATENATED_QUOTES` already applies for the same reason. A hyphen joining
the word to a lower-case one (`carry-over`) is also refused, because that is an English
compound rather than a pair. Lower-case symbols are not lost: written with their quote
(`ltcusdt`, `ltc/usdt`) they are read by `extract_symbols`, where the quote proves intent.

Covered by 14 new parametrised cases: eight sentences that must exclude nothing, and six
capitalised tickers that must still exclude, so the fix cannot cost the behaviour the
pattern exists for.

**Not run, with the exact reason:**

| Check | Blocked by |
|---|---|
| `pip-audit` | `No module named pip_audit` — not installed in this environment |
| `gitleaks` | Not on `PATH`; no binary present |
| Container build + Trivy (`container-scan` job) | `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` — the Docker daemon is not running |
| All four GitHub release-gate jobs, as jobs | No GitHub Actions run was triggered or inspected |
| PostgreSQL migration path | No PostgreSQL reachable; Docker unavailable |

Node v20.19.5 **is** available (CI specifies 22). Chromium **is** installed at
`chromium-1223`, so the browser suite is runnable in this environment.

---

## 8. Blockers

Everything that must be true before launch and is not.

| # | Blocker | Owner type |
|---|---|---|
| B1 | **An active administrator's password hash, the owner's personal email and an operational Telegram chat id are in git history** (blob `ea58e3e8`, commits `f0286e70` and earlier). Reachable by anyone who has cloned the repository, permanently. **Remediation:** rotate that account's password and the Telegram bot token first — rotation is what actually closes it. Then decide whether to rewrite history (`git filter-repo`, force-push, every clone re-cloned) or accept it as rotated-and-known. Do not delete the on-disk copy without the owner's decision; it is evidence. | Security / infrastructure |
| B2 | No green run of `release-gate.yml`, and branch protection does not require its four jobs. | Engineering |
| B3 | Migrations verified on SQLite only. The production database is PostgreSQL. | Engineering / infrastructure |
| B4 | No message has ever been delivered from this code to a real Telegram chat or a real mailbox. Alerting, the email outbox and operational paging are all unproven end to end. | Infrastructure |
| B5 | `OPERATIONAL_ALERT_TELEGRAM_CHAT_ID` and `OPERATIONAL_ALERT_EMAIL` are unset, so every page is recorded and nobody is woken. | Infrastructure |
| B6 | The deployed waitlist Apps Script has not been redeployed from the reconciled file (section 9). | Infrastructure |
| B7 | No staging environment and no seven-day soak. The soak tooling has only ever run over an empty database. | Engineering / infrastructure |
| B8 | Dependency and secret scanning have never run: `pip-audit` and `gitleaks` are not installed, and the container scan needs a Docker daemon. | Security |
| B9 | Legal/privacy review of waitlist and inquiry data collection, retention and cross-border processing. | Legal / privacy |
| B10 | Religious-governance sign-off: owner grants, explicit criterion decisions, separate approval and publication, and pilot Passport sign-off. No asset is customer-visible until this happens — which is the fail-closed behaviour working, and also a blocker. | Religious governance |
| B11 | Live provider proof: OpenAI model availability and pricing, Binance spot mapping, and behaviour under a real provider outage. | Provider |
| B12 | Neither `tests/browser` nor `tests/integration` was run in this session, so no rendered page was verified after the launch-stage change. Chromium **is** installed and the machine can run them; the whole-tree run was abandoned on time, not blocked. This is unfinished work, not an environment gap. | Engineering |

---

## 9. External dependencies

Not satisfiable from this repository.

1. **The Google Apps Script Web App.** Both halves of the waitlist contract now agree *in
   the repository*, but what is running is whatever was pasted into Apps Script previously,
   and this repository cannot tell you which version that is. The exact seven-step redeploy
   procedure — including "edit the existing deployment → New version", because creating a
   new deployment issues a new `/exec` URL the server will not post to — is in
   `docs/OPERATIONS.md`.
2. **Telegram** bot token, webhook secret, and a real chat for delivery proof.
3. **SMTP**: production sender and domain, SPF, DKIM, DMARC, bounce handling.
4. **OpenAI**: model availability, pricing confirmation, data-processing settings.
5. **Binance** spot availability and canonical symbol mapping.
6. **Cloudflare** Access and Tunnel/firewall for `/system-brain*`, plus the direct-origin
   and spoofed-header tests.
7. **PostgreSQL 16** and **Redis 7** instances for staging.
8. **GitHub Actions** — the release gate must actually run.

---

## 10. Verdict

# NOT READY — INTERNAL ONLY

**The evidence that justifies it:**

1. **B1 is decisive on its own.** A live administrator credential verifier is in the
   repository's permanent history, and it has not been rotated. Nothing about the product's
   quality changes that; opening anything to outside people before rotation is opening it
   with a known credential in circulation.
2. **The automated release authority has never passed.** `release-gate.yml` is the
   repository's own stated authority for automated checks, and no run of it has ever been
   observed green. Ruff, MyPy, the release-invariant script and the migration checks all
   pass locally, and that is genuinely good — but "passes on the author's laptop" is the
   claim, and it is a smaller claim.
3. **Nothing this product sends has ever arrived.** Not one Telegram message, not one email,
   from this code, to a real destination. A monitoring product whose alerting has never been
   observed working is not ready for anyone who would rely on the alerts.
4. **Migrations are verified on the wrong database.** SQLite passed; production is
   PostgreSQL.
5. **Two invariants did not hold at the start of this audit**, one of which silently widened
   public exposure whenever the launch stage was set to the product's honest value. Both are
   fixed and covered by tests. That they existed at all, in a repository this heavily
   documented, is the argument for requiring the gate rather than the report.

**Why not the next step up.** `READY FOR PUBLIC WAITLIST ONLY` would need only that the
public site be honest and that a stranger's email address be captured durably. After the
1.6 fix the site is honest, and `waitlist_signups` is durable and idempotent. It fails on
B1 and B9: an unrotated admin credential in public history, and no legal review of
collecting an email address from a member of the public.

**The specific evidence that would change this verdict**, in the order it must be obtained:

1. That account's password and the Telegram bot token rotated, with the history decision
   recorded either way (closes B1).
2. One green `release-gate.yml` run on GitHub, all four jobs, with branch protection
   requiring them (closes B2, B8).
3. `alembic upgrade head` against a real PostgreSQL 16, from empty **and** from
   `3ba17c6d40f2` (closes B3).
4. The browser suite run and green (closes B12).
5. A legal/privacy sign-off on waitlist collection and retention (closes B9).

With 1–5 done, and only then, the honest verdict becomes **READY FOR PUBLIC WAITLIST ONLY**.
Private beta additionally needs B4, B5, B6, B7, B10 and B11.

---

## 11. Minimum next gate

The shortest ordered sequence that advances the verdict by exactly one step, to
**READY FOR PUBLIC WAITLIST ONLY**. Nothing here needs staging, a provider or a soak.

| # | Do | Done when |
|---|---|---|
| 1 | Change the password on the `admin` account in `user_identities`, and rotate `TELEGRAM_BOT_TOKEN`. | The hash in blob `ea58e3e8` no longer verifies anything. |
| 2 | Decide history: rewrite with `git filter-repo` and force-push, or record acceptance in writing. | Written down either way. |
| 3 | `pip install pip-audit`, install `gitleaks`, run both, fix what they find. | Both exit 0. |
| 4 | Start Docker; `docker compose up --build`; run Trivy on the image. | `container-scan` reproducible locally. |
| 5 | Bring up PostgreSQL 16, run `alembic upgrade head` from empty and from `3ba17c6d40f2`, then `alembic check`. | Exit 0 on all three. |
| 6 | `python -m pytest tests/browser` with the installed Chromium. | Green, with the launch-stage change exercised on a rendered page. |
| 7 | Push `phase6-launch-readiness-audit`, let `release-gate.yml` run, require all four jobs in branch protection. | Four green jobs on a real run. |
| 8 | Legal/privacy sign-off on waitlist collection, retention and the privacy notice. | Recorded decision from a named person. |

Steps 1 and 2 are first because every later step distributes the repository further.

---

*This report describes what was executed. Where something was not run, the exact error and
the exact blocked check are named. No committed Markdown report was treated as proof of
anything, including this one.*
