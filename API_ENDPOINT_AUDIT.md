# API Endpoint Audit

Date: 2026-06-27

Routes were verified from FastAPI OpenAPI output and router declarations.

| Endpoint | Method | Purpose | Request / Response | Auth | Entitlement | Frontend caller | Backend service / models | Tests | Status |
|---|---|---|---|---|---|---|---|---|---|
| `/api/v1/dashboard/capabilities` | GET | Capability registry for builder templates and rule drawer. | Query filters / registry payload. | Dashboard user | Feature visibility by compatibility and plan metadata | Strategy Builder condition library | `condition_registry_payload`, capability compatibility | yes | working |
| `/api/v1/dashboard/strategies/interpret` | POST | Convert Strategy Builder prompt into `StrategyDefinition`. | Prompt payload / strategy, coverage, assumptions, issues. | Dashboard user | Blocks critical/provider-required issues before activation | Strategy Builder prompt path | OpenAI interpreter, rule-based fallback, prompt audit | yes | working |
| `/api/v1/dashboard/strategies` | GET, POST | List and create monitor records. | Strategy create payload / strategy summary. | Dashboard user | Plan limits enforced by strategy services | Monitors, Strategy Builder | User, Strategy, StrategyVersion | partial | working |
| `/api/v1/dashboard/strategies/{strategy_id}` | PATCH | Update monitor metadata/schema draft. | Patch payload / updated summary. | Owner | Plan/ownership checks | Monitors, Builder save | Strategy, StrategyVersion | partial | working |
| `/api/v1/dashboard/strategies/{strategy_id}/versions` | GET, POST | List/create strategy versions. | Version payload / version summary. | Owner | Ownership checks | Builder draft/version controls | StrategyVersion | partial | working |
| `/api/v1/dashboard/strategies/{strategy_id}/approve` | POST | Approve exact schema hash. | Version/hash payload / approval result. | Owner | Blocks hash mismatch | Builder approval | StrategyVersion, AuditEvent | yes | working |
| `/api/v1/dashboard/strategies/{strategy_id}/publish` | POST | Start monitoring from an approved schema. | Version/hash payload / activation result. | Owner | Plan and validation checks | Start monitoring button | Strategy, StrategyVersion, ScanJob | yes | working |
| `/api/v1/dashboard/cockpit/strategies/validate` | POST | Validate deterministic strategy health. | Strategy schema / validation report. | Dashboard user | Timeframe/provider checks | Strategy Cockpit, Builder validation | StrategyCockpitService | yes | working |
| `/api/v1/dashboard/scan-now` | POST | Run Quick Scan/Finder. | Scan request / result list and proof rows. | Dashboard user | Usage and plan limits | Quick Scan | On-demand scan service, market data | partial | working |
| `/api/v1/dashboard/scan-now/interpret` | POST | Convert Quick Scan prompt into mechanics. | Finder prompt / interpreted schema preview. | Dashboard user | Provider-required blocks | Quick Scan prompt path | Interpreter, scan schema builder | partial | working |
| `/api/v1/on-demand-scans` | POST | API on-demand scan creation. | Scan request / job result. | API user | Usage limits | External/API path | On-demand scan service | partial | working |
| `/api/v1/dashboard/charts/setup/{setup_id}` | GET | Setup chart evidence. | Setup id / chart payload. | Owner | Ownership checks | Lifecycle card chart | SetupInstance, condition results | partial | working |
| `/api/v1/dashboard/lifecycles/{setup_id}/chart` | GET | Lifecycle chart overlay. | Setup id / candles and markers. | Owner | Ownership checks | Lifecycles chart popup | SetupInstance, annotations | partial | working |
| `/api/v1/dashboard/lifecycles/{setup_id}/annotations` | PUT | Save lifecycle chart drawings. | Annotation payload / saved result. | Owner | Ownership checks | Chart drawing tools | SetupInstance annotations | partial | working |
| `/api/v1/dashboard/cockpit/alerts/{alert_id}/proof` | GET | Alert proof receipt. | Alert id / proof receipt. | Owner | Ownership checks | Proof/details views | Alert, proof receipt | partial | working |
| `/api/v1/dashboard/cockpit/alerts/{alert_id}/feedback` | POST | Alert feedback. | Feedback payload / saved feedback. | Owner | Ownership checks | Alert feedback buttons | Alert, UserFeedback, AuditEvent | partial | working |
| `/api/v1/investigations/why-no-alert` | POST | Deterministic missed-alert reconstruction. | Investigation request / forensic result. | API user | Forensic entitlement where configured | Why No Alert | Forensic engine, strategy versions | partial | working |
| `/api/v1/dashboard/cockpit/missed-moves` | POST | Cockpit missed-move analysis. | Analysis payload / queued result. | Dashboard user | Usage checks | Review center | Cockpit service | partial | working |
| `/api/v1/dashboard/exports` | GET, POST | Export job list/create. | Export request / job summary. | Dashboard user | Export entitlement | Exports page | ExportJob | partial | working |
| `/api/v1/dashboard/exports/{job_id}/run` | POST | Execute export. | Job id / run status. | Owner | Export entitlement | Exports page | Export service/job | partial | working |
| `/api/v1/dashboard/exports/{job_id}/download` | GET | Download generated export. | Job id / file response. | Owner | Export entitlement | Export download button | ExportJob/file store | partial | working |
| `/api/v1/billing/*` | GET, POST | Plans, checkout, portal, webhooks. | Billing requests / billing state. | Mixed | Source of truth for entitlements | Billing page, provider webhook | Billing provider abstraction, BillingEvent | partial | working |
| `/api/v1/telegram/webhook` | POST | Telegram webhook ingestion. | Telegram update / ok. | Secret header | Telegram entitlement at action layer | Telegram bot | Telegram service | partial | working |
| `/api/v1/discord/*` | POST | Discord OAuth, interactions, destinations, support, moderation. | Discord payloads / action responses. | Signature/state checks | Discord entitlement | Discord bot/dashboard | Discord services | partial | working |

## Missing Or Deferred

- Full browser automation coverage is still partial.
- Provider-backed endpoint tests are placeholders until the providers are
  configured.
- Replay/history UI is intentionally hidden in current product screens, but
  backend chart/replay-style endpoints still exist for lifecycle/chart use.

