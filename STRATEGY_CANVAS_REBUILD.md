# Strategy Canvas Rebuild

## Layout

Desktop uses a three-column cockpit:

- Left: strategy outline and section navigation.
- Center: connected, readable strategy map cards.
- Right: Summary, Validation, Preview, and AI Help tabs.

The sticky header and bottom action bar expose Save Draft, Preview Matches, Validate, and Start
Monitoring. Activation remains disabled until critical validation passes.

## Component Structure

The builder uses focused rendering and interaction functions:

- `renderStrategyCanvas()`
- `renderMonitorCard()`
- `renderUniverseCard()`
- `renderLogicGroupCard()`
- `renderConditionCard()`
- `renderRightPanel()`
- `renderValidationChecklist()`
- `renderPromptUnderstandingPreview()`
- `openConditionLibrary()`
- `openConditionDrawer()`
- `updateBuilderStatus()`

## State Management

The existing schema remains the source of truth. Existing compatibility functions are retained:

- `loadInitialSchema()`
- `hydrateBuilderForm()`
- `schemaFromForm()`
- `renderNode()`

Legacy form fields remain hidden behind the canvas so existing saved strategy schemas and API
payloads retain their shape. Visual edits update those fields before rebuilding the schema.

## Drawers And Modals

- Add Condition opens a searchable modal with category navigation.
- Condition cards remain sentence-first and open a right drawer for editing.
- Group editing uses the same drawer and keeps advanced parameters behind disclosure.
- Monitor, Universe, Alert Rules, and Risk Context use section-specific drawers.
- Raw JSON and template saving live under Advanced.
- Escape closes the drawer, dialog behavior is keyboard accessible, and focus is returned to the
  invoking control.

## Prompt And Template Flows

- Prompt interpretation now stops at an Understanding Preview.
- Assumptions, ambiguities, unsupported items, and interpreted rules are visible before the map.
- The user explicitly opens the visual map.
- Template cards provide Preview Template and Use Template actions.
- Neither path activates monitoring automatically.

## Responsive Behavior

- Desktop: three-column cockpit.
- Tablet: horizontal outline tabs and a collapsible review panel.
- Mobile: five-step navigation for Basics, Universe, Logic, Alerts, and Review.
- Drawers become full-width and the condition library becomes a single-column browser.

## Files Changed

- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`
- `tests/unit/test_dashboard_static_assets.py`
- `tests/integration/test_dashboard_api.py`
- `STRATEGY_BUILDER_UX_AUDIT.md`
- `STRATEGY_CANVAS_REBUILD.md`
- `BUILDER_COPY_GUIDE.md`
- `IMPLEMENTATION_SUMMARY.md`
