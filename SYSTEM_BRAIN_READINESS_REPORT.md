# System Brain operational workspace readiness report

Date: 2026-08-03

## Outcome

The production System Brain route is no longer a large-context, browser-memory Q&A path. The
authenticated route now enters `SystemBrainAgentService.run_turn()`, persists the administrator
conversation and run before provider work, offers a server-owned bounded tool shortlist, executes
typed tools through `SystemBrainToolRegistry.execute()`, validates evidence references, and stores
the exact response, usage, tool calls, and audit records.

The customer conversation explorer reads only exact persisted Setup Chat and future Public Chat
messages through `AdminConversationExplorer`. It does not reconstruct historical Public Chat text.
PostgreSQL event triggers provide a monotonic cursor for live updates; the UI uses SSE with bounded
polling fallback and renders chat messages through the shared `HilalChatRenderer`.

Code-owned core readiness is strong, but this report does **not** claim unconditional production
readiness. The final rating is **8.6/10 for a controlled private beta** because browser automation
could not initialize in this environment, Cloudflare origin/JWT enforcement needs deployment
verification, several consequential actions intentionally have no canonical adapter, growth
experiment outcome instrumentation is absent, and repeated load/p95 testing has not been run.

## 1. Existing limitations confirmed and removed from the live route

The legacy `SystemBrainAssistantService` still contains the previous eager context/repository scan
implementation solely for compatibility tests. No production router imports or calls it. The
compatibility POST endpoint `/dashboard/system-brain/assistant` creates a persisted conversation and
delegates to `SystemBrainAgentService.run_turn()`.

Enforcement proof:

- Production function: `system_brain_assistant()` and `system_brain_agent_turn()`.
- Persisted proof: `system_brain_conversations`, `system_brain_messages`, `agent_runs`, and
  `agent_tool_calls`.
- Regression test: `test_system_brain_agent_selects_tools_persists_and_replays_exactly`.
- Real evidence: run `fde7c226-c4cb-4bf7-a09a-351c525cde90` completed through the new route.

## 2. Unified administrator conversation read model

The canonical schemas are `AdminConversationSummary`, `AdminConversationTimeline`, and
`AdminConversationMessage`. `AdminConversationExplorer.list_conversations()` performs bounded,
cursor-paginated batch reads. `AdminConversationExplorer.conversation()` dispatches to exact Setup
or Public timeline readers and records the privileged access audit.

Supported sources:

- `authenticated_setup_chat`: exact persisted `AISetupChatMessage` rows.
- `public_site_chat`: exact `PublicChatMessage` rows created after migration.

Authoritative email comes only from `UserIdentity`. Anonymous Public Chat is always displayed as
`Anonymous visitor`. A deleted user is not returned as an identifiable user and message content is
replaced with the deletion-policy notice.

Live proof on the preserved PostgreSQL database: HTTP 200, `Cache-Control: no-store, max-age=0`, five
bounded items, a next cursor, and an exact six-message detail timeline. The content view created a
`system_brain.customer_conversation.view` audit record.

Regression tests:

- `test_admin_conversation_explorer_uses_authoritative_identity_redacts_and_audits`
- `test_deleted_customer_content_is_not_exposed`
- `test_conversation_cursor_pages_do_not_duplicate_or_omit`
- `test_operational_workspace_apis_persist_and_audit_exact_transcripts`

## 3. Retention and supported chat storage

`PublicChatService._persist_visible_messages()` stores exact visible future user/assistant messages
once per idempotent turn. `PublicChatService.cleanup_expired()` deletes expired conversations,
cascaded transcripts, and their `CustomerConversationEvent` cursors. Existing Public Chat rows from
before complete transcript storage remain explicitly incomplete and are never inferred from hashes,
metrics, state, or provider traces.

Regression test: `test_public_chat_persists_exact_future_transcript_once` plus the 32-test Public
Chat unit/integration suite.

## 4. Shared message renderer

`static/chat-message-renderer.js` is the single visible message renderer used by Dashboard Setup
Chat and System Brain. `ai-setup-chat.js` delegates normal message output to it; the System Brain
workspace uses the same renderer while keeping telemetry in a separate evidence drawer.

Regression test: `test_setup_chat_and_system_brain_share_message_renderer` in
`test_dashboard_ux_consolidation.py`. All 24 JavaScript files parsed successfully and all 66 Jinja
templates loaded.

## 5. Live update and reconnect contract

