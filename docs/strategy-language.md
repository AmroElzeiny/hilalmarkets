# Strategy Language

The Strategy Translator accepts normal trading language and converts it into approved mechanics.

Supported categories include:

- Direction: long, short-bias research, bullish, bearish.
- Universe: exchange, quote asset, symbols, exclusions.
- Time: timeframe, candle close, intrabar, session windows.
- Indicators: EMA, SMA, RSI, MACD, ATR, Bollinger Bands, VWAP, volume averages, Stochastic
  RSI, MFI, CCI, Williams %R, ROC, Momentum, TSI, Ultimate Oscillator, RVI, Connors RSI,
  WMA, HMA, DEMA, TEMA, KAMA, VWMA, LRMA, ZLEMA, moving-average ribbons, Ichimoku,
  SuperTrend, Parabolic SAR, Aroon, directional movement, Elder Impulse, Keltner Channels,
  Donchian Channels, Bollinger %B, and Bollinger/Keltner squeeze state.
- Price action: breakouts, breakdowns, new highs, percentage moves, range reclaim, liquidity sweep.
- Candles: engulfing, wick rejection, inside bar, pin bar style patterns where deterministic definitions exist.
- Liquidity: 24h quote volume, average candle volume, spread, listing age.
- Optional risk/trade-quality context when explicitly requested: fixed percent stop, ATR stop,
  swing stop, targets, reward-to-risk, fees and slippage.

The translator returns:

- Required rules.
- Optional rules.
- Optional risk/trade-quality assumptions only when the user asked for them.
- Unsupported rules.
- Clarifying questions.
- Executable strategy schema.

Activation rule:

No AI interpretation may run live until the user approves the structured version.

Condition trees also support deterministic temporal and branch logic: NOT, SEQUENCE/THEN,
WITHIN_LAST, PERSISTED_FOR, COUNT_OF, COOLDOWN_CONDITION, FIRST_TIME_TRUE, CHANGED_STATE,
CROSS_WITH_CONFIRMATION, and CONDITIONAL_BRANCH.
