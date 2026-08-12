# HilalMarkets

HilalMarkets is a screened-market intelligence and monitoring platform for crypto spot
traders. Users describe a Watch Plan, approve its structured interpretation, preview it against
recent market data, and receive evidence-backed in-app or Telegram alerts during private beta.
Version one never places trades.

The Watch Plan builder is AI Setup Chat. Every ordinary free-text message goes to one bounded
**Setup Agent** first: it reads the whole turn, splits it into segments — a greeting, an
instruction, a correction and a question can arrive together — and calls exactly one
state-changing server tool, `apply_setup_turn`. That tool is the only executable authority. It
checks each actionable span against the user's exact words, refuses any capability key the server
did not offer, applies the patch to `StrategyDraftV2`, runs semantic validation and compiles an
inactive preview. The final reply is written from the tool's execution result, so it can never
describe a change that did not land. An immutable approved strategy version is created only by the
separate authenticated approval action. `OPENAI_API_KEY` is server-side only.

One free-text turn costs at most one planning call, one deterministic execution and one reply call.
There are no tool loops, no interviewer and no fallback orchestrator. Explicit UI actions —
choosing Scanner or Monitor, answering a server-offered option, Review and approve — stay
deterministic and cost nothing. See
[docs/SETUP_CHAT_AGENT_REBUILD_REPORT.md](docs/SETUP_CHAT_AGENT_REBUILD_REPORT.md) and
[docs/AI_SETUP_CHAT_LAUNCH_V2.md](docs/AI_SETUP_CHAT_LAUNCH_V2.md).

The integrated `hm_chatbot_eval` package exercises this authenticated flow through the real
session/message APIs and Strategy Canvas, with production-derived JSON Schemas, a canonical field
map, and test-only LLM fault injection that deployed startup rejects. The public Support assistant
is explicitly outside its target boundary. Deterministic evaluator checks run in CI; credentialed
OpenAI and Playwright corpus runs remain manual or scheduled. See
[docs/AI_SETUP_CHAT_EVALUATOR.md](docs/AI_SETUP_CHAT_EVALUATOR.md).
For routine release confidence, `--mode budget --target both` covers every evaluator topic
through the authenticated backend, repeats only UI/Canvas boundary topics in Playwright, judges
the results, and enforces a measured all-in `$2.50` cap across evaluator and chatbot model calls.
Those resilience topics include test-only fault injection, so they must target an isolated
`APP_ENV=test` process rather than the normal development or production app. For a local fault
smoke, use `./scripts/run_isolated_setup_chat_smoke.ps1 -Topic partial_invalid_recovery
-EnableFaults -Target backend`. This is one fault-recovery case, not a full evaluation. The Docker
app on port 8000 intentionally rejects those controls.

Every change the agent proposes names the one message segment that authorised it, and each value in
it must appear in *that segment's own words*. Message-wide grounding is not authorization: in
`drop LTC, and is 5% a lot on a 15m candle?` the 5% and the 15m belong to a question, and a question
can never author a rule. Grounding is typed, not substring: `1` cannot match `15m`, `2` cannot match
`20`, `5m` cannot ground `5%`, `at least` grounds `gte`, and `open to close` grounds
`open_to_close_percentage` through the same readers the compiler uses.

Every gate — semantic validation, compilation, Sharia policy, screened universe, provider
availability, approval eligibility and the final chat status — runs inside the deterministic
execution phase, before the reply is written. The evidence for any "I changed X" claim comes from
comparing the draft before and after, never from the model's own summary. An approved setup survives
every turn that changes nothing; only a material edit invalidates it.

**`AI_AGENT_CONTROL_ENABLED` is not a Setup Chat switch.** Bounded Agent Control was a general
multi-tool coordinator; it has no authority over authenticated Setup Chat and none of the
`AI_AGENT_*` bounds govern that traffic — the `SETUP_AGENT_*` settings do. It defaults to false, and
turning it off is not a Setup Chat rollback. There is no Setup Chat feature flag: the agent path is
the only writable route, so a rollback means rolling back the deployment. Its document is retained
for history only: [docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md).

Registry, compiler, provider, scanner, ownership, entitlement, hash, approval and activation
authority all remain in application services. The model receives no approval, activation, network,
SQL, filesystem or trade tool of any kind.