Migration `a7d35e9c41b2` installs six PostgreSQL triggers covering:

- Setup/Public conversation creation
- message persistence
- turn completion/failure
- lifecycle changes
- authenticated Setup approval

`system_brain_customer_conversation_stream()` resumes from `Last-Event-ID` or `after_id`, checks for
new rows every 500 ms, emits exact event IDs, and sends keepalives. `system-brain.js` stores only the
cursor in session storage, deduplicates messages by persisted ID, reconnects with the cursor, and
falls back to bounded incremental event polling.

Regression test: `test_live_event_cursor_resumes_once_without_duplicates`. The transactional
PostgreSQL trigger smoke emitted the expected event and was rolled back. A browser-rendered timing
measurement could not be completed because the in-app browser runtime failed to create its kernel
assets; the <=2 second UI claim therefore remains unverified at browser level.

## 6. Privacy, PII, and audit policy

The router has global Cloudflare Access and application-admin dependencies. All workspace responses
pass through `_protect()` for no-store and security headers. Conversation detail requires an
`access_reason`; list and detail reads write `AuditEvent` records. `redact_customer_text()` removes
known credential assignments, bearer tokens, private-key blocks, and control characters. Tool-call
arguments are redacted/hashes are stored instead of raw queries where appropriate.

PII tools are permitted only when `SystemBrainAgentPolicy.allows_pii()` identifies an explicit
customer/user/conversation/profile/email request, and every tool independently verifies the
administrator principal through `_require_admin_tool_principal()`.

Regression tests:

- `test_pii_tools_require_explicit_admin_request_policy`
- `test_system_brain_requires_real_application_admin_role`
- `test_system_brain_can_require_cloudflare_access_before_admin`
- `test_operational_workspace_apis_persist_and_audit_exact_transcripts`

Deployment risk: `_require_cloudflare_access()` validates the asserted email and presence of the
Access assertion, but the origin must be restricted to Cloudflare or the assertion must be
cryptographically validated at the application boundary.

## 7. Persistent System Brain conversations

`SystemBrainConversationService` implements create, list/search, read, rename, archive, ownership,
and row locking. `SystemBrainAgentService._idempotent_replay()` returns the exact persisted turn for
the original client message ID. One active run is allowed per conversation. User messages point to
their `AgentRun`, enabling refresh/reconnect progress through
`system_brain_agent_run_progress()` and cancellation through
`system_brain_cancel_agent_run()`.

Real replay evidence for conversation `3b75d438-80b4-403d-ad67-75341eab37fe`:

- same run ID returned: true
- replay wall time: 50 ms
- persisted rows: one run, one tool call, two messages
- additional paid usage events after replay: zero

## 8. Agent architecture and policy

`SystemBrainAgentPolicy.offered_tools()` derives a compact, request-specific shortlist.
`SystemBrainAgentService.run_turn()` starts with compact persisted history and policy only, then runs
a bounded Responses loop. It enforces maximum steps, calls, repeats, per-tool timeout, turn deadline,
estimated per-turn cost, per-admin hourly turns, and per-admin daily cost.

Independent read calls use isolated sessions and execute concurrently. Artifact/proposal calls are
serialized. The Responses continuation replays the provider-authored `function_call` item with its
`function_call_output`; this production defect was found and fixed during the real-provider smoke.

Unexpected provider/tool exceptions are converted to safe typed run failures. Raw exception text is
not returned. If a provider narrative is ungrounded after successful tools,
`_deterministic_evidence_fallback()` discards the narrative and returns a `degraded` result made only
from exact tool coverage, freshness, limitations, and evidence refs.

Regression tests:

- `test_independent_read_tools_run_in_parallel_and_writes_remain_bounded`
- `test_unexpected_provider_failure_is_persisted_and_replayed_safely`
- `test_ungrounded_model_narrative_uses_only_deterministic_tool_evidence`
- `test_persisted_per_admin_budget_blocks_before_provider_call`

## 9. Read-tool registry

`SystemBrainToolRegistry.execute()` is the only agent tool dispatch. Every read returns
`EvidenceEnvelope(data, evidence_refs, freshness, coverage, limitations)` and is bounded by the
closed `SystemBrainToolArguments` schema.

Customer/product tools: `search_users`, `inspect_user_profile`,
`list_customer_conversations`, `inspect_customer_conversation`,
`inspect_setup_chat_failures`, `inspect_setup_funnel`, `inspect_feature_usage`,
`inspect_monitor_health`, `inspect_alert_delivery`, `inspect_support_activity`.

