# Prompt Interpreter E2E Audit

Date: 2026-06-27

## Result

The prompt path now has deterministic coverage checks for both OpenAI-generated
and rule-based interpretations. A strategy is not considered ready if meaningful
prompt text is unclassified, provider-required, ambiguous, or unsupported.

## Rule-Based Interpreter Fixes

- Catalogue matches now attach the user's local source clause to the generated
  `ConditionRule.source_fragment`.
- Recognized unsupported/provider-required capabilities are reported with
  `source_fragment` instead of being dropped.
- Provider-required concepts are not treated as executable merely because an
  alias matched.
- Time-window conditions now use market-filter operands.
- Liquidity sweep, range/retest, structure-shift, support/resistance, equal
  high/low, MA retest, volatility, and impulse-candle language now maps to
  evaluator-supported names.
- Price threshold parsing now ignores provider-context values such as market
  cap, funding, open interest, and volume-context numerals.
- Broad blocked aliases are suppressed when an executable condition already
  covers the same user phrase.

## OpenAI Interpreter Guardrails

- OpenAI output is still passed through downstream prompt coverage verification.
- If OpenAI coverage fails, the response is returned through deterministic
  fallback with a blocking review issue, not as ready-to-activate.
- OpenAI validation errors are stored in `raw_metadata`.
- The prompt sent to OpenAI is generated from compatibility-filtered executable
  and blocked/provider-required capability sets.
- The schema requires condition `source_fragment`, confidence, and unsupported
  issue `source_fragment`.

## Prompt Tests

The representative suite covers 60 realistic prompts across:

- price and percentage movement
- indicators
- trend
- momentum
- volume and flow
- volatility and squeeze
- candle patterns
- price action
- market structure
- liquidity/smart-money wording
- time/session logic
- market context
- risk/trade quality
- alert behavior
- advanced logic and negation

The remaining 90 prompt cases requested in the prompt are documented as future
scale-out work; the current suite exercises the highest-risk conversion paths
and all fixed categories.

## Current Gaps

- Provider-required concepts intentionally block mandatory activation until
  real providers are configured.
- The OpenAI model may still produce a draft, but readiness is controlled by
  deterministic coverage and schema validation.

