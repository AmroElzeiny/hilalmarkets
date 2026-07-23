# HilalMarkets AI Setup Chat Evaluation

**Release gate:** PENDING_JUDGE  
**Cases:** 24 · **Pass rate:** 0.0% · **Average score:** 0.000 · **Measured evaluator cost:** $0.0000

## Topic results

| Topic | Severity | Cases | Pass | Score |
|---|---:|---:|---:|---:|
| Contradiction detection and resolution | critical | 2 | 0.0% | 0.000 |
  - FAIL `clarification_quality` actual=None required >= 0.95 — Contradiction explicitly resolved
  - FAIL `unsafe_guess_rate` actual=None required == 0 — No guessed execution
| Cross-turn constraint binding | critical | 2 | 0.0% | 0.000 |
  - FAIL `semantic_accuracy` actual=None required >= 0.97 — Combined contract is correct
| Exclusions and negative constraints | critical | 2 | 0.0% | 0.000 |
  - FAIL `semantic_accuracy` actual=None required >= 0.98 — Exclusions correct
| Long-conversation context retention | critical | 2 | 0.0% | 0.000 |
  - FAIL `context_fact_recall` actual=None required >= 0.97 — Required early facts retained
  - FAIL `silent_constraint_loss` actual=None required == 0 — No silent loss
| Model and prompt version drift | critical | 2 | 0.0% | 0.000 |
  - FAIL `pass_flip_rate` actual=None required <= 0.01 — Minimal unexplained pass flips
  - FAIL `semantic_score_delta_abs` actual=None required <= 0.03 — Stable semantics
| Nested AND/OR logic | critical | 2 | 0.0% | 0.000 |
  - FAIL `grouping_accuracy` actual=None required >= 0.98 — Nested groups exact
| Comparison operator mapping | critical | 2 | 0.0% | 0.000 |
  - FAIL `operator_accuracy` actual=None required >= 0.99 — Operators exact
| Boolean precedence | critical | 2 | 0.0% | 0.000 |
  - FAIL `grouping_accuracy` actual=None required >= 0.98 — Precedence preserved
| Repeated correction cycles | critical | 2 | 0.0% | 0.000 |
  - FAIL `correction_adherence` actual=None required >= 0.98 — Latest correction wins
| Schema-valid but semantically incorrect mapping | critical | 2 | 0.0% | 0.000 |
  - FAIL `semantic_accuracy` actual=None required >= 0.98 — Correct meaning beyond schema
  - FAIL `false_executable_rate` actual=None required == 0 — No wrong executable output
| Numeric threshold mapping | critical | 2 | 0.0% | 0.000 |
  - FAIL `threshold_accuracy` actual=None required >= 0.99 — Thresholds exact
| Timeframe mapping | critical | 2 | 0.0% | 0.000 |
  - FAIL `timeframe_accuracy` actual=None required >= 0.99 — Timeframes exact

## Evidence-backed failures

### long_context_retention-001-1491321658 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z long_context_retention-001-1491321658 --target backend`

### long_context_retention-001-1491321658 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z long_context_retention-001-1491321658 --target ui`

### cross_turn_binding-001-468438448 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z cross_turn_binding-001-468438448 --target backend`

### cross_turn_binding-001-468438448 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z cross_turn_binding-001-468438448 --target ui`

### contradiction_resolution-001-1583695396 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z contradiction_resolution-001-1583695396 --target backend`

### contradiction_resolution-001-1583695396 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z contradiction_resolution-001-1583695396 --target ui`

### repeated_correction_cycles-001-950375139 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z repeated_correction_cycles-001-950375139 --target backend`

### repeated_correction_cycles-001-950375139 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z repeated_correction_cycles-001-950375139 --target ui`

### model_version_drift-001-505914558 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z model_version_drift-001-505914558 --target backend`

### model_version_drift-001-505914558 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z model_version_drift-001-505914558 --target ui`

### schema_valid_semantic_error-001-964743671 — 0.000
- Runtime error: `HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.openai.com/v1/responses'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z schema_valid_semantic_error-001-964743671 --target backend`

### schema_valid_semantic_error-001-964743671 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z schema_valid_semantic_error-001-964743671 --target ui`

### operator_mapping-001-508429853 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z operator_mapping-001-508429853 --target backend`

### operator_mapping-001-508429853 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z operator_mapping-001-508429853 --target ui`

### threshold_mapping-001-874133838 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z threshold_mapping-001-874133838 --target backend`

### threshold_mapping-001-874133838 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z threshold_mapping-001-874133838 --target ui`

### timeframe_mapping-001-812507485 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z timeframe_mapping-001-812507485 --target backend`

### timeframe_mapping-001-812507485 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z timeframe_mapping-001-812507485 --target ui`

### nested_boolean_logic-001-210481855 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z nested_boolean_logic-001-210481855 --target backend`

### nested_boolean_logic-001-210481855 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z nested_boolean_logic-001-210481855 --target ui`

### precedence_grouping-001-1568343993 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z precedence_grouping-001-1568343993 --target backend`

### precedence_grouping-001-1568343993 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z precedence_grouping-001-1568343993 --target ui`

### exclusion_mapping-001-1219669429 — 0.000
- Runtime error: `HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:8000/signin'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z exclusion_mapping-001-1219669429 --target backend`

### exclusion_mapping-001-1219669429 — 0.000
- Runtime error: `RuntimeError: Authenticated AI Setup Chat marker was not found exactly once`
- Reproduce: `hm-chatbot-eval replay 20260723T053736Z exclusion_mapping-001-1219669429 --target ui`
