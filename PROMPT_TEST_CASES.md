# Prompt Test Cases

Implemented test file:

- `tests/interpreter/test_prompt_interpreter_reliability.py`

The suite contains 100 supported prompt cases plus blocked ambiguity/provider-required
cases.

## Groups Covered

- Simple scanner prompts:
  - percent gain today
  - percent drop over 24h
- Indicator prompts:
  - RSI below/above thresholds
  - RSI cross with volume
  - MACD histogram positive
  - EMA/SMA trend filters
- Price-action prompts:
  - breakout
  - break and retest
  - liquidity sweep plus candle confirmation
- Candle prompts:
  - previous bullish/bearish candle
  - no bearish engulfing in a lookback
  - consecutive red daily candles
  - not doji
- Optional vs required:
  - required RSI with optional volume confirmation
- Cross-symbol context:
  - BTC trend filter blocks as provider-required
- Ambiguous prompts:
  - strong coins
  - ready to pump
  - good setups
  - high probability trades

## Assertions

Every case asserts:

- schema validates and hashes
- prompt coverage report exists
- coverage and confidence are present
- no meaningful fragment is unclassified
- conditions include source fragment and confidence
- blocked prompts are actually blocked
