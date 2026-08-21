import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from ai_market_monitor.db.models.enums import (
    ConditionType,
    LogicalOperator,
    MarketType,
    TriggerMode,
)
from ai_market_monitor.engine.boolean_expression import BooleanNode, parse_boolean_expression
from ai_market_monitor.engine.builder_templates import condition_template
from ai_market_monitor.engine.candle_patterns import pattern_names
from ai_market_monitor.engine.capabilities import capability_prompt_categories
from ai_market_monitor.engine.capability_compatibility import (
    compatibility_by_key,
    prompt_blocked_capabilities,
    prompt_executable_capabilities,
)
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.comparators import comparator_terms, find_comparator
from ai_market_monitor.engine.context_conditions import TIME_CONDITION_NAMES
from ai_market_monitor.engine.formula_compiler import (
    PERCENT_MOVE_OPERANDS,
    FormulaDirection,
    FormulaKind,
    PercentageFormulaSpec,
    compile_explicit_formula_group,
    compile_percentage_formula,
    parse_percentage_formula,
)
from ai_market_monitor.engine.grounded_patch import ungrounded_quantities
from ai_market_monitor.engine.lookback import read_lookback
from ai_market_monitor.engine.numeric_clause import LevelReading, clause_for, read_level
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES
from ai_market_monitor.engine.price_movement import (
    MOVEMENT_PATTERN,
    movement_direction,
    stated_side,
)
from ai_market_monitor.engine.prompt_aliases import (
    find_capability_matches,
    normalized_phrases,
)
from ai_market_monitor.engine.prompt_audit import audit_prompt_coverage
from ai_market_monitor.engine.prompt_semantics import analyze_prompt_semantics
from ai_market_monitor.engine.turn_fragments import (
    classify_fragment,
    classify_turn,
    extract_timeframe_roles,
    normalize_symbol,
    split_symbol,
    to_pair,
)
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

#: Regex alternation over every recognised comparison phrase, in the shared
#: longest-first order plus the symbolic forms. Built from one vocabulary so a phrase
#: cannot be understood by the classifier yet invisible to the compiler's own
#: patterns, and so `no less than` can never be matched as the `less than` inside it.
RELATION_PATTERN = "|".join(
    [*(re.escape(term) for term in comparator_terms()), ">=", "<=", ">", "<", "=="]
)

#: Plain wording for each comparator, used in rule labels so a reader can tell an
#: inclusive bound from a strict one without decoding the enum.
COMPARATOR_WORDS: dict[Comparator, str] = {
    Comparator.GREATER_THAN: "above",
    Comparator.GREATER_THAN_OR_EQUAL: "at or above",
    Comparator.LESS_THAN: "below",
    Comparator.LESS_THAN_OR_EQUAL: "at or below",
    Comparator.EQUAL: "equal to",
    Comparator.CROSSES_ABOVE: "crosses above",
    Comparator.CROSSES_BELOW: "crosses below",
    Comparator.IS_TRUE: "is true",
    Comparator.IS_FALSE: "is false",
}

PROMPT_MECHANIC_CATEGORIES = capability_prompt_categories()

def names_indicator(text: str, term: str) -> bool:
    """Whether ``text`` names ``term`` as a word rather than as a substring.

    ``"rsi" in text`` also matched ``version``, ``reversion`` and ``diversify``, so a
    conversation about rolling back to a previous *version* raised a blocking "RSI
    needs a level" finding for an indicator nobody had mentioned. Short indicator
    names sit inside ordinary English words, so every mention test needs a boundary.
    """
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text, re.IGNORECASE) is not None


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


#: Words that carry no requirement. Traders restate a point with different filler
#: each time — "again, a 5% move", "so the 5% move", "like I said, 5% move" — and the
#: requirement underneath is identical.
_FINDING_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "so", "then", "also", "again", "just",
        "like", "said", "i", "me", "my", "we", "our", "you", "your", "it", "its",
        "this", "that", "these", "those", "is", "are", "was", "were", "be", "been",
        "to", "of", "for", "on", "in", "at", "with", "as", "want", "need", "please",
        "ok", "okay", "now", "still", "yeah", "yes", "no", "nah", "confirm", "again's",
    }
)


def _finding_fingerprint(issue: InterpretationIssue) -> tuple[str, frozenset[str]]:
    """What a finding is *about*, ignoring how it was phrased.

    Findings are raised against the accumulated conversation, so a requirement the
    compiler cannot convert is reported once per restatement. Run
    `20260726T171424Z` ended with 197 findings covering far fewer real problems, and
    a trader answering one of them saw the count barely move.

    Two findings match when they share a code and the same significant words. Filler
    and word order are ignored; numbers, symbols and market terms are not, so
    `bullish 5%` and `bearish 5%` stay separate.
    """
    text = (issue.source_fragment or issue.message or "").casefold()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?%?", text)
        if token not in _FINDING_FILLER_WORDS
    }
    return issue.code, frozenset(tokens)


def _deduplicate_findings(issues: list[InterpretationIssue]) -> list[InterpretationIssue]:
    """Keep the first phrasing of each distinct problem, drop the restatements."""
    seen: set[tuple[str, frozenset[str]]] = set()
    unique: list[InterpretationIssue] = []
    for issue in issues:
        fingerprint = _finding_fingerprint(issue)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(issue)
    return unique


def _within_field_domain(model: type[BaseModel], field: str, value: float) -> bool:
    """Whether ``value`` satisfies the numeric bounds ``model.field`` declares.

    Read from the schema rather than copied, so a bound can never drift out of sync
    with the model that enforces it. A value outside the domain must be refused by
    the caller: passing it on raises a ValidationError deep inside model construction,
    which surfaces to the trader as an internal error on the whole turn.
    """
    for constraint in model.model_fields[field].metadata:
        for attribute, ok in (
            ("gt", lambda bound: value > bound),
            ("ge", lambda bound: value >= bound),
            ("lt", lambda bound: value < bound),
            ("le", lambda bound: value <= bound),
        ):
            bound = getattr(constraint, attribute, None)
            if bound is not None and not ok(bound):
                return False
    return True


@dataclass(frozen=True, slots=True)
class _ParsedRisk:
    enabled: bool
    maximum_stop_percent: float | None
    minimum_reward_to_risk: float | None
    #: Readings refused for being outside the schema's domain, surfaced rather than
    #: silently dropped.
    rejected: tuple[str, ...] = ()