Quality tools: `setup_chat_quality_metrics`, `top_failed_intents`,
`clarification_failure_analysis`, `latency_breakdown`, `model_cost_analysis`,
`user_feedback_analysis`, `knowledge_gap_analysis`, `release_comparison`, plus deterministic cost,
approval, support-driver, failure-cluster, and experiment-instrumentation tools.

Revenue/growth tools: all requested revenue, subscription, trial, plan, churn, cohort, referral,
waitlist, attribution, feature-revenue, and high-intent tools.

Governance/operations and engineering registries contain every requested tool. The smoke test
`test_every_read_tool_returns_a_bounded_evidence_envelope` executes every registered read tool.

## 10. Deterministic analytical tools

Important ratios and amounts are calculated in SQL/Python tools, not by the model. Returned records
include formula, date range, sample size, population, exclusions, result, and confidence
limitations. Implemented analyses include subscription/trial funnels, trial activation, cohorts,
retention, churn/expiry, list-price revenue movement, feature/conversion association, cost per
successful Setup Chat, approval association, support-driver ranking, and high-value failure
clusters.

`experiment_impact` intentionally reports missing assignment/outcome instrumentation instead of a
zero or invented causal result. Revenue-by-feature and high-intent tools explicitly label their
results observational and do not claim causation.

## 11. Controlled-action registry

Safe internal tools persist reversible, model-authored drafts in `SystemBrainArtifact`:

- report, insight, task, saved view, bounded CSV
- unsent email draft, experiment draft, internal note

Every artifact is known-secret redacted, evidence-bound, marked `model_authored_draft`, and audited.

`SystemBrainActionService.propose()` and `.confirm()` implement exact proposal binding, expiry,
ownership, row locking, idempotency, risk display, and human reason. The only currently executable
canonical adapters are `ban_user`, `delete_user`, `grant_access`, and `reduce_access`, all delegated
to `SystemBrainUserAdminService` after explicit UI confirmation. No model writes domain tables.

Other consequential actions are registered as prohibited but deliberately fail with
`canonical_action_unavailable` before a proposal is persisted. This includes sends, production
settings, billing alteration, campaigns, customer jobs, governance decisions, evidence publication,
and strategy approval/activation.

Regression tests:

- `test_action_proposal_requires_evidence_and_exact_human_binding`
- `test_unadapted_consequential_action_is_not_persisted`
- `test_agent_rejects_model_authored_sharia_ruling`

## 12. Repository evidence index

`RepositoryEvidenceIndexService.refresh()` hashes and updates only changed authorized files.
Interactive `search()` and `excerpt()` query the database index and never scan the filesystem.
Environment files, credentials, caches, generated reports, evaluation artifacts, and known secret
names are excluded. Prompt-bearing files retain path/hash/symbol metadata only and have empty
searchable text.

The worker task `ai_market_monitor.refresh_system_brain_repository_index` runs every five minutes.
Measured worker convergence: 464 unchanged files in 0.375 seconds. Current index: 456 internal-code,
2 internal-documentation, 6 restricted-prompt rows, with zero searchable text in restricted rows.

Regression test: `test_repository_index_excludes_secrets_and_prompt_text`.

## 13. Revenue and quality output contract

`GrowthQualityOpportunity` requires finding, funnel stage, measured evidence, exact evidence refs,
sample size, opportunity range, confidence, customer impact, revenue method, experiment, success and
guardrail metrics, and required human action. `_grounded()` rejects any finding/opportunity ref not
returned by authorized tools. Generic ideas can appear only as limitations/hypotheses, not measured
findings.

## 14. UI workspace

`system_brain.html`, `system-brain.js`, and `system-brain.css` provide:

- persisted agent-conversation sidebar with new/reopen/rename/archive/search
- assistant timeline and reconnectable persisted run progress
- evidence/tool drawer and saved artifacts
- exact action-proposal dialog with separate confirmation
- customer conversation explorer with filters, cursor pagination, unread activity, and detail links
- streamed event progress, cancellation request, and bounded polling fallback

The browser is not the history authority and hidden reasoning is never rendered.

## 15. Migration and database preservation

Migration: `a7d35e9c41b2_add_system_brain_workspace.py`, one Alembic head.

New tables: System Brain conversations/messages/artifacts/action proposals, customer conversation
events, repository evidence index, and Public Chat messages. The migration is additive and does not
rewrite existing users, canonical assets, strategies, or governance evidence.

