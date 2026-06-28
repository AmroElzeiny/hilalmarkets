import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from ai_market_monitor.db.models.enums import (
    ConditionType,
    LogicalOperator,
    MarketType,
    TriggerMode,
)
from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.candle_patterns import pattern_names
from ai_market_monitor.engine.capability_compatibility import (
    compatibility_by_key,
    prompt_blocked_capabilities,
    prompt_executable_capabilities,
)
from ai_market_monitor.engine.capabilities import capability_prompt_categories
from ai_market_monitor.engine.context_conditions import TIME_CONDITION_NAMES
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES
from ai_market_monitor.engine.prompt_aliases import (
    find_capability_matches,
    normalized_phrases,
)
from ai_market_monitor.engine.prompt_audit import audit_prompt_coverage
from ai_market_monitor.engine.prompt_semantics import analyze_prompt_semantics
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    Comparator,
    ConditionGroup,
    ConditionRule,
    EntryPolicy,
    InterpretationIssue,
    InterpretationPreview,
    NearMissPolicy,
    Operand,
    OperandKind,
    RiskPolicy,
    StrategyDefinition,
    StrategyDirection,
    UniverseDefinition,
)

TIMEFRAME_WORDS = {
    "one-minute": "1m",
    "1-minute": "1m",
    "one minute": "1m",
    "1 minute": "1m",
    "three-minute": "3m",
    "3-minute": "3m",
    "three minute": "3m",
    "five-minute": "5m",
    "5-minute": "5m",
    "five minute": "5m",
    "15-minute": "15m",
    "fifteen-minute": "15m",
    "15 minute": "15m",
    "30-minute": "30m",
    "30 minute": "30m",
    "one-hour": "1h",
    "one hour": "1h",
    "1-hour": "1h",
    "four-hour": "4h",
    "four hour": "4h",
    "4-hour": "4h",
    "hourly": "1h",
    "daily": "1d",
}

SUPPORTED_TIMEFRAMES = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
)

PROMPT_MECHANIC_CATEGORIES = capability_prompt_categories()

OPTIONAL_WORDS = ("nice to have", "prefer", "bonus", "extra confirmation", "optional")
MANDATORY_WORDS = ("must", "only if", "required", "require", "never", "do not", "no ", "avoid")
MAJORS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
EXTENDED_CAPABILITY_KEYS = {
    "stochastic_rsi",
    "money_flow_index",
    "commodity_channel_index",
    "williams_percent_r",
    "rate_of_change",
    "momentum_indicator",
    "true_strength_index",
    "ultimate_oscillator",
    "relative_vigor_index",
    "connors_rsi",
    "weighted_moving_average",
    "hull_moving_average",
    "double_exponential_moving_average",
    "triple_exponential_moving_average",
    "kaufman_adaptive_moving_average",
    "volume_weighted_moving_average",
    "linear_regression_moving_average",
    "zero_lag_ema",
    "ichimoku_cloud",
    "supertrend",
    "parabolic_sar",
    "aroon",
    "directional_movement_components",
    "elder_impulse",
    "keltner_channels",
    "donchian_channels",
    "bollinger_percent_b",
}


@dataclass(frozen=True, slots=True)
class _ParsedRisk:
    enabled: bool
    maximum_stop_percent: float | None
    minimum_reward_to_risk: float | None


