# Scanner And Proof Consistency Audit

Date: 2026-06-27

## Result

The scanner, preview, forward-test, and forensic paths are aligned around the
same `StrategyDefinition` schema and evaluator. Proof receipts are generated
from deterministic evaluation output, not from natural-language guesses.

## Verified Consistency Points

- The evaluator receives a `StrategyDefinition` and derives the canonical
  schema hash from it.
- Evaluation output carries strategy version metadata, schema hash, symbol,
  exchange, timeframe, evaluation time, market-data time, condition tree, and
  proof receipt fields.
- Forensics uses the same evaluator path and returns proof data from the
  deterministic evaluation.
- Forward testing uses the same evaluator and stores proof output.
- Dashboard alert chart/proof endpoints read stored proof receipts rather than
  recomputing unverifiable narrative text.
- Legacy approved strategy hashes are normalized before scanner/on-demand usage
  so old saved strategies do not fail with avoidable hash mismatches.

## Provider-Required Behavior

Provider-required rules must produce blocked/unavailable evidence when mandatory
provider data is not configured. They must not create live alerts as if candle
data alone satisfied the rule.

## Remaining Risk

Live exchange data and old queued scan jobs can still expose stale data or
provider outages. The deterministic code path is guarded, but production
verification should include a live worker smoke test after each deployment.