HilalMarkets also has a fail-closed Sharia-first market layer. Screened Market, one-time Scanner runs,
persistent Watch Plans, workers, opportunity evidence, and alerts share one versioned methodology
and universe resolver. Bounded authority adapters retain explicit asset-level results from SC
Malaysia, Shariah Review Bureau, and Fasset, verify canonical identity, build factual evidence dossiers, and create
administrator review cases. `All` is a deduplicated customer view over active published source
methodologies, not a separate ruling. No imported asset is customer-visible until its exact evidence,
identity, use scope, and methodology criteria are explicitly reviewed and separately published. AI
cannot set a religious status, infer missing source facts, or publish an asset. See
[docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md](docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md)
for the architecture, source boundaries, deployment order, tests, and manual review requirements.
The multi-authority extension is documented in
[docs/FASSET_AND_MULTI_METHODOLOGY_IMPLEMENTATION_REPORT.md](docs/FASSET_AND_MULTI_METHODOLOGY_IMPLEMENTATION_REPORT.md).
The validated three-authority import-pack workflow and operator commands are documented in
[docs/SHARIA_METHODOLOGY_IMPORT_PACK.md](docs/SHARIA_METHODOLOGY_IMPORT_PACK.md).
Current and historical Passport views now share one read model, alerts retain the exact Passport
version used at evaluation, and System Brain separates review from publication with optional
four-eyes enforcement. The first-party checkout uses the server Plan Catalog and a durable
successful-payment email outbox. See
[docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md](docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md).

The public landing and contact routes use the supplied `Hilal-Markets-Website/` React/Vite source,
Geometria/Onest typography, responsive motion, and Hilal Markets brand assets. FastAPI provides a
minimal metadata/consent shell and same-origin form APIs; the remaining public product pages keep
the shared Jinja shell and server-owned content sources. Dedicated routes cover Features, How It
Works, Pricing, Help, Contact, About, Trust & Safety, Risk Disclosure, Privacy, Terms, and Cookies.
`/how-we-screen` is still served so saved links and search results keep working, but it is
deliberately absent from every menu, page body, and assistant answer.
Public Pricing and authenticated Billing read the same plan catalog. The private beta exposes only
free invite access and rejects paid checkout while billing is disabled. Optional analytics is disabled
by default; Consent Mode denied defaults execute before the reusable GA4/GTM and Meta loaders, and
users can withdraw consent through Cookie Settings. Waitlist signups are idempotent database
records with optional server-only Google Apps Script delivery; contact messages produce one
idempotent office-email event. See
[docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md](docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md).
The landing/contact and analytics implementation is documented in
[docs/LANDING_CONTACT_ANALYTICS_IMPLEMENTATION_REPORT.md](docs/LANDING_CONTACT_ANALYTICS_IMPLEMENTATION_REPORT.md).
The landing-page product assistant is a separate public, non-executing boundary. It generates
multi-turn answers only from server-owned product knowledge and bounded read-only tools. Anonymous
visitors cannot inspect accounts; a signed-in user may read only their own account, Telegram,
Watch Plan, alert, entitlement, usage, Screened Watchlist, and published Passport state. Unknown
questions remain in chat unless the visitor explicitly chooses **No. Submit a support form** or
asks to contact the team. Every completed answer has one session-bound, idempotent feedback record;
the model can make a handoff available but cannot open the form. The inquiry commits exactly one
customer and one office email outbox row before returning, supports token-bound feedback/deletion,
and never exposes Setup Chat mutation tools. A bounded read-only index may retrieve Markdown, CSV,
JSON, and text from the project `Notion/` export as conversational context. Notion content cannot by
itself prove a current product fact or authorize a product-state claim.
The current private-beta correction status, including verification blockers and required staging
evidence, is recorded in
[docs/PRIVATE_BETA_READINESS_REPORT.md](docs/PRIVATE_BETA_READINESS_REPORT.md).

During the controlled beta, an explicitly confirmed candle-computable mechanic that is not in the
registry can be prepared as a user-scoped, versioned mechanic through a bounded JSON expression
DSL. The worker validates it, tests it against Binance spot, independently reviews it, and requires
the normal strategy approval flow before activation. Provider-only requests are rejected before
queueing. Certified artifacts remain fail-closed during approval and scheduled scans, and owner
quarantine/restore/repair-discard controls never replace an active version silently. The pipeline
never executes AI-generated Python or fabricates provider data. See
[docs/CAPABILITY_EXTENSION_PIPELINE.md](docs/CAPABILITY_EXTENSION_PIPELINE.md).

## Local development

