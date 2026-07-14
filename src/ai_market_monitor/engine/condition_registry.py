from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from ai_market_monitor.engine.builder_templates import builder_template_payload
from ai_market_monitor.engine.capabilities import (
    CAPABILITIES,
    PRIMARY_BUILDER_CATEGORIES,
    STRATEGY_TEMPLATE_CAPABILITIES,
    SYNONYMS,
    CapabilitySpec,
    capability_registry_payload,
)
from ai_market_monitor.engine.capability_compatibility import compatibility_report
from ai_market_monitor.engine.logic_operators import logic_operator_payload
from ai_market_monitor.engine.prompt_aliases import normalized_phrases

GUIDEBOOK_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "key": "popular",
        "display_name": "Popular",
        "description": "Beginner-friendly conditions traders most often use to start a monitor.",
        "examples": (
            "RSI pullback",
            "EMA trend filter",
            "Volume spike",
            "Breakout",
            "Liquidity sweep",
            "Price up/down percent move",
        ),
    },
    {
        "key": "price",
        "display_name": "Price",
        "description": (
            "Absolute price, percent move, highs/lows, and distance-from-reference rules."
        ),
        "examples": (
            "price above level",
            "percent move over lookback",
            "near 24h high",
            "all-time high",
        ),
    },
    {
        "key": "indicator",
        "display_name": "Indicator",
        "description": "Deterministic technical indicators calculated from OHLCV candles.",
        "examples": ("RSI", "MACD", "EMA/SMA", "VWAP", "ATR", "ADX", "Ichimoku"),
    },
    {
        "key": "price_action",
        "display_name": "Price Action",
        "description": "Breakouts, retests, reclaims, range behavior, and failed moves.",
        "examples": ("breakout", "break and retest", "failed breakout", "support bounce"),
    },
    {
        "key": "market_structure",
        "display_name": "Market Structure",
        "description": (
            "Swing and structure rules such as higher highs, lower lows, BOS, and CHoCH."
        ),
        "examples": ("higher high", "higher low", "break of structure", "swing low"),
    },
    {
        "key": "liquidity_smart_money",
        "display_name": "Liquidity / Smart Money",
        "description": (
            "Liquidity sweeps, equal highs/lows, FVGs, order-block candidates, and displacement."
        ),
        "examples": ("sell-side sweep", "sweep and reclaim", "fair value gap", "order block"),
    },
    {
        "key": "candle_pattern",
        "display_name": "Candle Pattern",
        "description": "Single and multi-candle state filters and recognizable candle formations.",
        "examples": ("bullish engulfing", "hammer", "doji", "three black crows"),
    },
    {
        "key": "volume_flow",
        "display_name": "Volume / Flow",
        "description": "Relative volume, dollar volume, volume trend, and flow proxy conditions.",
        "examples": ("volume above average", "relative volume", "OBV trend", "CMF above zero"),
    },
    {
        "key": "volatility_squeeze",
        "display_name": "Volatility / Squeeze",
        "description": "ATR, Bollinger width, compression, expansion, and choppiness conditions.",
        "examples": ("ATR percent", "Bollinger squeeze", "range expansion", "choppiness index"),
    },
    {
        "key": "trend",
        "display_name": "Trend",
        "description": "Moving average, SuperTrend, ADX, cloud, and trend-strength filters.",
        "examples": ("price above EMA", "MA crossover", "EMA stack", "SuperTrend direction"),
    },
    {
        "key": "momentum",
        "display_name": "Momentum",
        "description": "Momentum threshold, cross, acceleration, and oscillator conditions.",
        "examples": ("RSI cross", "MACD histogram", "StochRSI cross", "ROC positive"),
    },
    {
        "key": "time_session",
        "display_name": "Time / Session",
        "description": "Weekday, session, UTC window, open/close, and time-expiry filters.",
        "examples": ("New York session", "weekday only", "custom UTC window", "daily open"),
    },
    {
        "key": "market_context",
        "display_name": "Market Context",
        "description": "BTC, ETH, dominance, breadth, and relative market-context requirements.",
        "examples": ("BTC trend filter", "ETH/BTC strength", "market breadth", "risk-on context"),
    },
    {
        "key": "risk_trade_quality",
        "display_name": "Risk / Trade Quality",
        "description": (
            "Optional user-defined stop distance, reward-to-risk, spread, setup age, "
            "and trade-quality gates."
        ),
        "examples": ("max stop distance", "minimum RR", "spread too wide", "setup too old"),
    },
    {
        "key": "alert_behavior",
        "display_name": "Alert Behavior",
        "description": (
            "Cooldowns, alert budgets, forming/lifecycle alerts, priorities, and destinations."
        ),
        "examples": ("confirmed only", "forming alert", "cooldown", "max alerts per hour"),
    },
    {
        "key": "advanced_logic",
        "display_name": "Advanced Logic",
        "description": (
            "Boolean, sequence, state-change, count, persistence, and conditional logic blocks."
        ),
        "examples": ("ALL OF", "ANY OF", "NOT", "SEQUENCE", "WITHIN LAST", "COUNT OF"),
    },
)


