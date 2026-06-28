# ruff: noqa: E501

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ai_market_monitor.engine.candle_patterns import pattern_names
from ai_market_monitor.engine.condition_registry import condition_registry_payload
from ai_market_monitor.engine.context_conditions import TIME_CONDITION_NAMES
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES

ROOT = Path(__file__).resolve().parents[1]

NEW_INDICATOR_KEYS = {
    "historical_volatility",
    "normalized_atr",
    "choppiness_index",
    "ulcer_index",
    "on_balance_volume",
    "chaikin_money_flow",
    "accumulation_distribution",
    "ease_of_movement",
    "force_index",
    "volume_oscillator",
    "volume_profile_proxy",
    "relative_volume_by_session",
    "dollar_volume",
    "buy_sell_pressure_proxy",
    "pivot_points",
    "candle_anatomy",
    "distance_to_reference",
}
EXISTING_PATTERN_KEYS = {
    "bullish_engulfing",
    "bearish_engulfing",
    "hammer",
    "shooting_star",
    "doji",
    "inside_bar",
    "outside_bar",
    "pin_bar",
    "strong_close_near_high",
    "strong_close_near_low",
    "green_candle",
    "red_candle",
}

ALREADY_PRESENT = (
    "SMA, EMA, ATR, ATR percent, volume ratio, RSI, MACD, Bollinger Bands, "
    "Bollinger width/delta, Stochastic, VWAP, ADX, NOT, and SEQUENCE"
)

OVERLAPS_SKIPPED = (
    "higher/lower highs and lows, break of structure, change of character, liquidity sweeps, "
    "equal highs/lows, range breakout/breakdown, breakout retest, support/resistance retest, "
    "inside/outside bars, engulfing candles, hammer, shooting star, doji, pin bar, "
    "range expansion, volume spike/dry-up, time windows, spread/listing filters, and "
    "existing risk calculations"
)


