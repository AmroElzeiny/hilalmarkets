# Prompt Understanding Expansion Report

Date: 2026-06-28

## Summary

TraceEdge now has a data-driven prompt semantics layer for trader-language interpretation. The deterministic parser still runs first; the new layer adds vocabulary-backed semantic matches only when the phrase has enough market context and maps to a known executable condition.

This keeps TraceEdge as a research-monitoring system: prompts become measurable monitor conditions, not trade advice, entry requirements, or invented setup results.

## Current Prompt Flow

Before this change, prompt understanding was split across:

- `src/ai_market_monitor/services/interpreter.py` for rule-based prompt parsing and strategy preview creation.
- `src/ai_market_monitor/engine/prompt_aliases.py` for capability alias matching from the registry.
- `src/ai_market_monitor/engine/capabilities.py` and `src/ai_market_monitor/engine/condition_registry.py` for executable concept definitions.
- `src/ai_market_monitor/engine/prompt_audit.py` for source-fragment coverage and unclassified prompt fragments.

Provider-required concepts were already blocked through registry metadata and unsupported-condition issues. Vague prompts were handled by clarification-required conditions and unsupported interpretation issues.

## What Changed

Added:

- `src/ai_market_monitor/engine/prompt_vocabulary.json`
- `src/ai_market_monitor/engine/prompt_semantics.py`
- `scripts/generate_prompt_understanding_corpus.py`
- `tests/fixtures/prompt_understanding_corpus.jsonl`
- `tests/interpreter/test_prompt_semantics_vocabulary.py`
- `tests/interpreter/test_prompt_understanding_corpus.py`

Updated:

- `src/ai_market_monitor/services/interpreter.py`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/telegram/service.py`
- interpreter and integration tests that asserted old entry-first wording

## Vocabulary Coverage

Phrase groups: 19

Groups covered:

- candle bullishness / green candle
- candle bearishness / red candle
- candle body percent up
- candle body percent down
- general price percent up
- general price percent down
- strong volume
- volume not dead
- weak volume
- VWAP reclaim / above
- EMA above / reclaim / below
- RSI recovering
- New York session
- provider-required news
- provider-required open interest
- vague strength phrases

## Generated Corpus

Generated prompt cases: 1,200

Family counts:

- candle_direction: 162
- price_percent_move: 160
- volume: 110
- ma_vwap: 110
- rsi_momentum: 110
- negation: 110
- required_optional: 110
- timeframe_window: 110
- mixed_multi_condition: 110
- vague_ambiguous: 108

The corpus is generated from vocabulary and templates, not by manually hardcoding 1,000 one-off cases.

## Examples Now Understood

- `green candle`
- `bullish candle`
- `positive candle`
- `up candle`
- `red candle`
- `bearish candle`
- `negative candle`
- `down candle`
- `candle grew at least 0.01%`
- `candle dropped 0.01%`
- `coin up 5% today`
- `coin dropped 5% today`
- `volume not dead`
- `strong volume`
- `avoid doji`
- `no bearish engulfing`
- `optional volume spike`
- `must have volume spike`
- `reclaimed VWAP`
- `holding EMA 200`
- `RSI recovering`
- `must be during New York session`

## Examples Intentionally Blocked

- `positive news` does not become a bullish candle.
- `green project` does not become a green candle.
- `bullish` alone remains clarification-required unless tied to a deterministic concept.
- `looks strong` blocks as vague.
- `ready to pump` blocks as vague.
- `open interest rising` stays provider-required when the provider is unavailable.

## Ambiguity Rules

- Broad direction words such as `green`, `positive`, `bullish`, `up`, `red`, `negative`, and `down` only map when attached to candle, price, move, trend, or another measurable trading context.
- News, project, roadmap, team, community, or other non-candle context blocks candle interpretation.
- Candle-body percent language and general price-percent move language are separated, so `candle grew 1%` and `coin grew 5% today` do not collapse into the same condition.
- Negation phrases create negated conditions when the underlying condition is deterministic.
- Optional phrases preserve `required=False`; required phrases preserve `required=True`.
- Provider-required phrases become blocked issues, not executable conditions.

## Wording Cleanup

Old entry-first wording was replaced in active code and tests:

- `no_supported_entry_condition` became `no_supported_monitor_condition`.
- `No supported deterministic entry condition was recognized.` became `No supported deterministic monitor condition was recognized.`
- Strategy board labels now use `Condition Logic` instead of `Entry Logic`.
- Telegram and dashboard copy now prefers monitored conditions and research-monitor wording.

## Commands Run

- `.venv\Scripts\python.exe scripts\generate_prompt_understanding_corpus.py`
- `.venv\Scripts\python.exe -m pytest tests\interpreter\test_prompt_semantics_vocabulary.py -q`
- `.venv\Scripts\python.exe -m pytest tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_finder_conditions.py -q`
- `.venv\Scripts\python.exe -m pytest tests\interpreter\test_prompt_understanding_corpus.py -q`
- `.venv\Scripts\python.exe -m pytest tests\interpreter tests\unit\test_interpreter.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_finder_conditions.py -q`
- `.venv\Scripts\python.exe -m pytest tests\services\test_prompt_to_strategy_end_to_end.py tests\unit\test_interpreter_prompt_mechanics.py tests\unit\test_interpreter_part2_expansion.py tests\unit\test_openai_interpreter.py -q`
- `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml -q`
- `.venv\Scripts\python.exe -m pytest`

## Test Result

- Focused semantic vocabulary tests: passed.
- Generated 1,200-case corpus test: passed.
- Focused interpreter/service tests: passed.
- Browser tests: 9 passed.
- Full backend suite: 1,638 passed.

## Remaining Prompt Gaps

- The vocabulary is intentionally conservative. Some human slang should still ask for clarification until it can be mapped to measurable data.
- Provider-backed concepts remain blocked unless a tested provider adapter is enabled.
- AI suggestions are not automatically promoted into permanent vocabulary entries; they must go through review.