Post-migration authoritative database proof:

- users: 8
- canonical assets: 185
- indexed repository files: 464
- event triggers: 6

All four environment files have the same 385 keys. The live/production values remain authoritative;
example values were not copied into live secrets. A literal `$` in the existing System Brain password
hash is safely quoted for Compose without changing the hash. System Brain reasoning effort is `low`
because the configured provider returned HTTP 400 `unsupported_value` for `minimal`.

## 16. Verification performed

Passing checks:

- Ruff: all targeted production/test files
- mypy: 10 changed production files
- compileall: package, index script, and migration
- System Brain/admin scoped tests: 58 passed
- Public Chat scoped tests: 32 passed
- focused operational-agent tests after final fallback: 18 passed
- final combined System Brain/admin/Public Chat regression command: 80 passed
- JavaScript syntax: 24 files
- Jinja loading: 66 templates
- Docker API/db/Redis/worker healthy; scheduler running
- Alembic current: `a7d35e9c41b2 (head)`

Real provider run `fde7c226-c4cb-4bf7-a09a-351c525cde90`:

- status: completed
- model: `gpt-5.6-luna`, reasoning `low`
- tool: `revenue_summary`, success
- exact evidence refs: 1
- latency: 12,903 ms
- input tokens: 11,805
- output tokens: 440
- estimated cost: USD 0.00254043

This is a single acceptance sample, not a p50/p95 claim.

## 17. Exact files added or changed for this implementation

Added:

- `alembic/versions/a7d35e9c41b2_add_system_brain_workspace.py`
- `scripts/index_system_brain_repository.py`
- `services/system_brain_actions.py`
- `services/system_brain_agent.py`
- `services/system_brain_conversations.py`
- `services/system_brain_privacy.py`
- `services/system_brain_repository_index.py`
- `services/system_brain_tools.py`
- `static/chat-message-renderer.js`
- `tests/unit/test_system_brain_operational_agent.py`

Updated:

- `.env`, `.env.production`, `.env.example`, `.env.production.example`
- `core/config.py`
- `db/models/__init__.py`, `db/models/public_chat.py`, `db/models/system_brain.py`
- `schemas/system_brain.py`
- `services/public_chat.py`
- `api/routers/system_brain.py`
- `worker.py`
- `static/ai-setup-chat.js`, `static/system-brain.js`, `static/system-brain.css`
- `templates/hilal/dashboard/builder.html`, `templates/system_brain.html`
- `tests/integration/test_system_brain_web.py`
- `tests/unit/test_dashboard_ux_consolidation.py`

The working tree also contains unrelated pricing, billing, dashboard, scanner, strategy, and landing
changes that were present or changed concurrently. They were preserved and are not claimed here.

## 18. Remaining risks and unsupported production dependencies

1. The in-app browser runtime failed before a visual smoke could run. Route, JS, template, and API
   integration tests pass, but rendered reconnect and action-dialog behavior still needs a manual or
   working-browser pass.
2. SSE uses bounded 500 ms database polling, not PostgreSQL LISTEN/NOTIFY. Cursor correctness is
   tested; sustained concurrent-stream load is not.
3. Cancellation is observed between provider/tool steps. An in-flight provider request ends only on
   return or timeout.
4. Cloudflare Access origin restriction or JWT signature validation must be verified in deployment.
5. Consequential actions without an existing canonical adapter remain unavailable by design.
6. Growth experiment assignment/outcome instrumentation is missing; causal experiment impact cannot
   be measured yet.
7. Public Chat history before this migration remains unavailable and is never fabricated.
8. No load run exists for explorer query latency or agent p50/p95/cost distributions.
9. The worker logs show a Telegram `getUpdates` conflict from another bot consumer. This is unrelated
   to System Brain but is an unresolved production operations dependency.
10. A pre-existing unrelated authenticated-dashboard brand test fails because
    `hilalmarkets-dashboard-v2.css` contains unapproved `#f4f8eb`; this task did not overwrite that
    concurrent change.

## 19. Readiness rating

Controlled private beta: **8.6/10**.

The core authority, persistence, privacy, tool selection, deterministic analysis, evidence binding,
action confirmation, audit, repository index, and real-provider path are operational. Raising the
rating requires a successful visual browser pass, Cloudflare origin/JWT verification, SSE/load
measurements, repeated provider performance runs, and explicit product decisions/adapters for any
additional consequential actions intended for beta.
