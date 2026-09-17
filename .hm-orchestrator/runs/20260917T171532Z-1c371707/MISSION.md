# MISSION — CONTINUATION: finish WP4/WP5 review, WP6 verification and the report

Owner: Claude Code (architect, final judge). Written 2026-09-17.
Execution owner: OpenCode Go supervisor. Risk: **high** (unchanged from the original mission).
Branch `main`, HEAD `9a19d488`.

## Why this run exists

The original run `20260916T184200Z-dd1e00e6` was killed on 2026-09-17 around 00:10 (machine
stop), not by a failure of the work. Its OpenCode session cannot be resumed (the provider
refuses the old encrypted reasoning items). This run **continues** it in a fresh session.

**The original mission is the authority for every requirement, invariant and rule.** Read it
in full with the Read tool at this exact path:
`.hm-orchestrator/runs/20260916T184200Z-dd1e00e6/MISSION.md`
Requirement IDs R1–R23, sections 4 (invariants), 6 (verification floor), 7 (reviewer policy)
and 8 (completion condition) apply unchanged. Nothing below relaxes them.

Also read (exact paths):
- `.hm-orchestrator/runs/20260916T184200Z-dd1e00e6/WP1_CALL_SITE_MAP.md`
- `.hm-orchestrator/runs/20260916T184200Z-dd1e00e6/WP1_HILAL_SURFACES.md`

## Baseline of THIS run

This run's `BASELINE_STATUS.txt` / `BASELINE_DIFF.patch` already contain the uncommitted
WP2–WP5 work of the original run. For the final diff audit, the true baseline is the ORIGINAL
run's `.hm-orchestrator/runs/20260916T184200Z-dd1e00e6/BASELINE_STATUS.txt` and
`BASELINE_DIFF.patch`. Mission changes = current tree minus that original baseline. The
unrelated uncommitted work listed in the original mission must still never be touched.

## What is already done (recovered by Claude from the old session — verify, do not redo)

| WP | State | Evidence recovered |
|---|---|---|
| WP1 | Done | the two map files above |
| WP2 | Done; logic (glm) + adversarial (qwen) reviews ran; blocker B1 (agent_control did not pass `session_key`) fixed with a test | milestone 1433 passed; `test_invariant_ai_provider.py` + phase6 audit 240 passed |
| WP3 | Done; budgets resized from real calls (old 900/1200 output caps truncated every high-effort turn); stale-file check showed no stale file edited | milestone 617 passed, ruff clean; real-call measurements were in the worker's report, which is lost — see WP-C |
| WP4+WP5 | Implemented, **not reviewed**, joint milestone **not confirmed** | see below |

Files changed by the original run (since its baseline): `services/ai_provider.py` (new),
`core/config.py`, `core/startup.py`, `api/routers/dashboard_api.py`, `api/routers/public.py`,
`services/agent_control.py`, `services/agent_policy.py`, `services/openai_structured_call.py`,
`services/provider_credentials.py`, `services/security_review.py`, `services/public_chat.py`,
`services/public_support_ai.py`, `services/system_brain_agent.py`,
`services/system_brain_assistant.py`, `services/sharia_research.py`,
`services/sharia_source_ai_discovery.py`, `services/hilal_chat.py`,
`services/hilal_chat_agent.py`, `services/hilal_chat_knowledge.py`, `schemas/hilal_chat.py`,
`static/hm-page-context.js`, `static/hm-page-notes.js` (new), `static/hm-research-test.js`
(new), `static/hm-{connections,market,monitor,opportunities,passport,settings,subscription,support,watchlists}-test.js`,
`templates/hilal/dashboard_test/research.html`, `research_detail.html`, `.env.example`,
`.env.production.example`, `.env`, `.env.production` (backups `.bak-wp3-budgets`,
`.bak-hilal-evidence-cap`), tests: `tests/conftest.py`,
`tests/integration/test_public_chat_api.py`, `tests/integration/test_hilal_chat_evidence.py`
(new), `tests/unit/test_reliability_security.py`, `tests/unit/test_system_brain_assistant.py`,
`tests/unit/test_system_brain_operational_agent.py`, `tests/unit/test_invariant_ai_provider.py`
(new), `tests/unit/test_invariant_active_provider.py` (new),
`tests/unit/test_invariant_hilal_page_context.py` (new),
`tests/browser/test_hilal_page_context_e2e.py` (new). Confirm this list against `git status`.

**The one paid WP5 Hilal turn already ran — do NOT repeat it.** Recorded result
(2026-09-17 00:00, production code path, throwaway DB with BTC under one authority standard
and one automated standard):

