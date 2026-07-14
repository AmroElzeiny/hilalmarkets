# Registry-Driven Capability Resolver

## Purpose

TraceEdge uses AI to understand trader language, but never lets AI define executable market
mechanics. Every AI-selected condition carries an immutable `capability_key`. The backend resolves
that key against the versioned registry, validates typed parameters, rebuilds the canonical
condition operands, and rejects unknown or unavailable capabilities before approval.

This makes interpretation smarter without making evaluation probabilistic. Retrieval and AI help
with language; the approved strategy tree and market evaluation remain deterministic.

## Resolution pipeline

1. Split the user's setup into meaningful fragments while ignoring routing-only details such as a
   standalone timeframe or exchange choice.
2. Rank registry capabilities using exact aliases, ordered aliases, token overlap, intent examples,
   semantic tags, direction support, and negative examples.
3. Ask the user when a fragment has competing candidates or unknown words/acronyms. A question
   already answered in the prompt is suppressed.
4. Send only the compact candidate shortlist and its parameter schemas to OpenAI for reranking and
   parameter extraction.
5. Require `capability_key` on every AI condition. Reject a key outside the shortlist or registry.
6. Validate timeframe, comparator, parameters, provider availability, and parameter types.
7. Rebuild the condition from the registry template. AI-provided operand names are discarded.
8. Build the deterministic AND/OR strategy tree and run the existing prompt-coverage audit.
9. Hash the canonical approved schema, including non-null capability provenance.

Example AI selection:

```json
{
  "capability_key": "previous_daily_low_sweep",
  "parameters": {"timezone": "UTC"},
  "timeframe": "15m",
  "required": true,
  "source_fragment": "coins which swept PDL"
}
```

The registry turns this into `daily_low_swept`; the model cannot substitute another operand.

## Capability metadata contract

Every `CapabilitySpec` exposes:

- `semantic_tags`
- `intent_examples`
- `negative_examples`
- `direction_support`
- `temporal_behavior`
- `parameter_schema`
- `conflicts_with`
- `composes_with`
- `provider_requirements`
- `capability_version`
- `proof_template`
- `resource_cost`

Existing entries receive conservative derived metadata centrally. Concepts needing precise slang,
direction, temporal, conflict, or composition behavior should override those derived values in
their registry declaration.

## Unknown and ambiguous language

Known market acronyms such as PDL, PDH, RSI, RVOL, HTF, EMA, and VWAP participate in retrieval.
An unknown acronym such as `XYZ` pauses compilation even if the rest of the fragment matches RSI.
The user is asked what it means; TraceEdge does not silently ignore it. When two registered
capabilities are close, the user receives candidate choices rather than an invented rule.

Clarification answers are stored as context. Structural answers such as `15m`, `Binance`, or
`USDT spot pairs` are not reinterpreted as new conditions. AI summaries are display-only and are
never appended to compiler input.

## Expanding coverage safely

Use these paths in order:

1. **Alias and example expansion:** add real rejected prompt fragments as aliases, intent examples,
   and negative examples for an existing capability. This solves wording gaps without new logic.
2. **Composition:** express a new idea as an AND/OR/NOT/sequence tree of existing capabilities.
   Store a reviewed template rather than generating code.
3. **Parameter extension:** add a typed, bounded parameter to an existing capability and test its
   canonical template, evaluator, proof, warm-up, and invalid values.
4. **New local capability:** implement one canonical evaluator using supported OHLCV data, add
   deterministic fixtures, no-look-ahead tests, proof evidence, aliases, and capability metadata.
5. **Provider-backed capability:** keep it hidden and activation-blocked until a real adapter,
   configuration validation, freshness evidence, rate-limit behavior, and tests exist.
6. **Unresolved-prompt corpus:** record privacy-safe unmatched fragments and the user's selected
   resolution. Review recurring clusters to improve aliases or propose capabilities.
7. **Shadow evaluation:** test proposed capabilities on fixtures and staging data without alerting
   users. Promote only after deterministic and notification-quality review.

Do not generate or execute Python from a user's prompt. A future custom-builder feature should
produce a restricted declarative capability proposal, then require automated tests and human/admin
promotion into the registry before it can monitor live markets.

## Main files

- `src/ai_market_monitor/engine/capabilities.py`
- `src/ai_market_monitor/engine/capability_resolver.py`
- `src/ai_market_monitor/engine/builder_templates.py`
- `src/ai_market_monitor/services/openai_interpreter.py`
- `src/ai_market_monitor/services/ai_semantic_fallback.py`
- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/schemas/strategy.py`