@dataclass(frozen=True, slots=True)
class RegistrySearchResult:
    capability: CapabilitySpec
    score: int


class ConditionCapabilityRegistry:
    def __init__(self, capabilities: tuple[CapabilitySpec, ...] = CAPABILITIES) -> None:
        self._capabilities = capabilities
        keys = [capability.key for capability in capabilities]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"Duplicate condition capability keys: {duplicates}")
        self._by_key = {capability.key: capability for capability in capabilities}

    def get(self, key: str) -> CapabilitySpec:
        try:
            return self._by_key[key]
        except KeyError as exc:
            raise KeyError(f"Unknown condition capability: {key}") from exc

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        executable_only: bool = True,
    ) -> list[CapabilitySpec]:
        normalized = query.casefold().strip()
        results: list[RegistrySearchResult] = []
        compatibility = {row.key: row for row in compatibility_report()}
        for capability in self._capabilities:
            if (
                category
                and capability.category != category
                and capability.to_dict()["builder_category"] != category
            ):
                continue
            availability = compatibility.get(capability.key)
            if executable_only and (
                not capability.executable
                or availability is None
                or availability.availability != "available"
            ):
                continue
            phrases = normalized_phrases(capability)
            if not normalized:
                score = 0
            elif capability.key == normalized.replace(" ", "_"):
                score = 100
            elif capability.label.casefold().startswith(normalized):
                score = 80
            elif any(phrase.startswith(normalized) for phrase in phrases):
                score = 60
            elif any(normalized in phrase for phrase in phrases):
                score = 40
            elif normalized in capability.description.casefold():
                score = 20
            else:
                continue
            results.append(RegistrySearchResult(capability, score))
        return [
            result.capability
            for result in sorted(
                results,
                key=lambda result: (
                    -result.score,
                    result.capability.category,
                    result.capability.label,
                ),
            )
        ]

    def payload(self, *, include_provider_required: bool = False) -> dict[str, Any]:
        payload = capability_registry_payload()
        compatibility = {row.key: row for row in compatibility_report()}
        payload["schema_version"] = "2.0"
        payload["logic_operators"] = logic_operator_payload()
        payload["items"] = []
        hidden_provider_required: list[dict[str, Any]] = []
        hidden_unavailable: list[dict[str, Any]] = []
        candle_parameters = [
            {
                "name": "min_body_percent",
                "type": "number",
                "default": 25,
                "required": False,
                "description": "Minimum real-body percentage of the candle range.",
                "options": (),
            },
            {
                "name": "max_body_percent",
                "type": "number",
                "default": 40,
                "required": False,
                "description": "Maximum real-body percentage of the candle range.",
                "options": (),
            },
            {
                "name": "wick_ratio",
                "type": "number",
                "default": 2,
                "required": False,
                "description": "Required wick-to-body ratio.",
                "options": (),
            },
            {
                "name": "trend_context_required",
                "type": "boolean",
                "default": False,
                "required": False,
                "description": "Require deterministic preceding trend context.",
                "options": (),
            },
            {
                "name": "confirmation_required",
                "type": "boolean",
                "default": False,
                "required": False,
                "description": "Require a confirming candle after the pattern.",
                "options": (),
            },
        ]
        for capability in self._capabilities:
            item = capability.to_dict()
            compatibility_row = compatibility.get(capability.key)
            if compatibility_row is not None:
                item["availability"] = compatibility_row.availability
                if compatibility_row.availability in {"unsupported", "planned"}:
                    item["executable"] = False
                item["implementation_status"] = (
                    "implemented"
                    if compatibility_row.availability == "available"
                    else compatibility_row.availability
                )
                item["compatibility_notes"] = list(compatibility_row.notes)
            if capability.condition_type == "candle_pattern":
                known = {parameter["name"] for parameter in item["parameters"]}
                item["parameters"].extend(
                    parameter for parameter in candle_parameters if parameter["name"] not in known
                )
                item["default_parameters"] = {
                    "min_body_percent": 25,
                    "max_body_percent": 40,
                    "wick_ratio": 2,
                    "trend_context_required": False,
                    "confirmation_required": False,
                    "pattern_strength": "medium",
                    "direction": "neutral",
                    **item["default_parameters"],
                }
            item["condition_template"] = builder_template_payload(capability)
            item["condition_template"]["availability"] = item["availability"]
            item["condition_template"]["provider_required"] = bool(
                item.get("provider_required") or item["availability"] == "provider_required"
            )
            hidden_entry = {
                "key": item["key"],
                "display_name": item["display_name"],
                "category": item["category"],
                "builder_category": item["builder_category"],
                "availability": item["availability"],
                "provider_required": item.get("provider_required"),
                "required_data": item.get("required_data", []),
            }
            if item["availability"] == "provider_required":
                hidden_provider_required.append(hidden_entry)
                if not include_provider_required:
                    continue
            if item["availability"] != "available" and item["availability"] != "provider_required":
                hidden_unavailable.append(hidden_entry)
                if not include_provider_required:
                    continue
            payload["items"].append(item)
        payload["by_category"] = {}
        for item in payload["items"]:
            payload["by_category"].setdefault(item["builder_category"], []).append(item)
        category_names = {
            "price": "Price",
            "indicator": "Indicator",
            "candle_pattern": "Candle Pattern",
            "price_action": "Price Action",
            "market_structure": "Market Structure",
            "liquidity_smart_money": "Liquidity / Smart Money",
            "volume_flow": "Volume / Flow",
            "volatility_squeeze": "Volatility / Squeeze",
            "trend": "Trend",
            "momentum": "Momentum",
            "time_session": "Time / Session",
            "market_context": "Market Context",
            "relative_strength": "Relative Strength",
            "risk_trade_quality": "Risk / Trade Quality",
            "news_events": "News / Events",
            "order_book_liquidity": "Order Book / Liquidity",
            "ranking_universe": "Ranking / Universe",
            "alert_behavior": "Alert Behavior",
            "setup_lifecycle": "Setup Lifecycle",
            "advanced_logic": "Advanced Logic",
        }
        payload["categories"] = [
            {
                "key": key,
                "display_name": category_names[key],
                "count": len(payload["by_category"].get(key, [])),
            }
            for key in PRIMARY_BUILDER_CATEGORIES
        ]
        for category in payload["categories"]:
            if category["key"] == "advanced_logic":
                category["count"] = len(payload["logic_operators"])
        guidebook_categories: list[dict[str, Any]] = []
        beginner_keys = {
            "rsi_oversold",
            "rsi_overbought",
            "price_above_ema",
            "price_below_ema",
            "relative_volume",
            "volume_spike",
            "breakout",
            "breakdown",
            "bullish_engulfing",
            "bearish_engulfing",
            "liquidity_sweep",
            "vwap_reclaim",
            "bollinger_squeeze",
            "percent_change_up",
            "percent_change_down",
        }
        for category in GUIDEBOOK_CATEGORIES:
            key = category["key"]
            if key == "popular":
                count = sum(
                    1
                    for item in payload["items"]
                    if item.get("beginner_friendly") or item.get("key") in beginner_keys
                )
            elif key == "advanced_logic":
                count = len(payload["logic_operators"])
            else:
                count = len(payload["by_category"].get(key, []))
            guidebook_categories.append({**category, "count": count})
        payload["guidebook_categories"] = guidebook_categories
        payload["primary_categories"] = list(PRIMARY_BUILDER_CATEGORIES)
        payload["hidden_provider_required"] = {
            "count": len(hidden_provider_required),
            "hidden_from_normal_ui": not include_provider_required,
            "reason": (
                "Provider-required concepts are hidden from the normal beta builder "
                "until a real data adapter, proof support, and tests are configured."
            ),
            "items": hidden_provider_required if include_provider_required else [],
        }
        payload["hidden_unavailable"] = {
            "count": len(hidden_unavailable),
            "hidden_from_normal_ui": not include_provider_required,
            "items": hidden_unavailable if include_provider_required else [],
        }
        payload["deduplication"] = {
            "already_present": [
                "sma",
                "ema",
                "atr",
                "volume_ratio",
                "rsi",
                "macd",
                "bollinger_band",
                "bollinger_bandwidth_percent",
                "stochastic",
                "vwap",
                "adx",
                "not",
                "sequence",
            ],
            "registry_keys_unique": True,
        }
        payload["counts"].update(
            {
                "logic_operators": len(payload["logic_operators"]),
                "builder_condition_templates": len(payload["items"]),
                "templates": len(STRATEGY_TEMPLATE_CAPABILITIES),
                "synonyms": len(SYNONYMS),
                **{
                    f"compatibility_{key}": value
                    for key, value in Counter(
                        item.get("availability", "unknown") for item in payload["items"]
                    ).items()
                },
                "manual_builder_addable": sum(
                    1 for item in payload["items"] if item.get("availability") == "available"
                ),
            }
        )
        return payload


CONDITION_REGISTRY = ConditionCapabilityRegistry()


def condition_registry_payload(*, include_provider_required: bool = False) -> dict[str, Any]:
    return CONDITION_REGISTRY.payload(
        include_provider_required=include_provider_required,
    )
