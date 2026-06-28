# Alert Proof

Every alert must be reconstructable from deterministic data.

Proof receipt fields:

- Strategy name and version.
- Symbol, exchange, timeframe.
- Evaluation time and market-data timestamp.
- Data latency.
- Candle close or intrabar state.
- Every condition with required value, actual value, status, and blocking flag.
- Required condition completion and match status.
- Optional confirmations.
- User-defined entry zone, stop, invalidation, targets and reward-to-risk only when provided.
- Liquidity and spread.
- Setup completion score.
- Setup lifecycle transition.
- Chart reference when available.
- Alert Trust Score.

Alert Trust Score:

The score is deterministic and explainable. It considers mandatory rule pass rate, optional confirmations, data freshness, candle completeness, liquidity, risk validity, and reliability warnings.

Natural-language summaries may explain the receipt, but must never contradict it.