def _escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _write_registry(payload: dict[str, Any]) -> None:
    lines = [
        "# Condition Registry",
        "",
        "Generated from `ai_market_monitor.engine.condition_registry`.",
        "",
        f"- Schema version: `{payload['schema_version']}`",
        f"- Total capabilities: `{payload['counts']['total']}`",
        f"- Executable now: `{payload['counts']['executable']}`",
        f"- Deferred or provider-bound: `{payload['counts']['recognized_not_executable']}`",
        f"- Logic operators: `{payload['counts']['logic_operators']}`",
        "",
        "| Category | Key | Status | Example sentence | Required data | Comparators | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in sorted(
        payload["items"],
        key=lambda row: (row["builder_category"], row["display_name"]),
    ):
        notes = []
        if item.get("provider_required"):
            notes.append(f"Provider: {item['provider_required']}")
        if item.get("approximation"):
            notes.append(item.get("approximation_note") or "Approximate")
        if item.get("risk_notes"):
            notes.append(item["risk_notes"])
        if item.get("guidance"):
            notes.append(item["guidance"])
        lines.append(
            "| {category} | `{key}` | `{status}` | {example} | {data} | {comparators} | {notes} |".format(
                category=_escape(item["builder_category"].replace("_", " ").title()),
                key=_escape(item["key"]),
                status=_escape(item["implementation_status"]),
                example=_escape(item["example_sentence"]),
                data=_escape(", ".join(item["required_data"])),
                comparators=_escape(", ".join(item["supported_comparators"])),
                notes=_escape(" ".join(notes)),
            )
        )
    (ROOT / "CONDITION_REGISTRY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_audit(payload: dict[str, Any]) -> None:
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in payload["items"]:
        by_status[item["implementation_status"]].append(item)
    new_executable_keys = (
        NEW_INDICATOR_KEYS
        | set(PRICE_ACTION_NAMES)
        | set(TIME_CONDITION_NAMES)
        | (set(pattern_names()) - EXISTING_PATTERN_KEYS)
    )
    new_executable = sorted(
        item["key"]
        for item in payload["items"]
        if item["key"] in new_executable_keys and item["implementation_status"] == "implemented"
    )
    lines = [
        "# Condition Capability Audit",
        "",
        "## Summary",
        "",
        f"- Registry capabilities: **{payload['counts']['total']}**",
        f"- Deterministically executable: **{payload['counts']['executable']}**",
        f"- Deferred/provider/runtime dependent: **{payload['counts']['recognized_not_executable']}**",
        "- No trade execution, exchange trading keys, or AI-only signal outcomes were added.",
        "",
        "## Already Existing Capabilities Skipped",
        "",
        f"- {ALREADY_PRESENT}.",
        f"- {OVERLAPS_SKIPPED}.",
        "- Existing keys were retained rather than duplicated; registry import validates key uniqueness.",
        "",
        "## Newly Added Capabilities",
        "",
        "The following keys are executable from OHLCV or timezone-safe runtime context:",
        "",
        ", ".join(f"`{key}`" for key in new_executable),
        "",
        "## Deterministic Approximation Notes",
        "",
        "- `volume_profile_proxy` uses typical-price bins weighted by candle volume. It is not a true exchange volume profile.",
        "- `buy_sell_pressure_proxy` uses close location within candle range. It is not real aggressor-side order flow.",
        "- FVG, order-block, smart-money, swing-strength, trendline, and structure conditions use documented OHLCV definitions. They are deterministic labels, not claims about institutional intent.",
        "- Custom sessions and calendar rules are timezone-aware. UTC remains the default unless the strategy condition supplies a user timezone.",
        "",
        "## Deferred Due to Provider or Runtime Limitations",
        "",
    ]
    for status in sorted(key for key in by_status if key != "implemented"):
        lines.extend(
            [
                f"### {status.replace('_', ' ').title()}",
                "",
            ]
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in by_status[status]:
            grouped[item.get("provider_required") or "internal runtime"].append(item["key"])
        for provider, keys in sorted(grouped.items()):
            lines.append(f"- **{provider}:** " + ", ".join(f"`{key}`" for key in sorted(keys)))
        lines.append("")
    lines.extend(
        [
            "## Partial Implementations",
            "",
            "- Cross-symbol, crypto-index, macro-index, breadth, sector, news/event, order-book, trade-tape, derivatives, and universe-ranking interfaces exist, but no production provider is configured.",
            "- Universe ranking is registered for a future two-pass scanner. It does not yet affect live scanner sorting.",
            "- Post-evaluation risk-quality keys are registered, but the current engine calculates risk after the entry tree, so those keys cannot block the same tree evaluation yet.",
            "- `time_since_condition_true` evaluates only when a persisted `condition_first_true_at` value is supplied. That timestamp is not yet stored for every condition.",
            "- Provider-required cards are visible only through category selection or search and cannot be added until available.",
            "- Generated capability families have registry metadata and shared evaluator-family tests. Positive, negative, and insufficient-data fixtures are representative rather than one handcrafted fixture for every generated alias/key.",
            "- Every generated condition template is schema-validated in bulk, but provider-bound conditions cannot receive positive live-data tests until their providers exist.",
            "",
            "## Unsupported or Unsafe Capabilities Rejected",
            "",
            "- Automated trade placement or order execution.",
            "- Wallet seed phrases, private keys, withdrawal permissions, or remote access.",
            "- News-derived buy/sell recommendations or unverified sentiment predictions.",
            "- Guaranteed-profit, future-price prediction, or AI-invented market values.",
            "- Futures-only conditions on spot-only plans without a derivatives provider and entitlement.",
            "",
            "## Files Changed",
            "",
            "- `src/ai_market_monitor/engine/indicators.py`",
            "- `src/ai_market_monitor/engine/candle_patterns.py`",
            "- `src/ai_market_monitor/engine/price_action.py`",
            "- `src/ai_market_monitor/engine/context_conditions.py`",
            "- `src/ai_market_monitor/engine/capabilities.py`",
            "- `src/ai_market_monitor/engine/condition_registry.py`",
            "- `src/ai_market_monitor/engine/builder_templates.py`",
            "- `src/ai_market_monitor/engine/evaluator.py`",
            "- `src/ai_market_monitor/services/interpreter.py`",
            "- `src/ai_market_monitor/services/interfaces.py`",
            "- `src/ai_market_monitor/services/scanner.py`",
            "- `src/ai_market_monitor/templates/dashboard.html`",
            "- `src/ai_market_monitor/static/dashboard.js`",
            "- `src/ai_market_monitor/static/dashboard.css`",
            "",
            "## Tests Added",
            "",
            "- Indicator warm-up and extended calculation coverage.",
            "- Positive, negative, and insufficient-data candle-pattern cases.",
            "- Breakout and fair-value-gap price-action cases.",
            "- Timezone-safe weekend/weekday evaluation.",
            "- Prompt alias conversion into condition keys.",
            "- Provider-unavailable proof behavior.",
            "- Registry deduplication, categories, provider badges, and builder markup.",
            "",
        ]
    )
    (ROOT / "CONDITION_CAPABILITY_AUDIT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _write_builder_notes(payload: dict[str, Any]) -> None:
    categories = ", ".join(
        f"`{category['display_name']}`" for category in payload["categories"]
    )
    lines = [
        "# Builder UX Notes",
        "",
        "## Condition Categories",
        "",
        f"The Add Condition library exposes these primary categories: {categories}.",
        "",
        "- The default view shows a limited beginner-friendly Phase 1 selection.",
        "- Search or category selection reveals the wider catalog.",
        "- Provider-required and runtime-dependent conditions show availability badges and disabled add buttons.",
        "- Every card includes a preview sentence, required-data summary, warm-up count, provider badge, and an Explain this condition action.",
        "- Advanced raw condition remains available, but it is no longer the primary creation path.",
        "",
        "## Prompt Aliases",
        "",
        "- Prompt matching searches canonical keys, display names, and aliases.",
        "- Executable phrases become validated `ConditionRule` objects in the visual tree.",
        "- Provider-bound phrases become explicit unsupported/provider-required issues.",
        "- Existing deterministic parsers retain priority, preventing duplicate rules.",
        "- Ambiguous or unsupported requests still require user clarification and approval.",
        "",
        "Examples:",
        "",
        "- `OBV rising` -> `on_balance_volume`",
        "- `CMF above zero` -> `chaikin_money_flow`",
        "- `takes previous high` -> `previous_high_swept`",
        "- `reclaims level` -> `sweep_and_reclaim`",
        "- `avoid weekends` -> `weekday_only`",
        "- `London open` -> `session_open_window`",
        "- `alts stronger than BTC` -> provider-required relative-strength context",
        "",
        "## Complex Logic Without Raw JSON",
        "",
        "- Groups expose named operator controls for lookback candles, persistence count, sequence gap, minimum pass count, cooldown, and confirmation bars.",
        "- Condition cards expose named capability parameters such as periods, components, wick ratios, trend context, and confirmation requirements.",
        "- AND, OR, NOT, SEQUENCE, WITHIN_LAST, PERSISTED_FOR, COUNT_OF, COOLDOWN_CONDITION, FIRST_TIME_TRUE, CHANGED_STATE, CROSS_WITH_CONFIRMATION, and CONDITIONAL_BRANCH remain editable as nested visual groups.",
        "- Advanced JSON fields remain inside editor drawers as an escape hatch for expert users.",
        "- Prompt-created strategies still require visual review and explicit approval before activation.",
        "",
    ]
    (ROOT / "BUILDER_UX_NOTES.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    payload = condition_registry_payload()
    _write_registry(payload)
    _write_audit(payload)
    _write_builder_notes(payload)


if __name__ == "__main__":
    main()
