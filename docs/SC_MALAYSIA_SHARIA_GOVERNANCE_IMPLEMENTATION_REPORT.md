# SC Malaysia Sharia Governance Implementation Report

Date: 2026-07-15

## 1. Outcome and Boundary

HilalMarkets now has a fail-closed path from the Securities Commission Malaysia digital-assets
page to a retained source record, canonical identity, factual research dossier, human review,
versioned publication, two-layer Evidence Passport, and ongoing source monitoring.

No imported row is public merely because it was parsed. No AI output can set a Sharia status,
approve/reject a case, or publish an asset. Only an authenticated application user with the
`ADMIN` role can record the terminal publication decision. The customer label is scoped to the
source: `SC Malaysia SAC reference: Shariah-compliant`.

`rejected`, `not covered`, `insufficient evidence`, and `not approved for publication` remain
distinct workflow facts. They are never converted into `haram` or `not halal` without an explicit
authoritative source result.

## 2. Audit Matrix

| Area | Initial state | Result |
|---|---|---|
| Existing Sharia screening, universe resolver, history and Passport | Complete | Preserved as the customer-facing authority after publication. |
| Existing System Brain | Conflicting/stale | Replaced with a live governance workspace protected by application `ADMIN` RBAC. |
| Customer navigation | Complete | Preserved; System Brain remains absent from customer navigation, sitemap, and public discovery. |
| Official SC import | Missing | Added idempotent robots-aware HTTP/Scrapling import with raw and normalized snapshots. |
| Canonical identity | Partial | Added name/chain/native-token/contracts/official-site/spot-market validation; ticker-only fails. |
| Research dossier | Missing | Added sequential official-source collection and one strict Flex response per asset/run. |
| Human review/publication | Partial | Added immutable cases/decisions and a transactional publication bridge. |
| Admin Telegram workflow | Partial | Reused the Telegram gateway with durable idempotent attempts, reminders, retry, and audit. |
| Source monitoring | Partial | Added published-assets-only daily diffing, zero AI on unchanged evidence, and material-change review. |
| Pilot/full gating | Missing | Added `BTC,ETH,SOL` pilot allowlist and an off-by-default continuation flag. |

## 3. Architecture and Files

### Persistence

- `db/models/sharia_governance.py` adds methodology family, canonical asset, exchange market,
  monitoring run, official source/snapshot, external assessment, dossier/evidence, AI snapshot,
  review case/decision, publication, source change, and Telegram attempt records.
- `db/models/sharia.py` links existing methodology versions to an optional family.
- `alembic/versions/d6e7f8a9b0c1_add_sc_malaysia_governance_workflow.py` creates normalized tables,
  constraints and queue indexes. It seeds the active SC reference family/version and no assets.

### Domain services

- `services/sc_malaysia_import.py`: authoritative import, explicit-row parser, snapshots and diff.
- `services/sharia_identity.py`: canonical identity and official source-registry creation.
- `services/sharia_research.py`: sequential evidence retrieval and bounded factual AI analysis.
- `services/sharia_governance.py`: review decisions, publication, Passport, audit and Telegram.
- `services/sharia_source_monitoring.py`: one-window published-source monitoring and safety holds.
- `services/sharia_admin_dashboard.py`: live aggregate and review-case read models.
- `services/sharia_screening.py`: exposes the two-layer Passport without breaking older records.

### API and presentation

- `api/routers/system_brain.py` uses the normal dashboard principal and rechecks `UserRole.ADMIN`
  on every overview, section, case, and decision route. Optional Cloudflare Access remains an
  outer gate, not the application authorization authority.
- `templates/system_brain.html`, `templates/system_brain_case_table.html`,
  `static/system-brain.css`, and `static/system-brain.js` provide the dark emerald/ivory/gold
  responsive workspace, real aggregate charts, queue, dossier, timeline and sticky decision panel.
- `templates/hilal/dashboard/passport.html` explicitly separates Layer A official SC reference from
  Layer B HilalMarkets factual information and separate-use boundaries.
- `api/routers/public.py` reports redacted Sharia notification, AI research and source-policy health.

## 4. Official Import and Live Source Validation

Source: `https://www.sc.com.my/digital-assets`

The importer uses normal HTTP first, checks `robots.txt`, waits the configured delay, and parses
with Scrapling adaptive selectors. It does not use captcha bypass, proxies, stealth, or evasion.
Rows require all of:

1. Parseable asset name and symbol.
2. Exact `Shariah-compliant` wording in that asset row.
3. SAC meeting number.
4. Parseable decision date.

A live read on 2026-07-15 returned HTTP 200 and parsed 15 explicit rows:
`BTC, ETH, XRP, LTC, BCH, SOL, ADA, LINK, UNI, MATIC, AVAX, DOT, ATOM, WLD, XLM`.
Eight non-qualifying/notice rows were retained as parser exclusions. This was a local live-source
validation, not a write to staging and not a publication.