Python 3.12+ is required.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
python HilalMarkets_Sharia_Methodology_Import_Pack/HilalMarkets_Sharia_Methodology_Import_Pack/scripts/validate_bundle.py
python scripts/import_sharia_methodology_pack.py
uvicorn ai_market_monitor.main:app --reload
```

For the complete service stack, install Docker and run `docker compose up --build`.
Capability creation and five-scan reviews are asynchronous, so local non-Docker operation also
requires Redis, the Celery worker and the Celery beat scheduler described in
[docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

## Safety boundary

The LLM/interpretation layer may propose structured rules and explanations. Indicator values,
condition outcomes, Near-Miss scores, and lifecycle transitions are produced by deterministic
services only. Strategy activation requires explicit user approval and a recent-market preview.

See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md),
[docs/OPERATIONS.md](docs/OPERATIONS.md), [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md),
[docs/PRIVATE_BETA_SOAK_RUNBOOK.md](docs/PRIVATE_BETA_SOAK_RUNBOOK.md),
[docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md](docs/AI_SETUP_CHAT_IMPLEMENTATION_REPORT.md), and
[docs/AI_SETUP_CHAT_EVALUATOR.md](docs/AI_SETUP_CHAT_EVALUATOR.md), and
[docs/CAPABILITY_EXTENSION_PIPELINE.md](docs/CAPABILITY_EXTENSION_PIPELINE.md), and
[docs/BOUNDED_AGENT_CONTROL.md](docs/BOUNDED_AGENT_CONTROL.md), and
[docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md](docs/SHARIA_FIRST_PRODUCT_LAYER_IMPLEMENTATION_REPORT.md), and
[docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md](docs/SC_MALAYSIA_SHARIA_GOVERNANCE_IMPLEMENTATION_REPORT.md), and
[docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md](docs/SHARIA_PASSPORT_GOVERNANCE_BILLING_IMPLEMENTATION_REPORT.md), and
[docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md](docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md).
The production page map, exact prototype asset mapping, and UI verification commands are in
[docs/HILALMARKETS_UI_MIGRATION.md](docs/HILALMARKETS_UI_MIGRATION.md); the supplied design,
component, UX, and QA references are preserved under [docs/hilalmarkets-ui](docs/hilalmarkets-ui/README.md).

Current beta infrastructure includes idempotent scheduled scans, shared CCXT REST clients,
deterministic proof persistence, setup lifecycle records, and Telegram webhook delivery. WhatsApp
code remains dormant pending Meta onboarding and controlled tests; it is not mounted or shown when
disabled. Certified user-scoped OHLCV capability creation is enabled; paid billing remains disabled.
See
[docs/WHATSAPP_CLOUD_API_RUNBOOK.md](docs/WHATSAPP_CLOUD_API_RUNBOOK.md). Retained payment providers
remain covered by tests for a later explicit rollout. See
[docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) for the required fail-closed
configuration.

The current live-AI code and verification status is recorded in
[docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md](docs/CONTROLLED_BETA_AI_IMPLEMENTATION_REPORT.md).

## Operational truth and product boundaries

`src/ai_market_monitor/observability/` is the one place the product decides what is measured, what
is promised and what wakes somebody up: one metric registry, eleven service-level objectives, twelve
alert rules, a deduplicated operational issue queue, and the customer-facing degradation banners.
Every objective names a metric that something actually emits — startup refuses to boot otherwise —
and no alert may be delivered through the subsystem it is reporting on. `docs/OPERATIONS.md` holds
one runbook section per alert, and a test fails if an alert points at a section that does not exist.

Values are held in one process and read by `/api/v1/admin/health`. There is no exporter and no alert
transport yet, so nothing survives a restart and nothing is actually sent to a person.

How open the product is, is server-owned: `core/launch_stage.py` holds four stages
(`internal`, `private_beta_invite`, `public_waitlist`, `public_launch`) and one table saying what
each exposes. `PUBLIC_WAITLIST_MODE` is now an emergency ceiling over that stage rather than an
independent switch. `core/product_boundaries.py` is the versioned list of what the product does,
does not do yet, and will never do; an unsupported request is refused by name and never answered
with a nearby capability. Hilal Markets does not execute trades, is not a broker, gives no buy or
sell recommendations, and provides no financial advice.

The `.github/workflows/release-gate.yml` workflow is the release authority for automated checks.
Committed Markdown reports are explanatory records, not test proof. Require every workflow job in
branch protection before merging a release candidate.
