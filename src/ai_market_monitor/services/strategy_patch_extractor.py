from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.boolean_expression import BooleanNode, parse_boolean_expression
from ai_market_monitor.engine.comparators import comparator_alternation, detect_comparator
from ai_market_monitor.engine.formula_compiler import parse_percentage_formula
from ai_market_monitor.engine.turn_fragments import (
    TurnFragmentReport,
    classify_turn,
    extract_timeframe_roles,
)
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection
from ai_market_monitor.schemas.strategy_draft_v2 import (
    STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    ConditionNodeType,
    ConditionNodeV2,
    CorrectionV2,
    DraftFieldPatch,
    DraftMode,
    FormulaKind,
    MovementDirection,
    OperandV2,
    ReversionV2,
    SetupIntent,
    StrategyBias,
    StrategyDraftV2,
    StrategyPatch,
    StrategyPatchExtraction,
    StrategyUniverseV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.agent_tools import strict_json_schema
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.ai_setup_evaluator_control import consume_evaluator_llm_fault


class StrategyPatchExtractionError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StrategyPatchNonMutation(ValueError):
    """A structured model response that correctly carries no strategy patch."""

    def __init__(self, intent: SetupIntent, answer: str | None = None) -> None:
        super().__init__(answer or "This turn does not change the strategy.")
        self.intent = intent
        self.answer = answer


class StrategyPatchExtractor(Protocol):
    last_usage: dict[str, Any]

    async def extract(
        self,
        *,
        current_draft: StrategyDraftV2,
        message: str,
        source_turn_id: str,
    ) -> StrategyPatch: ...


class LaunchStrategyPatchExtractor:
    """Zero-call deterministic extraction, then one bounded structured call."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.last_usage: dict[str, Any] = {}
        self.model_call_count = 0

    async def extract(
        self,
        *,
        current_draft: StrategyDraftV2,
        message: str,
        source_turn_id: str,
    ) -> StrategyPatch:
        self.last_usage = {}
        self.model_call_count = 0
        deterministic = deterministic_strategy_patch(
            current_draft,
            message,
            source_turn_id=source_turn_id,
        )
        if deterministic is not None:
            return deterministic
        return await self._extract_once(
            current_draft=current_draft,
            message=message,
            source_turn_id=source_turn_id,
        )

    async def _extract_once(
        self,
        *,
        current_draft: StrategyDraftV2,
        message: str,
        source_turn_id: str,
    ) -> StrategyPatch:
        if self.settings.openai_api_key is None:
            raise StrategyPatchExtractionError(
                "OPENAI_NOT_CONFIGURED",
                "This setup needs interpretation, but the AI provider is unavailable.",
            )
        route = select_setup_model(
            self.settings,
            current_message=message,
            accumulated_setup="",
        )
        schema = strict_json_schema(StrategyPatchExtraction)
        payload = {
            "model": route.model,
            "store": False,
            "stream": False,
            "max_output_tokens": 4800,
            "reasoning": {"effort": route.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "hilalmarkets_strategy_patch_v2",
                    "strict": True,
                    "schema": schema,
                }
            },
            "instructions": _PATCH_PROMPT,
            "input": json.dumps(
                {
                    "current_draft": _extraction_context(current_draft),
                    "current_user_turn": message,
                    "source_turn_id": source_turn_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        self.model_call_count += 1
        if self.model_call_count > 1:
            raise StrategyPatchExtractionError(
                "MODEL_CALL_LIMIT",
                "A setup turn cannot use more than one extraction call.",
            )
        try:
            response_payload = consume_evaluator_llm_fault()
            if response_payload is None:
                async with httpx.AsyncClient(
                    base_url=str(self.settings.openai_base_url).rstrip("/"),
                    timeout=httpx.Timeout(self.settings.openai_timeout_seconds),
                    transport=self.transport,
                ) as client:
                    response = await client.post("/responses", headers=headers, json=payload)
                response.raise_for_status()
                response_payload = response.json()
            self.last_usage = {
                **dict(response_payload.get("usage") or {}),
                **route.usage_metadata(),
            }
            extraction = StrategyPatchExtraction.model_validate_json(
                _response_output_text(response_payload)
            )
        except httpx.ConnectTimeout as exc:
            raise StrategyPatchExtractionError(
                "TARGET_CONNECT_TIMEOUT",
                "The strategy interpreter could not be reached in time.",
                retryable=True,
            ) from exc
        except httpx.ReadTimeout as exc:
            raise StrategyPatchExtractionError(
                "TARGET_READ_TIMEOUT",
                "The strategy interpreter timed out.",
                retryable=True,
            ) from exc
        except httpx.RemoteProtocolError as exc:
            raise StrategyPatchExtractionError(
                "TARGET_PARTIAL_STREAM",
                "The strategy interpreter disconnected before completing its response.",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            code = (
                "TARGET_DNS_RESOLUTION_FAILURE"
                if _is_dns_failure(exc)
                else "TARGET_CONNECTION_REFUSED"
            )
            raise StrategyPatchExtractionError(
                code,
                "The strategy interpreter could not be reached.",
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise StrategyPatchExtractionError(
                "TARGET_TOTAL_TIMEOUT",
                "The strategy interpreter exceeded its bounded turn time.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            code = (
                "TARGET_HTTP_429"
                if exc.response.status_code == 429
                else "TARGET_HTTP_409"
                if exc.response.status_code == 409
                else "TARGET_HTTP_5XX"
                if exc.response.status_code >= 500
                else "TARGET_PROVIDER_ERROR"
            )
            raise StrategyPatchExtractionError(
                code,
                "The strategy interpreter could not complete this turn.",
                retryable=exc.response.status_code == 429 or exc.response.status_code >= 500,
            ) from exc
        except ValidationError as exc:
            error_types = {str(item.get("type") or "") for item in exc.errors()}
            code = (
                "TARGET_INVALID_JSON"
                if "json_invalid" in error_types
                else "TARGET_SCHEMA_VALIDATION"
            )
            raise StrategyPatchExtractionError(
                code,
                "The strategy interpreter returned an invalid structured patch.",
            ) from exc
        except (KeyError, json.JSONDecodeError) as exc:
            raise StrategyPatchExtractionError(
                "TARGET_INVALID_JSON",
                "The strategy interpreter returned invalid JSON.",
            ) from exc
        except ValueError as exc:
            raise StrategyPatchExtractionError(
                (
                    "TARGET_EMPTY_RESPONSE"
                    if "no structured output" in str(exc).casefold()
                    else "TARGET_INVALID_JSON"
                ),
                "The strategy interpreter did not return a usable structured patch.",
            ) from exc
        if extraction.patch is None or extraction.intent != SetupIntent.STRATEGY_PATCH:
            raise StrategyPatchNonMutation(
                extraction.intent,
                extraction.answer,
            )
        if extraction.patch.source_turn_id != source_turn_id:
            raise StrategyPatchExtractionError(
                "UNGROUNDED_PATCH",
                "The patch provenance does not match the current user turn.",
            )
        grounding_errors = validate_patch_grounding(
            extraction.patch,
            message=message,
            source_turn_id=source_turn_id,
        )
        if grounding_errors:
            raise StrategyPatchExtractionError(
                "UNGROUNDED_PATCH",
                "The structured patch was not grounded in the current user turn: "
                + "; ".join(grounding_errors),
            )
        return extraction.patch


#: The price fields a launch primitive can compare, and the comparison itself.
#:
#: The operator comes from the one shared table rather than a hand-written subset.
#: Two subsets had already drifted apart here: the parser knew ``equal to`` but not
#: ``at least``, and the gate in front of it knew ``above`` but not ``equal to``, so
#: `price is equal to 3500` was refused by the gate and never reached the parser that
#: understood it.
_PRICE_FIELD_GROUP = r"(?P<left>price|close|open|high|low)"
_OPERATOR_GROUP = rf"(?P<operator>{comparator_alternation()})"

#: The mechanics named in the launch grammar, spelled as the trader spells them.
_NAMED_PRIMITIVE_RE = re.compile(
    r"\b(?:open-to-close|close-to-close|high-to-low|low-to-high|"
    r"previous\s+(?:closed\s+)?candle|prior\s+candle|"
    r"highest\s+high|lowest\s+low|fixed\s+(?:price\s+)?level|"
    r"cross(?:es|ed|ing)?\s+(?:above|below))\b",
    re.IGNORECASE,
)

#: A price field compared against a level or a reference, using the shared operator
#: vocabulary so the gate can never understand less than the parser behind it.
_PRICE_COMPARISON_RE = re.compile(
    _PRICE_FIELD_GROUP
    + r".{0,32}?(?:"
    + comparator_alternation()
    + r").{0,32}?(?:\$?\d+(?:\.\d+)?|previous|prior|highest|lowest)",
    re.IGNORECASE,
)

_SWEEP_RE = re.compile(r"\bsweep(?:s|ing|t)?\b", re.IGNORECASE)
_RECLAIM_RE = re.compile(r"\breclaim(?:s|ed|ing)?\b", re.IGNORECASE)


def _is_dns_failure(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        text = f"{type(current).__name__}: {current}".casefold()
        if any(
            marker in text
            for marker in (
                "getaddrinfo failed",
                "name or service not known",
                "temporary failure in name resolution",
                "name resolution",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def deterministic_strategy_patch(
    draft: StrategyDraftV2,
    message: str,
    *,
    source_turn_id: str,
) -> StrategyPatch | None:
    cleaned = " ".join(message.split())
    report = classify_turn(cleaned)
    reversion = re.search(r"\b(?:revert|restore|go back)\b.*?\bversion\s+(\d+)\b", cleaned, re.I)
    if reversion:
        return StrategyPatch(
            source_turn_id=source_turn_id,
            reversion=ReversionV2(target_version=int(reversion.group(1))),
        )
    if _contains_unresolved_choice_language(cleaned):
        return None
    if _requires_structured_multi_condition_extraction(cleaned):
        return None
    if any(
        not _is_direct_primitive_fragment(item.text, turn_text=cleaned)
        for item in report.trading_conditions
    ):
        return None

    set_fields = DraftFieldPatch()
    lowered = cleaned.casefold()
    if re.search(r"\bscanner\b|\bcheck (?:the )?market now\b", lowered):
        set_fields.mode = DraftMode.SCANNER
    elif re.search(r"\bmonitor\b|\bwatchlist\b", lowered):
        set_fields.mode = DraftMode.MONITOR
    if "bybit" in lowered:
        set_fields.exchange = "bybit"
    elif "binance" in lowered:
        set_fields.exchange = "binance"
    quote_match = re.search(r"\b(USDT|USDC|FDUSD|USD)\b", cleaned, re.I)
    if quote_match:
        set_fields.quote_asset = quote_match.group(1).upper()

    # `report.symbols` is every symbol the turn *mentions*, which includes the ones it
    # mentions in order to exclude them. Copying it straight into the inclusions put
    # the same symbol in both sets, and the patch was then refused whole — so
    # `Watchlist for BTCUSDT only and exclude LTCUSDT` set up nothing at all.
    # Saying "exclude" is the specific instruction; merely appearing is not.
    exclude = list(report.excluded_symbols)
    excluded_canonical = set(
        StrategyUniverseV2(excluded_symbols=exclude).excluded_symbols
    )
    include = [
        symbol
        for symbol in report.symbols
        if StrategyUniverseV2(included_symbols=[symbol]).included_symbols[0]
        not in excluded_canonical
    ]
    explicit_structure = parse_boolean_expression(cleaned)
    condition: ConditionNodeV2 | None = None
    if explicit_structure is not None:
        condition = _compile_boolean_node(
            explicit_structure,
            source_turn_id=source_turn_id,
            draft=draft,
        )
        if condition is None:
            return None
    else:
        condition = _deterministic_condition(
            cleaned,
            source_turn_id=source_turn_id,
            draft=draft,
        )

    has_mechanic = bool(report.trading_conditions)
    if has_mechanic and condition is None:
        return None
    unresolved: list[UnresolvedFieldV2] = []
    if condition is not None and not any(
        item.trigger_timeframe
        for item in condition.walk()
        if item.node_type == ConditionNodeType.CONDITION
    ):
        first_condition = next(
            item
            for item in condition.walk()
            if item.node_type == ConditionNodeType.CONDITION
        )
        unresolved.append(
            UnresolvedFieldV2(
                unresolved_id="timeframe",
                source_turn_id=source_turn_id,
                source_fragment=_source_excerpt(cleaned),
                target_type="condition_field",
                target_field="trigger_timeframe",
                target_condition_id=first_condition.node_id,
                expected_answer_schema={"type": "string", "format": "timeframe"},
                question="Which timeframe should evaluate this condition?",
                reason="The condition needs one trigger timeframe before it can run.",
            )
        )
    if condition is None and (include or exclude or set_fields != DraftFieldPatch()):
        unresolved.append(
            UnresolvedFieldV2(
                unresolved_id="conditions",
                source_turn_id=source_turn_id,
                source_fragment=_source_excerpt(cleaned),
                target_type="condition_creation",
                expected_answer_schema={"type": "string"},
                question="What measurable market condition should these assets match?",
                reason="A market scope needs at least one measurable condition.",
            )
        )
    if (
        condition is None
        and not include
        and not exclude
        and set_fields == DraftFieldPatch()
    ):
        return None
    correction = (
        CorrectionV2(target="current strategy", reason=_source_excerpt(cleaned)[:500])
        if re.search(r"\b(?:change|correct|instead|replace|remove)\b", lowered)
        else None
    )
    if correction is not None and draft.condition_ast is not None:
        # A deterministic parser cannot know which existing node the user means.
        # The one structured patch call must name that node; replacing the full AST
        # here would discard unrelated conditions.
        return None
    return StrategyPatch(
        source_turn_id=source_turn_id,
        set_fields=set_fields,
        add_conditions=[condition] if condition is not None else [],
        add_inclusions=include,
        add_exclusions=exclude,
        correction=correction,
        unresolved_references=unresolved,
    )


def validate_patch_grounding(
    patch: StrategyPatch,
    *,
    message: str,
    source_turn_id: str,
) -> list[str]:
    errors: list[str] = []
    normalized_message = _normalized(message)
    nodes = [
        *patch.add_conditions,
        *(item.replacement for item in patch.update_conditions),
        *([patch.replace_groups] if patch.replace_groups is not None else []),
    ]
    for root in nodes:
        for node in root.walk():
            if node.node_type != ConditionNodeType.CONDITION:
                continue
            if node.source_turn_id != source_turn_id:
                errors.append(f"{node.node_id}:source_turn")
            fragment = node.source_fragment or ""
            if not fragment or _normalized(fragment) not in normalized_message:
                errors.append(f"{node.node_id}:source_fragment")
                continue
            if node.threshold is not None and not _fragment_contains_number(
                fragment, node.threshold
            ):
                errors.append(f"{node.node_id}:threshold")
            if (
                node.trigger_timeframe is not None
                and node.trigger_timeframe.casefold() not in fragment.casefold()
            ):
                errors.append(f"{node.node_id}:trigger_timeframe")
            explicit_operator = detect_comparator(fragment)
            if (
                explicit_operator is not None
                and node.operator is not None
                and explicit_operator != node.operator
            ):
                errors.append(f"{node.node_id}:operator")
            if node.formula is not None and not _formula_grounded(node.formula, fragment):
                errors.append(f"{node.node_id}:formula")
    for unresolved in patch.unresolved_references:
        if unresolved.source_turn_id not in {None, source_turn_id}:
            errors.append(f"{unresolved.key}:source_turn")
        if _normalized(unresolved.source_fragment) not in normalized_message:
            errors.append(f"{unresolved.key}:source_fragment")
    for unsupported in patch.unsupported_requirements:
        if unsupported.source_turn_id not in {None, source_turn_id}:
            errors.append(f"{unsupported.key}:source_turn")
        if _normalized(unsupported.source_fragment) not in normalized_message:
            errors.append(f"{unsupported.key}:source_fragment")
    return list(dict.fromkeys(errors))


def _compile_boolean_node(
    node: BooleanNode,
    *,
    source_turn_id: str,
    draft: StrategyDraftV2,
) -> ConditionNodeV2 | None:
    if node.is_leaf:
        return _deterministic_condition(
            node.text,
            source_turn_id=source_turn_id,
            draft=draft,
        )
    children: list[ConditionNodeV2] = []
    for child in node.children:
        compiled = _compile_boolean_node(child, source_turn_id=source_turn_id, draft=draft)
        if compiled is None:
            return None
        children.append(compiled)
    assert node.operator is not None
    return ConditionNodeV2(
        node_id=f"group_{node.operator}_{_stable_id(node.shape())}",
        node_type=ConditionNodeType(node.operator),
        children=children,
    )


def _deterministic_condition(
    text: str,
    *,
    source_turn_id: str,
    draft: StrategyDraftV2,
) -> ConditionNodeV2 | None:
    report = classify_turn(text)
    roles = _resolve_timeframe_roles(text, report=report, draft=draft)
    confirmation_timeframes = list(roles.confirmation)
    default_timeframe = roles.trigger or _current_trigger_timeframe(draft) or "15m"
    movement_direction = {
        StrategyDirection.LONG: MovementDirection.UP,
        StrategyDirection.SHORT: MovementDirection.DOWN,
        StrategyDirection.BOTH: MovementDirection.NEUTRAL,
        None: MovementDirection.NEUTRAL,
    }[report.direction]
    if report.is_sweep:
        return _sweep_condition(
            text,
            source_turn_id=source_turn_id,
            trigger_timeframe=roles.trigger,
            context_timeframes=list(roles.context),
            confirmation_timeframes=confirmation_timeframes,
        )
    reference_condition = _deterministic_reference_condition(
        text,
        source_turn_id=source_turn_id,
        trigger_timeframe=roles.trigger,
        context_timeframes=list(roles.context),
        confirmation_timeframes=confirmation_timeframes,
        default_direction=movement_direction,
    )
    if reference_condition is not None:
        return reference_condition
    low_to_high = _low_to_high_percentage_condition(
        text,
        source_turn_id=source_turn_id,
        trigger_timeframe=roles.trigger,
        context_timeframes=list(roles.context),
        confirmation_timeframes=confirmation_timeframes,
    )
    if low_to_high is not None:
        return low_to_high
    if _is_unanchored_percentage(text):
        return None
    formula = parse_percentage_formula(
        text,
        default_timeframe=default_timeframe,
        default_direction=report.direction or StrategyDirection.BOTH,
    )
    if formula is not None:
        formula_kind = {
            "open_to_close": FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
            "close_to_close": FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
            "reference_to_current": FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
            "high_to_low": FormulaKind.HIGH_TO_LOW_PERCENTAGE,
        }[formula.formula]
        if formula.direction == "up":
            movement_direction = MovementDirection.UP
        elif formula.direction == "down":
            movement_direction = MovementDirection.DOWN
        return ConditionNodeV2(
            node_id=f"condition_{_stable_id(source_turn_id, text)}",
            node_type=ConditionNodeType.CONDITION,
            source_turn_id=source_turn_id,
            source_fragment=_source_excerpt(text),
            movement_direction=movement_direction,
            strategy_bias=_explicit_strategy_bias(text),
            formula=formula_kind,
            operands=[
                OperandV2(
                    role="measured_value",
                    kind="market_metric",
                    name="percentage_change",
                    parameters=formula.parameters(),
                )
            ],
            operator=formula.comparator,
            threshold=_explicit_signed_percentage(text) or formula.threshold_percent,
            unit="percent",
            trigger_timeframe=roles.trigger,
            context_timeframes=list(roles.context),
            confirmation_timeframes=confirmation_timeframes,
            reference_timeframe=formula.reference_timeframe,
            reference_definition=(
                f"{formula.reference_field} to {formula.current_field}; "
                f"lookback={formula.lookback}"
            ),
        )
    return None


def _sweep_condition(
    text: str,
    *,
    source_turn_id: str,
    trigger_timeframe: str | None,
    context_timeframes: list[str],
    confirmation_timeframes: list[str],
) -> ConditionNodeV2:
    lowered = text.casefold()
    movement_direction = (
        MovementDirection.DOWN
        if re.search(r"\bsweeps?\s+below\b", lowered)
        else MovementDirection.UP
        if re.search(r"\bsweeps?\s+above\b", lowered)
        else MovementDirection.NEUTRAL
    )
    return ConditionNodeV2(
        node_id=f"condition_{_stable_id(source_turn_id, text)}",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=source_turn_id,
        source_fragment=_source_excerpt(text),
        movement_direction=movement_direction,
        strategy_bias=_explicit_strategy_bias(text),
        formula=FormulaKind.SWEEP_AND_RECLAIM,
        operands=[
            OperandV2(
                role="sweep_state",
                kind="market_metric",
                name="sweep_and_reclaim",
                parameters={"pierce_required": True, "reclaim_required": True},
            )
        ],
        operator=Comparator.IS_TRUE,
        unit="boolean",
        trigger_timeframe=trigger_timeframe,
        context_timeframes=context_timeframes,
        confirmation_timeframes=confirmation_timeframes,
        reference_timeframe=(
            context_timeframes[0] if context_timeframes else trigger_timeframe
        ),
        reference_definition=_source_excerpt(text),
    )


def _low_to_high_percentage_condition(
    text: str,
    *,
    source_turn_id: str,
    trigger_timeframe: str | None,
    context_timeframes: list[str],
    confirmation_timeframes: list[str],
) -> ConditionNodeV2 | None:
    if not re.search(r"\blow(?:_|[\s-]+)to(?:_|[\s-]+)high\b", text, re.IGNORECASE):
        return None
    percentage = re.search(r"(?P<value>-?\d+(?:\.\d+)?)\s*%", text)
    if percentage is None:
        return None
    comparator = detect_comparator(text) or Comparator.GREATER_THAN_OR_EQUAL
    threshold = float(percentage.group("value"))
    return ConditionNodeV2(
        node_id=f"condition_{_stable_id(source_turn_id, text)}",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=source_turn_id,
        source_fragment=_source_excerpt(text),
        movement_direction=MovementDirection.UP,
        strategy_bias=_explicit_strategy_bias(text),
        formula=FormulaKind.LOW_TO_HIGH_PERCENTAGE,
        operands=[
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters={
                    "formula": "low_to_high",
                    "reference_field": "low",
                    "current_field": "high",
                    "scale": "percent",
                },
            )
        ],
        operator=comparator,
        threshold=threshold,
        unit="percent",
        trigger_timeframe=trigger_timeframe,
        context_timeframes=context_timeframes,
        confirmation_timeframes=confirmation_timeframes,
        reference_timeframe=trigger_timeframe,
        reference_definition="low to high",
    )


def _deterministic_reference_condition(
    text: str,
    *,
    source_turn_id: str,
    trigger_timeframe: str | None,
    context_timeframes: list[str],
    confirmation_timeframes: list[str],
    default_direction: MovementDirection,
) -> ConditionNodeV2 | None:
    field = _PRICE_FIELD_GROUP
    operator = _OPERATOR_GROUP
    lookback = re.search(
        field
        + r".{0,40}?"
        + operator
        + r".{0,40}?(?P<reference>highest\s+high|lowest\s+low)"
        + r".{0,40}?\b(?:last|previous|prior)\s+(?P<count>\d+)\s+candles?\b",
        text,
        re.IGNORECASE,
    )
    if lookback is not None:
        comparator = detect_comparator(lookback.group("operator"))
        if comparator is None:
            return None
        reference_name = lookback.group("reference").casefold().replace(" ", "_")
        return _reference_node(
            source_turn_id=source_turn_id,
            text=text,
            formula=FormulaKind.LOOKBACK_REFERENCE_LEVEL,
            comparator=comparator,
            movement_direction=_direction_for_comparator(comparator, default_direction),
            strategy_bias=_explicit_strategy_bias(text),
            trigger_timeframe=trigger_timeframe,
            context_timeframes=context_timeframes,
            confirmation_timeframes=confirmation_timeframes,
            left_field=lookback.group("left").casefold(),
            reference_name=reference_name,
            reference_parameters={"lookback": int(lookback.group("count"))},
            reference_definition=(
                f"{reference_name} of previous {lookback.group('count')} candles"
            ),
        )

    previous = re.search(
        field
        + r".{0,40}?"
        + operator
        + r".{0,40}?\b(?:previous|prior)\s+candle(?:'s)?\s*"
        + r"(?P<right>price|close|open|high|low)\b",
        text,
        re.IGNORECASE,
    )
    if previous is not None:
        comparator = detect_comparator(previous.group("operator"))
        if comparator is None:
            return None
        right = previous.group("right").casefold()
        return _reference_node(
            source_turn_id=source_turn_id,
            text=text,
            formula=FormulaKind.PREVIOUS_CANDLE_REFERENCE,
            comparator=comparator,
            movement_direction=_direction_for_comparator(comparator, default_direction),
            strategy_bias=_explicit_strategy_bias(text),
            trigger_timeframe=trigger_timeframe,
            context_timeframes=context_timeframes,
            confirmation_timeframes=confirmation_timeframes,
            left_field=previous.group("left").casefold(),
            reference_name=f"previous_candle_{right}",
            reference_parameters={"lookback": 1, "field": right},
            reference_definition=f"previous closed candle {right}",
        )

    fixed = re.search(
        field
        + r".{0,30}?"
        + operator
        + r"\s+(?:a\s+)?(?:fixed\s+)?(?:level\s+)?\$?"
        + r"(?P<value>-?\d+(?:\.\d+)?)\b(?![\d.]|\s*%)",
        text,
        re.IGNORECASE,
    )
    if fixed is None:
        return None
    comparator = detect_comparator(fixed.group("operator"))
    if comparator is None:
        return None
    value = float(fixed.group("value"))
    formula = (
        FormulaKind.CROSS
        if comparator in {Comparator.CROSSES_ABOVE, Comparator.CROSSES_BELOW}
        else FormulaKind.FIXED_REFERENCE_LEVEL
    )
    return ConditionNodeV2(
        node_id=f"condition_{_stable_id(source_turn_id, text)}",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=source_turn_id,
        source_fragment=_source_excerpt(text),
        movement_direction=_direction_for_comparator(comparator, default_direction),
        strategy_bias=_explicit_strategy_bias(text),
        formula=formula,
        operands=[
            OperandV2(
                role="current_value",
                kind="price",
                field=fixed.group("left").casefold(),
            )
        ],
        operator=comparator,
        threshold=value,
        unit="price",
        trigger_timeframe=trigger_timeframe,
        context_timeframes=context_timeframes,
        confirmation_timeframes=confirmation_timeframes,
        reference_timeframe=trigger_timeframe,
        reference_definition=f"fixed price level {value:g}",
    )


def _reference_node(
    *,
    source_turn_id: str,
    text: str,
    formula: FormulaKind,
    comparator: Comparator,
    movement_direction: MovementDirection,
    strategy_bias: StrategyBias,
    trigger_timeframe: str | None,
    context_timeframes: list[str],
    confirmation_timeframes: list[str],
    left_field: str,
    reference_name: str,
    reference_parameters: dict[
        str,
        int | float | str | bool | list[int | float | str | bool],
    ],
    reference_definition: str,
) -> ConditionNodeV2:
    return ConditionNodeV2(
        node_id=f"condition_{_stable_id(source_turn_id, text)}",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=source_turn_id,
        source_fragment=_source_excerpt(text),
        movement_direction=movement_direction,
        strategy_bias=strategy_bias,
        formula=formula,
        operands=[
            OperandV2(role="current_value", kind="price", field=left_field),
            OperandV2(
                role="reference_value",
                kind="reference",
                name=reference_name,
                parameters=reference_parameters,
            ),
        ],
        operator=comparator,
        unit="price",
        trigger_timeframe=trigger_timeframe,
        context_timeframes=context_timeframes,
        confirmation_timeframes=confirmation_timeframes,
        reference_timeframe=trigger_timeframe,
        reference_definition=reference_definition,
    )


def _direction_for_comparator(
    comparator: Comparator,
    default: MovementDirection,
) -> MovementDirection:
    if comparator in {
        Comparator.GREATER_THAN,
        Comparator.GREATER_THAN_OR_EQUAL,
        Comparator.CROSSES_ABOVE,
    }:
        return MovementDirection.UP
    if comparator in {
        Comparator.LESS_THAN,
        Comparator.LESS_THAN_OR_EQUAL,
        Comparator.CROSSES_BELOW,
    }:
        return MovementDirection.DOWN
    return default


def _explicit_strategy_bias(text: str) -> StrategyBias:
    if re.search(r"\blong\b", text, re.IGNORECASE):
        return StrategyBias.LONG
    if re.search(r"\bshort\b", text, re.IGNORECASE):
        return StrategyBias.SHORT
    return StrategyBias.NEUTRAL


#: Every spelling that puts a timeframe in the confirming role. `confirmation` alone
#: missed the way traders actually write it — `confirmed on the 1h`, `confirm on 4h` —
#: and the timeframe then fell through to the trigger fallback and *replaced* the
#: trigger, so the alert fired on the confirming candle instead of the real one.
_CONFIRMATION_RE = re.compile(
    r"(?:(?P<prefix>\d{1,2}\s*[mhdw])\s+confirm(?:ation|ed|s)?\b|"
    r"\bconfirm(?:ation|ed|s|ing)?"
    r"(?:\s+timeframe)?\s*(?:on|at|with|using|by|:|=)?\s*(?:the\s+)?"
    r"(?P<suffix>\d{1,2}\s*[mhdw])\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _ResolvedTimeframeRoles:
    """Which candle fires the rule, and which ones only support it."""

    trigger: str | None
    context: tuple[str, ...]
    confirmation: tuple[str, ...]


def _resolve_timeframe_roles(
    text: str,
    *,
    report: TurnFragmentReport,
    draft: StrategyDraftV2,
) -> _ResolvedTimeframeRoles:
    """Assign every stated timeframe exactly one role.

    Two separate readers used to decide this and neither knew the other's answer.
    The context list excluded whatever *it* thought the trigger was, while the caller
    picked the trigger separately as "the last timeframe mentioned". Both failed the
    same way:

    * `using the 4h as context ... the 15m candle rises 2%` put 15m in **both** the
      trigger and the context role, so the same candle selected and fired the rule.
    * `the 15m candle rises 2%, confirmed on the 1h` made **1h** the trigger, because
      it was mentioned last, and the alert fired on the confirming candle.

    Resolving all four roles in one place fixes both: a timeframe another role already
    claimed can never become the fallback trigger, and the supporting lists are built
    against the trigger that was actually chosen.
    """

    # The word `confirmation` names its timeframe outright, so it is resolved first
    # and removes that timeframe from every other role. The role reader has no
    # confirming role of its own: given `... 2% with 1h confirmation` it offered 1h
    # as the *trigger*, which would have fired the alert on the confirming candle.
    confirmation = list(
        dict.fromkeys(
            (prefix or suffix).replace(" ", "").casefold()
            for prefix, suffix in _CONFIRMATION_RE.findall(text)
            if (prefix or suffix)
        )
    )
    roles = extract_timeframe_roles(text)
    context = [item for item in roles.context if item not in confirmation]
    trigger = roles.trigger if roles.trigger not in confirmation else None
    if trigger is None:
        # "The last timeframe mentioned" is only a safe guess among the ones no role
        # has taken.
        trigger = next(
            (
                item
                for item in reversed(list(report.timeframes))
                if item not in confirmation and item not in context
            ),
            None,
        )
    if trigger is None and context:
        # Every stated timeframe ended up in a supporting role, yet a rule has to
        # fire on one candle. The first one stated is the sentence's subject.
        trigger = context[0]
    return _ResolvedTimeframeRoles(
        trigger=trigger,
        context=tuple(item for item in context if item != trigger),
        confirmation=tuple(item for item in confirmation if item != trigger),
    )


def _current_trigger_timeframe(draft: StrategyDraftV2) -> str | None:
    if draft.condition_ast is None:
        return None
    for item in draft.condition_ast.walk():
        if item.node_type == ConditionNodeType.CONDITION and item.trigger_timeframe:
            return item.trigger_timeframe
    return None


def _explicit_signed_percentage(text: str) -> float | None:
    values = re.findall(r"(?<![\w.])(-\d+(?:\.\d+)?)\s*%", text)
    return float(values[-1]) if values else None


def _source_excerpt(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH:
        return normalized
    anchors = [
        match.start()
        for pattern in (
            r"-?\d+(?:\.\d+)?\s*%",
            r"\b(?:open-to-close|close-to-close|sweep|reclaim|cross)\b",
        )
        if (match := re.search(pattern, normalized, re.IGNORECASE)) is not None
    ]
    center = min(anchors) if anchors else 0
    start = max(0, center - STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH // 2)
    end = start + STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH
    if end > len(normalized):
        end = len(normalized)
        start = max(0, end - STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH)
    return normalized[start:end].strip()


def _stable_id(*values: str) -> str:
    return sha256("\x1f".join(values).encode()).hexdigest()[:16]


def _response_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise ValueError("response contained no structured output")


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _fragment_contains_number(fragment: str, expected: float) -> bool:
    return any(
        abs(float(token) - expected) < 1e-9
        for token in re.findall(r"-?\d+(?:\.\d+)?", fragment)
    )


def _formula_grounded(formula: FormulaKind, fragment: str) -> bool:
    lowered = fragment.casefold()
    required_terms = {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: ("open", "close"),
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: ("close", "close"),
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: (
            "reference|today|swing|fixed|lookback|previous",
            "current|now|close|high|low",
        ),
        FormulaKind.HIGH_TO_LOW_PERCENTAGE: ("high", "low"),
        FormulaKind.LOW_TO_HIGH_PERCENTAGE: ("low", "high"),
        FormulaKind.PREVIOUS_CANDLE_REFERENCE: ("previous|prior", "candle|bar"),
        FormulaKind.FIXED_REFERENCE_LEVEL: ("fixed|level",),
        FormulaKind.LOOKBACK_REFERENCE_LEVEL: ("lookback|last|previous",),
        FormulaKind.CROSS: ("cross",),
        FormulaKind.SWEEP_AND_RECLAIM: ("sweep", "reclaim|recover|reject"),
        FormulaKind.CAPABILITY: (),
    }[formula]
    return all(re.search(term, lowered) for term in required_terms)


def _is_unanchored_percentage(text: str) -> bool:
    lowered = text.casefold()
    if not re.search(r"(?:%|\bpercent(?:age)?\b|\bpct\b)", lowered):
        return False
    return not re.search(
        r"\b(?:open|close|high|low|reference|today|previous|prior|swing|"
        r"lookback|fixed|level)\b",
        lowered,
    )


def _contains_unresolved_choice_language(text: str) -> bool:
    """Keep questions, examples, and option lists out of deterministic execution."""

    if not re.search(r"[?\u061f]", text):
        return False
    return bool(
        re.search(
            r"\b(?:pick|choose|which|what|from\s+what|for\s+example|e\.g\.|"
            r"option|a/b|yes/no|define|counts?\s+as)\b",
            text,
            re.IGNORECASE,
        )
    )


def _requires_structured_multi_condition_extraction(text: str) -> bool:
    """Keep the simple parser from flattening several independent conditions."""

    numbered_sections = re.findall(r"(?:^|[\s;])(?:\d{1,2}[.)]|[A-Z][.)])\s+", text)
    if len(numbered_sections) >= 2:
        return True
    explicit_comparisons = re.findall(
        r"(?:>=|<=|==|(?<![-=])>(?!=)|(?<![-=])<(?!=)|"
        r"\b(?:crosses?\s+(?:above|below)|above|below|at\s+least|at\s+most)\b)",
        text,
        re.IGNORECASE,
    )
    formula_anchors = re.findall(
        r"\b(?:open-to-close|close-to-close|sweep(?:s|ing)?|reclaim(?:s|ed|ing)?|"
        r"previous\s+candle|last\s+closed|rolling|lowest|highest|ema\d*|sma\d*)\b",
        text,
        re.IGNORECASE,
    )
    return (
        len(text) > 240
        and len(explicit_comparisons) >= 2
        and len(formula_anchors) >= 2
    )


def _is_direct_primitive_fragment(text: str, *, turn_text: str | None = None) -> bool:
    """Return true only for mechanics owned by the deterministic V2 compiler.

    ``turn_text`` is the whole user turn. A mechanic can span more of the turn than
    the fragment that named it: ``sweeps below the previous candle low and reclaims
    it on 15m`` is one primitive, but the fragment reader cuts it at ``and`` and puts
    the reclaim half in a timeframe fragment. Judging the sweep on the fragment alone
    saw a pierce with no reclaim and refused a primitive the compiler owns.
    """

    lowered = text.casefold()
    if _SWEEP_RE.search(lowered):
        # A pierce without its reclaim is a different mechanic, so the reclaim must
        # be stated somewhere in the same turn — never assumed.
        return bool(_RECLAIM_RE.search((turn_text or text).casefold()))
    return bool(
        _NAMED_PRIMITIVE_RE.search(lowered) or _PRICE_COMPARISON_RE.search(lowered)
    )


def _extraction_context(draft: StrategyDraftV2) -> dict[str, Any]:
    """Send only authoritative semantic state needed to construct the next patch."""

    return {
        "schema_version": draft.schema_version,
        "draft_id": str(draft.draft_id),
        "version": draft.version,
        "mode": draft.mode.value,
        "name": draft.name,
        "universe": draft.universe.model_dump(mode="json"),
        "market_scope": draft.market_scope.model_dump(mode="json"),
        "condition_ast": (
            draft.condition_ast.model_dump(mode="json")
            if draft.condition_ast is not None
            else None
        ),
        "unresolved_fields": [
            item.model_dump(mode="json") for item in draft.unresolved_fields
        ],
        "unsupported_requirements": [
            item.model_dump(mode="json") for item in draft.unsupported_requirements
        ],
        "provider_requirements": [
            item.model_dump(mode="json") for item in draft.provider_requirements
        ],
        "approval": {
            "approved": draft.approval.approved,
            "draft_version": draft.approval.draft_version,
            "semantic_hash": draft.approval.semantic_hash,
        },
        "semantic_hash": draft.semantic_hash,
    }


_PATCH_PROMPT = """You extract exactly one patch for HilalMarkets StrategyDraftV2.
Use only the current user turn and the supplied canonical draft. Never reconstruct the
whole strategy from conversation memory. Every added or replaced condition must quote
an exact source_fragment from the current turn and use the supplied source_turn_id.

Preserve formula, operands, direction, comparator, threshold, unit, timeframe roles,
reference definition, Boolean ordering, and parentheses exactly. Do not choose a nearby
capability. Standard symbols, exchanges, quote assets, spot scope, timeframes,
percentages, directions, operators, corrections, and exclusions are fields, not
capabilities. If an exact executable representation is unavailable, add one typed
blocking unsupported requirement with the original fragment and missing contract.
If a material definition is missing, add one concise unresolved reference. Do not
invent defaults.

Conversation, greetings, acknowledgements, formatting requests, objections,
explanations, and approval wording must not appear in a patch. Exclusions must remain
only in exclusion fields and can never become positive conditions. Questions,
examples, alternatives, and A/B/C option lists are not selected requirements. Preserve
only unequivocal user statements; represent each material unanswered choice as one
unresolved reference rather than selecting or compiling an example. A request for the
assistant to choose a trading definition does not authorize that choice. Return one
strict StrategyPatchExtraction object and no prose.

When the current turn supplies several independent conditions, preserve all of them as
one recursive AND/OR/NOT tree in replace_groups. Do not compress several formulas into
one node. Use FormulaKind.CAPABILITY only for an exact registered capability contract;
put the exact indicator parameters in its operand. If the turn directly answers an
existing unresolved field, do not repeat that unresolved item in the patch."""