class RuleBasedStrategyInterpreter:
    deterministic_core_authority = True

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

        # A multi-turn session resolves fields in the order the user stated them, so a
        # correction supersedes what it replaced. The accumulated text still contains
        # both statements; re-parsing it would let the superseded wording win.
        settled = guided_setup.resolved_state or {}

        # `4h context and a 1h trigger` must compile with 1h as the timeframe rules
        # fire on and 4h as supporting context. Taking whichever timeframe appeared
        # first silently inverted the two and dropped the trigger entirely.
        timeframe_roles = extract_timeframe_roles(original_text)
        base_timeframe = (
            self._settled_timeframe(settled.get("base_timeframe"))
            or timeframe_roles.trigger
            or self._timeframe_from_text(text, guided_setup.timeframe)
        )
        direction = self._settled_direction(settled.get("direction")) or self._direction(text)
        risk = self._risk(text, guided_setup)
        exchange = self._exchange(text, guided_setup.exchange)
        quote_currency = self._quote_currency(text, guided_setup.quote_currency)
        settled_include = self._settled_symbols(settled.get("include_symbols"))
        settled_exclude = self._settled_symbols(settled.get("exclude_symbols"))
        include_symbols = settled_include or self._include_symbols(
            original_text, guided_setup.symbols, quote_currency
        )
        exclude_symbols = settled_exclude or self._exclude_symbols(text, quote_currency)
        conditions: list[ConditionRule] = []
        condition_groups: list[ConditionGroup] = []
        assumptions: list[str] = []
        unsupported: list[InterpretationIssue] = []
        # INV-05: the watch list and the ignore list can never share a symbol.
        # Exclusion only won when it came from settled state, so a symbol excluded in
        # the current turn stayed in `include_symbols` — `threshold_mapping-001`
        # monitored BTCUSDT after the trader had excluded it. Exclusion always wins:
        # monitoring something the trader ruled out is the unsafe direction.
        excluded = set(exclude_symbols)
        contested = [symbol for symbol in include_symbols if symbol in excluded]
        include_symbols = [item for item in include_symbols if item not in excluded]
        if contested and settled_include:
            # Both sides were settled by the conversation, so this is the trader's own
            # contradiction rather than a symbol the scanner picked up in passing.
            # Surface it instead of silently choosing a side.
            unsupported.append(
                InterpretationIssue(
                    code="universe_include_exclude_conflict",
                    field="setup_text",
                    message=(
                        f"{', '.join(contested)} is both watched and excluded. I left it "
                        "out, because excluding is the safer reading. Tell me which you "
                        "meant."
                    ),
                    blocking=True,
                    source_fragment=", ".join(contested)[:500],
                )
            )
        supporting_timeframes: set[str] = set()
        resolver = CapabilityResolver()
        capability_resolution = resolver.resolve_prompt(original_text)

        def add(condition: ConditionRule | None) -> None:
            if condition is None:
                return
            condition = resolver.bind_known_condition(condition)
            if condition.timeframe != base_timeframe:
                supporting_timeframes.add(condition.timeframe)
            self._append_unique(conditions, condition)

        self._parse_liquidity_sweeps(text, base_timeframe, add, assumptions)
        self._parse_moving_averages(text, base_timeframe, add, assumptions)
        self._parse_momentum(text, base_timeframe, add, unsupported)
        self._parse_bollinger_and_volatility(text, base_timeframe, add)
        self._parse_volume_and_vwap(text, base_timeframe, add)
        self._parse_direct_market_search(text, base_timeframe, add, assumptions)
        explicit_formula_group = compile_explicit_formula_group(
            original_text,
            timeframe=base_timeframe,
        )
        formula_compiled = explicit_formula_group is not None
        if explicit_formula_group is not None:
            condition_groups.append(explicit_formula_group)
        else:
            formula_spec = self._settled_percentage_formula(
                settled,
                base_timeframe=base_timeframe,
                direction=direction,
            ) or parse_percentage_formula(
                original_text,
                default_timeframe=base_timeframe,
                default_direction=direction,
            )
            if formula_spec is not None:
                formula_condition = compile_percentage_formula(formula_spec)
                formula_condition.left.parameters.update(
                    self._event_search_parameters(text, formula_spec.timeframe)
                )
                add(formula_condition)
                formula_compiled = True
        self._parse_price_action(
            text,
            base_timeframe,
            add,
            assumptions,
            unsupported,
            skip_percent_move=formula_compiled,
        )
        self._parse_candles(text, base_timeframe, add)
        self._parse_extended_capabilities(text, base_timeframe, add, conditions)
        semantic_result = analyze_prompt_semantics(original_text, base_timeframe)
        for condition in semantic_result.conditions:
            if any(self._conditions_equivalent(existing, condition) for existing in conditions):
                continue
            # The formula compiler and the semantic parser both recognise a percentage
            # move, and they build it with different operands — so `rose at least 3%
            # today` compiled *two* conditions for one requirement and joined them with
            # AND. `_conditions_equivalent` could not see it: the operands differ in
            # name even though they state the same thing.
            if formula_compiled and condition.left.name in PERCENT_MOVE_OPERANDS:
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

        for binding in guided_setup.capability_bindings:
            try:
                bound = resolver.validate_selection(
                    capability_key=str(binding.get("capability_key") or ""),
                    parameters=dict(binding.get("parameters") or {}),
                    timeframe=str(binding.get("timeframe") or base_timeframe),
                    required=bool(binding.get("required", True)),
                    source_fragment=str(binding.get("source_fragment") or original_text),
                    confidence=float(binding.get("confidence") or 0.9),
                )
            except (TypeError, ValueError) as exc:
                unsupported.append(
                    InterpretationIssue(
                        code="capability_binding_invalid",
                        field="setup_text",
                        message=str(exc),
                        blocking=True,
                        source_fragment=str(binding.get("source_fragment") or original_text),
                    )
                )
            else:
                if not any(self._conditions_equivalent(existing, bound) for existing in conditions):
                    add(bound)

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

        # INV-08: if the trader wrote an OR or used brackets, the compiled strategy
        # must have that shape. Every condition above is joined with AND, so
        # `(A or B) and C` became `A and B and C` — a different rule, firing on
        # different markets, in an artifact that passes schema validation.
        #
        # The shape is now rebuilt rather than refused: each branch of the expression
        # is compiled on its own text and reassembled into the `or`/`and`/`not` group
        # the trader wrote. `or` is a first-class operator in the schema and in the
        # evaluator; only the compiler never emitted one.
        requested_shape = parse_boolean_expression(original_text)
        shape_group: ConditionGroup | None = None
        unbuildable_branches: tuple[str, ...] = ()
        if requested_shape is not None and "or(" in requested_shape.shape():
            shape_group, unbuildable_branches = self._compile_boolean_shape(
                requested_shape,
                base_timeframe=base_timeframe,
                direction=direction,
                resolver=resolver,
            )
        if shape_group is not None:
            shape_rules = self._group_rules(shape_group)
            # The group now owns these rules. Leaving the flat copies behind would
            # AND them back in beside the group, so `A or B` would still require both.
            conditions[:] = [
                condition
                for condition in conditions
                if not any(self._conditions_equivalent(condition, rule) for rule in shape_rules)
            ]
            for rule in shape_rules:
                if rule.timeframe != base_timeframe:
                    supporting_timeframes.add(rule.timeframe)
            condition_groups.append(shape_group)

        # INV-06: a rule fires on the trigger timeframe. Context timeframes select and
        # filter; they never fire. `contradiction_resolution-001` compiled its +5% rule
        # on `1d` after the trader said "trigger evaluated on 15m only" — the timeframe
        # had been inferred from elsewhere in the message and silently outranked the
        # role they stated.
        timeframe_scope = [
            *conditions,
            *(rule for group in condition_groups for rule in self._group_rules(group)),
        ]
        for note in self._enforce_timeframe_roles(original_text, timeframe_scope, base_timeframe):
            if note not in assumptions:
                assumptions.append(note)

        # Fail closed on the part that could not be built. A branch that compiles
        # nothing cannot be silently dropped: the group would then fire on fewer
        # alternatives than the trader asked for, which is a different rule again.
        if requested_shape is not None and not self._shape_is_preserved(
            requested_shape, condition_groups
        ):
            branches = "; ".join(unbuildable_branches[:3])
            detail = (
                f" I could not turn this part into a rule: {branches}."
                if branches
                else " I could not turn every part of it into a rule."
            )
            unsupported.append(
                InterpretationIssue(
                    code="boolean_grouping_not_preserved",
                    field="setup_text",
                    message=(
                        "You grouped these rules with 'or' or brackets."
                        f"{detail} Combining them with 'and' instead would fire on "
                        "different markets than you asked for, so I stopped. Restate "
                        "that part, or split it into separate setups."
                    ),
                    blocking=True,
                    source_fragment=original_text[:500],
                )
            )

        min_quote_volume = self._minimum_quote_volume(text)
        min_average_volume = self._minimum_average_volume(text)
        max_spread = self._maximum_spread(text)
        if "avoid low liquidity" in text and min_quote_volume is None:
            min_quote_volume = 1_000_000
            assumptions.append(
                "Avoiding low liquidity defaults to minimum 24h quote volume of 1,000,000."
            )

        if not conditions and not condition_groups:
            unsupported.append(
                InterpretationIssue(
                    code="no_supported_monitor_condition",
                    field="setup_text",
                    message=(
                        "No supported deterministic monitor condition was recognized. Clarify the "
                        "indicator, price-action event, timeframe, comparator, and threshold."
                    ),
                    blocking=True,
                    source_fragment=original_text[:500],
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
            assumptions.append(f"Stop distance must be under {risk.maximum_stop_percent:g}%.")
        if risk.minimum_reward_to_risk is not None:
            assumptions.append(f"Reward-to-risk must be at least {risk.minimum_reward_to_risk:g}R.")
        for refused in risk.rejected:
            unsupported.append(
                InterpretationIssue(
                    code="risk_value_out_of_range",
                    field="setup_text",
                    message=(
                        f"I read a {refused}, which is outside the range this platform "
                        "can monitor, so I did not apply it. Restate it if you meant it."
                    ),
                    blocking=False,
                    source_fragment=original_text[:500],
                )
            )

        root_children: list[ConditionRule | ConditionGroup] = [
            *conditions,
            *condition_groups,
        ]
        root = ConditionGroup(
            key="entry_conditions",
            operator=LogicalOperator.AND,
            children=root_children,
        )
        definition = StrategyDefinition(
            name=self._strategy_name(text),
            description=(guided_setup.setup_text or "")[:2000],
            direction=direction,
            base_timeframe=base_timeframe,
            # A timeframe the trader named as context is part of the strategy even
            # when no condition happened to compile onto it.
            supporting_timeframes=sorted(
                (
                    supporting_timeframes
                    | set(timeframe_roles.context)
                    | {
                        value
                        for value in (
                            self._settled_timeframe(item)
                            for item in (settled.get("context_timeframes") or ())
                        )
                        if value
                    }
                )
                - {base_timeframe}
            ),
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
                channels=[channel for channel in guided_setup.delivery_channels],
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
                condition.left.name == "break_and_retest_confirmed" for condition in conditions
            ):
                for condition in conditions:
                    if condition.left.name == "break_and_retest_confirmed":
                        condition.source_fragment = original_text[:500]
                        coverage_dirty = True
                continue
            # Coverage gaps are reported against *trading* instructions. A question,
            # an approval rule, a labelling policy or a rollback request has no
            # market mechanic to cover, so reporting it here raised a blocking
            # finding the trader could never clear — the single largest cause of
            # drafts stuck in `needs_clarification`.
            if classify_fragment(fragment).category != "TRADING_MECHANIC":
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
            unsupported_conditions=_deduplicate_findings(unsupported),
            interpreter=self.name,
            raw_metadata={
                "detected_categories": detected_categories,
                "capability_registry": "engine.condition_registry",
                "deterministic_evaluation_required": True,
                "prompt_coverage_report": coverage.model_dump(mode="json"),
                "prompt_semantics": semantic_result.metadata(),
                "capability_resolution": capability_resolution.to_dict(),
            },
        )

    @staticmethod
    def _settled_percentage_formula(
        settled: dict[str, Any],
        *,
        base_timeframe: str,
        direction: StrategyDirection,
    ) -> PercentageFormulaSpec | None:
        payload = settled.get("formula")
        if not isinstance(payload, dict):
            return None
        try:
            formula_direction = str(payload["direction"])
            if formula_direction != "signed" and settled.get("direction") is not None:
                formula_direction = "down" if direction is StrategyDirection.SHORT else "up"
            comparator_value = settled.get("comparator") or payload["comparator"]
            threshold = settled.get("threshold")
            return PercentageFormulaSpec(
                formula=cast(FormulaKind, payload["formula"]),
                direction=cast(FormulaDirection, formula_direction),
                comparator=Comparator(str(comparator_value)),
                threshold_percent=float(
                    payload["threshold_percent"] if threshold is None else threshold
                ),
                timeframe=base_timeframe,
                reference_timeframe=payload.get("reference_timeframe"),
                reference_field=str(payload["reference_field"]),
                current_field=str(payload["current_field"]),
                lookback=int(payload.get("lookback") or 1),
                source_fragment=str(payload.get("source_fragment") or "")[:500],
            )
        except (KeyError, TypeError, ValueError):
            return None

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
            # `condition_template` copies the capability's example values. Those exist
            # so the builder UI has something to show; shipping them as a monitoring
            # rule states a size the trader never gave. `alert me on a dump this week`
            # matched the alias `dump` and compiled "price up 5%" — the size and the
            # side both invented.
            #
            # Prefer the size the trader did state; only refuse when they stated none.
            # Refusing outright would have dropped `coins that pumped 8%` too, because
            # the template's own 5 is ungrounded there — punishing a trader for the
            # catalogue's example rather than for anything they wrote.
            template_parameters = dict(payload.get("left", {}).get("parameters") or {})
            invented = ungrounded_quantities(template_parameters, text)
            if invented:
                stated_percent = self._stated_percent_near(text, match.start, match.end)
                if stated_percent is None:
                    continue
                for name in invented:
                    payload["left"]["parameters"][name] = stated_percent
                template_parameters = dict(payload["left"]["parameters"])
            # The trader's own wording owns the side. The template carries the
            # catalogue's, which is `up` for a capability that covers both.
            stated_move = movement_direction(snippet)
            if stated_move is not None and "direction" in template_parameters:
                side = "down" if stated_move == "down" else "up"
                payload["left"]["parameters"]["direction"] = side
                # Some operands carry the side in their *name* as well
                # (`percent_change_up` / `percent_change_down`). Setting only the
                # parameter left the name contradicting it, and readers that trust the
                # name — the evaluator's dispatch, the coverage audit, the UI label —
                # would still have shown a rise for a fall.
                payload["left"]["name"] = self._operand_for_side(
                    str(payload["left"].get("name") or ""), side
                )
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
    def _operand_for_side(name: str, side: str) -> str:
        """The ``_up``/``_down`` variant of ``name`` matching ``side``.

        An operand that spells its side into its own name has two places to keep in
        step. Returns ``name`` unchanged when it carries no side, so this is safe to
        apply to every operand rather than to a list of known ones.
        """
        for suffix, opposite in (("_up", "_down"), ("_down", "_up")):
            if name.endswith(suffix):
                wanted = "_up" if side == "up" else "_down"
                return name if suffix == wanted else name[: -len(suffix)] + opposite
        return name

    @staticmethod
    def _stated_percent_near(text: str, start: int, end: int) -> float | None:
        """The percentage the trader wrote for this mechanic, if they wrote one.

        Read from the clause that owns the match rather than from a character window
        over the whole message — the same resolution rule the comparator and direction
        readers follow. A window scan is what let a `1.0%` from a different clause
        supply the size for this one.
        """
        clause = RuleBasedStrategyInterpreter._matched_clause(text, start, end)
        matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", clause)
        if len(matches) != 1:
            # No number, or more than one and no way to tell which governs this
            # mechanic. Both are "the trader did not state it *here*".
            return None
        return float(matches[0])

    @staticmethod
    def _matched_clause(text: str, start: int, end: int) -> str:
        left_edges = [
            index + 1
            for separator in (".", ";", "\n", ",")
            if (index := text.rfind(separator, 0, start)) != -1
        ]
        left_edges.extend(
            match.end()
            for match in re.finditer(
                r"\s+\band\b\s+|\s+\bthen\b\s+|\s+\balso\b\s+",
                text[:start],
                re.I,
            )
        )
        left = max(left_edges, default=0)
        right_candidates = [
            index
            for separator in (".", ";", "\n", ",")
            if (index := text.find(separator, end)) != -1
        ]
        right_candidates.extend(
            end + match.start()
            for match in re.finditer(
                r"\s+\band\b\s+|\s+\bthen\b\s+|\s+\balso\b\s+",
                text[end:],
                re.I,
            )
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
        # PDL/PDH are common shorthand for the previous UTC daily low/high. Handle them
        # before the generic 20-candle sweep parser so their reference level stays exact.
        previous_daily_low = re.search(
            r"\b(?:pdl|previous\s+(?:day|daily)\s+low|prior\s+(?:day|daily)\s+low)\b",
            text,
        )
        previous_daily_high = re.search(
            r"\b(?:pdh|previous\s+(?:day|daily)\s+high|prior\s+(?:day|daily)\s+high)\b",
            text,
        )
        sweep_verb = re.search(r"\b(?:swept|sweep(?:ed|s|ing)?)\b", text)
        if sweep_verb and (previous_daily_low or previous_daily_high):
            is_low = previous_daily_low is not None
            add(
                self._price_action(
                    "previous_daily_low_sweep" if is_low else "previous_daily_high_sweep",
                    (
                        "Previous daily low swept and reclaimed"
                        if is_low
                        else "Previous daily high swept and reclaimed"
                    ),
                    timeframe,
                    "daily_low_swept" if is_low else "daily_high_swept",
                    parameters={"timezone": "UTC"},
                    weight=2,
                    forming_tolerance_percent=15,
                    required=not self._term_optional(text, "sweep"),
                )
            )
            assumptions.append(
                "PDL is interpreted as the previous UTC daily low; the candle must trade "
                "below it and close back above it."
                if is_low
                else "PDH is interpreted as the previous UTC daily high; the candle must trade "
                "above it and close back below it."
            )
            return
        reference_period = re.search(
            r"\b(?:previous|prior|last)\s+(day|daily|week|weekly|month|monthly)(?:\s+candle)?\b",
            text,
        )
        period_side = re.search(r"\b(high|low)\b", text)
        if sweep_verb and reference_period and period_side:
            period = {
                "daily": "day",
                "weekly": "week",
                "monthly": "month",
            }.get(reference_period.group(1), reference_period.group(1))
            side = period_side.group(1)
            add(
                self._price_action(
                    "reference_period_sweep",
                    f"Previous {period} {side} swept and reclaimed",
                    timeframe,
                    "reference_period_sweep",
                    parameters={
                        "reference_period": period,
                        "side": side,
                        "timezone": "UTC",
                    },
                    weight=2,
                    forming_tolerance_percent=15,
                    required=not self._term_optional(text, "sweep"),
                ).model_copy(update={"capability_key": "reference_period_sweep"})
            )
            assumptions.append(
                f"The previous completed UTC {period} {side} is the reference; price must "
                "trade beyond it and close back through it."
            )
            return
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
            "buy_side_liquidity_sweep" if direction == "bearish" else "sell_side_liquidity_sweep"
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
            comparator = self._ma_relation_comparator(text, span_start, relation)
            label_relation = COMPARATOR_WORDS[comparator]
            add(
                self._price_vs_indicator(
                    f"price_{self._comparator_slug(comparator)}_{ma_timeframe}_{average}_{period}",
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

    def _parse_momentum(
        self,
        text: str,
        timeframe: str,
        add,
        unsupported: list[InterpretationIssue] | None = None,
    ) -> None:
        rsi_cross = re.search(
            r"rsi.{0,30}?cross(?:es)?(?: back)?\s+(above|below)\s+(\d{1,3}(?:\.\d+)?)", text
        )
        if rsi_cross:
            cross_level = float(rsi_cross.group(2))
            add(
                self._indicator_constant(
                    f"rsi_cross_{rsi_cross.group(1)}_{self._level_slug(cross_level)}",
                    f"RSI crosses {rsi_cross.group(1)} {cross_level:g}",
                    timeframe,
                    "rsi",
                    Comparator.CROSSES_ABOVE
                    if rsi_cross.group(1) == "above"
                    else Comparator.CROSSES_BELOW,
                    cross_level,
                    {"period": 14, "field": "close"},
                    forming_tolerance_percent=10,
                )
            )
        elif "rsi exits oversold" in text or (
            names_indicator(text, "rsi")
            and (
                ("oversold" in text and ("exit" in text or "cross" in text or "back above" in text))
                # `RSI recovering` and `RSI turning up` are the same mechanic stated
                # without the word oversold: momentum leaving the low band.
                or self._RSI_RECOVERY_RE.search(text) is not None
            )
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
        elif names_indicator(text, "rsi"):
            reading = self._bounded_level(text, "rsi", minimum=0.0, maximum=100.0)
            level: tuple[Comparator, float] | None = (
                (reading.comparator, reading.value) if reading else None
            )
            if level is None and "overbought" in text:
                level = (Comparator.GREATER_THAN_OR_EQUAL, 70.0)
            elif level is None and "oversold" in text:
                level = (Comparator.LESS_THAN_OR_EQUAL, 30.0)
            if level is None:
                # Fail closed. The previous default was `RSI >= 50`, which turned
                # `RSI at most 30` into the opposite rule and invented a level for a
                # bare mention of RSI. A level the trader never gave is not a rule.
                if unsupported is not None:
                    unsupported.append(
                        InterpretationIssue(
                            code="rsi_level_required",
                            field="setup_text",
                            message=(
                                "RSI was requested without a level and comparison. State "
                                "the level, for example 'RSI below 30' or 'RSI at least "
                                "70'."
                            ),
                            blocking=True,
                            source_fragment=clause_for(text, "rsi") or text,
                        )
                    )
            else:
                comparator, threshold = level
                add(
                    self._indicator_constant(
                        f"rsi_{self._comparator_slug(comparator)}_{self._level_slug(threshold)}",
                        f"RSI {COMPARATOR_WORDS[comparator]} {threshold:g}",
                        timeframe,
                        "rsi",
                        comparator,
                        threshold,
                        {"period": 14, "field": "close"},
                        forming_tolerance_percent=10,
                        required=not self._term_optional(text, "rsi"),
                    )
                )

        if names_indicator(text, "macd"):
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
            atr_percent = self._bounded_level(
                text,
                "atr",
                default_comparator=Comparator.GREATER_THAN_OR_EQUAL,
                minimum=0.0,
            )
            if atr_percent is not None:
                add(
                    self._indicator_constant(
                        "atr_percent_threshold",
                        f"ATR percent {COMPARATOR_WORDS[atr_percent.comparator]} "
                        f"{atr_percent.value:g}%",
                        timeframe,
                        "atr_percent",
                        atr_percent.comparator,
                        atr_percent.value,
                        {"period": 14},
                    )
                )
        elif names_indicator(text, "atr") and "stop" not in text:
            atr_level = self._bounded_level(
                text,
                "atr",
                default_comparator=Comparator.GREATER_THAN_OR_EQUAL,
                minimum=0.0,
            )
            if atr_level is not None:
                add(
                    self._indicator_constant(
                        "atr_threshold",
                        f"ATR {COMPARATOR_WORDS[atr_level.comparator]} {atr_level.value:g}",
                        timeframe,
                        "atr",
                        atr_level.comparator,
                        atr_level.value,
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
        # `volume.*?` reached across the whole prompt and discarded the operator it
        # matched, so `volume at most 2x average` compiled as `>= 2x`. The shared
        # reader stays inside the volume clause and honours the stated comparison;
        # a bare `2x average` keeps the documented "at least" convention.
        volume_reading = self._bounded_level(
            text,
            "volume",
            default_comparator=Comparator.GREATER_THAN_OR_EQUAL,
            require_unit="multiple",
            minimum=0.0,
        )
        if volume_reading is not None:
            add(
                self._volume_ratio(timeframe, volume_reading.value, volume_reading.comparator, text)
            )
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
                        *self._level_or_default(
                            text,
                            "deviation",
                            comparator=Comparator.LESS_THAN_OR_EQUAL,
                            value=2.0,
                        ),
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
            rf"\b({RELATION_PATTERN})\b"
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
        relation_pattern = RELATION_PATTERN
        number_pattern = r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:usd|usdt|dollars?|\$)?"
        field_pattern = r"(price|close|closing price|current price|last price|high|low|open)"
        patterns = [
            rf"\b{field_pattern}\b[^.?,;]{{0,35}}?\b({relation_pattern})\b"
            rf"[^.?,;]{{0,20}}?{number_pattern}",
            rf"\b({relation_pattern})\b[^.?,;]{{0,20}}?{number_pattern}"
            r"[^.?,;]{0,25}?\b(price|close|current price|last price|usd|usdt|dollars?)\b",
            rf"\b(?:symbols|coins|pairs|markets)\b[^.?,;]{{0,35}}?"
            rf"\b({relation_pattern})\b[^.?,;]{{0,20}}?{number_pattern}",
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
                    term in segment or term in context
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
        """Map captured relation wording through the shared operator vocabulary.

        The previous hand-written table understood four phrases and silently
        returned `>=` for everything else, so any wording it did not list became an
        inclusive lower bound regardless of what the trader wrote.
        """
        normalized = relation.casefold().strip()
        symbols = {
            ">=": Comparator.GREATER_THAN_OR_EQUAL,
            "<=": Comparator.LESS_THAN_OR_EQUAL,
            ">": Comparator.GREATER_THAN,
            "<": Comparator.LESS_THAN,
            "=": Comparator.EQUAL,
            "==": Comparator.EQUAL,
        }
        if normalized in symbols:
            return symbols[normalized]
        found = find_comparator(normalized)
        if found is not None:
            return found[0]
        return Comparator.GREATER_THAN_OR_EQUAL

    def _parse_price_action(
        self,
        text: str,
        timeframe: str,
        add,
        assumptions: list[str],
        unsupported: list[InterpretationIssue] | None = None,
        *,
        skip_percent_move: bool = False,
    ) -> None:
        if self._mentions_breakout_high(text):
            # The window is read from the breakout clause, not from the whole message.
            # A whole-text scan took `prior day` out of an unrelated sentence about a
            # close comparison and made it the breakout's window — the same
            # nearest-clause rule the comparator and direction readers follow.
            breakout_clause = self._breakout_clause(text)
            lookback = self._lookback_candles(breakout_clause, timeframe)
            label_window = self._lookback_label(breakout_clause)
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
        if re.search(
            r"\b(?:support\s+retest|retest(?:s|ed)?\s+(?:the\s+)?support|"
            r"bounce(?:s|d)?\s+(?:from|off)\s+(?:the\s+)?support|"
            r"hold(?:s|ing)?\s+(?:the\s+)?support)\b",
            text,
        ):
            add(
                self._price_action(
                    "support_retest",
                    "Support retest bounce",
                    timeframe,
                    "price_bounces_from_support",
                    {"lookback": 20},
                )
            )
        if re.search(
            r"\b(?:resistance\s+retest|retest(?:s|ed)?\s+(?:the\s+)?resistance|"
            r"reject(?:s|ed|ion)?\s+(?:from|at)\s+(?:the\s+)?resistance)\b",
            text,
        ):
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
                    *self._level_or_default(
                        text,
                        "pullback",
                        comparator=Comparator.LESS_THAN_OR_EQUAL,
                        value=50.0,
                    ),
                    {
                        "lookback": 20,
                        "direction": "short" if "short" in text or "bearish" in text else "long",
                    },
                )
            )
        percent_move = None if skip_percent_move else self._percent_move(text, timeframe)
        if percent_move is not None:
            direction, threshold, lookback, comparator = percent_move
            if comparator in {Comparator.LESS_THAN, Comparator.LESS_THAN_OR_EQUAL}:
                # An upper bound *is* representable — just not by the boolean
                # `percent_change_up` operand, whose name fixes its comparison at "at
                # least". The numeric `percentage_change` operand carries the
                # comparison on the condition instead, and the evaluator already
                # reports a fall as a positive magnitude when `direction` is `down`,
                # so `<= 2.5` reads as "fell by no more than 2.5%".
                #
                # Refusing here was a false fail-closed: the platform could express
                # this all along, and the trader was told to rewrite a rule that
                # needed no rewriting. Fail closed is for meaning that *cannot* be
                # represented.
                add(
                    compile_percentage_formula(
                        PercentageFormulaSpec(
                            formula="close_to_close",
                            direction=cast(FormulaDirection, direction),
                            comparator=comparator,
                            threshold_percent=threshold,
                            timeframe=timeframe,
                            reference_timeframe=timeframe,
                            reference_field="close",
                            current_field="close",
                            lookback=lookback,
                            source_fragment=(clause_for(text, "%") or text)[:500],
                        ),
                        key=f"price_{direction}_at_most_{str(threshold).replace('.', '_')}pct",
                    )
                )
                assumptions.append(
                    f"The {direction} move must stay at or under {threshold:g}%, measured "
                    f"from the close {lookback} candle(s) ago on {timeframe}."
                )
            else:
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
                    f"Percentage move is measured from the close {lookback} candle(s) ago "
                    f"to the current signal close on {timeframe}."
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
        for candle_match in self._consecutive_candle_matches(text, timeframe):
            count, color, candle_timeframe = candle_match
            component = (
                "consecutive_bullish" if color in {"green", "bullish"} else "consecutive_bearish"
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
        for color_match in color_matches:
            color = color_match.group(3)
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
                color_match.group(2) or color_match.group(4) or timeframe
            )
            offset = 1 if color_match.group(1) else 0
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
            move_parameters: dict[str, int | float | str] = {
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
                    move_parameters,
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
            elif capability.key in {"market_cap_minimum", "meme_coin_exclusion"}:
                code = "external_data_required"
                reason = f"{capability.label} requires external market metadata before activation."
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
            return any(
                "time_window" in value or "session" in value or "midnight" in value
                for value in haystacks
            )
        if capability_key in {
            "range_breakout",
            "range_breakdown",
            "new_n_day_high",
            "new_n_day_low",
        }:
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
            "head and shoulders",
            "head & shoulders",
            "head and sholders",
            "head & sholders",
            "neckline",
            "double top",
            "double bottom",
            "ascending triangle",
            "descending triangle",
            "symmetrical triangle",
            "symmetric triangle",
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
            # Only wording that carries a market mechanic can be an *unconverted
            # trading instruction*. Approval gating, Sharia and labelling policy,
            # reversions, symbols, timeframes and conversation controls are handled
            # by other parts of the pipeline; reporting them here produced blocking
            # findings that no clarification could ever resolve, which left every
            # affected draft permanently ineligible for approval.
            if classify_fragment(display).category != "TRADING_MECHANIC":
                continue
            # Ask the compiler, not a keyword list, whether this was convertible. A
            # fully specified `%move >= 7.5 direction=long operator=gte` was reported
            # as unconvertible only because those spellings were missing from the
            # list; the formula compiler had understood it all along.
            if (
                parse_percentage_formula(
                    display,
                    default_timeframe="15m",
                    default_direction=StrategyDirection.LONG,
                )
                is not None
            ):
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
                    # Reaching here means the fragment *is* a market instruction the
                    # compiler could not convert, so it blocks. Vague wording such as
                    # "only if the chart feels unusually optimistic" names no
                    # measurable quantity, and that is exactly why it must be defined
                    # before the setup can run — not a reason to wave it through.
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

    #: RSI leaving the low band, stated without the word `oversold`.
    _RSI_RECOVERY_RE = re.compile(
        r"\brsi\b[^.;]{0,24}?\b(?:recover(?:s|ing|y|ed)?|turning\s+up|turns\s+up|"
        r"bouncing|bounces|rebound(?:s|ing)?)\b"
        r"|\b(?:recover(?:s|ing|y|ed)?|turning\s+up|turns\s+up|bouncing|bounces|"
        r"rebound(?:s|ing)?)\b[^.;]{0,24}?\brsi\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _bounded_level(
        text: str,
        term: str,
        *,
        default_comparator: Comparator | None = None,
        require_unit: Literal["plain", "percent", "multiple"] | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> LevelReading | None:
        """Read a stated comparison and level for ``term`` from its own clause.

        Every indicator that takes a user-supplied level goes through here, so a fix
        to the reading applies to all of them rather than to whichever one was
        reported. Out-of-domain values are rejected rather than clamped: a level the
        indicator cannot take is wording we have not understood.
        """
        reading = read_level(
            text,
            term,
            default_comparator=default_comparator,
            require_unit=require_unit,
        )
        if reading is None:
            return None
        if minimum is not None and reading.value < minimum:
            return None
        if maximum is not None and reading.value > maximum:
            return None
        return reading

    @staticmethod
    def _level_slug(value: float) -> str:
        """A key-safe rendering that keeps decimals distinguishable."""
        return f"{value:g}".replace(".", "_").replace("-", "neg_")

    @staticmethod
    def _comparator_slug(comparator: Comparator) -> str:
        """The established key word for a comparator.

        Keys are part of the visible contract: Canvas node ids, confidence rows and
        the translation sheet all reference them, so the strict/inclusive distinction
        lives in the ``comparator`` field rather than churning every key. A crossing
        does get its own key, because it is a different rule from a plain comparison.
        """
        if comparator is Comparator.CROSSES_ABOVE:
            return "crosses_above"
        if comparator is Comparator.CROSSES_BELOW:
            return "crosses_below"
        if comparator is Comparator.EQUAL:
            return "equal"
        return (
            "above"
            if comparator in {Comparator.GREATER_THAN, Comparator.GREATER_THAN_OR_EQUAL}
            else "below"
        )

    @staticmethod
    def _settled_timeframe(value: Any) -> str | None:
        """Accept a settled timeframe only if the schema actually supports it."""
        if not isinstance(value, str):
            return None
        candidate = value.strip().casefold()
        return candidate if candidate in SUPPORTED_TIMEFRAMES else None

    @staticmethod
    def _settled_direction(value: Any) -> StrategyDirection | None:
        if isinstance(value, StrategyDirection):
            return value
        if not isinstance(value, str):
            return None
        try:
            return StrategyDirection(value.strip().casefold())
        except ValueError:
            return None

    @staticmethod
    def _settled_symbols(value: Any) -> list[str]:
        """Canonicalise settled symbols and drop anything unusable."""
        if not isinstance(value, list | tuple):
            return []
        results: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                continue
            pair = to_pair(item.strip())
            if pair not in results:
                results.append(pair)
        return results

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
        reference_period = text[start:end].casefold() in {
            "daily",
            "weekly",
            "monthly",
            "day",
            "week",
            "month",
        }
        reference_level = bool(
            re.search(r"\b(?:previous|prior|last)\s*$", before)
            and re.match(r"\s*(?:candle\s+)?(?:high|low|level)\b", after)
        )
        if reference_period and reference_level:
            return True
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
    def _ma_relation_comparator(text: str, relation_start: int, relation: str) -> Comparator:
        """Read the whole operator phrase, not just the direction word.

        `price crosses above the 20 EMA` is a transition; `price above the 20 EMA` is a
        state. Matching only `above` collapsed the two, so a crossing alert fired on
        every candle that happened to be above the average.
        """
        window = text[max(0, relation_start - 16) : relation_start + len(relation)]
        found = find_comparator(window)
        if found is not None:
            return found[0]
        return Comparator.GREATER_THAN if relation in {"above", "over"} else Comparator.LESS_THAN

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
        """Which side the strategy watches.

        This used to be a seventh movement vocabulary — a hand-written list of
        `bearish`, `short`, `breakdown`, `reject`. `price moved down 2%` matched none
        of them and fell through to the invented default `LONG`, so a strategy that
        watched for a fall was labelled a long. The shared module already knows
        `down`, `drops`, `dumped`, `نزل` and `yenzel`; asking it first is what stops
        the two readers from disagreeing.
        """
        if "both" in text or "long and short" in text:
            return StrategyDirection.BOTH
        # An explicitly stated side (`direction=short`, `short only`) outranks the
        # movement wording, because it names the side rather than describing a move.
        side = stated_side(text)
        if side is not None:
            return StrategyDirection.SHORT if side == "down" else StrategyDirection.LONG
        movement = movement_direction(text)
        if movement is not None:
            return StrategyDirection.SHORT if movement == "down" else StrategyDirection.LONG
        if any(word in text for word in ("bearish", "short", "sell setup", "breakdown", "reject")):
            return StrategyDirection.SHORT
        return StrategyDirection.LONG

    @staticmethod
    def _strategy_name(text: str) -> str:
        if "vwap" in text:
            return "VWAP strategy"
        if names_indicator(text, "rsi"):
            return "RSI strategy"
        if "bollinger" in text or "squeeze" in text:
            return "Bollinger strategy"
        if names_indicator(text, "macd"):
            return "MACD strategy"
        if "breakout" in text or "high" in text:
            return "Breakout strategy"
        if "liquidity sweep" in text or "sweep" in text:
            return "Liquidity sweep strategy"
        return "My market setup"

    @classmethod
    def _group_rules(cls, node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
        """Every leaf rule inside ``node``, in order."""
        if isinstance(node, ConditionGroup):
            return [rule for child in node.children for rule in cls._group_rules(child)]
        return [node]

    def _compile_branch_conditions(
        self,
        fragment: str,
        *,
        base_timeframe: str,
        direction: StrategyDirection,
        resolver: CapabilityResolver,
    ) -> list[ConditionRule]:
        """Compile one branch of a boolean expression from its own wording alone.

        The same parser battery the whole prompt goes through, run over a single
        branch so the rules it produces can be attached to that branch instead of
        being flattened into one AND with everything else.
        """
        lowered = fragment.casefold()
        produced: list[ConditionRule] = []
        # A branch is a fragment of the prompt. Assumptions and findings are raised
        # once for the prompt as a whole, so the ones repeated here are discarded
        # rather than reported twice.
        notes: list[str] = []
        ignored: list[InterpretationIssue] = []

        def add(condition: ConditionRule | None) -> None:
            if condition is None:
                return
            self._append_unique(produced, resolver.bind_known_condition(condition))

        self._parse_liquidity_sweeps(lowered, base_timeframe, add, notes)
        self._parse_moving_averages(lowered, base_timeframe, add, notes)
        self._parse_momentum(lowered, base_timeframe, add, ignored)
        self._parse_bollinger_and_volatility(lowered, base_timeframe, add)
        self._parse_volume_and_vwap(lowered, base_timeframe, add)
        formula_spec = parse_percentage_formula(
            fragment,
            default_timeframe=base_timeframe,
            default_direction=direction,
        )
        if formula_spec is not None:
            formula_condition = compile_percentage_formula(formula_spec)
            formula_condition.left.parameters.update(
                self._event_search_parameters(lowered, formula_spec.timeframe)
            )
            add(formula_condition)
        self._parse_price_action(
            lowered,
            base_timeframe,
            add,
            notes,
            ignored,
            skip_percent_move=formula_spec is not None,
        )
        self._parse_candles(lowered, base_timeframe, add)
        return produced

    def _compile_boolean_shape(
        self,
        requested: BooleanNode,
        *,
        base_timeframe: str,
        direction: StrategyDirection,
        resolver: CapabilityResolver,
    ) -> tuple[ConditionGroup | None, tuple[str, ...]]:
        """Rebuild the trader's own `or`/`and`/`not` shape as a condition group.

        Returns the group, or ``None`` together with the branches that compiled
        nothing. Refusing on an empty branch is deliberate: an OR that quietly loses
        one of its alternatives fires less often than the trader asked for, which is
        the same silent substitution flattening produced.
        """
        used_keys: set[str] = set()
        unbuildable: list[str] = []
        counter = itertools.count(1)

        def group_key(stem: str) -> str:
            while True:
                candidate = f"{stem}_{next(counter)}"
                if candidate not in used_keys:
                    used_keys.add(candidate)
                    return candidate

        def claim(rule: ConditionRule) -> ConditionRule:
            """Give the rule a key unique across the whole tree.

            Two branches can compile the same mechanic — `RSI below 30 or RSI below
            30 on the 4h` — and duplicate keys would collide in the artifact.
            """
            if rule.key not in used_keys:
                used_keys.add(rule.key)
                return rule
            stem = rule.key[:90]
            index = 2
            while f"{stem}_{index}" in used_keys:
                index += 1
            used_keys.add(f"{stem}_{index}")
            return rule.model_copy(update={"key": f"{stem}_{index}"})

        def build(node: BooleanNode) -> ConditionRule | ConditionGroup | None:
            if node.is_leaf:
                rules = [
                    claim(rule)
                    for rule in self._compile_branch_conditions(
                        node.text,
                        base_timeframe=base_timeframe,
                        direction=direction,
                        resolver=resolver,
                    )
                ]
                if not rules:
                    unbuildable.append(node.text[:120])
                    return None
                if len(rules) == 1:
                    return rules[0]
                # Several rules from one branch are all required by that branch.
                return ConditionGroup(
                    key=group_key("all_of"),
                    operator=LogicalOperator.AND,
                    children=list(rules),
                )
            children: list[ConditionRule | ConditionGroup] = []
            for child in node.children:
                compiled = build(child)
                if compiled is None:
                    return None
                children.append(compiled)
            if node.operator == "not":
                return ConditionGroup(
                    key=group_key("not"),
                    operator=LogicalOperator.NOT,
                    children=children,
                )
            if node.operator == "or":
                return ConditionGroup(
                    key=group_key("any_of"),
                    operator=LogicalOperator.OR,
                    children=children,
                )
            return ConditionGroup(
                key=group_key("all_of"),
                operator=LogicalOperator.AND,
                children=children,
            )

        try:
            root = build(requested)
        except ValidationError:
            # A shape the schema refuses is not shipped in a different shape.
            return None, tuple(unbuildable)
        if root is None or isinstance(root, ConditionRule):
            return None, tuple(unbuildable)
        return root, tuple(unbuildable)

    @staticmethod
    def _shape_is_preserved(
        requested: BooleanNode,
        condition_groups: list[ConditionGroup],
    ) -> bool:
        """Whether some compiled group already carries the requested shape.

        A shape with no OR flattens to AND without loss, so it needs no group of its
        own. Anything containing an OR does: that is exactly the structure the flat
        AND destroys.
        """
        if "or(" not in requested.shape():
            return True

        def holds_an_or(node: ConditionRule | ConditionGroup) -> bool:
            if not isinstance(node, ConditionGroup):
                return False
            if node.operator is LogicalOperator.OR:
                return True
            # Recursive, because `(A or B) and (C or D)` nests the OR two levels down
            # and a fixed one-level check reported a group it had actually built.
            return any(holds_an_or(child) for child in node.children)

        return any(holds_an_or(group) for group in condition_groups)

    @staticmethod
    def _enforce_timeframe_roles(
        original_text: str,
        conditions: list[ConditionRule],
        base_timeframe: str,
    ) -> list[str]:
        """Move rules onto the stated trigger timeframe. Returns what was changed.

        Only runs when the trader named a trigger explicitly. A condition is left
        alone when it sits on a timeframe they also named as context, because a
        multi-timeframe setup legitimately filters on one timeframe and fires on
        another. Anything on a *third* timeframe was inferred, not stated, and the
        stated role outranks an inference.
        """
        roles = extract_timeframe_roles(original_text)
        trigger = roles.trigger
        if trigger is None:
            return []
        allowed = {trigger, *roles.context}
        notes: list[str] = []
        moved: set[str] = set()
        for condition in conditions:
            if condition.timeframe in allowed:
                continue
            moved.add(condition.timeframe)
            condition.timeframe = trigger
        if moved:
            notes.append(
                f"Rules read on {', '.join(sorted(moved))} were moved to the {trigger} "
                f"trigger timeframe you specified."
            )
        if base_timeframe != trigger and trigger not in {base_timeframe}:
            notes.append(f"The trigger timeframe is {trigger}.")
        return notes

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
        # A reading outside the field's own domain is wording we have not understood,
        # not a rule. `r:?r.{0,20}?(\d+)` is a loose pattern and matched a `100` from
        # unrelated text; feeding it to the schema raised a ValidationError that
        # escaped as HTTP 500 for the whole turn. Refuse it — never clamp it, which
        # would invent a risk rule the trader never gave.
        rejected: list[str] = []
        if stop is not None and not _within_field_domain(RiskPolicy, "maximum_stop_percent", stop):
            rejected.append(f"maximum stop of {stop:g}%")
            stop, stop_specified = None, False
        if reward is not None and not _within_field_domain(
            RiskPolicy, "minimum_reward_to_risk", reward
        ):
            rejected.append(f"reward-to-risk of {reward:g}R")
            reward, reward_specified = None, False
        enabled = stop_specified or reward_specified
        if enabled:
            stop = stop if stop is not None else 100
            reward = reward if reward is not None else 1
        return _ParsedRisk(
            enabled=enabled,
            maximum_stop_percent=stop if enabled else None,
            minimum_reward_to_risk=reward if enabled else None,
            rejected=tuple(rejected),
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
            return sorted({to_pair(symbol) for symbol in guided_symbols})
        text = original_text.upper()
        if "MAJORS" in text or "MAJORS ONLY" in text:
            return list(MAJORS)
        # The deterministic classifier understands every spelling a trader uses
        # (BTCUSDT, BTC/USDT, BTC-USDT) and separates inclusions from exclusions,
        # so a bare `SOLUSDT` is no longer invisible to the universe builder.
        report = classify_turn(original_text)
        excluded = set(report.excluded_symbols)
        symbols = {to_pair(symbol) for symbol in report.symbols if symbol not in excluded}
        # A bare asset name without a quote still means that asset on the configured
        # quote currency; a word boundary keeps `SOL` out of `SOLUSDT`, already handled.
        for asset in ("BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "AVAX", "ADA", "DOGE", "MATIC"):
            if re.search(rf"\b{asset}\b(?!/)", text) and f"{asset}/{quote}" not in symbols:
                if normalize_symbol(asset, quote) in excluded:
                    continue
                symbols.add(f"{asset}/{quote}")
        return sorted(symbols)

    @staticmethod
    def _exclude_symbols(text: str, quote: str) -> list[str]:
        excluded: set[str] = set()
        if "alts" in text or "altcoins" in text:
            excluded.update({f"BTC/{quote}", f"ETH/{quote}"})
        # `to_pair` is idempotent, so an already complete `BTCUSDT` becomes `BTC/USDT`
        # instead of the corrupted `BTCUSDT/USDT` seen in run 20260725T122105Z.
        excluded.update(to_pair(symbol) for symbol in classify_turn(text).excluded_symbols)
        for match in re.finditer(r"(?:exclude|avoid|ignore)\s+([a-z0-9/, ]{2,80})", text):
            for token in re.findall(r"\b[a-z]{2,12}(?:/(?:usdt|usdc|btc|eth))?\b", match.group(1)):
                if token in {"low", "liquidity", "meme", "memes", "stablecoins"}:
                    continue
                symbol = token.upper()
                # A complete symbol keeps its own quote; only a bare base asset
                # takes the configured one.
                if "/" in symbol or split_symbol(symbol) is not None:
                    excluded.add(to_pair(symbol))
                else:
                    excluded.add(f"{symbol}/{quote}")
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

    _BREAKOUT_HIGH_TERMS = (
        "all time high",
        "all-time high",
        "ath",
        "new high",
        "day high",
        "month high",
        "highest high",
    )

    @classmethod
    def _mentions_breakout_high(cls, text: str) -> bool:
        return any(
            term in text for term in ("break", "breaking", "above", "crossing", "crosses", "making")
        ) and any(term in text for term in cls._BREAKOUT_HIGH_TERMS)

    @classmethod
    def _breakout_clause(cls, text: str) -> str:
        """The sentence that names the high being broken.

        Returns the whole text when the phrase cannot be located, so the reading is
        never *narrower* than before — only better scoped when scoping is possible.
        """
        for term in cls._BREAKOUT_HIGH_TERMS:
            index = text.find(term)
            if index == -1:
                continue
            return cls._matched_clause(text, index, index + len(term))
        return text

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

    #: Bars searched when a historical event is requested with no window stated. Named
    #: so the choice is visible at the one place that makes it.
    _DEFAULT_EVENT_SEARCH_CANDLES = 100

    @classmethod
    def _lookback_candles(cls, text: str, timeframe: str) -> int:
        """How far back an event search should look.

        The window itself is read by the shared reader. This method used to be a
        second full implementation with its own month/week/day/hour/minute regexes,
        which disagreed with the formula compiler's reader about the same sentence.
        """
        reading = read_lookback(text, timeframe=timeframe)
        if reading is None:
            return cls._DEFAULT_EVENT_SEARCH_CANDLES
        return reading.candles

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
        # Only a window the trader actually stated becomes a search bound. Comparing
        # the result against the default (`!= 100`) meant a trader who genuinely asked
        # for 100 bars was treated as having asked for nothing.
        reading = read_lookback(text, timeframe=timeframe)
        if reading is not None:
            parameters["search_lookback"] = reading.candles
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
    def _percent_move(
        cls, text: str, timeframe: str = "15m"
    ) -> tuple[str, float, int, Comparator] | None:
        # One movement vocabulary, shared with the formula compiler. Two hand-written
        # lists disagreed about `drops`, `down` and `sell-off`, so the same wording
        # compiled to opposite directions depending on which reader ran.
        match = re.search(
            rf"({MOVEMENT_PATTERN})" r".{0,36}?(\d+(?:\.\d+)?)\s*%",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        context = text[max(0, match.start() - 32) : min(len(text), match.end() + 24)]
        if "candle" in context and not any(
            term in context for term in ("coin", "price", "symbol", "market", "pair")
        ):
            return None
        # The matched word decides the side. There is no `or "up"` fallback: the match
        # came from the shared movement vocabulary, so a direction it cannot read is a
        # gap in that vocabulary, and inventing `up` here would hide it — and compile a
        # rise for a trader who asked about a fall.
        direction = movement_direction(match.group(1))
        if direction is None:
            return None
        # One window reader, shared with the formula compiler and the semantic parser.
        # This clause used to scan for `(last|past) N candles` itself and understood a
        # narrower set of wording than the other readers did.
        reading = read_lookback(text, timeframe=timeframe)
        lookback = reading.candles if reading is not None else 1
        # The bound the trader stated, read from this clause only. `up 5%` states no
        # operator, which conventionally means a move of at least that size.
        found = find_comparator(match.group(0))
        comparator = found[0] if found else Comparator.GREATER_THAN_OR_EQUAL
        return direction, float(match.group(2)), lookback, comparator

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

    @classmethod
    def _level_or_default(
        cls,
        text: str,
        term: str,
        *,
        comparator: Comparator,
        value: float,
    ) -> tuple[Comparator, float]:
        """Read ``term``'s stated bound, falling back to this rule's documented one.

        The fallback applies only to a rule whose wording is already present and
        whose default is part of its definition (`VWAP deviation within 2%`). It is
        never used to conjure a rule the trader did not ask for, and a stated
        operator always wins over it.
        """
        reading = cls._bounded_level(text, term, default_comparator=comparator)
        if reading is None:
            return comparator, value
        return reading.comparator, reading.value

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
