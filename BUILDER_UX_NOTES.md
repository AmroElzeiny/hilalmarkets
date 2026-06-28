# Builder UX Notes

## Condition Categories

The Add Condition library exposes these primary categories: `Price`, `Indicator`, `Candle Pattern`, `Price Action`, `Market Structure`, `Liquidity / Smart Money`, `Volume / Flow`, `Volatility / Squeeze`, `Trend`, `Momentum`, `Time / Session`, `Market Context`, `Relative Strength`, `Risk / Trade Quality`, `News / Events`, `Order Book / Liquidity`, `Ranking / Universe`, `Alert Behavior`, `Setup Lifecycle`, `Advanced Logic`.

- The default view shows a limited beginner-friendly Phase 1 selection.
- Search or category selection reveals the wider catalog.
- Provider-required and runtime-dependent conditions show availability badges and disabled add buttons.
- Every card includes a preview sentence, required-data summary, warm-up count, provider badge, and an Explain this condition action.
- Advanced raw condition remains available, but it is no longer the primary creation path.

## Prompt Aliases

- Prompt matching searches canonical keys, display names, and aliases.
- Executable phrases become validated `ConditionRule` objects in the visual tree.
- Provider-bound phrases become explicit unsupported/provider-required issues.
- Existing deterministic parsers retain priority, preventing duplicate rules.
- Ambiguous or unsupported requests still require user clarification and approval.

Examples:

- `OBV rising` -> `on_balance_volume`
- `CMF above zero` -> `chaikin_money_flow`
- `takes previous high` -> `previous_high_swept`
- `reclaims level` -> `sweep_and_reclaim`
- `avoid weekends` -> `weekday_only`
- `London open` -> `session_open_window`
- `alts stronger than BTC` -> provider-required relative-strength context

## Complex Logic Without Raw JSON

- Groups expose named operator controls for lookback candles, persistence count, sequence gap, minimum pass count, cooldown, and confirmation bars.
- Condition cards expose named capability parameters such as periods, components, wick ratios, trend context, and confirmation requirements.
- AND, OR, NOT, SEQUENCE, WITHIN_LAST, PERSISTED_FOR, COUNT_OF, COOLDOWN_CONDITION, FIRST_TIME_TRUE, CHANGED_STATE, CROSS_WITH_CONFIRMATION, and CONDITIONAL_BRANCH remain editable as nested visual groups.
- Advanced JSON fields remain inside editor drawers as an escape hatch for expert users.
- Prompt-created strategies still require visual review and explicit approval before activation.

