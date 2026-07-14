from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AISetupChatSession, Strategy, StrategyVersion
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.schemas.agent_control import (
    AgentToolResult,
    CompileStrategyDraftArgs,
    GetMarketSnapshotArgs,
    GetMonitorStatusArgs,
    InspectCurrentDraftArgs,
    ResolveTradingCapabilitiesArgs,
    RunOneTimeScanArgs,
    ValidateCapabilitySelectionArgs,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.agent_policy import (
    AgentRuntimePolicyState,
    AgentServerContext,
)
from ai_market_monitor.services.setup_observability import SetupObservabilityService

CompileDraftCallback = Callable[
    [AsyncSession, AISetupChatSession, str, list[dict[str, Any]]],
    Awaitable[dict[str, Any]],
]
MarketSnapshotCallback = Callable[[str | None, str], Awaitable[dict[str, Any]]]
RunScannerCallback = Callable[
    [AsyncSession, AISetupChatSession, str],
    Awaitable[dict[str, Any]],
]


@dataclass(slots=True)
class AgentToolRuntime:
    context: AgentServerContext
    chat: AISetupChatSession
    history: list[dict[str, str]]
    setup_fragments: list[str]
    policy_state: AgentRuntimePolicyState = field(default_factory=AgentRuntimePolicyState)
    validated_bindings: list[dict[str, Any]] = field(default_factory=list)
    resolution_data: dict[str, Any] | None = None
    results: list[AgentToolResult] = field(default_factory=list)

    @property
    def accumulated_setup(self) -> str:
        return "\n".join(self.setup_fragments)


class AgentToolService:
    """Adapts existing domain services; it contains no independent trading mechanics."""

    def __init__(
        self,
        settings: Settings,
        *,
        compile_draft: CompileDraftCallback,
        market_snapshot: MarketSnapshotCallback,
        run_scanner: RunScannerCallback,
    ) -> None:
        self.settings = settings
        self.compile_draft_callback = compile_draft
        self.market_snapshot_callback = market_snapshot
        self.run_scanner_callback = run_scanner
        self.resolver = get_capability_index().resolver

    def openai_tools(
        self,
        names: tuple[str, ...],
        *,
        context: AgentServerContext,
    ) -> list[dict[str, Any]]:
        return [self._openai_tool(name, context=context) for name in names]

    async def execute(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        *,
        call_id: str,
        tool_name: str,
        arguments: BaseModel,
    ) -> AgentToolResult:
        dispatch = {
            "resolve_trading_capabilities": self._resolve_trading_capabilities,
            "validate_capability_selection": self._validate_capability_selection,
            "compile_strategy_draft": self._compile_strategy_draft,
            "get_market_snapshot": self._get_market_snapshot,
            "run_one_time_scan": self._run_one_time_scan,
            "inspect_current_draft": self._inspect_current_draft,
            "get_monitor_status": self._get_monitor_status,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return AgentToolResult(
                status="blocked",
                tool_name=tool_name,
                call_id=call_id,
                warnings=["Unknown tools cannot execute."],
                authoritative=True,
            )
        result = await handler(session, runtime, call_id, arguments)
        runtime.results.append(result)
        if result.status == "success":
            runtime.policy_state.successful_tools.add(tool_name)
        return result

    async def _resolve_trading_capabilities(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        del session
        args = ResolveTradingCapabilitiesArgs.model_validate(raw.model_dump())
        unauthorized = [
            fragment
            for fragment in args.fragments
            if not _is_user_authored_fragment(fragment, runtime)
        ]
        if unauthorized:
            return AgentToolResult(
                status="validation_error",
                tool_name="resolve_trading_capabilities",
                call_id=call_id,
                warnings=[
                    "Capability resolution accepts only exact user-authored source fragments."
                ],
                allowed_next_actions=["answer_clarification"],
            )

        report = self.resolver.resolve_prompt("\n".join(args.fragments))
        explicit_timeframes = _explicit_timeframes(
            " ".join([runtime.accumulated_setup, runtime.context.request_text])
        )
        default_timeframe = args.default_timeframe
        timeframe_authorized = default_timeframe is None or default_timeframe in explicit_timeframes
        if default_timeframe and not timeframe_authorized:
            return AgentToolResult(
                status="validation_error",
                tool_name="resolve_trading_capabilities",
                call_id=call_id,
                warnings=["The model cannot choose a timeframe the user did not provide."],
                allowed_next_actions=["answer_clarification"],
            )
        for fragment in args.fragments:
            if _normalized(fragment) not in {
                _normalized(item) for item in runtime.setup_fragments
            }:
                runtime.setup_fragments.append(fragment)

        candidates = {
            candidate.capability_key
            for item in report.fragments
            for candidate in item.candidates
        }
        sources = {_normalized(item.fragment) for item in report.fragments}
        runtime.policy_state.candidate_capability_keys.update(candidates)
        runtime.policy_state.candidate_source_fragments.update(sources)
        unresolved = [item for item in report.fragments if item.status != "matched"]
        missing_timeframe = not explicit_timeframes and not runtime.chat.draft_schema_json
        runtime.policy_state.resolution_complete = not unresolved and not missing_timeframe
        clarifications = [
            {
                "key": f"capability_meaning_{index}",
                "question": item.clarification_question
                or f"Which registered meaning should HilalMarkets use for '{item.fragment}'?",
                "reason": "HilalMarkets will not silently substitute a different market mechanic.",
                "source_fragment": item.fragment,
                "options": [
                    {
                        "key": f"capability_meaning_{index}",
                        "label": candidate.label,
                        "value": f"{candidate.label} ({candidate.capability_key})",
                        "description": (
                            f"Registered capability | {candidate.availability} | "
                            f"confidence {candidate.confidence:.0%}"
                        ),
                        "action": "answer",
                    }
                    for candidate in item.candidates[:5]
                ],
            }
            for index, item in enumerate(unresolved)
        ]
        if missing_timeframe:
            clarifications.append(
                {
                    "key": "timeframe_choice",
                    "question": "Which candle timeframe should HilalMarkets evaluate?",
                    "reason": "A timeframe is required and cannot be selected silently.",
                    "source_fragment": "",
                    "options": [],
                }
            )
        provider_requirements = []
        for capability_key in sorted(candidates):
            capability = self.resolver.get(capability_key)
            availability = self.resolver.compatibility(capability_key).availability
            if availability == "available" and not capability.provider_requirements:
                continue
            provider_requirements.append(
                {
                    "capability_key": capability_key,
                    "availability": availability,
                    "requirements": list(capability.provider_requirements),
                }
            )
        data = report.to_dict() | {
            "clarifications": clarifications,
            "missing_parameters": ["timeframe"] if missing_timeframe else [],
            "unsupported_fragments": [
                item.fragment for item in report.fragments if not item.candidates
            ],
            "provider_requirements": provider_requirements,
        }
        runtime.resolution_data = data
        context = dict(runtime.chat.context_json or {})
        context["setup_fragments"] = runtime.setup_fragments[-30:]
        context["capability_resolution"] = data
        runtime.chat.context_json = context
        if not runtime.chat.original_idea and runtime.setup_fragments:
            runtime.chat.original_idea = runtime.setup_fragments[0]
        evidence_ref = f"capability-resolution:{_sha256_json(data)[:24]}"
        return AgentToolResult(
            status="success",
            tool_name="resolve_trading_capabilities",
            call_id=call_id,
            data=data,
            evidence_refs=[evidence_ref],
            warnings=(
                ["One or more fragments require user clarification."] if clarifications else []
            ),
            allowed_next_actions=(
                ["answer_clarification"] if clarifications else ["review_draft"]
            ),
        )

    async def _validate_capability_selection(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        del session
        args = ValidateCapabilitySelectionArgs.model_validate(raw.model_dump())
        if not _is_user_authored_fragment(args.source_fragment, runtime):
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                warnings=["The source fragment is not present in the user's setup language."],
                allowed_next_actions=["answer_clarification"],
            )
        explicit = _explicit_timeframes(
            " ".join([runtime.accumulated_setup, runtime.context.request_text])
        )
        if args.timeframe not in explicit and not _draft_uses_timeframe(
            runtime.chat.draft_schema_json, args.timeframe
        ):
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                warnings=["The selected timeframe was not supplied by the user."],
                allowed_next_actions=["answer_clarification"],
            )
        try:
            capability = self.resolver.get(args.capability_key)
        except ValueError as exc:
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=[str(exc)[:500]],
                allowed_next_actions=["answer_clarification"],
            )
        if args.direction and args.direction not in capability.direction_support:
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=[
                    f"{capability.label} does not support {args.direction} direction."
                ],
                allowed_next_actions=["answer_clarification"],
            )
        if args.direction and not _direction_is_user_authored(
            args.direction,
            args.source_fragment,
        ):
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=["The selected direction was not expressed by the user."],
                allowed_next_actions=["answer_clarification"],
            )
        parameters = {item.name: item.value for item in args.parameters}
        expected_required = _required_from_source(args.source_fragment)
        if args.required != expected_required:
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=[
                    "The required flag conflicts with the user's required/optional wording."
                ],
                allowed_next_actions=["answer_clarification"],
            )
        ungrounded_numbers = [
            name
            for name, value in parameters.items()
            if _numeric_parameter_requires_user_value(name, value)
            and not _numeric_value_is_user_authored(value, runtime)
        ]
        if ungrounded_numbers:
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=[
                    "HilalMarkets needs the user to provide numeric values for: "
                    + ", ".join(sorted(ungrounded_numbers))
                ],
                allowed_next_actions=["answer_clarification"],
            )
        if args.comparator and not _comparator_is_user_authored(
            args.comparator,
            args.source_fragment,
        ):
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=["The selected comparator was not expressed by the user."],
                allowed_next_actions=["answer_clarification"],
            )
        try:
            rule = self.resolver.validate_selection(
                capability_key=args.capability_key,
                parameters=parameters,
                timeframe=args.timeframe,
                required=args.required,
                source_fragment=args.source_fragment,
                comparator=args.comparator,
            )
        except ValueError as exc:
            return AgentToolResult(
                status="validation_error",
                tool_name="validate_capability_selection",
                call_id=call_id,
                data={"capability_key": args.capability_key},
                warnings=[str(exc)[:500]],
                allowed_next_actions=["answer_clarification"],
            )
        binding: dict[str, Any] = {
            "capability_key": rule.capability_key,
            "parameters": parameters,
            "timeframe": args.timeframe,
            "direction": args.direction,
            "required": args.required,
            "source_fragment": args.source_fragment,
            "confidence": rule.confidence,
            "selection_source": "bounded_agent_registry_validation",
        }
        runtime.validated_bindings = [
            item
            for item in runtime.validated_bindings
            if not (
                item["capability_key"] == binding["capability_key"]
                and _normalized(item["source_fragment"])
                == _normalized(binding["source_fragment"])
            )
        ]
        runtime.validated_bindings.append(binding)
        resolved_sources = {
            _normalized(item["source_fragment"]) for item in runtime.validated_bindings
        }
        unresolved_sources = {
            _normalized(item.get("fragment") or "")
            for item in (runtime.resolution_data or {}).get("fragments", [])
            if item.get("status") != "matched"
        }
        missing = (runtime.resolution_data or {}).get("missing_parameters") or []
        runtime.policy_state.resolution_complete = (
            unresolved_sources.issubset(resolved_sources) and not missing
        )
        return AgentToolResult(
            status="success",
            tool_name="validate_capability_selection",
            call_id=call_id,
            data={
                "capability_key": rule.capability_key,
                "capability_version": rule.capability_version,
                "validated_parameters": parameters,
                "timeframe": rule.timeframe,
                "direction": args.direction,
                "required": rule.required,
                "source_fragment": rule.source_fragment,
            },
            evidence_refs=[
                f"capability:{rule.capability_key}:v{rule.capability_version}"
            ],
            allowed_next_actions=["review_draft"],
        )

    async def _compile_strategy_draft(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        CompileStrategyDraftArgs.model_validate(raw.model_dump())
        if not runtime.policy_state.resolution_complete:
            return AgentToolResult(
                status="blocked",
                tool_name="compile_strategy_draft",
                call_id=call_id,
                warnings=["Capability resolution still has unresolved or missing information."],
                allowed_next_actions=["answer_clarification"],
            )
        context = dict(runtime.chat.context_json or {})
        existing = list(context.get("capability_bindings") or [])
        bindings = _deduplicate_bindings([*existing, *runtime.validated_bindings])
        context["capability_bindings"] = bindings
        context["setup_fragments"] = runtime.setup_fragments[-30:]
        runtime.chat.context_json = context
        try:
            artifact = await self.compile_draft_callback(
                session,
                runtime.chat,
                runtime.accumulated_setup,
                bindings,
            )
        except Exception as exc:
            return AgentToolResult(
                status="validation_error",
                tool_name="compile_strategy_draft",
                call_id=call_id,
                warnings=[f"Deterministic draft validation failed: {type(exc).__name__}"],
                allowed_next_actions=["answer_clarification"],
            )
        canonical_hash = str(artifact.get("canonical_hash") or "")
        if len(canonical_hash) != 64:
            return AgentToolResult(
                status="validation_error",
                tool_name="compile_strategy_draft",
                call_id=call_id,
                warnings=["The compiler did not produce a valid canonical strategy hash."],
            )
        runtime.policy_state.compiled_hash = canonical_hash
        return AgentToolResult(
            status="success",
            tool_name="compile_strategy_draft",
            call_id=call_id,
            data=artifact,
            evidence_refs=[f"strategy-draft:{canonical_hash}"],
            warnings=[
                str(item.get("message") or item.get("code") or "Strategy lint finding")
                for item in artifact.get("lint_warnings", [])
            ][:20],
            allowed_next_actions=["review_draft"],
        )

    async def _get_market_snapshot(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        del session, runtime
        args = GetMarketSnapshotArgs.model_validate(raw.model_dump())
        quote_currency = (args.quote_currency or "USDT").upper()
        snapshot = await self.market_snapshot_callback(args.exchange, quote_currency)
        if snapshot.get("status") != "available":
            return AgentToolResult(
                status="unavailable",
                tool_name="get_market_snapshot",
                call_id=call_id,
                data=snapshot,
                warnings=[
                    str(snapshot.get("unavailable_reason") or "Market data was unavailable.")
                ],
                allowed_next_actions=["retry"],
            )
        evidence_ref = (
            f"market-snapshot:{snapshot.get('provider_name')}:{snapshot.get('captured_at')}"
        )
        return AgentToolResult(
            status="success",
            tool_name="get_market_snapshot",
            call_id=call_id,
            data=snapshot,
            evidence_refs=[evidence_ref[:300]],
        )

    async def _run_one_time_scan(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        args = RunOneTimeScanArgs.model_validate(raw.model_dump())
        current_hash = runtime.policy_state.compiled_hash or runtime.context.draft_hash
        if args.expected_draft_hash != current_hash:
            return AgentToolResult(
                status="blocked",
                tool_name="run_one_time_scan",
                call_id=call_id,
                warnings=["The draft changed before the scan could start."],
                allowed_next_actions=["review_draft"],
            )
        existing = dict((runtime.chat.context_json or {}).get("scanner_result") or {})
        if existing.get("draft_hash") == current_hash:
            return AgentToolResult(
                status="success",
                tool_name="run_one_time_scan",
                call_id=call_id,
                data=existing,
                evidence_refs=list(existing.get("evidence_refs") or []),
                warnings=["The current draft was already scanned; the stored result was reused."],
            )
        try:
            result = await self.run_scanner_callback(session, runtime.chat, str(current_hash))
        except Exception as exc:
            return AgentToolResult(
                status="unavailable",
                tool_name="run_one_time_scan",
                call_id=call_id,
                warnings=[f"Scanner was unavailable: {type(exc).__name__}"],
                allowed_next_actions=["retry"],
            )
        evidence_refs = list(result.get("evidence_refs") or [])
        return AgentToolResult(
            status="success",
            tool_name="run_one_time_scan",
            call_id=call_id,
            data=result,
            evidence_refs=evidence_refs,
        )

    async def _inspect_current_draft(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        del session
        InspectCurrentDraftArgs.model_validate(raw.model_dump())
        definition = None
        canonical_hash = None
        if runtime.chat.draft_schema_json:
            definition_model = StrategyDefinition.model_validate(runtime.chat.draft_schema_json)
            definition = definition_model.model_dump(mode="json")
            canonical_hash = definition_model.canonical_hash()
        data = {
            "status": runtime.chat.status,
            "setup_mode": runtime.context.setup_mode,
            "draft": definition,
            "canonical_hash": canonical_hash,
            "lint_warnings": runtime.chat.lint_warnings or [],
            "ambiguities": runtime.chat.ambiguities or [],
            "unsupported_conditions": runtime.chat.unsupported_conditions or [],
            "rule_confidence": runtime.chat.rule_confidence or [],
            "approval_eligible": runtime.chat.status == "ready_for_approval",
            "approval_is_external": True,
        }
        refs = [f"strategy-draft:{canonical_hash}"] if canonical_hash else []
        return AgentToolResult(
            status="success",
            tool_name="inspect_current_draft",
            call_id=call_id,
            data=data,
            evidence_refs=refs,
            allowed_next_actions=["review_draft"] if canonical_hash else [],
        )

    async def _get_monitor_status(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        args = GetMonitorStatusArgs.model_validate(raw.model_dump())
        monitor_id = UUID(args.monitor_id)
        strategy = await session.get(Strategy, monitor_id)
        if strategy is None or strategy.user_id != runtime.context.user_id:
            return AgentToolResult(
                status="blocked",
                tool_name="get_monitor_status",
                call_id=call_id,
                warnings=["Monitor status is unavailable for this user."],
            )
        version = (
            await session.get(StrategyVersion, strategy.active_version_id)
            if strategy.active_version_id
            else None
        )
        health = await SetupObservabilityService(session, self.settings).health(
            runtime.context.user_id,
            monitor_id=strategy.id,
        )
        data = {
            "monitor_id": str(strategy.id),
            "monitor_name": strategy.name,
            "status": strategy.status.value,
            "active_version_id": str(version.id) if version else None,
            "active_version_number": version.version_number if version else None,
            "activated_at": strategy.activated_at,
            "paused_at": strategy.paused_at,
            "health": health[0] if health else None,
        }
        return AgentToolResult(
            status="success",
            tool_name="get_monitor_status",
            call_id=call_id,
            data=data,
            evidence_refs=[f"monitor:{strategy.id}"],
            allowed_next_actions=["open_monitor"],
        )

    def _openai_tool(
        self,
        name: str,
        *,
        context: AgentServerContext,
    ) -> dict[str, Any]:
        model, description = _TOOL_MODELS_AND_DESCRIPTIONS[name]
        schema = strict_json_schema(model)
        if name == "get_monitor_status" and context.owned_monitor_ids:
            schema["properties"]["monitor_id"]["enum"] = sorted(
                str(item) for item in context.owned_monitor_ids
            )
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": schema,
            "strict": True,
        }


_TOOL_MODELS_AND_DESCRIPTIONS: dict[str, tuple[type[BaseModel], str]] = {
    "resolve_trading_capabilities": (
        ResolveTradingCapabilitiesArgs,
        "Resolve exact user-authored trading fragments against HilalMarkets' immutable capability "
        "registry. Use for setup language; never paraphrase the fragments.",
    ),
    "validate_capability_selection": (
        ValidateCapabilitySelectionArgs,
        "Validate one shortlisted registry capability, its schema-declared parameters, timeframe, "
        "required flag, comparator, availability, and exact user source fragment.",
    ),
    "compile_strategy_draft": (
        CompileStrategyDraftArgs,
        "Compile the accumulated user-authored setup and validated bindings into a deterministic "
        "draft. This cannot approve or activate a monitor.",
    ),
    "get_market_snapshot": (
        GetMarketSnapshotArgs,
        "Get a provider-backed crypto spot market snapshot. Use this for current market facts; "
        "return unavailable rather than estimating.",
    ),
    "run_one_time_scan": (
        RunOneTimeScanArgs,
        "Run one idempotent Scanner-mode scan for the current validated draft hash. Available only "
        "after an explicit user scan request and entitlement check.",
    ),
    "inspect_current_draft": (
        InspectCurrentDraftArgs,
        "Read the authoritative current draft, lint, unresolved issues, confidence, hash, and "
        "approval eligibility without changing it.",
    ),
    "get_monitor_status": (
        GetMonitorStatusArgs,
        "Read persisted status and health for one monitor from the authenticated user's offered "
        "monitor IDs.",
    ),
}


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                properties = node.setdefault("properties", {})
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return schema


def _is_user_authored_fragment(fragment: str, runtime: AgentToolRuntime) -> bool:
    needle = _normalized(fragment)
    if not needle:
        return False
    authored = [
        _normalized(runtime.context.request_text),
        *map(_normalized, runtime.setup_fragments),
    ]
    return any(needle in item for item in authored if item)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _explicit_timeframes(value: str) -> set[str]:
    unit_map = {
        "m": "m",
        "min": "m",
        "mins": "m",
        "minute": "m",
        "minutes": "m",
        "h": "h",
        "hr": "h",
        "hrs": "h",
        "hour": "h",
        "hours": "h",
        "d": "d",
        "day": "d",
        "days": "d",
        "w": "w",
        "week": "w",
        "weeks": "w",
    }
    matches = {
        f"{amount}{unit_map[unit.casefold()]}"
        for amount, unit in re.findall(
            r"\b(\d{1,3})\s*[- ]?"
            r"(m|mins?|minutes?|h|hrs?|hours?|d|days?|w|weeks?)\b",
            value,
            flags=re.IGNORECASE,
        )
    }
    lowered = value.casefold()
    natural = {
        "1m": ("one minute", "one-minute"),
        "5m": ("five minute", "five-minute"),
        "15m": ("fifteen minute", "fifteen-minute"),
        "30m": ("thirty minute", "thirty-minute"),
        "1h": ("hourly", "one hour", "one-hour"),
        "4h": ("four hour", "four-hour"),
        "1d": ("daily", "one day", "one-day"),
        "1w": ("weekly", "one week", "one-week"),
    }
    matches.update(
        timeframe
        for timeframe, phrases in natural.items()
        if any(phrase in lowered for phrase in phrases)
    )
    return matches


def _required_from_source(source_fragment: str) -> bool:
    return not bool(
        re.search(
            r"\b(?:optional(?:ly)?|nice\s+to\s+have|suggestion|if\s+possible)\b",
            source_fragment,
            flags=re.IGNORECASE,
        )
    )


def _numeric_parameter_requires_user_value(name: str, value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    tokens = (
        "threshold",
        "period",
        "length",
        "lookback",
        "bars",
        "candles",
        "window",
        "multiplier",
        "percent",
        "percentage",
        "distance",
    )
    return any(token in name.casefold() for token in tokens)


def _numeric_value_is_user_authored(value: Any, runtime: AgentToolRuntime) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = format(float(value), "g")
    authored = " ".join(
        [runtime.context.request_text, runtime.accumulated_setup]
    ).casefold()
    return bool(re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", authored))


def _comparator_is_user_authored(comparator: str, source_fragment: str) -> bool:
    if comparator == "is_true":
        return True
    lowered = source_fragment.casefold()
    if comparator == "crosses_above":
        return bool(re.search(r"\b(?:cross(?:es|ed)?|reclaim(?:s|ed)?)\b.{0,24}\babove\b", lowered))
    if comparator == "crosses_below":
        return bool(re.search(r"\b(?:cross(?:es|ed)?)\b.{0,24}\bbelow\b", lowered))
    vocabulary = {
        "above": ("above", "over", "greater", "higher"),
        "below": ("below", "under", "less", "lower"),
        "gt": ("above", "over", "greater", "higher"),
        "gte": ("above", "at least", "minimum", "or more"),
        "lt": ("below", "under", "less", "lower"),
        "lte": ("below", "at most", "maximum", "or less"),
        "eq": ("equal", "equals", "exactly", "is"),
        "greater_than": ("above", "over", "greater", "higher"),
        "greater_than_or_equal": ("at least", "minimum", "or more"),
        "less_than": ("below", "under", "less", "lower"),
        "less_than_or_equal": ("at most", "maximum", "or less"),
        "equals": ("equal", "equals", "exactly", "is"),
        "is_false": ("not", "avoid", "without", "no "),
    }
    words = vocabulary.get(comparator)
    if not words:
        return False
    return any(word in lowered for word in words)


def _direction_is_user_authored(direction: str, source_fragment: str) -> bool:
    vocabulary = {
        "bullish": ("bullish", "long", "upward", "green"),
        "bearish": ("bearish", "short", "downward", "red"),
        "neutral": ("neutral", "either direction", "both directions", "any direction"),
    }
    lowered = source_fragment.casefold()
    return any(phrase in lowered for phrase in vocabulary[direction])


def _draft_uses_timeframe(payload: dict[str, Any] | None, timeframe: str) -> bool:
    if not payload:
        return False
    try:
        definition = StrategyDefinition.model_validate(payload)
    except ValueError:
        return False
    return timeframe in {definition.base_timeframe, *definition.supporting_timeframes}


def _deduplicate_bindings(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for binding in bindings:
        key = (
            str(binding.get("capability_key") or ""),
            _normalized(str(binding.get("source_fragment") or "")),
        )
        if all(key):
            deduplicated[key] = binding
    return list(deduplicated.values())


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def redact_agent_arguments(tool_name: str, arguments: BaseModel) -> dict[str, Any]:
    payload = arguments.model_dump(mode="json")
    for field_name in ("fragments", "source_fragment"):
        if field_name not in payload:
            continue
        value = payload[field_name]
        values = value if isinstance(value, list) else [value]
        payload[field_name] = [
            {"sha256": hashlib.sha256(str(item).encode()).hexdigest(), "characters": len(str(item))}
            for item in values
        ]
    return {"tool": tool_name, "arguments": payload}


def canonical_argument_hash(tool_name: str, arguments: BaseModel) -> str:
    return _sha256_json(
        {"tool_name": tool_name, "arguments": arguments.model_dump(mode="json")}
    )