class RuleBasedStrategyInterpreter:
    """Deterministic fallback interpreter.

    It converts recognized trader language into measurable rules and makes unsupported ideas
    explicit. It never produces indicator values or setup outcomes.
    """

    name = "rules-v2"

    async def interpret(self, guided_setup: GuidedSetupRequest) -> InterpretationPreview:
        original_text = (guided_setup.setup_text or "").strip()
        text = original_text.casefold()
        if guided_setup.setup_mode == "template":
            text = self._template_text(guided_setup.template_key or "").casefold()
            original_text = text

        base_timeframe = self._timeframe_from_text(text, guided_setup.timeframe)
        direction = self._direction(text)
        risk = self._risk(text, guided_setup)
        exchange = self._exchange(text, guided_setup.exchange)
        quote_currency = self._quote_currency(text, guided_setup.quote_currency)
        include_symbols = self._include_symbols(original_text, guided_setup.symbols, quote_currency)
        exclude_symbols = self._exclude_symbols(text, quote_currency)
        conditions: list[ConditionRule] = []
        assumptions: list[str] = []
        unsupported: list[InterpretationIssue] = []
        supporting_timeframes: set[str] = set()

        def add(condition: ConditionRule | None) -> None:
            if condition is None:
                return
            if condition.timeframe != base_timeframe:
                supporting_timeframes.add(condition.timeframe)
            self._append_unique(conditions, condition)

        self._parse_liquidity_sweeps(text, base_timeframe, add, assumptions)
        self._parse_moving_averages(text, base_timeframe, add, assumptions)
        self._parse_momentum(text, base_timeframe, add)
        self._parse_bollinger_and_volatility(text, base_timeframe, add)
        self._parse_volume_and_vwap(text, base_timeframe, add)
        self._parse_direct_market_search(text, base_timeframe, add, assumptions)
        self._parse_price_action(text, base_timeframe, add, assumptions)
        self._parse_candles(text, base_timeframe, add)
        self._parse_extended_capabilities(text, base_timeframe, add, conditions)
        semantic_result = analyze_prompt_semantics(original_text, base_timeframe)
        for condition in semantic_result.conditions:
            if any(self._conditions_equivalent(existing, condition) for existing in conditions):
                continue
            add(condition)
        for note in semantic_result.assumptions:
            if note not in assumptions:
                assumptions.append(note)
        for issue in semantic_result.issues:
            if not any(
                existing.code == issue.code and existing.source_fragment == issue.source_fragment
                for existing in unsupported
            ):
                unsupported.append(issue)
        for condition, note in self._time_window_conditions(text, base_timeframe):
            add(condition)
            assumptions.append(note)

        for issue in self._recognized_unsupported(text, conditions):
            if not any(
                existing.code == issue.code and existing.message == issue.message
                for existing in unsupported
            ):
                unsupported.append(issue)
        for issue in self._cross_symbol_context_issues(text):
            if not any(
                existing.source_fragment == issue.source_fragment for existing in unsupported
            ):
                unsupported.append(issue)
        for issue in self._vague_prompt_issues(text):
            if not any(
                existing.source_fragment == issue.source_fragment for existing in unsupported
            ):
                unsupported.append(issue)
        if conditions:
            for issue in self._unparsed_instruction_issues(original_text, text):
                if not any(existing.message == issue.message for existing in unsupported):
                    unsupported.append(issue)

        min_quote_volume = self._minimum_quote_volume(text)
        min_average_volume = self._minimum_average_volume(text)
        max_spread = self._maximum_spread(text)
        if "avoid low liquidity" in text and min_quote_volume is None:
            min_quote_volume = 1_000_000
            assumptions.append(
                "Avoiding low liquidity defaults to minimum 24h quote volume of 1,000,000."
            )

        if not conditions:
            unsupported.append(
                InterpretationIssue(
                    code="no_supported_monitor_condition",
                    field="setup_text",
                    message=(
                        "No supported deterministic monitor condition was recognized. Clarify the "
                        "indicator, price-action event, timeframe, comparator, and threshold."
                    ),
                    blocking=True,
                    source_fragment=original_text,
                )
            )
            add(
                ConditionRule(
                    key="clarification_required",
                    label="Clarification required",
                    condition_type=ConditionType.MARKET_FILTER,
                    timeframe=base_timeframe,
                    left=Operand(kind=OperandKind.CONSTANT, value=False),
                    comparator=Comparator.IS_TRUE,
                    notes="Blocked sentinel; this strategy cannot be approved until clarified.",
                )
            )

        detected_categories = self._detected_categories(text)
        if "one condition" in text and "missing" in text:
            assumptions.append("Near-Miss one-condition-remaining alerts are enabled.")
        if risk.maximum_stop_percent is not None:
            assumptions.append(
                f"Stop distance must be under {risk.maximum_stop_percent:g}%."
            )
        if risk.minimum_reward_to_risk is not None:
            assumptions.append(
                f"Reward-to-risk must be at least {risk.minimum_reward_to_risk:g}R."
            )

        root = ConditionGroup(
            key="entry_conditions",
            operator=LogicalOperator.AND,
            children=conditions,
        )
        definition = StrategyDefinition(
            name=self._strategy_name(text),
            description=guided_setup.setup_text,
            direction=direction,
            base_timeframe=base_timeframe,
            supporting_timeframes=sorted(supporting_timeframes - {base_timeframe}),
            trigger_mode=TriggerMode(guided_setup.trigger_mode),
            universe=UniverseDefinition(
                exchange=exchange,
                market_type=MarketType.SPOT,
                quote_currencies=[quote_currency],
                include_symbols=include_symbols,
                exclude_symbols=exclude_symbols,
                min_quote_volume_24h=(
                    min_quote_volume
                    if min_quote_volume is not None
                    else guided_setup.minimum_quote_volume_24h
                ),
                min_average_candle_volume=min_average_volume,
                min_order_book_depth=guided_setup.minimum_liquidity,
                max_spread_bps=(
                    max_spread if max_spread is not None else guided_setup.maximum_spread_bps
                ),
                min_listing_age_days=30 if "listing age" in text or "new listing" in text else None,
                exclude_stablecoins="include stable" not in text,
                exclude_leveraged_tokens=True,
            ),
            conditions=root,
            entry=EntryPolicy(
                calculation="signal_close",
                expires_after_candles=3,
                invalidate_if_price_moves_percent=risk.maximum_stop_percent,
            ),
            risk=RiskPolicy(
                enabled=risk.enabled,
                stop_method="structure",
                maximum_stop_percent=risk.maximum_stop_percent,
                target_method="risk_multiple",
                target_value=risk.minimum_reward_to_risk or 1,
                minimum_reward_to_risk=risk.minimum_reward_to_risk,
            ),
            near_miss=NearMissPolicy(
                enabled=True,
                one_condition_remaining_enabled=True,
            ),
            alerts=AlertPolicy(
                forming_alerts=guided_setup.forming_alerts,
                near_miss_threshold=guided_setup.near_miss_threshold,
                channels=guided_setup.delivery_channels,
                maximum_alerts_per_hour=guided_setup.maximum_alerts_per_hour,
                alert_on_one_condition_remaining=True,
            ),
        )
        coverage = audit_prompt_coverage(
            original_text,
            definition,
            assumptions=assumptions,
            ambiguities=[],
            unsupported=unsupported,
            ai_interpreted=False,
        )
        coverage_dirty = False
        for fragment in coverage.ignored_fragments:
            if not any(
                row.fragment == fragment and row.bucket == "unclassified"
                for row in coverage.mapping_table
            ):
                continue
            if any(issue.source_fragment == fragment for issue in unsupported):
                continue
            lowered_fragment = fragment.casefold()
            if "retest" in lowered_fragment and any(
                condition.left.name == "break_and_retest_confirmed"
                for condition in conditions
            ):
                for condition in conditions:
                    if condition.left.name == "break_and_retest_confirmed":
                        condition.source_fragment = original_text[:500]
                        coverage_dirty = True
                continue
            unsupported.append(
                InterpretationIssue(
                    code="prompt_fragment_unclassified",
                    field="setup_text",
                    message=(
                        "This meaningful instruction was not converted into a rule, "
                        f"assumption, ambiguity, or unsupported item: '{fragment}'."
                    ),
                    blocking=True,
                    source_fragment=fragment,
                )
            )
        if unsupported or coverage_dirty:
            coverage = audit_prompt_coverage(
                original_text,
                definition,
                assumptions=assumptions,
                ambiguities=[],
                unsupported=unsupported,
                ai_interpreted=False,
            )
        return InterpretationPreview(
            strategy=definition,
            assumptions=assumptions,
            unsupported_conditions=unsupported,
            interpreter=self.name,
            raw_metadata={
                "detected_categories": detected_categories,
                "capability_registry": "engine.condition_registry",
                "deterministic_evaluation_required": True,
                "prompt_coverage_report": coverage.model_dump(mode="json"),
                "prompt_semantics": semantic_result.metadata(),
            },
        )

    def _parse_extended_capabilities(
        self,
        text: str,
        timeframe: str,
        add,
        existing_conditions: list[ConditionRule],
    ) -> None:
        moving_averages = {
            "weighted_moving_average",
            "hull_moving_average",
            "double_exponential_moving_average",
            "triple_exponential_moving_average",
            "kaufman_adaptive_moving_average",
            "volume_weighted_moving_average",
            "linear_regression_moving_average",
            "zero_lag_ema",
        }
        generic_keys = (
            {capability.key for capability in prompt_executable_capabilities()}
            | EXTENDED_CAPABILITY_KEYS
            | set(PRICE_ACTION_NAMES)
            | set(TIME_CONDITION_NAMES)
            | set(pattern_names())
            | {
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
                    "impulse_candle",
                    "volume_spike",
            }
        )
        for match in find_capability_matches(text):
            capability = match.capability
            if capability.key not in generic_keys:
                continue
            if any(
                condition.left.name == capability.operand_name
                or (condition.right and condition.right.name == capability.operand_name)
                for condition in existing_conditions
            ):
                continue
            condition_timeframe = self._timeframe_near(text, match.start) or timeframe
            payload = condition_template(
                capability,
                timeframe=condition_timeframe,
                key=self._key(capability.key),
            )
            snippet = text[max(0, match.start - 36) : min(len(text), match.end + 64)]
            payload["source_fragment"] = self._matched_clause(
                text,
                match.start,
                match.end,
            )
            payload["confidence"] = max(float(payload.get("confidence") or 0.0), 0.88)
            relation = re.search(
                r"\b(cross(?:es)?\s+above|cross(?:es)?\s+below|"
                r"above|over|greater than|below|under|less than)\s+"
                r"(-?\d+(?:\.\d+)?)\s*([kmb])?\b",
                snippet,
            )
            right = payload.get("right") or {}
            if relation and right.get("kind") == "constant":
                word = relation.group(1)
                payload["comparator"] = (
                    "crosses_above"
                    if "cross" in word and "above" in word
                    else (
                        "crosses_below"
                        if "cross" in word and "below" in word
                        else "gte"
                        if word in {"above", "over", "greater than"}
                        else "lte"
                    )
                )
                multiplier = {
                    "k": 1_000,
                    "m": 1_000_000,
                    "b": 1_000_000_000,
                }.get(str(relation.group(3) or "").casefold(), 1)
                payload["right"]["value"] = float(relation.group(2)) * multiplier
            payload["required"] = not self._term_optional(text, match.phrase)
            if capability.key in moving_averages:
                period_match = re.search(
                    rf"(?:{re.escape(match.phrase)}\s*)(\d{{1,3}})\b",
                    snippet,
                )
                if period_match and payload.get("right", {}).get("kind") == "indicator":
                    payload["right"]["parameters"]["period"] = int(period_match.group(1))
            if capability.key in {"supertrend", "parabolic_sar", "elder_impulse"}:
                if "bearish" in snippet:
                    payload["right"] = {"kind": "constant", "value": -1}
                elif "bullish" in snippet:
                    payload["right"] = {"kind": "constant", "value": 1}
            if capability.key == "ichimoku_cloud":
                component = (
                    "price_below_cloud"
                    if "below" in snippet
                    else "price_inside_cloud"
                    if "inside" in snippet
                    else "future_cloud_bearish"
                    if "future" in snippet and "bearish" in snippet
                    else "future_cloud_bullish"
                    if "future" in snippet and "bullish" in snippet
                    else "price_above_cloud"
                )
                payload["left"]["parameters"]["component"] = component
            if capability.key == "bollinger_percent_b" and "%b" in snippet:
                payload["label"] = "Bollinger %B condition"
            if capability.key == "choppiness_index":
                if "range" in snippet or "choppy" in snippet:
                    payload["comparator"] = "gte"
                    payload["right"]["value"] = 61.8
                elif "trend" in snippet:
                    payload["comparator"] = "lte"
                    payload["right"]["value"] = 38.2
            if capability.key in {
                "displacement_candle_bullish",
                "market_structure_shift_bullish",
            }:
                payload["left"]["parameters"]["direction"] = "bullish"
            if capability.key in {
                "displacement_candle_bearish",
                "market_structure_shift_bearish",
            }:
                payload["left"]["parameters"]["direction"] = "bearish"
            add(ConditionRule.model_validate(payload))

    @staticmethod
    def _matched_clause(text: str, start: int, end: int) -> str:
        left_edges = [
            index + 1
            for separator in (".", ";", "\n", ",")
            if (index := text.rfind(separator, 0, start)) != -1
        ]
        left_edges.extend(
            match.end()
            for match in re.finditer(r"\s+\band\b\s+|\s+\bthen\b\s+|\s+\balso\b\s+", text[:start], re.I)
        )
        left = max(left_edges, default=0)
        right_candidates = [
            index
            for separator in (".", ";", "\n", ",")
            if (index := text.find(separator, end)) != -1
        ]
        right_candidates.extend(
            end + match.start()
            for match in re.finditer(r"\s+\band\b\s+|\s+\bthen\b\s+|\s+\balso\b\s+", text[end:], re.I)
        )
        right = min(right_candidates, default=len(text))
        clause = " ".join(text[left:right].strip(" -:\t").split())
        return clause[:500] or text[start:end][:500]

    @staticmethod
    def _append_unique(conditions: list[ConditionRule], condition: ConditionRule) -> None:
        keys = {item.key for item in conditions}
        if condition.key not in keys:
            conditions.append(condition)
            return
        index = 2
        base_key = condition.key[:90]
        while f"{base_key}_{index}" in keys:
            index += 1
        conditions.append(condition.model_copy(update={"key": f"{base_key}_{index}"}))

    @staticmethod
    def _conditions_equivalent(left: ConditionRule, right: ConditionRule) -> bool:
        if left.condition_type != right.condition_type or left.timeframe != right.timeframe:
            return False
        if left.comparator != right.comparator:
            return False
        left_right = left.right.model_dump(mode="json") if left.right else None
        right_right = right.right.model_dump(mode="json") if right.right else None
        return (
            left.left.model_dump(mode="json") == right.left.model_dump(mode="json")
            and left_right == right_right
        )

    def _parse_liquidity_sweeps(
        self,
        text: str,
        timeframe: str,
        add,
        assumptions: list[str],
    ) -> None:
        if not any(
            term in text
            for term in (
                "liquidity sweep",
                "sweep low",
                "sweep lows",
                "previous low sweep",
                "stop hunt low",
            )
        ) and not any(
            term in text
            for term in ("sweep high", "sweep highs", "previous high sweep", "stop hunt high")
        ):
            return
        bearish = any(
            term in text
            for term in ("bearish", "short", "sweep high", "sweep highs", "previous high sweep")
        )
        direction = "bearish" if bearish else "bullish"
        operand_name = (
            "buy_side_liquidity_sweep"
            if direction == "bearish"
            else "sell_side_liquidity_sweep"
        )
        add(
            self._price_action(
                f"{direction}_liquidity_sweep",
                f"{direction.title()} liquidity sweep",
                timeframe,
                operand_name,
                weight=2,
                forming_tolerance_percent=15,
                required=not self._term_optional(text, "sweep"),
            )
        )
        assumptions.append(
            f"A {direction} liquidity sweep uses a 20-candle swing: price breaches the prior "
            "extreme and closes back inside the prior range."
        )

    def _parse_moving_averages(
        self, text: str, timeframe: str, add, assumptions: list[str]
    ) -> None:
        for relation, ma_timeframe, average, period, span_start in self._ma_relation_matches(text):
            prefix = text[max(0, span_start - 18) : span_start]
            if any(asset in prefix for asset in ("btc", "bitcoin", "eth", "ethereum")):
                continue
            comparator = (
                Comparator.GREATER_THAN if relation in {"above", "over"} else Comparator.LESS_THAN
            )
            label_relation = "above" if comparator == Comparator.GREATER_THAN else "below"
            add(
                self._price_vs_indicator(
                    f"price_{label_relation}_{ma_timeframe}_{average}_{period}",
                    f"Price {label_relation} {ma_timeframe} {average.upper()} {period}",
                    ma_timeframe,
                    average,
                    period,
                    comparator,
                    weight=2 if period >= 100 or ma_timeframe != timeframe else 1,
                    required=not self._term_optional(text, average),
                    forming_tolerance_percent=1,
                )
            )

        cross_match = re.search(
            r"\b(ema|sma)\s*(\d{1,3}).{0,25}?cross(?:es|ing)?\s+"
            r"(above|below).{0,20}?(?:ema|sma)?\s*(\d{1,3})",
            text,
        )
        if cross_match:
            average = cross_match.group(1)
            fast = int(cross_match.group(2))
            relation = cross_match.group(3)
            slow = int(cross_match.group(4))
            add(
                self._indicator_vs_indicator(
                    f"{average}_{fast}_cross_{relation}_{average}_{slow}",
                    f"{average.upper()} {fast} crosses {relation} {average.upper()} {slow}",
                    timeframe,
                    average,
                    {"period": fast, "field": "close"},
                    Comparator.CROSSES_ABOVE if relation == "above" else Comparator.CROSSES_BELOW,
                    average,
                    {"period": slow, "field": "close"},
                )
            )
        elif "ema crossover" in text or "ema cross" in text:
            add(
                self._indicator_vs_indicator(
                    "ema_20_crosses_ema_50",
                    "EMA 20 crosses above EMA 50",
                    timeframe,
                    "ema",
                    {"period": 20, "field": "close"},
                    Comparator.CROSSES_ABOVE,
                    "ema",
                    {"period": 50, "field": "close"},
                )
            )
        elif "sma crossover" in text or "sma cross" in text:
            add(
                self._indicator_vs_indicator(
                    "sma_20_crosses_sma_50",
                    "SMA 20 crosses above SMA 50",
                    timeframe,
                    "sma",
                    {"period": 20, "field": "close"},
                    Comparator.CROSSES_ABOVE,
                    "sma",
                    {"period": 50, "field": "close"},
                )
            )

        for average in ("ema", "sma"):
            if f"{average} stack" in text or f"{average} 20 > {average} 50" in text:
                add(
                    self._indicator_vs_indicator(
                        f"{average}_20_above_{average}_50",
                        f"{average.upper()} 20 above {average.upper()} 50",
                        timeframe,
                        average,
                        {"period": 20, "field": "close"},
                        Comparator.GREATER_THAN,
                        average,
                        {"period": 50, "field": "close"},
                    )
                )
                add(
                    self._indicator_vs_indicator(
                        f"{average}_50_above_{average}_200",
                        f"{average.upper()} 50 above {average.upper()} 200",
                        timeframe,
                        average,
                        {"period": 50, "field": "close"},
                        Comparator.GREATER_THAN,
                        average,
                        {"period": 200, "field": "close"},
                    )
                )
                assumptions.append(f"Bullish {average.upper()} stack uses 20 > 50 > 200.")
            if f"{average} slope" in text or f"{average} rising" in text:
                down = "down" in text or "falling" in text
                indicator_name = f"{average}_slope"
                add(
                    self._indicator_constant(
                        f"{indicator_name}_{'down' if down else 'up'}",
                        f"{average.upper()} slope {'down' if down else 'up'}",
                        timeframe,
                        indicator_name,
                        Comparator.LESS_THAN if down else Comparator.GREATER_THAN,
                        0,
                        {"period": 20, "field": "close"},
                    )
                )
            if f"{average} retest" in text or "moving average retest" in text:
                add(
                    self._price_action(
                        f"{average}_retest",
                        f"{average.upper()} retest",
                        timeframe,
                        "pullback_to_ema",
                        {
                            "average": average,
                            "period": 20,
                            "direction": "short"
                            if "short" in text or "bearish" in text
                            else "long",
                        },
                        forming_tolerance_percent=5,
                    )
                )
            if f"reclaim {average}" in text or f"reclaims {average}" in text:
                period = self._period_near(text, average, 20)
                add(
                    self._price_vs_indicator(
                        f"price_reclaims_{average}_{period}",
                        f"Price reclaims {average.upper()} {period}",
                        timeframe,
                        average,
                        period,
                        Comparator.CROSSES_ABOVE,
                    )
                )

    def _parse_momentum(self, text: str, timeframe: str, add) -> None:
        rsi_cross = re.search(
            r"rsi.{0,30}?cross(?:es)?(?: back)?\s+(above|below)\s+(\d{1,3})", text
        )
        if rsi_cross:
            add(
                self._indicator_constant(
                    f"rsi_cross_{rsi_cross.group(1)}_{rsi_cross.group(2)}",
                    f"RSI crosses {rsi_cross.group(1)} {rsi_cross.group(2)}",
                    timeframe,
                    "rsi",
                    Comparator.CROSSES_ABOVE
                    if rsi_cross.group(1) == "above"
                    else Comparator.CROSSES_BELOW,
                    float(rsi_cross.group(2)),
                    {"period": 14, "field": "close"},
                    forming_tolerance_percent=10,
                )
            )
        elif "rsi exits oversold" in text or (
            "rsi" in text
            and "oversold" in text
            and ("exit" in text or "cross" in text or "back above" in text)
        ):
            add(
                self._indicator_constant(
                    "rsi_exits_oversold",
                    "RSI exits oversold above 30",
                    timeframe,
                    "rsi",
                    Comparator.CROSSES_ABOVE,
                    30,
                    {"period": 14, "field": "close"},
                )
            )
        elif "rsi exits overbought" in text:
            add(
                self._indicator_constant(
                    "rsi_exits_overbought",
                    "RSI exits overbought below 70",
                    timeframe,
                    "rsi",
                    Comparator.CROSSES_BELOW,
                    70,
                    {"period": 14, "field": "close"},
                )
            )
        elif "rsi" in text:
            threshold_match = re.search(r"rsi.{0,20}?(above|below|over|under)\s+(\d{1,3})", text)
            if threshold_match:
                above = threshold_match.group(1) in {"above", "over"}
                threshold = float(threshold_match.group(2))
            elif "overbought" in text:
                above = True
                threshold = 70
            elif "oversold" in text:
                above = False
                threshold = 30
            else:
                above = True
                threshold = 50
            add(
                self._indicator_constant(
                    f"rsi_{'above' if above else 'below'}_{int(threshold)}",
                    f"RSI {'above' if above else 'below'} {threshold:g}",
                    timeframe,
                    "rsi",
                    Comparator.GREATER_THAN_OR_EQUAL if above else Comparator.LESS_THAN_OR_EQUAL,
                    threshold,
                    {"period": 14, "field": "close"},
                    forming_tolerance_percent=10,
                    required=not self._term_optional(text, "rsi"),
                )
            )

        if "macd" in text:
            if "histogram" in text and any(
                term in text for term in ("turns positive", "flip positive", "positive")
            ):
                add(
                    self._macd_histogram_zero(
                        timeframe, Comparator.CROSSES_ABOVE, "MACD histogram turns positive"
                    )
                )
            elif "histogram" in text and any(
                term in text for term in ("turns negative", "flip negative", "negative")
            ):
                add(
                    self._macd_histogram_zero(
                        timeframe, Comparator.CROSSES_BELOW, "MACD histogram turns negative"
                    )
                )
            elif "histogram increasing" in text or "histogram rising" in text:
                add(
                    self._indicator_constant(
                        "macd_histogram_increasing",
                        "MACD histogram increasing",
                        timeframe,
                        "macd_histogram_delta",
                        Comparator.GREATER_THAN,
                        0,
                    )
                )
            elif "histogram decreasing" in text:
                add(
                    self._indicator_constant(
                        "macd_histogram_decreasing",
                        "MACD histogram decreasing",
                        timeframe,
                        "macd_histogram_delta",
                        Comparator.LESS_THAN,
                        0,
                    )
                )
            else:
                add(
                    self._indicator_vs_indicator(
                        "macd_line_crosses_signal",
                        "MACD line crosses signal",
                        timeframe,
                        "macd",
                        {"component": "line"},
                        Comparator.CROSSES_ABOVE,
                        "macd",
                        {"component": "signal"},
                    )
                )

        if "stochastic" in text or "stoch" in text:
            if "oversold" in text or "pullback" in text:
                add(
                    self._indicator_constant(
                        "stochastic_k_exits_oversold",
                        "Stochastic K exits oversold above 20",
                        timeframe,
                        "stochastic",
                        Comparator.CROSSES_ABOVE,
                        20,
                        {"component": "k"},
                    )
                )
            elif "overbought" in text:
                add(
                    self._indicator_constant(
                        "stochastic_k_exits_overbought",
                        "Stochastic K exits overbought below 80",
                        timeframe,
                        "stochastic",
                        Comparator.CROSSES_BELOW,
                        80,
                        {"component": "k"},
                    )
                )
            add(
                self._indicator_vs_indicator(
                    "stochastic_k_crosses_d",
                    "Stochastic K crosses D",
                    timeframe,
                    "stochastic",
                    {"component": "k"},
                    Comparator.CROSSES_ABOVE,
                    "stochastic",
                    {"component": "d"},
                    required=not self._term_optional(text, "stochastic"),
                )
            )

    def _parse_bollinger_and_volatility(self, text: str, timeframe: str, add) -> None:
        if "bollinger" in text or "bb " in text or "squeeze" in text:
            if "squeeze" in text:
                add(
                    self._price_action(
                        "bollinger_squeeze",
                        "Bollinger squeeze",
                        timeframe,
                        "bollinger_squeeze",
                        {"period": 20, "max_bandwidth_percent": 5},
                        weight=1.5,
                        forming_tolerance_percent=15,
                    )
                )
            if "re-entry" in text or "reentry" in text:
                add(
                    self._price_action(
                        "bollinger_reentry", "Bollinger re-entry", timeframe, "bollinger_reentry"
                    )
                )
            if "outside" in text or "upper band" in text or "lower band" in text:
                bearish = "lower" in text or "below" in text or "bearish" in text
                add(
                    self._price_vs_indicator_component(
                        "bollinger_close_outside_lower"
                        if bearish
                        else "bollinger_close_outside_upper",
                        "Close outside lower Bollinger band"
                        if bearish
                        else "Close outside upper Bollinger band",
                        timeframe,
                        "close",
                        "bollinger_band",
                        {"period": 20, "component": "lower" if bearish else "upper"},
                        Comparator.LESS_THAN if bearish else Comparator.GREATER_THAN,
                    )
                )
            if "touch" in text:
                lower = "lower" in text or "low" in text
                add(
                    self._price_vs_indicator_component(
                        "bollinger_lower_touch" if lower else "bollinger_upper_touch",
                        "Bollinger lower band touch" if lower else "Bollinger upper band touch",
                        timeframe,
                        "low" if lower else "high",
                        "bollinger_band",
                        {"period": 20, "component": "lower" if lower else "upper"},
                        Comparator.LESS_THAN_OR_EQUAL
                        if lower
                        else Comparator.GREATER_THAN_OR_EQUAL,
                    )
                )
        if "atr percent" in text or "atr %" in text:
            threshold = self._number_before_after(text, "atr", default=1.0)
            add(
                self._indicator_constant(
                    "atr_percent_threshold",
                    f"ATR percent at least {threshold:g}%",
                    timeframe,
                    "atr_percent",
                    Comparator.GREATER_THAN_OR_EQUAL,
                    threshold,
                    {"period": 14},
                )
            )
        elif "atr" in text and "stop" not in text:
            add(
                self._indicator_constant(
                    "atr_threshold",
                    "ATR above configured threshold",
                    timeframe,
                    "atr",
                    Comparator.GREATER_THAN_OR_EQUAL,
                    self._number_before_after(text, "atr", default=1.0),
                    {"period": 14},
                )
            )
        if "range expansion" in text or "volatility expansion" in text:
            add(
                self._price_action(
                    "range_expansion_candle",
                    "Range expansion candle",
                    timeframe,
                    "range_expansion",
                    {"lookback": 20, "expansion_multiplier": 1.5},
                    forming_tolerance_percent=10,
                )
            )
        if "volatility contraction" in text:
            add(
                self._price_action(
                    "volatility_contraction",
                    "Volatility contraction range",
                    timeframe,
                    "consolidation_range",
                    {"lookback": 30, "maximum_range_percent": 4},
                )
            )

    def _parse_volume_and_vwap(self, text: str, timeframe: str, add) -> None:
        volume_match = re.search(
            r"volume.*?(?:at least|above|over|>=|more than)?\s*(\d+(?:\.\d+)?)\s*(?:x|times)",
            text,
        )
        if volume_match:
            ratio = float(volume_match.group(1))
            add(self._volume_ratio(timeframe, ratio, Comparator.GREATER_THAN_OR_EQUAL, text))
        elif "volume confirmation" in text or "volume above average" in text:
            add(
                self._volume_ratio(
                    timeframe,
                    1.0,
                    Comparator.GREATER_THAN_OR_EQUAL,
                    text,
                    label="Volume above 20-candle average",
                )
            )
        elif any(
            term in text
            for term in ("volume spike", "volume breakout", "strong volume", "volume expansion")
        ):
            threshold = 1.8 if "spike" in text or "breakout" in text else 1.5
            add(self._volume_ratio(timeframe, threshold, Comparator.GREATER_THAN_OR_EQUAL, text))
        elif "low volume" in text or "dry-up" in text or "dry up" in text:
            add(
                self._volume_ratio(
                    timeframe,
                    0.8,
                    Comparator.LESS_THAN_OR_EQUAL,
                    text,
                    label="Volume dry-up below 0.8x average",
                )
            )
        if "relative volume rising" in text or "rvol rising" in text:
            add(
                self._indicator_constant(
                    "relative_volume_rising",
                    "Relative volume rising",
                    timeframe,
                    "relative_volume_slope",
                    Comparator.GREATER_THAN,
                    0,
                )
            )
        if "vwap" in text:
            if "reclaim" in text or "cross" in text:
                add(
                    self._price_vs_indicator(
                        "price_reclaims_vwap",
                        "Price reclaims VWAP",
                        timeframe,
                        "vwap",
                        20,
                        Comparator.CROSSES_ABOVE,
                    )
                )
            elif "pullback" in text or "retest" in text:
                add(
                    self._price_action(
                        "vwap_retest",
                        "VWAP pullback/retest",
                        timeframe,
                        "vwap_retest",
                        {
                            "period": 20,
                            "direction": "short"
                            if "short" in text or "bearish" in text
                            else "long",
                        },
                        forming_tolerance_percent=5,
                    )
                )
            elif "below" in text:
                add(
                    self._price_vs_indicator(
                        "price_below_vwap",
                        "Price below VWAP",
                        timeframe,
                        "vwap",
                        20,
                        Comparator.LESS_THAN,
                    )
                )
            else:
                add(
                    self._price_vs_indicator(
                        "price_above_vwap",
                        "Price above VWAP",
                        timeframe,
                        "vwap",
                        20,
                        Comparator.GREATER_THAN,
                    )
                )
            if "deviation" in text:
                add(
                    self._indicator_constant(
                        "vwap_deviation_percent",
                        "VWAP deviation percent within threshold",
                        timeframe,
                        "vwap_deviation_percent",
                        Comparator.LESS_THAN_OR_EQUAL,
                        self._number_before_after(text, "deviation", default=2.0),
                    )
                )

    def _parse_direct_market_search(
        self,
        text: str,
        timeframe: str,
        add,
        assumptions: list[str],
    ) -> None:
        for field, relation, threshold in self._price_threshold_matches(text):
            comparator = self._relation_comparator(relation)
            direction = (
                "above"
                if comparator
                in {
                    Comparator.GREATER_THAN,
                    Comparator.GREATER_THAN_OR_EQUAL,
                }
                else "below"
            )
            inclusive = comparator in {
                Comparator.GREATER_THAN_OR_EQUAL,
                Comparator.LESS_THAN_OR_EQUAL,
            }
            label = (
                f"{field.title()} price {'at or ' if inclusive else ''}{direction} ${threshold:g}"
            )
            search_parameters = self._event_search_parameters(text, timeframe)
            if search_parameters:
                search_parameters["aggregate"] = "max" if direction == "above" else "min"
            add(
                self._price_vs_constant(
                    f"{field}_price_{direction}_{threshold:g}".replace(".", "_"),
                    label,
                    timeframe,
                    field,
                    comparator,
                    threshold,
                    parameters=search_parameters,
                    forming_tolerance_percent=2,
                )
            )
            assumptions.append(
                f"Plain market search uses the latest {field} price on {timeframe} "
                f"against ${threshold:g}."
            )

        volume_match = re.search(
            r"\bvolume\b(?![^.]{0,35}\b(?:x|times|ratio|multiplier)\b)[^.]{0,40}?"
            r"\b(above|over|greater than|more than|at least|>=|below|under|less than|<=)\b"
            r"\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)",
            text,
        )
        if volume_match:
            segment = text[volume_match.start() : min(len(text), volume_match.end() + 24)]
            if any(term in segment for term in ("x", "times", "ratio", "multiplier", "average")):
                return
            threshold = self._clean_number(volume_match.group(2))
            comparator = self._relation_comparator(volume_match.group(1))
            direction = (
                "above"
                if comparator
                in {
                    Comparator.GREATER_THAN,
                    Comparator.GREATER_THAN_OR_EQUAL,
                }
                else "below"
            )
            add(
                self._market_metric_constant(
                    f"average_volume_{direction}_{threshold:g}".replace(".", "_"),
                    f"Average candle volume {direction} {threshold:g}",
                    timeframe,
                    "average_volume",
                    comparator,
                    threshold,
                    {"period": 20},
                    forming_tolerance_percent=10,
                )
            )
            assumptions.append(
                "Plain volume search uses 20-candle average base volume unless relative "
                "volume such as 1.5x is specified."
            )

    @classmethod
    def _price_threshold_matches(cls, text: str) -> list[tuple[str, str, float]]:
        matches: list[tuple[str, str, float]] = []
        relation_pattern = r"above|over|greater than|more than|at least|>=|below|under|less than|<="
        number_pattern = r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:usd|usdt|dollars?|\$)?"
        field_pattern = r"(price|close|closing price|current price|last price|high|low|open)"
        patterns = [
            rf"\b{field_pattern}\b[^.?,;]{{0,35}}?\b({relation_pattern})\b[^.?,;]{{0,20}}?{number_pattern}",
            rf"\b({relation_pattern})\b[^.?,;]{{0,20}}?{number_pattern}"
            r"[^.?,;]{0,25}?\b(price|close|current price|last price|usd|usdt|dollars?)\b",
            rf"\b(?:symbols|coins|pairs|markets)\b[^.?,;]{{0,35}}?\b({relation_pattern})\b[^.?,;]{{0,20}}?{number_pattern}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                segment = text[match.start() : min(len(text), match.end() + 24)]
                context = text[max(0, match.start() - 36) : min(len(text), match.end() + 24)]
                if "above open" in context or "below open" in context:
                    continue
                if "%" in segment:
                    continue
                if any(
                    term in segment
                    or term in context
                    for term in (
                        "ema",
                        "sma",
                        "rsi",
                        "period",
                        "market cap",
                        "volume",
                        "funding",
                        "open interest",
                        "spread",
                        "depth",
                    )
                ):
                    continue
                if any(
                    capability_match.capability.condition_type == "indicator"
                    for capability_match in find_capability_matches(segment)
                ):
                    continue
                groups = match.groups()
                if pattern.startswith(r"\b(price"):
                    field = cls._price_field(groups[0])
                    relation = groups[1]
                    value = cls._clean_number(groups[2])
                elif "symbols|coins|pairs|markets" in pattern:
                    field = "close"
                    relation = groups[0]
                    value = cls._clean_number(groups[1])
                else:
                    field = cls._price_field(groups[2])
                    relation = groups[0]
                    value = cls._clean_number(groups[1])
                matches.append((field, relation, value))
        deduped: list[tuple[str, str, float]] = []
        seen: set[tuple[str, str, float]] = set()
        for item in matches:
            key = (item[0], item[1], item[2])
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped

    @staticmethod
    def _price_field(value: str) -> str:
        lowered = value.casefold()
        if "high" in lowered:
            return "high"
        if "low" in lowered:
            return "low"
        if "open" in lowered:
            return "open"
        return "close"

    @staticmethod
    def _clean_number(value: str) -> float:
        return float(value.replace(",", ""))

    @staticmethod
    def _relation_comparator(relation: str) -> Comparator:
        normalized = relation.casefold().strip()
        if normalized in {"above", "over", "greater than", "more than"}:
            return Comparator.GREATER_THAN
        if normalized in {"below", "under", "less than"}:
            return Comparator.LESS_THAN
        if normalized in {"<="}:
            return Comparator.LESS_THAN_OR_EQUAL
        return Comparator.GREATER_THAN_OR_EQUAL

    def _parse_price_action(self, text: str, timeframe: str, add, assumptions: list[str]) -> None:
        if self._mentions_breakout_high(text):
            lookback = self._lookback_candles(text, timeframe)
            label_window = self._lookback_label(text)
            add(
                self._price_action(
                    f"breakout_{lookback}_candle_high",
                    f"Price breaks the {label_window} high",
                    timeframe,
                    "higher_high",
                    {"lookback": lookback},
                    weight=2,
                    forming_tolerance_percent=5,
                )
            )
            assumptions.append(
                f"High breakout is treated as the latest candle high exceeding the previous "
                f"{lookback} closed candles on {timeframe}."
            )
        elif any(
            phrase in text
            for phrase in ("breakout", "break out", "breaks out", "breaking out", "breaking above")
        ):
            add(
                self._price_action(
                    "range_breakout",
                    "Range breakout",
                    timeframe,
                    "breakout_from_consolidation",
                    {"lookback": 40},
                    weight=2,
                    forming_tolerance_percent=5,
                )
            )
        if "breakdown" in text or "breaking below" in text:
            add(
                self._price_action(
                    "range_breakdown",
                    "Range breakdown",
                    timeframe,
                    "breakdown_from_consolidation",
                    {"lookback": 40},
                    weight=2,
                    forming_tolerance_percent=5,
                )
            )
        if (
            "breakout retest" in text
            or "retest of breakout" in text
            or "break and retest" in text
            or "breaks and retests" in text
            or "break retest" in text
        ):
            add(
                self._price_action(
                    "break_and_retest",
                    "Break and retest confirmed",
                    timeframe,
                    "break_and_retest_confirmed",
                    {"lookback": 40},
                )
            )
        if "support" in text or "bounce" in text:
            add(
                self._price_action(
                    "support_retest",
                    "Support retest bounce",
                    timeframe,
                    "price_bounces_from_support",
                    {"lookback": 20},
                )
            )
        if "resistance" in text or "reject" in text or "rejection" in text:
            add(
                self._price_action(
                    "resistance_retest",
                    "Resistance rejection",
                    timeframe,
                    "price_rejects_resistance",
                    {"lookback": 20},
                )
            )
        if "break of structure" in text or " bos" in text:
            bearish = "bearish" in text or "short" in text
            add(
                self._price_action(
                    "break_of_structure_bearish" if bearish else "break_of_structure_bullish",
                    "Bearish break of structure" if bearish else "Bullish break of structure",
                    timeframe,
                    "market_structure_shift_bearish"
                    if bearish
                    else "market_structure_shift_bullish",
                    {"lookback": 20},
                    weight=1.5,
                )
            )
        if "change of character" in text or "choch" in text:
            bearish = "bearish" in text or "short" in text
            add(
                self._price_action(
                    "change_of_character_bearish" if bearish else "change_of_character_bullish",
                    "Bearish change of character" if bearish else "Bullish change of character",
                    timeframe,
                    "market_structure_shift_bearish"
                    if bearish
                    else "market_structure_shift_bullish",
                    {"lookback": 20},
                )
            )
        for phrase, name, label in (
            ("higher high", "higher_high", "Higher high"),
            ("higher low", "higher_low", "Higher low"),
            ("lower high", "lower_high", "Lower high"),
            ("lower low", "lower_low", "Lower low"),
        ):
            if phrase in text:
                add(self._price_action(name, label, timeframe, name, {"lookback": 20}))
        if "equal highs" in text:
            add(
                self._price_action(
                    "equal_highs",
                    "Equal highs liquidity pool",
                    timeframe,
                    "equal_highs_liquidity_pool",
                    {"lookback": 20, "tolerance_percent": 0.2},
                )
            )
        if "equal lows" in text:
            add(
                self._price_action(
                    "equal_lows",
                    "Equal lows liquidity pool",
                    timeframe,
                    "equal_lows_liquidity_pool",
                    {"lookback": 20, "tolerance_percent": 0.2},
                )
            )
        if "consolidation" in text or "tight range" in text:
            add(
                self._price_action(
                    "consolidation_range",
                    "Consolidation range",
                    timeframe,
                    "tight_consolidation",
                    {"lookback": 40, "maximum_range_percent": 5},
                )
            )
        if "impulse candle" in text or "momentum candle" in text:
            add(
                self._price_action(
                    "impulse_candle",
                    "Impulse candle",
                    timeframe,
                    "wide_range_candle",
                    {"lookback": 20, "range_multiplier": 1.5},
                )
            )
        if "pullback depth" in text:
            add(
                self._indicator_constant(
                    "pullback_depth_percent",
                    "Pullback depth percent below maximum",
                    timeframe,
                    "pullback_depth_percent",
                    Comparator.LESS_THAN_OR_EQUAL,
                    self._number_before_after(text, "pullback", default=50),
                    {
                        "lookback": 20,
                        "direction": "short" if "short" in text or "bearish" in text else "long",
                    },
                )
            )
        percent_move = self._percent_move(text, timeframe)
        if percent_move is not None:
            direction, threshold, lookback = percent_move
            add(
                self._price_action(
                    f"price_{direction}_{str(threshold).replace('.', '_')}pct",
                    (
                        f"Price {'increases' if direction == 'up' else 'decreases'} "
                        f"by at least {threshold:g}%"
                    ),
                    timeframe,
                    "percent_change_up" if direction == "up" else "percent_change_down",
                    {"threshold_percent": threshold, "lookback": lookback},
                    weight=1.5,
                    forming_tolerance_percent=10,
                )
            )
            assumptions.append(
                f"Percentage move is measured from the close {lookback} candle(s) ago to "
                f"the current signal close on {timeframe}."
            )

    def _parse_candles(self, text: str, timeframe: str, add) -> None:
        previous_color = re.search(
            r"\b(?:previous|prior|last closed)\s+candle.{0,24}?"
            r"\b(green|bullish|red|bearish)\b",
            text,
        )
        if previous_color:
            color = previous_color.group(1)
            name = (
                "bullish_candle"
                if color == "bullish"
                else "green_candle"
                if color == "green"
                else "bearish_candle"
                if color == "bearish"
                else "red_candle"
            )
            add(
                self._candle_pattern(
                    f"previous_{name}",
                    f"Previous {color} candle",
                    timeframe,
                    name,
                    {"offset": 1},
                )
            )
        for match in self._consecutive_candle_matches(text, timeframe):
            count, color, candle_timeframe = match
            component = (
                "consecutive_bullish"
                if color in {"green", "bullish"}
                else "consecutive_bearish"
            )
            add(
                self._indicator_constant(
                    f"{count}_consecutive_{color}_candles",
                    f"{count} consecutive {color} {candle_timeframe} candles",
                    candle_timeframe,
                    "candle_anatomy",
                    Comparator.GREATER_THAN_OR_EQUAL,
                    1,
                    {"component": component, "count": count},
                    weight=1.2,
                )
            )

        color_matches = list(
            re.finditer(
                r"\b(?:(previous|prior|last closed)\s+)?"
                r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|1d|daily|hourly|one minute|1 minute)"
                r"[- ]+)?"
                r"(green|bullish|red|bearish)"
                r"(?:[- ]+(1m|3m|5m|15m|30m|1h|2h|4h|1d|daily|hourly|one minute|1 minute))?"
                r"[- ]+candle\b",
                text,
            )
        )
        for match in color_matches:
            color = match.group(3)
            name = (
                "bullish_candle"
                if color == "bullish"
                else "green_candle"
                if color == "green"
                else "bearish_candle"
                if color == "bearish"
                else "red_candle"
            )
            candle_timeframe = self._normalize_timeframe(
                match.group(2) or match.group(4) or timeframe
            )
            offset = 1 if match.group(1) else 0
            parameters = self._event_search_parameters(text, candle_timeframe)
            if offset:
                parameters["offset"] = offset
            label_prefix = "Previous " if offset else ""
            add(
                self._candle_pattern(
                    f"{'previous_' if offset else ''}{name}",
                    f"{label_prefix}{color} candle",
                    candle_timeframe,
                    name,
                    parameters,
                )
            )

        candle_move = re.search(
            r"\b(?:find|show|bring|had|with).{0,80}?"
            r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|1d|one minute|1 minute|daily)[- ]+)?"
            r"candle.{0,60}?(\d+(?:\.\d+)?)\s*%",
            text,
        )
        if candle_move:
            candle_timeframe = self._normalize_timeframe(candle_move.group(1) or timeframe)
            direction = "absolute"
            if any(word in text for word in ("green", "bullish", "increase", "increased", "up")):
                direction = "up"
            elif any(
                word in text for word in ("red", "bearish", "decrease", "decreased", "down", "drop")
            ):
                direction = "down"
            parameters = {
                "threshold_percent": float(candle_move.group(2)),
                "direction": direction,
                **self._event_search_parameters(text, candle_timeframe),
            }
            add(
                self._candle_pattern(
                    f"candle_move_{str(candle_move.group(2)).replace('.', '_')}pct",
                    (
                        f"Any {candle_timeframe} candle moved at least "
                        f"{float(candle_move.group(2)):g}%"
                    ),
                    candle_timeframe,
                    "candle_change_percent",
                    parameters,
                )
            )

        patterns = {
            "bullish engulfing": ("bullish_engulfing", "Bullish engulfing"),
            "bearish engulfing": ("bearish_engulfing", "Bearish engulfing"),
            "hammer": ("hammer", "Hammer candle"),
            "hummer": ("hammer", "Hammer candle"),
            "shooting star": ("shooting_star", "Shooting star candle"),
            "doji": ("doji", "Doji candle"),
            "inside bar": ("inside_bar", "Inside bar"),
            "outside bar": ("outside_bar", "Outside bar"),
            "pin bar": ("pin_bar", "Pin bar"),
            "close near high": ("strong_close_near_high", "Strong close near high"),
            "strong close near high": ("strong_close_near_high", "Strong close near high"),
            "close near low": ("strong_close_near_low", "Strong close near low"),
            "strong close near low": ("strong_close_near_low", "Strong close near low"),
            "closes green": ("green_candle", "Candle closes green"),
            "close green": ("green_candle", "Candle closes green"),
            "green candle": ("green_candle", "Green candle"),
            "bullish candle": ("bullish_candle", "Bullish candle"),
            "closes red": ("red_candle", "Candle closes red"),
            "red candle": ("red_candle", "Red candle"),
            "bearish candle": ("bearish_candle", "Bearish candle"),
        }
        for phrase, (name, label) in patterns.items():
            if phrase in text and not (
                name in {"green_candle", "red_candle", "bullish_candle", "bearish_candle"}
                and color_matches
            ):
                phrase_index = text.index(phrase)
                candle_timeframe = self._timeframe_near(text, phrase_index) or timeframe
                negated = self._pattern_negated(text, phrase_index)
                previous = self._previous_candle_requested(text, phrase_index)
                parameters = self._event_search_parameters(text, candle_timeframe)
                if previous:
                    parameters["offset"] = 1
                add(
                    self._candle_pattern(
                        f"{'previous_' if previous else ''}{name}",
                        (
                            f"Previous {label.lower()}"
                            if previous and not negated
                            else f"Previous not {label.lower()}"
                            if previous
                            else f"Not {label.lower()}"
                            if negated
                            else label
                        ),
                        candle_timeframe,
                        name,
                        parameters,
                        comparator=Comparator.IS_FALSE if negated else Comparator.IS_TRUE,
                    )
                )

    @classmethod
    def _consecutive_candle_matches(
        cls,
        text: str,
        fallback_timeframe: str,
    ) -> list[tuple[int, str, str]]:
        results: list[tuple[int, str, str]] = []
        patterns = (
            re.compile(
                r"\b(\d+)\s+(?:days?|daily candles?|1d candles?)\s+"
                r"(?:in a row|consecutive|straight).{0,24}?"
                r"\b(green|bullish|red|bearish)\b"
            ),
            re.compile(
                r"\b(\d+)\s+(?:consecutive|straight|in a row)\s+"
                r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|1d|daily)\s+)?"
                r"(green|bullish|red|bearish)\s+candles?\b"
            ),
            re.compile(
                r"\b(green|bullish|red|bearish)\s+"
                r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|1d|daily)\s+)?"
                r"candles?\s+(?:for\s+)?(\d+)\s+"
                r"(?:days?|candles?)\s+(?:in a row|consecutive|straight)\b"
            ),
        )
        for pattern in patterns:
            for match in pattern.finditer(text):
                groups = match.groups()
                if groups[0].isdigit():
                    count = int(groups[0])
                    color = groups[-1]
                    raw_timeframe = next(
                        (
                            group
                            for group in groups[1:-1]
                            if group and group not in {"green", "bullish", "red", "bearish"}
                        ),
                        None,
                    )
                else:
                    color = groups[0]
                    count = int(groups[-1])
                    raw_timeframe = next((group for group in groups[1:-1] if group), None)
                window = text[max(0, match.start() - 24) : match.end() + 24]
                candle_timeframe = cls._normalize_timeframe(
                    raw_timeframe
                    or ("1d" if "day" in window or "daily" in window else fallback_timeframe)
                )
                item = (max(1, min(count, 5000)), color, candle_timeframe)
                if item not in results:
                    results.append(item)
        return results

    @staticmethod
    def _pattern_negated(text: str, phrase_index: int) -> bool:
        before = text[max(0, phrase_index - 72) : phrase_index]
        return bool(
            re.search(
                r"\b(?:not|no|without|avoid|is not|isn't|isnt|did not|didn't|"
                r"does not|doesn't|do not|don't)\b",
                before,
            )
        )

    @staticmethod
    def _previous_candle_requested(text: str, phrase_index: int) -> bool:
        window = text[max(0, phrase_index - 48) : phrase_index + 48]
        return bool(re.search(r"\b(?:previous|prior|last closed)\b", window))

    def _recognized_unsupported(
        self, text: str, existing_conditions: list[ConditionRule]
    ) -> list[InterpretationIssue]:
        issues: list[InterpretationIssue] = []
        compatibility = compatibility_by_key()
        for capability in prompt_blocked_capabilities():
            matched = next(
                (
                    phrase
                    for phrase in normalized_phrases(capability)
                    if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text)
                ),
                None,
            )
            if matched is None:
                continue
            if self._blocked_capability_already_covered(
                capability.key,
                matched,
                existing_conditions,
            ):
                continue
            blocking = not self._term_optional(text, matched)
            availability = compatibility[capability.key].availability
            if availability == "provider_required":
                code = "provider_required"
                reason = (
                    f"{capability.label} requires {capability.provider_required or 'external'} "
                    "data that is not configured for live activation."
                )
            elif capability.key in {"btc_trend_filter", "eth_trend_filter"}:
                code = "cross_market_filter"
                reason = (
                    f"{capability.label} is recognized, but is not executable in the "
                    "current deterministic scanner."
                )
            elif capability.key in {"market_cap_minimum", "meme_coin_exclusion"}:
                code = "external_data_required"
                reason = (
                    f"{capability.label} requires external market metadata before activation."
                )
            else:
                code = "recognized_not_executable"
                reason = (
                    f"{capability.label} is recognized, but is not executable in the "
                    "current deterministic scanner."
                )
            issues.append(
                InterpretationIssue(
                    code=code,
                    field="setup_text",
                    message=f"{reason} {capability.guidance or ''}".strip(),
                    blocking=blocking,
                    source_fragment=matched,
                )
            )
        return issues

    @staticmethod
    def _blocked_capability_already_covered(
        capability_key: str,
        matched_phrase: str,
        conditions: list[ConditionRule],
    ) -> bool:
        haystacks: list[str] = []
        for condition in conditions:
            values = [
                condition.key.replace("_", " "),
                condition.label,
                condition.source_fragment or "",
                condition.left.name or "",
                condition.right.name if condition.right else "",
            ]
            haystacks.extend(value.casefold() for value in values if value)
        phrase = matched_phrase.casefold()
        if any(phrase and phrase in value for value in haystacks):
            return True
        if capability_key == "percent_change_lookback":
            return any("percent_change_" in value for value in haystacks)
        if capability_key in {"time_window", "killzone_filter"}:
            return any("time_window" in value or "session" in value or "midnight" in value for value in haystacks)
        if capability_key in {"range_breakout", "range_breakdown", "new_n_day_high", "new_n_day_low"}:
            return any(
                term in value
                for value in haystacks
                for term in (
                    "breakout",
                    "breakdown",
                    "all_time_high_breakout",
                    "n_day_high_breakout",
                    "n_day_low_breakdown",
                    "higher_high",
                    "lower_low",
                )
            )
        return False

    @staticmethod
    def _cross_symbol_context_issues(text: str) -> list[InterpretationIssue]:
        issues: list[InterpretationIssue] = []
        patterns = (
            r"\b(?:only if\s+)?btc.{0,40}?(?:ema|sma|trend|above|below)",
            r"\b(?:only if\s+)?eth.{0,40}?(?:ema|sma|trend|above|below)",
            r"\balts?\s+outperforming\s+btc\b",
            r"\beth/btc\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            fragment = match.group(0)
            issues.append(
                InterpretationIssue(
                    code="cross_symbol_context_provider_required",
                    field="setup_text",
                    message=(
                        "Cross-symbol market context was recognized but is not enabled as a "
                        "fully executable single-symbol scanner condition yet."
                    ),
                    blocking=True,
                    source_fragment=fragment,
                )
            )
        return issues

    @staticmethod
    def _vague_prompt_issues(text: str) -> list[InterpretationIssue]:
        issues: list[InterpretationIssue] = []
        for pattern in (
            r"\bready to pump\b",
            r"\bhigh probability\b",
            r"\bgood setups?\b",
            r"\bstrong coins?\b",
        ):
            match = re.search(pattern, text)
            if not match:
                continue
            fragment = match.group(0)
            issues.append(
                InterpretationIssue(
                    code="ambiguous_discretionary_language",
                    field="setup_text",
                    message=(
                        "This discretionary phrase needs a measurable definition before it "
                        f"can be monitored: '{fragment}'."
                    ),
                    blocking=True,
                    source_fragment=fragment,
                )
            )
        return issues

    @staticmethod
    def _unparsed_instruction_issues(
        original_text: str,
        normalized_text: str,
    ) -> list[InterpretationIssue]:
        recognized_terms = (
            "rsi",
            "mfi",
            "money flow",
            "macd",
            "roc",
            "rate of change",
            "ema",
            "sma",
            "hma",
            "hull moving average",
            "wma",
            "weighted moving average",
            "moving average",
            "vwap",
            "bollinger",
            "percent change",
            "lookback",
            "stochastic",
            "adx",
            "atr",
            "volume",
            "liquidity",
            "spread",
            "candle",
            "doji",
            "hammer",
            "hummer",
            "shooting star",
            "engulfing",
            "green",
            "red",
            "bullish",
            "bearish",
            "price",
            "close",
            "open",
            "up",
            "down",
            "gain",
            "gained",
            "grew",
            "growth",
            "increase",
            "increased",
            "moved up",
            "dropped",
            "dumped",
            "fell",
            "lost",
            "decrease",
            "decreased",
            "moved down",
            "sold off",
            "support",
            "resistance",
            "breakout",
            "all time high",
            "all-time high",
            "ath",
            "session",
            "weekday",
            "weekdays",
            "weekend",
            "midnight",
            "new york",
            "london",
            "risk",
            "stop",
            "target",
        )
        structural_terms = (
            "find",
            "show",
            "bring",
            "symbols",
            "coins",
            "binance",
            "bybit",
            "spot",
            "usdt",
            "usdc",
            "timeframe",
            "alerts",
        )
        requirement_terms = (
            "must",
            "only",
            "required",
            "require",
            "never",
            "not",
            "no ",
            "without",
            "avoid",
            "above",
            "below",
            "over",
            "under",
            "cross",
            "with",
            "where",
            "when",
            "if",
        )
        fragments = [
            fragment.strip(" .:-")
            for fragment in re.split(r"\s+(?:and|also|plus|but)\s+|[,;\n]+", normalized_text)
        ]
        original_fragments = [
            fragment.strip(" .:-")
            for fragment in re.split(
                r"\s+(?:and|also|plus|but)\s+|[,;\n]+",
                original_text,
                flags=re.IGNORECASE,
            )
        ]
        issues: list[InterpretationIssue] = []
        for index, fragment in enumerate(fragments):
            if len(fragment) < 8:
                continue
            if any(term in fragment for term in recognized_terms):
                continue
            if not any(term in fragment for term in requirement_terms):
                continue
            if all(term in fragment for term in structural_terms[:1]) and not any(
                term in fragment for term in requirement_terms[:8]
            ):
                continue
            display = original_fragments[index] if index < len(original_fragments) else fragment
            if any(term in fragment for term in structural_terms) and len(fragment.split()) <= 5:
                continue
            issues.append(
                InterpretationIssue(
                    code="instruction_not_converted",
                    field="setup_text",
                    message=(
                        "This instruction was not converted into an executable deterministic "
                        f"rule: '{display}'. Clarify it using a supported indicator, candle "
                        "pattern, price action, timeframe, comparator, and threshold."
                    ),
                    blocking=not any(word in fragment for word in OPTIONAL_WORDS),
                    source_fragment=display,
                )
            )
        return issues

    @staticmethod
    def _indicator_constant(
        key: str,
        label: str,
        timeframe: str,
        indicator: str,
        comparator: Comparator,
        threshold: float,
        parameters: dict | None = None,
        *,
        weight: float = 1,
        required: bool = True,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.INDICATOR,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.INDICATOR, name=indicator, parameters=parameters or {}),
            comparator=comparator,
            right=Operand(kind=OperandKind.CONSTANT, value=threshold),
            required=required,
            weight=weight,
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _indicator_vs_indicator(
        key: str,
        label: str,
        timeframe: str,
        left_name: str,
        left_parameters: dict,
        comparator: Comparator,
        right_name: str,
        right_parameters: dict,
        *,
        required: bool = True,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.INDICATOR,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.INDICATOR, name=left_name, parameters=left_parameters),
            comparator=comparator,
            right=Operand(kind=OperandKind.INDICATOR, name=right_name, parameters=right_parameters),
            required=required,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _price_vs_indicator(
        key: str,
        label: str,
        timeframe: str,
        indicator: str,
        period: int,
        comparator: Comparator,
        *,
        weight: float = 1,
        required: bool = True,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        parameters = (
            {"period": period, "field": "close"}
            if indicator in {"ema", "sma"}
            else {"period": period}
        )
        return RuleBasedStrategyInterpreter._price_vs_indicator_component(
            key,
            label,
            timeframe,
            "close",
            indicator,
            parameters,
            comparator,
            weight=weight,
            required=required,
            forming_tolerance_percent=forming_tolerance_percent,
        )

    @staticmethod
    def _price_vs_indicator_component(
        key: str,
        label: str,
        timeframe: str,
        price_field: str,
        indicator: str,
        parameters: dict,
        comparator: Comparator,
        *,
        weight: float = 1,
        required: bool = True,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.INDICATOR,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.PRICE, field=price_field),
            comparator=comparator,
            right=Operand(kind=OperandKind.INDICATOR, name=indicator, parameters=parameters),
            required=required,
            weight=weight,
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _price_vs_constant(
        key: str,
        label: str,
        timeframe: str,
        price_field: str,
        comparator: Comparator,
        threshold: float,
        *,
        parameters: dict | None = None,
        weight: float = 1,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.MARKET_FILTER,
            timeframe=timeframe,
            left=Operand(
                kind=OperandKind.PRICE,
                field=price_field,
                parameters=parameters or {},
            ),
            comparator=comparator,
            right=Operand(kind=OperandKind.CONSTANT, value=threshold),
            required=True,
            weight=weight,
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _market_metric_constant(
        key: str,
        label: str,
        timeframe: str,
        metric: str,
        comparator: Comparator,
        threshold: float,
        parameters: dict | None = None,
        *,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.MARKET_FILTER,
            timeframe=timeframe,
            left=Operand(
                kind=OperandKind.MARKET_METRIC,
                name=metric,
                parameters=parameters or {},
            ),
            comparator=comparator,
            right=Operand(kind=OperandKind.CONSTANT, value=threshold),
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _price_action(
        key: str,
        label: str,
        timeframe: str,
        name: str,
        parameters: dict | None = None,
        *,
        weight: float = 1,
        required: bool = True,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        payload = {"lookback": 20, **(parameters or {})}
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.PRICE_ACTION,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.PRICE_ACTION, name=name, parameters=payload),
            comparator=Comparator.IS_TRUE,
            required=required,
            weight=weight,
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _candle_pattern(
        key: str,
        label: str,
        timeframe: str,
        name: str,
        parameters: dict | None = None,
        *,
        comparator: Comparator = Comparator.IS_TRUE,
        forming_tolerance_percent: float | None = None,
    ) -> ConditionRule:
        return ConditionRule(
            key=RuleBasedStrategyInterpreter._key(key),
            label=label,
            condition_type=ConditionType.CANDLE_PATTERN,
            timeframe=timeframe,
            left=Operand(kind=OperandKind.CANDLE_PATTERN, name=name, parameters=parameters or {}),
            comparator=comparator,
            forming_tolerance_percent=forming_tolerance_percent,
            required_data=["ohlcv"],
        )

    @staticmethod
    def _volume_ratio(
        timeframe: str,
        threshold: float,
        comparator: Comparator,
        text: str,
        *,
        label: str | None = None,
    ) -> ConditionRule:
        return RuleBasedStrategyInterpreter._indicator_constant(
            "relative_volume",
            label or f"Volume at least {threshold:g}x 20-candle average",
            timeframe,
            "volume_ratio",
            comparator,
            threshold,
            {"period": 20},
            forming_tolerance_percent=15,
            required=not RuleBasedStrategyInterpreter._term_optional(text, "volume"),
        )

    @staticmethod
    def _macd_histogram_zero(
        timeframe: str,
        comparator: Comparator,
        label: str,
    ) -> ConditionRule:
        return RuleBasedStrategyInterpreter._indicator_constant(
            "macd_histogram_zero_cross",
            label,
            timeframe,
            "macd",
            comparator,
            0,
            {"component": "histogram"},
        )

    @staticmethod
    def _normalize_timeframe(value: str) -> str:
        lowered = value.strip().casefold()
        return TIMEFRAME_WORDS.get(lowered, lowered)

    @classmethod
    def _timeframe_from_text(cls, text: str, fallback: str) -> str:
        for word, timeframe in TIMEFRAME_WORDS.items():
            for match in re.finditer(re.escape(word), text):
                if cls._timeframe_reference_is_condition_context(text, match.start(), match.end()):
                    continue
                return timeframe
        for match in re.finditer(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)\b", text):
            if cls._timeframe_reference_is_condition_context(text, match.start(), match.end()):
                continue
            return match.group(1)
        return cls._normalize_timeframe(fallback)

    @staticmethod
    def _timeframe_reference_is_condition_context(text: str, start: int, end: int) -> bool:
        before = text[max(0, start - 32) : start]
        after = text[end : end + 36]
        indicator_after = re.search(
            r"\b(?:ema|sma|ma|rsi|macd|vwap|bollinger|stochastic|atr|adx)\b",
            after,
        )
        relation_before = re.search(r"\b(?:above|below|over|under|cross|crosses)\b.{0,24}$", before)
        return bool(indicator_after and relation_before)

    @classmethod
    def _ma_relation_matches(cls, text: str) -> Iterable[tuple[str, str, str, int, int]]:
        pattern = re.compile(
            r"\b(above|below|over|under)\b.{0,24}?"
            r"(?:(1m|3m|5m|15m|30m|1h|2h|4h|1d|one-hour|four-hour|daily)[ -]?)?"
            r"(?:(\d{1,3})\s*(ema|sma)|(ema|sma)\s*(\d{1,3}))"
        )
        for match in pattern.finditer(text):
            relation = match.group(1)
            timeframe = cls._normalize_timeframe(match.group(2) or "")
            if not timeframe:
                timeframe = cls._timeframe_near(text, match.start()) or "15m"
            period = int(match.group(3) or match.group(6))
            average = str(match.group(4) or match.group(5))
            yield relation, timeframe, average, period, match.start()

    @staticmethod
    def _timeframe_near(text: str, index: int) -> str | None:
        window = text[max(0, index - 24) : index + 24]
        for timeframe in SUPPORTED_TIMEFRAMES:
            if re.search(rf"(?<![a-z0-9]){re.escape(timeframe)}(?![a-z0-9])", window):
                return timeframe
        for word, timeframe in TIMEFRAME_WORDS.items():
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", window):
                return timeframe
        return None

    @staticmethod
    def _template_text(key: str) -> str:
        templates = {
            "liquidity_sweep_trend_volume": (
                "Bullish liquidity sweep, price above the four-hour 200 EMA, "
                "volume at least 1.5 times average"
            ),
            "trend_pullback": "Price above the four-hour 200 EMA",
        }
        return templates.get(key, "")

    @staticmethod
    def _direction(text: str) -> StrategyDirection:
        if "both" in text or "long and short" in text:
            return StrategyDirection.BOTH
        if any(word in text for word in ("bearish", "short", "sell setup", "breakdown", "reject")):
            return StrategyDirection.SHORT
        return StrategyDirection.LONG

    @staticmethod
    def _strategy_name(text: str) -> str:
        if "vwap" in text:
            return "VWAP strategy"
        if "rsi" in text:
            return "RSI strategy"
        if "bollinger" in text or "squeeze" in text:
            return "Bollinger strategy"
        if "macd" in text:
            return "MACD strategy"
        if "breakout" in text or "high" in text:
            return "Breakout strategy"
        if "liquidity sweep" in text or "sweep" in text:
            return "Liquidity sweep strategy"
        return "My market setup"

    @staticmethod
    def _risk(text: str, guided_setup: GuidedSetupRequest) -> _ParsedRisk:
        stop = guided_setup.maximum_stop_percent
        stop_specified = stop is not None
        stop_match = re.search(
            r"(?:stop|tight stop|stop distance).{0,30}?(\d+(?:\.\d+)?)\s*%", text
        )
        if stop_match:
            stop = float(stop_match.group(1))
            stop_specified = True
        elif "tight stop" in text:
            stop = min(stop or 2, 2)
            stop_specified = True
        reward = guided_setup.minimum_reward_to_risk
        reward_specified = reward is not None
        reward_match = re.search(r"(\d+(?:\.\d+)?)\s*r\b", text)
        if reward_match:
            reward = float(reward_match.group(1))
            reward_specified = True
        rr_match = re.search(r"r:?r.{0,20}?(\d+(?:\.\d+)?)", text)
        if rr_match:
            reward = float(rr_match.group(1))
            reward_specified = True
        if "good r:r" in text or "good risk" in text:
            reward = max(reward or 2, 2)
            reward_specified = True
        enabled = stop_specified or reward_specified
        if enabled:
            stop = stop if stop is not None else 100
            reward = reward if reward is not None else 1
        return _ParsedRisk(
            enabled=enabled,
            maximum_stop_percent=stop if enabled else None,
            minimum_reward_to_risk=reward if enabled else None,
        )

    @staticmethod
    def _exchange(text: str, fallback: str) -> str:
        if "bybit" in text:
            return "bybit"
        if "binance" in text:
            return "binance"
        return fallback.lower()

    @staticmethod
    def _quote_currency(text: str, fallback: str) -> str:
        for quote in ("USDT", "USDC", "BTC", "ETH"):
            if quote.casefold() in text:
                return quote
        return fallback.upper()

    @staticmethod
    def _include_symbols(original_text: str, guided_symbols: list[str], quote: str) -> list[str]:
        if guided_symbols:
            return [symbol.upper().replace("-", "/") for symbol in guided_symbols]
        text = original_text.upper()
        if "MAJORS" in text or "MAJORS ONLY" in text:
            return list(MAJORS)
        symbols = set(re.findall(r"\b[A-Z]{2,12}/(?:USDT|USDC|BTC|ETH)\b", text))
        for asset in ("BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "AVAX", "ADA", "DOGE", "MATIC"):
            if re.search(rf"\b{asset}\b", text) and f"{asset}/{quote}" not in symbols:
                symbols.add(f"{asset}/{quote}")
        return sorted(symbols)

    @staticmethod
    def _exclude_symbols(text: str, quote: str) -> list[str]:
        excluded: set[str] = set()
        if "alts" in text or "altcoins" in text:
            excluded.update({f"BTC/{quote}", f"ETH/{quote}"})
        for match in re.finditer(r"(?:exclude|avoid|ignore)\s+([a-z0-9/, ]{2,80})", text):
            for token in re.findall(r"\b[a-z]{2,12}(?:/(?:usdt|usdc|btc|eth))?\b", match.group(1)):
                if token in {"low", "liquidity", "meme", "memes", "stablecoins"}:
                    continue
                symbol = token.upper()
                if "/" not in symbol:
                    symbol = f"{symbol}/{quote}"
                excluded.add(symbol)
        return sorted(excluded)

    @staticmethod
    def _minimum_quote_volume(text: str) -> float | None:
        match = re.search(r"(?:24h|quote)?\s*volume.{0,30}?(\d+(?:\.\d+)?)\s*([kmb])?", text)
        if not match:
            return None
        value = float(match.group(1))
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2) or "", 1)
        return value * multiplier

    @staticmethod
    def _minimum_average_volume(text: str) -> float | None:
        match = re.search(r"average candle volume.{0,30}?(\d+(?:\.\d+)?)\s*([kmb])?", text)
        if not match:
            return None
        value = float(match.group(1))
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(match.group(2) or "", 1)
        return value * multiplier

    @staticmethod
    def _maximum_spread(text: str) -> float | None:
        match = re.search(r"spread.{0,20}?(\d+(?:\.\d+)?)\s*(?:bps|bp|basis)", text)
        return float(match.group(1)) if match else None

    @staticmethod
    def _mentions_breakout_high(text: str) -> bool:
        high_terms = (
            "all time high",
            "all-time high",
            "ath",
            "new high",
            "day high",
            "month high",
            "highest high",
        )
        return any(
            term in text for term in ("break", "breaking", "above", "crossing", "crosses", "making")
        ) and any(term in text for term in high_terms)

    @staticmethod
    def _lookback_label(text: str) -> str:
        if "6 month" in text or "six month" in text or "6-month" in text:
            return "6-month"
        match = re.search(r"(\d+)[ -]day", text)
        if match:
            return f"{match.group(1)}-day"
        month_match = re.search(r"(?:(\d+)|one|a|last|past)[ -]month", text)
        if month_match:
            months = int(month_match.group(1) or 1)
            return f"{months}-month"
        week_match = re.search(r"(?:(\d+)|one|a|last|past)[ -]week", text)
        if week_match:
            weeks = int(week_match.group(1) or 1)
            return f"{weeks}-week"
        return "lookback"

    @staticmethod
    def _lookback_candles(text: str, timeframe: str) -> int:
        minutes_by_timeframe = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "8h": 480,
            "12h": 720,
            "1d": 1440,
        }
        minutes = minutes_by_timeframe.get(timeframe, 15)
        if "6 month" in text or "six month" in text or "6-month" in text:
            return max(20, min(50_000, int((180 * 24 * 60) / minutes)))
        month_match = re.search(
            r"(?:last|past|previous|over the last|over the past)?\s*"
            r"(?:(\d+)|one|a)?\s*[ -]?months?\b",
            text,
        )
        if month_match:
            months = int(month_match.group(1) or 1)
            return max(20, min(50_000, int((months * 30 * 24 * 60) / minutes)))
        week_match = re.search(
            r"(?:last|past|previous|over the last|over the past)?\s*"
            r"(?:(\d+)|one|a)?\s*[ -]?weeks?\b",
            text,
        )
        if week_match:
            weeks = int(week_match.group(1) or 1)
            return max(20, min(50_000, int((weeks * 7 * 24 * 60) / minutes)))
        day_match = re.search(r"(?:last\s+)?(\d+)[ -]?(?:day|days)", text)
        if day_match:
            return max(20, min(50_000, int((int(day_match.group(1)) * 24 * 60) / minutes)))
        hour_matches = list(
            re.finditer(
                r"(?:(last|past|previous|within the last)\s+)?(\d+)[ -]?(?:hour|hours|h)\b",
                text,
            )
        )
        if hour_matches:
            hour_match = next((match for match in hour_matches if match.group(1)), hour_matches[-1])
            return max(1, min(50_000, int((int(hour_match.group(2)) * 60) / minutes)))
        minute_matches = list(
            re.finditer(
                r"(?:(last|past|previous|within the last)\s+)?"
                r"(\d+)[ -]?(?:minute|minutes|min|mins)\b",
                text,
            )
        )
        if minute_matches:
            minute_match = next(
                (match for match in minute_matches if match.group(1)),
                minute_matches[-1],
            )
            return max(1, min(50_000, int(int(minute_match.group(2)) / minutes)))
        return 100

    @classmethod
    def _event_search_parameters(cls, text: str, timeframe: str) -> dict[str, int | str]:
        parameters: dict[str, int | str] = {}
        historical_event = any(
            phrase in text
            for phrase in (
                "had a",
                "had an",
                "any candle",
                "at any time",
                "during the",
                "over the last",
                "over the past",
                "within the last",
                "last ",
                "past ",
                "did not have any",
                "does not have any",
                "not have any",
                "in 20",
            )
        )
        if not historical_event:
            return parameters
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            year = int(year_match.group(1))
            parameters["search_start"] = datetime(year, 1, 1, tzinfo=UTC).isoformat()
            parameters["search_end"] = datetime(year + 1, 1, 1, tzinfo=UTC).isoformat()
            return parameters
        lookback = cls._lookback_candles(text, timeframe)
        if lookback != 100 or any(
            term in text
            for term in ("past week", "last week", "past month", "last month", "last 30 days")
        ):
            parameters["search_lookback"] = lookback
        return parameters

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        return {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "8h": 480,
            "12h": 720,
            "1d": 1440,
        }.get(timeframe, 15)

    @classmethod
    def _percent_move(cls, text: str, timeframe: str = "15m") -> tuple[str, float, int] | None:
        match = re.search(
            r"(increase|increasing|increased|gain|gains|gained|grow|grows|grew|growth|"
            r"rise|rises|rose|rally|rallies|rallied|jump|jumps|jumped|up|pump|pumped|"
            r"decrease|decreasing|decreased|lose|loses|lost|fall|falls|fell|drop|drops|"
            r"dropped|down|dump|dumped)"
            r".{0,36}?(\d+(?:\.\d+)?)\s*%",
            text,
        )
        if not match:
            return None
        context = text[max(0, match.start() - 32) : min(len(text), match.end() + 24)]
        if "candle" in context and not any(
            term in context for term in ("coin", "price", "symbol", "market", "pair")
        ):
            return None
        direction_word = match.group(1)
        direction = (
            "down"
            if direction_word
            in {
                "decrease",
                "decreasing",
                "decreased",
                "lose",
                "loses",
                "lost",
                "fall",
                "falls",
                "fell",
                "drop",
                "drops",
                "dropped",
                "down",
                "dump",
                "dumped",
            }
            else "up"
        )
        lookback = 1
        minutes = cls._timeframe_minutes(timeframe)
        if any(term in text for term in ("today", "since midnight", "daily move", "this day")):
            lookback = max(1, int((24 * 60) / minutes))
        candle_match = re.search(r"(?:last|past)\s+(\d+)\s*(?:candle|candles|bars)", text)
        if candle_match:
            lookback = max(1, min(5000, int(candle_match.group(1))))
        elif any(
            term in text
            for term in ("past day", "last day", "last 24 hours", "24h", "24 hours")
        ):
            lookback = max(1, int((24 * 60) / minutes))
        elif any(term in text for term in ("past week", "last week", "7 days", "seven days")):
            lookback = max(1, int((7 * 24 * 60) / minutes))
        elif any(term in text for term in ("past month", "last month", "30 days", "thirty days")):
            lookback = max(1, int((30 * 24 * 60) / minutes))
        return direction, float(match.group(2)), lookback

    @staticmethod
    def _time_window_conditions(
        text: str,
        timeframe: str,
    ) -> list[tuple[ConditionRule, str]]:
        windows: list[tuple[str, str, float, float, str]] = []
        if "ny session" in text or "new york session" in text or "ny killzone" in text:
            windows.append(
                (
                    "new_york_session",
                    "Evaluation during New York session",
                    9.5,
                    16,
                    "America/New_York",
                )
            )
        if "midnight" in text:
            timezone = "America/New_York" if "new york" in text or "ny" in text else "UTC"
            windows.append(("near_midnight", "Evaluation near midnight", 23, 1, timezone))
        if "london session" in text or "london killzone" in text:
            windows.append(
                ("london_session", "Evaluation during London session", 8, 16, "Europe/London")
            )
        results: list[tuple[ConditionRule, str]] = []
        for key, label, start_hour, end_hour, timezone in windows:
            results.append(
                (
                    ConditionRule(
                        key=key,
                        label=label,
                        condition_type=ConditionType.MARKET_FILTER,
                        timeframe=timeframe,
                        left=Operand(
                            kind=OperandKind.MARKET_METRIC,
                            name="time_window",
                            parameters={
                                "timezone": timezone,
                                "start_hour": start_hour,
                                "end_hour": end_hour,
                                "lookback": 0,
                            },
                        ),
                        comparator=Comparator.IS_TRUE,
                        weight=0.5,
                        required_data=["candle_timestamp"],
                        explanation_template=(
                            "Signal candle timestamp must fall inside the requested session window."
                        ),
                    ),
                    f"'{label}' is evaluated using candle timestamp in {timezone}.",
                )
            )
        return results

    @staticmethod
    def _detected_categories(text: str) -> list[str]:
        categories = {
            category
            for category, keywords in PROMPT_MECHANIC_CATEGORIES.items()
            if any(keyword in text for keyword in keywords)
        }
        if RuleBasedStrategyInterpreter._percent_move(text) is not None:
            categories.add("percent_move")
            categories.add("price_action")
        if any(term in text for term in ("ema", "sma", "moving average")):
            categories.add("trend")
        if any(term in text for term in ("volume", "liquidity", "vwap", "spread")):
            categories.add("volume_liquidity")
        if any(term in text for term in ("rsi", "macd", "stochastic", "adx")):
            categories.add("momentum")
        if any(
            term in text
            for term in (
                "break of structure",
                "bos",
                "change of character",
                "choch",
            )
        ):
            categories.add("price_action")
        if any(
            term in text
            for term in ("ny session", "new york session", "london session", "midnight")
        ):
            categories.add("session_timing")
        if any(
            term in text
            for term in (
                "all time high",
                "all-time high",
                "ath",
                "6 month high",
                "six month high",
                "6-month high",
            )
        ):
            categories.add("all_time_high")
        return sorted(categories)

    @staticmethod
    def _term_optional(text: str, term: str) -> bool:
        if not any(word in text for word in OPTIONAL_WORDS):
            return False
        index = text.find(term)
        if index < 0:
            return any(word in text for word in OPTIONAL_WORDS) and not any(
                word in text for word in MANDATORY_WORDS
            )
        segment = RuleBasedStrategyInterpreter._clause_around(text, index, index + len(term))
        if any(word in segment for word in OPTIONAL_WORDS):
            return True
        if any(word in segment for word in MANDATORY_WORDS):
            return False
        return False

    @staticmethod
    def _clause_around(text: str, start: int, end: int) -> str:
        left = max(
            text.rfind(",", 0, start),
            text.rfind(";", 0, start),
            text.rfind(".", 0, start),
            text.rfind(" and ", 0, start),
            text.rfind(" but ", 0, start),
        )
        right_candidates = [
            value
            for value in (
                text.find(",", end),
                text.find(";", end),
                text.find(".", end),
                text.find(" and ", end),
                text.find(" but ", end),
            )
            if value >= 0
        ]
        right = min(right_candidates) if right_candidates else len(text)
        return text[left + 1 : right]

    @staticmethod
    def _number_before_after(text: str, term: str, *, default: float) -> float:
        match = re.search(rf"{re.escape(term)}.{{0,30}}?(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        match = re.search(rf"(\d+(?:\.\d+)?).{{0,16}}?{re.escape(term)}", text)
        if match:
            return float(match.group(1))
        return default

    @staticmethod
    def _period_near(text: str, term: str, default: int) -> int:
        match = re.search(
            rf"(\d{{1,3}}).{{0,12}}?{re.escape(term)}|{re.escape(term)}.{{0,12}}?(\d{{1,3}})", text
        )
        if not match:
            return default
        return int(match.group(1) or match.group(2))

    @staticmethod
    def _key(value: str) -> str:
        key = re.sub(r"[^a-z0-9_]+", "_", value.casefold()).strip("_")
        if not key or not key[0].isalpha():
            key = f"condition_{key}"
        return key[:100]
