from __future__ import annotations

import json
from itertools import islice, product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY_PATH = ROOT / "src" / "ai_market_monitor" / "engine" / "prompt_vocabulary.json"
OUTPUT_PATH = ROOT / "tests" / "fixtures" / "prompt_understanding_corpus.jsonl"

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
TIMEFRAME_WORDS = ["one minute", "five minute", "15 minute", "hourly", "four hour", "daily"]
THRESHOLDS = ["0.01", "0.1", "1", "2.5", "5", "7.5", "10"]
PRICE_WINDOWS = ["today", "in the last 24h", "this week", "over the past week", "in the last 30 days"]
RSI_LEVELS = [20, 25, 30, 35, 50, 55, 60, 70]
VOLUME_LEVELS = ["1.0", "1.2", "1.5", "1.8", "2.0"]
REQUIRED_PHRASES = ["must have", "only if", "required", "make sure", "has to", "mandatory"]
OPTIONAL_PHRASES = ["optional", "nice to have", "bonus", "if possible", "prefer", "confirmation only"]
NEGATION_PHRASES = ["avoid", "no", "without", "do not show", "filter out"]


def main() -> None:
    vocabulary = json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))
    groups = {group["key"]: group for group in vocabulary["phrase_groups"]}
    cases: list[dict] = []

    def add(prompt: str, *, family: str, names: list[str] | None = None, keys: list[str] | None = None,
            timeframe: str | None = None, should_block: bool = False, required: list[bool] | None = None,
            reason: str = "") -> None:
        cases.append(
            {
                "prompt": prompt,
                "expected_capability_keys": keys or [],
                "expected_condition_names": names or [],
                "expected_required_flags": required or [],
                "expected_timeframe": timeframe,
                "expected_direction": None,
                "should_block": should_block,
                "reason": reason or family,
                "source_family": family,
            }
        )

    # 150+ candle direction prompts.
    for group_key, name in (("candle_bullish", "green_candle"), ("candle_bearish", "red_candle")):
        phrases = groups[group_key]["phrases"]
        for phrase, tf in product(phrases, TIMEFRAMES):
            add(f"find coins with {phrase} on {tf}", family="candle_direction", names=[name], keys=[name], timeframe=tf)
            add(f"previous {phrase} on {tf}", family="candle_direction", names=[name], keys=[name], timeframe=tf)

    # 150+ price percent move prompts.
    for phrase, threshold, window in product(groups["price_percent_up"]["phrases"], THRESHOLDS, PRICE_WINDOWS):
        add(f"find coins that {phrase} {threshold}% {window}", family="price_percent_move", names=["percent_change_up"], keys=["percent_change_lookback"])
    for phrase, threshold, window in product(groups["price_percent_down"]["phrases"], THRESHOLDS, PRICE_WINDOWS):
        add(f"show symbols that {phrase} {threshold}% {window}", family="price_percent_move", names=["percent_change_down"], keys=["percent_change_lookback"])

    # 100+ volume prompts.
    for phrase, tf in product(groups["volume_strong"]["phrases"], TIMEFRAMES):
        add(f"{phrase} on {tf}", family="volume", names=["volume_ratio"], keys=["volume_ratio"], timeframe=tf)
    for phrase, tf in product(groups["volume_not_dead"]["phrases"], TIMEFRAMES):
        add(f"find markets where {phrase} on {tf}", family="volume", names=["volume_ratio"], keys=["volume_ratio"], timeframe=tf)
    for phrase, tf in product(groups["volume_weak"]["phrases"], TIMEFRAMES):
        add(f"find markets with {phrase} on {tf}", family="volume", names=["volume_ratio"], keys=["volume_dry_up"], timeframe=tf)
    for level, tf in product(VOLUME_LEVELS, TIMEFRAMES):
        add(f"volume at least {level}x average on {tf}", family="volume", names=["volume_ratio"], keys=["volume_ratio"], timeframe=tf)

    # 100+ MA/VWAP prompts.
    for period, tf in product([20, 50, 100, 200], TIMEFRAMES):
        add(f"price above EMA {period} on {tf}", family="ma_vwap", names=["ema"], keys=["price_above_ema"], timeframe=tf)
        add(f"holding EMA {period} on {tf}", family="ma_vwap", names=["ema"], keys=["price_above_ema"], timeframe=tf)
        add(f"reclaimed EMA {period} on {tf}", family="ma_vwap", names=["ema"], keys=["ma_reclaim"], timeframe=tf)
        add(f"price below EMA {period} on {tf}", family="ma_vwap", names=["ema"], keys=["price_below_ema"], timeframe=tf)
    for phrase, tf in product(groups["vwap_reclaim"]["phrases"] + groups["vwap_above"]["phrases"], TIMEFRAMES):
        add(f"{phrase} on {tf}", family="ma_vwap", names=["vwap"], keys=["vwap_reclaim"], timeframe=tf)

    # 100+ RSI/momentum prompts.
    for level, tf in product(RSI_LEVELS, TIMEFRAMES):
        relation = "below" if level <= 35 else "above"
        add(f"RSI {relation} {level} on {tf}", family="rsi_momentum", names=["rsi"], keys=["rsi_threshold"], timeframe=tf)
    for phrase, tf in product(groups["rsi_recovering"]["phrases"], TIMEFRAMES):
        add(f"{phrase} on {tf}", family="rsi_momentum", names=["rsi"], keys=["rsi_exits_oversold"], timeframe=tf)
    for tf in TIMEFRAMES:
        add(f"RSI crosses above 30 on {tf}", family="rsi_momentum", names=["rsi"], keys=["rsi_cross"], timeframe=tf)
        add(f"RSI crosses below 70 on {tf}", family="rsi_momentum", names=["rsi"], keys=["rsi_cross"], timeframe=tf)
        add(f"MFI below 20 on {tf}", family="rsi_momentum", names=["money_flow_index"], keys=["money_flow_index"], timeframe=tf)
        add(f"MACD histogram turns positive on {tf}", family="rsi_momentum", names=["macd"], keys=["macd_histogram_flip"], timeframe=tf)
        add(f"MACD histogram turns negative on {tf}", family="rsi_momentum", names=["macd"], keys=["macd_histogram_flip"], timeframe=tf)
        add(f"stochastic crosses above 20 on {tf}", family="rsi_momentum", names=["stochastic"], keys=["stochastic_kd_cross"], timeframe=tf)
        add(f"ADX above 25 on {tf}", family="rsi_momentum", names=["adx"], keys=["adx_trend_strength"], timeframe=tf)

    # 100+ negation prompts.
    for negation, pattern, tf in product(
        NEGATION_PHRASES,
        ["doji", "bearish engulfing", "red candle", "hammer", "shooting star", "bearish candle"],
        ["15m", "1h", "1d"],
    ):
        add(f"{negation} {pattern} on {tf}", family="negation", names=[pattern.replace(" ", "_")], timeframe=tf)
    for negation, phrase in product(NEGATION_PHRASES, groups["candle_bearish"]["phrases"][:8]):
        add(f"{negation} {phrase}", family="negation", names=["red_candle"])

    # 100+ required/optional prompts.
    for phrase, tf in product(REQUIRED_PHRASES, TIMEFRAMES):
        add(f"{phrase} RSI below 30 on {tf}", family="required_optional", names=["rsi"], required=[True], timeframe=tf)
        add(f"{phrase} volume spike on {tf}", family="required_optional", names=["volume_ratio"], required=[True], timeframe=tf)
    for phrase, tf in product(OPTIONAL_PHRASES, TIMEFRAMES):
        add(f"RSI below 30 and {phrase} volume spike on {tf}", family="required_optional", names=["rsi", "volume_ratio"], required=[True, False], timeframe=tf)
        add(f"{phrase} strong volume on {tf}", family="required_optional", names=["volume_ratio"], required=[False], timeframe=tf)

    # 100+ timeframe/window prompts.
    for phrase, tf in zip(TIMEFRAME_WORDS, TIMEFRAMES, strict=False):
        for threshold in THRESHOLDS:
            add(f"candle grew at least {threshold}% on {phrase}", family="timeframe_window", names=["candle_change_percent"], timeframe=tf)
            add(f"coin up {threshold}% over the past week on {phrase}", family="timeframe_window", names=["percent_change_up"], timeframe=tf)
    for candles in [3, 5, 10, 20, 50]:
        for tf in TIMEFRAMES:
            add(f"price moved up 2% over the last {candles} candles on {tf}", family="timeframe_window", names=["percent_change_up"], timeframe=tf)

    # 100+ mixed multi-condition prompts.
    for tf, level, vol in product(TIMEFRAMES, [25, 30, 35, 50], ["1.2", "1.5", "1.8"]):
        add(f"RSI below {level} and volume at least {vol}x average on {tf}", family="mixed_multi_condition", names=["rsi", "volume_ratio"], timeframe=tf)
        add(f"green candle with strong volume and price above EMA 200 on {tf}", family="mixed_multi_condition", names=["green_candle", "volume_ratio", "ema"], timeframe=tf)
        add(f"avoid doji, reclaimed vwap, and coin up 5% today on {tf}", family="mixed_multi_condition", names=["doji", "vwap", "percent_change_up"], timeframe=tf)
        add(f"positive candle plus RSI below {level} and holding EMA 50 on {tf}", family="mixed_multi_condition", names=["green_candle", "rsi", "ema"], timeframe=tf)
        add(f"red candle with weak volume and price below EMA 20 on {tf}", family="mixed_multi_condition", names=["red_candle", "volume_ratio", "ema"], timeframe=tf)

    # 100+ vague and provider-required prompts that should block.
    vague = groups["vague_strength"]["phrases"]
    provider = groups["provider_news"]["phrases"] + groups["provider_open_interest"]["phrases"]
    for phrase in vague:
        for suffix in [
            "",
            " on Binance",
            " for USDT pairs",
            " today",
            " this week",
            " with good setup",
            " on 15m",
            " during NY session",
            " before the close",
        ]:
            add(f"find {phrase}{suffix}", family="vague_ambiguous", should_block=True, reason="vague phrase needs measurable definition")
    for phrase in provider:
        for prefix in ["only if", "must have", "find coins with", "show symbols with"]:
            add(f"{prefix} {phrase}", family="vague_ambiguous", should_block=True, reason="provider data required")

    # Keep a deterministic but balanced corpus. Requirements call for at least 1,000
    # and specific family minimums, so do not just take the first generated rows.
    unique: dict[str, dict] = {}
    for case in cases:
        unique.setdefault(case["prompt"].casefold(), case)
    unique_cases = list(unique.values())
    targets = {
        "candle_direction": 160,
        "price_percent_move": 160,
        "volume": 110,
        "ma_vwap": 110,
        "rsi_momentum": 110,
        "negation": 110,
        "required_optional": 110,
        "timeframe_window": 110,
        "mixed_multi_condition": 110,
        "vague_ambiguous": 110,
    }
    selected: list[dict] = []
    selected_prompts: set[str] = set()
    for family, target in targets.items():
        for case in [item for item in unique_cases if item["source_family"] == family][:target]:
            selected.append(case)
            selected_prompts.add(case["prompt"].casefold())
    for case in unique_cases:
        if len(selected) >= 1200:
            break
        key = case["prompt"].casefold()
        if key not in selected_prompts:
            selected.append(case)
            selected_prompts.add(key)
    selected = list(islice(selected, 1200))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "".join(json.dumps(case, sort_keys=True) + "\n" for case in selected),
        encoding="utf-8",
    )
    print(f"Wrote {len(selected)} prompt cases to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
