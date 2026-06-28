# Builder Schema Preservation Audit

Date: 2026-06-27

## Result

The Strategy Builder uses the dedicated dashboard strategy interpretation
endpoint and preserves advanced condition metadata through board/drawer edits.

## Verified Paths

- Prompt interpretation calls `POST /api/v1/dashboard/strategies/interpret`.
- Quick Scan uses separate `/scan-now` endpoints.
- Manual builder conditions are sourced from the same capability registry
  templates used by prompt-created conditions.
- The condition drawer exposes and persists:
  - `source_fragment`
  - `confidence`
  - `provider_required`
  - `availability`
  - `approximation_note`
- Raw JSON remains hidden behind an advanced details panel.
- Provider-required and unavailable states are rendered as badges.

## Preservation Rules

The builder must not discard:

- source provenance
- confidence
- provider-required status
- availability
- approximation notes
- advanced operand parameters
- universe filters
- risk rules
- alert rules
- lifecycle/proof metadata attached to strategy versions

## Tests

- `tests/dashboard/test_builder_schema_preservation.py`

The test verifies prompt-to-board schema preservation, drawer edit behavior,
and schema-hash stability except for the intended edit.

## Remaining Risk

The browser-side workflow board still needs heavier end-to-end UI automation.
Current coverage proves schema preservation at the JavaScript/source contract
level and backend schema validation level.

