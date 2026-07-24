# HilalMarkets AI Setup Chat Evaluation

**Release gate:** FAIL  
**Cases:** 2 · **Pass rate:** 0.0% · **Average score:** 0.000 · **Measured evaluator cost:** $0.0913

## Topic results

| Topic | Severity | Cases | Pass | Score |
|---|---:|---:|---:|---:|
| UI and backend parity | critical | 2 | 0.0% | 0.000 |
  - PASS `ui_backend_parity` actual=1.0 required >= 0.99 — Outputs equivalent

## Evidence-backed failures

### ui_backend_parity-001-1753804835 — 0.000
- Schema: No structured strategy object captured
- Failure: {'reference': 'scenario.success_criteria[0] (metric ui_backend_parity >= 0.99, critical=true)', 'detail': 'No UI/backend equivalent result was produced. The transcript shows repeated bounded-policy blocks and no captured structured strategy object/contract. Deterministic checks also report schema_valid=0.0 and mapped_field_coverage=0.0.', 'evidence': [{'turn_id': 'a3', 'quote': 'The requested action was blocked by the bounded control policy. Nothing was executed.'}, {'turn_id': 'a4', 'quote': 'The requested action was blocked by the bounded control policy. Nothing was executed.'}], 'path': 'structured_output'}
- Failure: {'reference': 'deterministic_checks.schema_valid', 'detail': "schema_valid is explicitly 0.0 and schema_errors contains 'No structured strategy object captured'. Therefore there is no schema-valid, capturable UI/backend contract to compare for parity.", 'path': 'schema_errors[0]'}
- Failure: {'reference': 'deterministic_checks.mapped_field_coverage and mapped_field_accuracy', 'detail': 'Both mapped_field_coverage=0.0 and mapped_field_accuracy=0.0 indicate that required fields (including symbol/timeframe/filter/approval gating/exclude) were not actually mapped into any structured output.', 'path': 'deterministic_checks.mapped_field_coverage'}
- Proof `u1`: User requires: SOLUSDT only (exclude XRPUSDT), 5m context + 1d trigger, bullish move >= 7.5%, long direction, no sharia-status tagging, and explicit approval gate.
- Proof `a3`: Assistant states action was blocked by bounded control policy; nothing was executed.
- Proof `a4`: Assistant again states action was blocked by bounded control policy; nothing was executed.
- Proof `schema_valid`: schema_valid=0.0; schema_errors includes 'No structured strategy object captured'.
- Fix: {'reference': 'schema_valid / structured_output requirement', 'detail': 'Produce a schema-valid structured strategy/watchlist object (captured under `structured_output`) that includes: include_symbol=SOLUSDT only, excluded_symbol=XRPUSDT, context_timeframe=5m, trigger_timeframe=1d, direction=long, move filter bullish_move_percent >= 7.5 with operator gte, and an explicit approval gate mechanism—then ensure the UI output and backend contract are derived from the same assembled fields to satisfy ui_backend_parity.', 'path': 'structured_output'}
- Fix: {'reference': 'Approval gating constraint', 'detail': "Instead of blocking the whole task, ask for explicit 'approve' approval in a dedicated step and only emit the final drafted UI/backend-equivalent object after the user says approve.", 'path': 'transcript[a*].text'}
- Reproduce: `hm-chatbot-eval replay 20260723T152031Z ui_backend_parity-001-1753804835 --target backend`

### ui_backend_parity-001-1753804835 — 0.000
- Schema: No structured strategy object captured
- Failure: {'kind': 'missing_schema_or_structured_output', 'detail': 'No structured strategy object was captured; structured_output is null and schema_valid is 0.0. Therefore the required UI fields vs backend contract fields equivalence cannot be validated.', 'evidence': [{'reference': 'deterministic_checks.structured_output', 'path': 'deterministic_checks.structured_output', 'detail': 'structured_output is null'}, {'reference': 'deterministic_checks.schema_valid', 'path': 'deterministic_checks.schema_valid', 'detail': 'schema_valid = 0.0'}, {'reference': 'scenario.success_criteria', 'path': 'scenario.success_criteria[0]', 'detail': 'Metric requires outputs equivalent (ui_backend_parity >= 0.99, critical=true), but no comparable output artifacts exist.'}]}
- Failure: {'kind': 'ui_backend_parity_not_measurable', 'detail': 'The transcript contains approval discussion but no finalized UI output format and no backend contract JSON/fields were produced. This prevents determining whether UI and backend results match.', 'evidence': [{'reference': 'transcript turn a4', 'path': 'transcript[7]', 'detail': 'Assistant describes a desired mirrored rule format, but does not provide an actual finalized UI record or backend contract record.'}, {'reference': 'deterministic_checks.mapped_field_coverage', 'path': 'deterministic_checks.mapped_field_coverage', 'detail': 'mapped_field_coverage = 0.0'}, {'reference': 'deterministic_checks.mapped_field_accuracy', 'path': 'deterministic_checks.mapped_field_accuracy', 'detail': 'mapped_field_accuracy = 0.0'}]}
- Proof `transcript turn a1`: Assistant attempt resulted in TimeoutError: Page.wait_for_function: Timeout 90000ms exceeded.
- Proof `transcript turn a3`: Assistant states 'Yes — that’s the exact spec being approved...' but does not provide the finalized UI/backend structured artifacts.
- Proof `deterministic_checks.structured_output`: structured_output is null and structured strategy object was not captured.
- Proof `deterministic_checks.schema_valid`: schema_valid = 0.0
- Fix: {'kind': 'produce_schema_valid_structured_strategy_object', 'detail': 'Return a schema-valid structured output object (not null) that contains both (1) UI fields and (2) backend contract fields, such that the fields are explicitly mapped and mirrored (symbol SOLUSDT, excluded_symbol XRPUSDT, trigger_timeframe 1d, context_timeframe 5m, direction long, condition bullish move >= 7.5%, operator gte, sharia-status none).', 'evidence': [{'reference': 'deterministic_checks.schema_valid', 'path': 'deterministic_checks.schema_valid', 'detail': 'Currently 0.0; must become >0.99 for schema validity.'}]}
- Fix: {'kind': 'demonstrate_ui_backend_parity_with_comparable_artifacts', 'detail': 'Include the exact finalized UI output and the exact finalized backend contract output in the same response (or separately but both present), so the evaluator can verify equivalence and score ui_backend_parity >= 0.99.', 'evidence': [{'reference': 'scenario.success_criteria', 'path': 'scenario.success_criteria[0]', 'detail': 'ui_backend_parity requires outputs equivalent with threshold >= 0.99 (critical). Currently not verifiable.'}]}
- Fix: {'kind': 'avoid implying completion without emitting the actual outputs', 'detail': 'The assistant should not stop at describing a format. It must emit the actual finalized watchlist rule/output format and the backend contract fields values so parity can be checked.', 'evidence': [{'reference': 'transcript turn a4', 'path': 'transcript[7]', 'detail': "a4 only states what the format 'should be' rather than providing the actual finalized records."}]}
- Reproduce: `hm-chatbot-eval replay 20260723T152031Z ui_backend_parity-001-1753804835 --target ui`