Every stored import has HTTP metadata, retrieval time, raw content where permitted, normalized
text, content hash, parser version/result and idempotency key. A replay of the same content creates
neither a second run nor duplicate external assessments.

## 5. Canonical Identity and Source Registry

The resolver requires agreement across imported name/symbol and registered canonical metadata:
native coin versus token, chain, contracts where applicable, HTTPS official site/docs and a current
Binance spot-market symbol. Ticker-only matching is impossible. Any discrepancy creates
`SOURCE_IDENTITY_CONFLICT` and blocks research/publication.

The pilot registry covers BTC, ETH and SOL. The continuation registry covers all 15 explicit rows.
It deliberately retains the official MATIC identity; if the current exchange no longer lists
`MATIC/USDT`, the row becomes a conflict rather than silently changing the SC row to POL.

Only deterministic official URLs can enter research: project site/docs, official releases,
governance, GitHub, staking/product disclosures, treasury/tokenomics, and relevant official
regulator/exchange notices. The AI cannot create a URL.

## 6. Factual AI Research

Each asset run fetches registered sources one at a time in stable priority order. Non-critical
failures are recorded and do not scramble source order. After collection, one aggregate Responses
request is made for the asset/run with:

- model `SHARIA_AI_MODEL` (default `gpt-5.4-nano`);
- reasoning `SHARIA_AI_REASONING_EFFORT` (default `low`);
- service tier `SHARIA_AI_SERVICE_TIER` (deployed value `flex`);
- `store=false`, no user PII, strict structured output;
- bounded timeout/retry with jitter and one schema-repair request;
- no implicit standard-tier fallback.

The schema contains identity, activity, token role, product/use findings, evidence references,
gaps, contradictions, change severity, affected methodology areas, review recommendation,
confidence and limitations. It intentionally has no Sharia-status, approval, rejection or
publication field. Invalid output is stored as failed and cannot affect public data.

## 7. Human Review and Publication

Review lifecycle: `DRAFT -> RESEARCHING -> READY_FOR_REVIEW -> NEEDS_EVIDENCE -> APPROVED/REJECTED`.
Returning to research remains open. Approval/rejection sets `done_at`; publication has a separate
state so a publication failure cannot erase the decision.

Decision actions require a reason and preserve admin ID, timestamp, methodology/version and source
snapshot IDs:

- Approve and Publish
- Reject and Store
- Request More Evidence
- Return to Research
- Add Admin Note

Approval creates the immutable decision, existing effective asset assessment, versioned Passport,
integrity hash, active publication, audit event and universe invalidation transactionally. Rejection
retains every source/dossier/AI/decision record and creates no public assessment.

## 8. Two-Layer Passport and Uses

Layer A contains exact imported wording, SAC authority/meeting/date, source, retrieval date,
regulatory scope, and the limitation that coin-specific detailed reasoning may not be public.

Layer B contains HilalMarkets factual identity/activity/utility research, sources reviewed,
staking/lending/yield/derivatives, treasury/governance/tokenomics, evidence gaps, contradictions,
verification/review dates and a statement that it is not unpublished SC reasoning.

The Passport separately records the asset-level reference, spot inclusion, native staking,
third-party lending, yield products, leverage/derivatives and wrapped/bridged representations.
Approval of the asset never approves every related use. HilalMarkets remains spot-only.

## 9. Telegram, Pilot and Scheduling

`SHARIA_ADMIN_TELEGRAM_CHAT_ID` is configuration, never a service constant. New review messages are
compact and include case/asset/reference evidence, completeness, gaps, AI review recommendation,
severity, timestamp and a secure System Brain deep link. Telegram is notification-only.

Notification rows have unique idempotency keys. Immediate review notifications advance the next
reminder by six hours. The hourly checker uses one key per case/window, survives restarts, and stops
after terminal decisions. Failed deliveries use persisted exponential backoff; permanent failure is
visible in System Brain. A minute-level task processes due retries.

The daily import task processes only `SHARIA_PILOT_SYMBOLS` by default. Setting
`SHARIA_PROCESS_REMAINING_IMPORTS=true` allows the other explicit rows into research, but every asset
still needs its own admin decision. The flag does not publish anything.

## 10. Ongoing Monitoring and Fail-Closed Behavior

Only active published assessments are monitored. A run is idempotent for its configured UTC
window. Sources are fetched sequentially. Normalized unchanged pages produce zero AI requests.
All meaningful changed sources for one asset are aggregated into one request with previous/current
evidence. AI cannot change publication.

