# Capability Compatibility Report

Generated compatibility layer: `engine/capability_compatibility.py`.

Current registry count: 473 capabilities.

Current classification:

- Available: 301
- Provider-required: 140
- Unsupported: 32
- Planned: 0
- Experimental: 0

## Compatibility Checks

For every capability the checker verifies:

- registry entry exists
- condition template can be generated
- condition schema validates
- evaluator operand is supported where possible
- provider-required capabilities are not marked fully available
- prompt aliases are counted
- required data is listed

## Dashboard Enforcement

`condition_registry.py` now applies compatibility rows to the dashboard payload:

- `available` appears as implemented.
- `provider_required` appears as provider-required.
- `unsupported` appears as unsupported.

This prevents the UI from presenting a non-executable capability as fully available.

## Important Limitation

Some price-action families remain classified as unsupported by the compatibility checker
because their template operand names do not yet match evaluator names exactly. They should
stay visible as not fully available until their templates and evaluator implementations are
aligned.
