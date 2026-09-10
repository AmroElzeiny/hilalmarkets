# Mission baseline

- **run_id**: 20260910T195812Z-d8f498bb
- **started_at**: 2026-09-10
- **commit**: 145275f2206dfbd8b9b0ab2ab5156ac0fe9aa269 (Launch Y6.97)
- **branch**: cloudflare-access-service-tokens (ahead by 2 commits)
- **workdir**: C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant
- **memory_budget**: 2.5 GB free (Windows, 15.4 GB total)
- **working_tree_state**: many uncommitted modifications across 208 files; mission says do NOT touch unrelated changes

## Untracked but expected (this mission):
- `.claude/`, `.hm-orchestrator/`, `.opencode/`
- `src/ai_market_monitor/services/plan_changes.py` (WP4 landing site)
- `src/ai_market_monitor/services/system_brain_payments.py`
- `src/ai_market_monitor/static/hm-plan-change.js`
- `templates/hilal/macros/ask_ai.html`
- `alembic/versions/d4f61a09c73b_subscription_plan_changes.py`
- new tests under `tests/unit/`, `tests/integration/`, `tests/browser/`, `tests/engine/`
- `tools/hm-orchestrator/`

## Mission critical pre-existing items
- Settings jump bar unified (8 groups, including g-data)
- Cache key currently one value: `20260910-pay-terms` (125 places)
- Resume-billing link has the cycle hole that WP1 must close