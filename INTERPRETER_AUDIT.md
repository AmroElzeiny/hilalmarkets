# Interpreter Audit

## Summary

This pass audited the TraceEdge prompt-to-strategy path across OpenAI interpretation,
rule-based fallback parsing, schema validation, capability templates, evaluator
compatibility, and dashboard prompt handoff.

## Bugs And Reliability Gaps Found

- Strategy Builder prompts were using the Quick Scan interpretation endpoint.
- Conditions had no durable `source_fragment`, `confidence`, `ai_interpreted`,
  `provider_required`, or `availability` fields.
- OpenAI validation failures were collapsed into generic fallback metadata.
- OpenAI JSON schema allowed too many extra fields and did not require source fragments.
- Prompt fragments could be silently ignored if another recognized term existed nearby.
- Decimal values such as `1.5x` could be split into separate fragments.
- Optional/mandatory detection used a wide text window and could mark unrelated rules optional.
- BTC/ETH cross-symbol context could be interpreted as a normal single-symbol EMA rule.
- Vague phrases such as `ready to pump`, `good setups`, and `high probability` needed
  explicit ambiguity handling.
- Dashboard schema hydration rebuilt some sections and risked dropping advanced values.
- Capability registry payload did not reflect template/evaluator compatibility.

## Repairs Implemented

- Added `engine/prompt_audit.py` and `PromptCoverageReport`.
- Added condition provenance fields to the strategy schema.
- Added prompt coverage reports to interpretation metadata and dashboard API payloads.
- Added blocking issues for unclassified meaningful fragments.
- Added stricter OpenAI schema requirements and diagnosable fallback metadata.
- Added OpenAI coverage guard for drafts that drop prompt fragments.
- Added a dedicated `POST /api/v1/dashboard/strategies/interpret` endpoint.
- Updated dashboard prompt-to-canvas flow to use the strategy endpoint.
- Added clause-level optional/mandatory detection.
- Added ambiguity/provider-required handling for vague and cross-symbol prompts.
- Added capability compatibility checker and dashboard registry availability overrides.

## Known Limitations

- Cross-symbol context is recognized and blocked as provider-required until a dedicated
  market-context evaluator is enabled.
- The rule-based parser is deterministic and safer, but still not a full natural-language
  parser. Uncovered meaningful clauses now block instead of disappearing.
- OpenAI can still fail, but the failure now includes validation metadata and safe output
  excerpts for debugging.
