# TraceEdge Dashboard Implementation Plan

## Repository Inspection Summary

Existing implementation found:

- FastAPI application with Jinja2 templates and static CSS/JS.
- Dashboard router with email signup/signin sessions, dashboard home, monitors, scan-now shell,
  Near-Miss Radar, setup list, alert proof page, trial, billing, connections, settings, support,
  referrals, and billing result pages.
- Public landing page and auth templates.
- SQLAlchemy models for users, identities, Telegram/Discord connections, billing, plans, trials,
  strategies, strategy versions, strategy conditions, scan jobs/results, setup lifecycle,
  near-miss snapshots, alerts, alert deliveries, support requests, incidents, audits and feedback.
- Deterministic strategy schema with `ConditionGroup` and `ConditionRule`.
- Strategy service with interpretation, version creation, approval, preview and activation.
- On-demand scan service using the deterministic engine and market-data provider.
- Billing abstraction and NOWPayments/Static provider support.
- Telegram and Discord service layers.
- Existing admin/support/reliability services and routes.
- Alembic migrations for the existing core schema.

Existing features to preserve and reuse:

- Web auth/session cookie flow.
- Existing dashboard session model.
- Existing strategy schema and strategy service.
- Existing on-demand scan service.
- Existing alert proof receipt storage.
- Existing setup lifecycle and near-miss snapshot models.
- Existing billing service and plan catalog.
- Existing Telegram/Discord connection models and APIs.
- Existing admin routes and audit model.

## Missing Components

Missing or incomplete dashboard pieces:

- Full strategy builder UI for nested AND/OR editing.
- Strategy template persistence.
- Strategy version comparison UI/API.
- Chart API and chart window with candle overlays.
- Setup Replay job/result persistence and visualization.
- Backtest job/result persistence and analytics shells.
- Candle-by-candle proof timeline.
- User export jobs.
- Support ticket message thread model.
- Integration test result model.
- Dashboard API routes for current user, strategies, templates, charts, analytics, integrations
  and support tickets.
- Modern dark-first responsive dashboard layout.

## Additions In This Pass

Backend/API:

- Add dashboard persistence models:
  - `StrategyTemplate`
  - `SetupReplayJob`
  - `SetupReplayResult`
  - `BacktestJob`
  - `BacktestResult`
  - `ChartSnapshot`
  - `UserExportJob`
  - `SupportTicketMessage`
  - `IntegrationTestResult`
- Add Alembic migration for those models.
- Add secure dashboard API router with user-owned access checks.
- Add chart candle/overlay/proof/analytics/support/template endpoints.
- Reuse existing market data provider for candle APIs.

Frontend:

- Upgrade dashboard layout to dark-first trading terminal style.
- Add global navigation entries for Strategy Builder, Setup Replay, Analytics, Integrations
  and Admin.
- Add strategy builder shell that manipulates the existing `ConditionGroup`/`ConditionRule`
  compatible JSON.
- Add version comparison page shell backed by strategy version data.
- Add chart window component using lightweight canvas/SVG-free DOM rendering first, with candle
  API integration and overlay placeholders from backend proof data.
- Add proof viewer, setup replay, analytics, integrations, support and settings panels with real
  empty states where source data is absent.

## Data Models and Migrations

Migration required:

- Create the dashboard extension tables listed above.
- Use UUID primary keys and user ownership constraints.
- Add indexes for user/status/created lookups and job status queues.

## API Routes Needed

Routes added or completed in this pass:

- `GET /api/v1/dashboard/current-user`
- `GET /api/v1/dashboard/strategies`
- `POST /api/v1/dashboard/strategies`
- `PATCH /api/v1/dashboard/strategies/{strategy_id}`
- `POST /api/v1/dashboard/strategies/{strategy_id}/approve`
- `GET /api/v1/dashboard/strategies/{strategy_id}/versions`
- `POST /api/v1/dashboard/strategies/{strategy_id}/versions`
- `POST /api/v1/dashboard/strategies/compare`
- `GET /api/v1/dashboard/templates`
- `POST /api/v1/dashboard/templates`
- `POST /api/v1/dashboard/scan-now`
- `GET /api/v1/dashboard/charts/candles`
- `GET /api/v1/dashboard/charts/setup/{setup_id}`
- `GET /api/v1/dashboard/charts/alert/{alert_id}`
- `POST /api/v1/dashboard/setup-replay`
- `GET /api/v1/dashboard/setup-replay`
- `GET /api/v1/dashboard/setup-replay/{job_id}`
- `POST /api/v1/dashboard/backtests`
- `GET /api/v1/dashboard/analytics/overview`
- `GET /api/v1/dashboard/analytics/symbols`
- `GET /api/v1/dashboard/analytics/setups`
- `GET /api/v1/dashboard/billing/status`
- `GET /api/v1/dashboard/integrations`
- `POST /api/v1/dashboard/support/tickets`

Routes still planned:

- Full replay execution worker endpoints.
- Backtest execution worker endpoints.
- Chart replay/backtest overlay endpoints beyond the persisted job/result APIs.
- Export job execution and downloads.
- Billing portal endpoint if provider supports it.
- Full admin HTML pages beyond the existing admin API.

## Tests Needed

Added/updated tests should cover:

- Dashboard pages render.
- Strategy templates create/list.
- Chart candle API returns normalized candles.
- Alert proof ownership checks.
- Analytics empty and populated states.
- Support ticket creation.
- User cannot access another user's data.
- Full suite remains passing.

## Remaining Limitations

- The chart component starts with a lightweight custom renderer to avoid adding a heavy frontend
  framework. A dedicated library such as TradingView Lightweight Charts can be introduced later
  if licensing and bundling are approved.
- Setup Replay and Backtest models/routes are scaffolded for persistence and visualization, but
  long-running replay/backtest workers remain a follow-up.
- Strategy builder supports schema-compatible editing and validation shells; drag-and-drop can be
  enhanced later with a lightweight library while preserving keyboard controls.