`human_review_required=true` or HIGH/CRITICAL severity creates a material-change case, stores the
diff, notifies the admin, and applies the configured `under_review` safety hold for new technical
opportunities. Historical SC references and prior Passport/alert evidence remain unchanged.

## 11. Admin Workspace and Visual QA

System Brain sections are Overview, Initial Coin Reviews, Change Reviews, Published Assets,
Rejected/Stored, Methodologies, Source Registry, Scraper Runs, AI Assessments, Telegram/Delivery,
and Audit History. Metrics and charts are database aggregates with explicit empty states. Raw JSON,
Python objects and source code are not rendered.

Browser screenshots:

- `reports/playwright/sharia-governance/admin-overview-desktop-1440.png`
- `reports/playwright/sharia-governance/admin-overview-tablet-900.png`
- `reports/playwright/sharia-governance/review-case-tablet-900.png`
- `reports/playwright/sharia-governance/review-case-mobile-390.png`

The browser scenario verifies a normal signed-up customer sees no System Brain link, promotes that
database user to the real `ADMIN` role, renders live aggregates and a real review case, verifies
decision controls, checks no raw tracebacks/JSON, and confirms reduced-motion CSS.

## 12. Environment and Health

The example environment files now document all requested SC URL, admin Telegram, Flex, retry,
reminder, source cadence, scraper, pilot and continuation settings. They contain no real secret.
Deployed startup fails closed when screening is enforced without the admin destination, Telegram,
OpenAI key, Flex tier, robots compliance or at least a one-second delay. `/health/deep` exposes only
redacted `ok`/`degraded` dimensions.

## 13. Deployment Commands

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\celery.exe -A ai_market_monitor.worker.app worker --loglevel=INFO
.venv\Scripts\celery.exe -A ai_market_monitor.worker.app beat --loglevel=INFO
.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 0.0.0.0 --port 8000
```

Docker deployment:

```powershell
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose ps
```

After migration, confirm `d6e7f8a9b0c1` is the sole head, inspect worker registered/scheduled tasks,
run one pilot import, review Telegram delivery health, and make publication decisions only in the
authenticated admin workspace.

## 14. Verification Results

- Focused governance, screening, API, health, security and AI tests: 44 passed.
- Migration upgrade/head/seed/no-auto-asset tests: 3 passed.
- System Brain route/RBAC/rendering tests: 4 passed.
- Deployment security tests: 10 passed.
- Governance Playwright visual workflow: 1 passed.
- Complete Playwright suite: 17 passed, with zero console, network or page errors.
- Complete repository suite: 1,904 passed, with two upstream Scrapling/lxml deprecation warnings.
- Ruff on changed application, test and migration files: passed.
- Mypy on 12 changed source modules: passed.
- System Brain JavaScript syntax: passed.
- Jinja loading for System Brain, case table and Passport: passed.
- Live official-source dry validation: HTTP 200, 15 explicit rows, 8 excluded rows.

Counts above overlap and must not be summed.

## 15. Manual Staging Checks

1. Back up PostgreSQL and apply the migration with scanning paused.
2. Configure secrets through the deployment platform, not Git or image layers.
3. Confirm the designated account has application `ADMIN`; optionally require Cloudflare Access.
4. Run the pilot import with continuation disabled and inspect all retained source snapshots.
5. Verify current Binance spot identities for BTC, ETH and SOL.
6. Verify one real Flex dossier per pilot asset and inspect token usage/tier/error records.
7. Verify new-case, six-hour reminder, retry and terminal-stop Telegram behavior with the admin bot.
8. Have a qualified human review each pilot dossier; do not bulk approve.
9. Verify a Passport's SC/HilalMarkets layers and use-specific boundaries on customer surfaces.
10. Exercise an unchanged daily scan (zero AI) and a controlled material source change in staging.
11. Enable remaining imports only after pilot sign-off; resolve identity conflicts one by one.

No staging database or live AI request was available in this local task. Therefore no pilot asset
was approved/published here, and this report does not claim that staging import or qualified human
review has occurred.

## 16. Security, Data Rights, Governance and Legal Limitations

- Application RBAC is authoritative; Cloudflare Access is defense in depth.
- Source retrieval follows robots policy and bounded delays. Operators must still verify source
  terms, retention rights, attribution and raw-content storage policy before production retention.
- Raw source/AI/provider payloads never appear in Telegram or customer UI. Secrets are excluded from
  snapshots, AI input, health output and logs.
- Content hashes and immutable version references protect audit integrity but are not blockchain
  attestations.
- The workflow preserves source wording and scope. It does not create a universal halal ruling,
  infer unpublished SAC reasoning, or approve third-party financial products.
- Qualified governance, counsel, incident ownership, review SLA, source licensing and periodic
  methodology review remain organizational responsibilities outside software enforcement.