| Field | Value |
|---|---|
| Question | "why is BTC listed and under which standard" |
| Payload | 10,115 chars (cap 30,000); 2 passport rows; 2 methodologies |
| Provider | opencode_go, `muse-spark-1.3-contributor`, HTTP 200, attempt 1 |
| Tokens | input 5,356, output 1,653 |
| Latency | provider 15,452 ms; turn 16,391 ms |
| Estimated cost | USD 0.0008662 |
| Mode / grounding | ANSWER; grounded_in = asset:BTC, passport:BTC:LIVE_TURN_AUTH, passport:BTC:HILAL_MARKETS_METHODOLOGY, methodology:LIVE_TURN_AUTH, methodology:HILAL_MARKETS_METHODOLOGY; grounded_outside_payload = [] |
| Behaviour checks | cites_authority_standard, names_automated_as_automated, no_combined_verdict, all_citations_in_payload — all true |

The worker was stopped while re-running
`.venv/Scripts/python -m pytest tests/browser/test_hilal_page_context_e2e.py -q -p no:randomly`
after editing that test (its previous run: 1 failed, 1 passed — report page / passport page
routing with `methodology_id`). Outcome unknown.

## Work packages

### WP-A — Finish and prove WP4+WP5 (R15–R21)
Objective: check every item of R15–R21 against the code as it is now; finish anything missing.
Scope: the Hilal/page-context files above and their tests only.
Steps: run the browser file once; if red, find the cause (product or test) and fix the cause,
never loosen an assertion. Confirm: R16 card inputs reach the payload (filled + empty); R17
cross-user test exists and passes; R18 parametrised over every `ShariaAssetStatus` × both
methodology kinds; R19 origin fields; R20 adversarial prompt tests; R21 JS caps == schema caps and
over-cap truncates; `hilal_chat.py` passes `session_key=str(conversation.id)`; the new cap key is
in all four env files (counts-only proof: key count rose by exactly the number added, no other
value changed, compare with `.bak-hilal-evidence-cap`; never print values).
Forbidden: as original mission section 4 and WP4/WP5 "do not change".
Acceptance: joint milestone once — hilal chat test files + new invariant files + the browser
file + ruff on changed files — green.
Model: `muse-spark-1.3-contributor` worker. Escalate if: a page has no data source without a new API.

### WP-B — Two independent reviews of WP4+WP5
Logic `glm-5.3-flash`; adversarial `qwen3.7-plus`. Focus: cross-user leak in Hilal records,
Shariah authority wording, our automated standard never default and never merged into an
aggregate, no evidence URLs in the payload, caps truncate and never raise, page publishers add
no visible UI. Close every finding (fix + test) or escalate.

### WP-C — Re-establish WP3 real-call evidence without re-spending where possible
The WP3 measurement table was lost with the session. First look for it on disk (run dirs,
`%TEMP%\opencode`, `%TEMP%\hm-live`, test fixtures) and in the current settings/defaults and
code comments. If the per-feature table (input/output/reasoning tokens, latency) for R2–R5
cannot be recovered, make **one** real call per feature for R2, R3 (may reuse the WP5 turn
above as R3 evidence), R4, R5 (tool loop: ≥1 tool call + final answer) — at most 4 calls, and
R6 only if cheap. Record the table. Do not change budgets unless a call truncates.

### WP-D — Final verification (original WP6)
Exactly as original WP6: `ruff check src tests scripts`, `mypy src`,
`pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly --junitxml=<run dir>/junit.xml`,
plus the `tests/integration` files for chat, System Brain and Shariah research. Compare any
failing IDs against a clean worktree at `C:\wt-head` at HEAD, copying only mission files, before
calling anything a regression; fix real regressions. R8: prove no stale-only file is in the
mission diff. R22: list exactly when each suite ran across both sessions.

### WP-E — Report
`SUPERVISOR_REPORT.json` (validate with
`.venv/Scripts/python tools/hm-orchestrator/validate-report.py <json> .hm-orchestrator/policy/supervisor-report.schema.json`)
and `SUPERVISOR_REPORT.md` in THIS run's dir. Cover all R1–R23 with evidence (earlier-session
evidence may be cited from the table above, marked as such). Include: the server step as a
command (add `OPENCODE_GO_API_KEY` and every changed model/effort/budget/cap key to the server's
`.env.production`, then redeploy); the privacy fact (Muse Spark 1.3 Contributor: training
enabled, not ZDR, region limited); the quota risk (Shariah research batches share the OpenCode
Go quota); that the original run was interrupted and continued here. Plain simple English per
CLAUDE.md "Reporting".

## Reviewer policy
High risk: WP-B's two reviewers plus a final adversarial pass (`qwen3.7-plus`) over the whole
mission diff before WP-E.

## Completion condition
Every R1–R23 has PASS evidence or a named blocker. No paid Hilal turn repeated.
