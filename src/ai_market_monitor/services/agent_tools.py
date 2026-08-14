from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatSession,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    CapabilityExtension,
    Strategy,
    StrategyVersion,
)
from ai_market_monitor.db.models.enums import StrategyStatus
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.formula_compiler import (
    compile_explicit_formula_group,
    parse_percentage_formula,
)
from ai_market_monitor.engine.strategy_state import StrategyDraftState
from ai_market_monitor.engine.turn_fragments import classify_turn
from ai_market_monitor.schemas.agent_control import (
    AgentToolResult,
    CompileStrategyDraftArgs,
    GetCustomCapabilityStatusArgs,
    GetMarketSnapshotArgs,
    GetMonitorStatusArgs,
    GetRecentScannerResultArgs,
    InspectCurrentDraftArgs,
    InspectScreenedWatchlistArgs,
    ListWatchPlansArgs,
    RequestCustomCapabilityArgs,
    ResolveTradingCapabilitiesArgs,
    RunOneTimeScanArgs,
    ValidateCapabilitySelectionArgs,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition, StrategyDirection
from ai_market_monitor.services.agent_policy import (
    AgentRuntimePolicyState,
    AgentServerContext,
)
from ai_market_monitor.services.capability_extension_scope import CapabilityExtensionScopeError
from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
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
            "list_watch_plans": self._list_watch_plans,
            "inspect_screened_watchlist": self._inspect_screened_watchlist,
            "get_recent_scanner_result": self._get_recent_scanner_result,
            "request_custom_capability": self._request_custom_capability,
            "get_custom_capability_status": self._get_custom_capability_status,
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
        # The model selects the tool, but source-fragment authority remains on the
        # server. Re-derive candidate mechanics from exact persisted user turns so
        # a paraphrase cannot become executable logic or produce a false
        # "not user-authored" dead end.
        authored_mechanics = _authoritative_mechanic_fragments(runtime)
        capability_fragments = [
            fragment
            for fragment in authored_mechanics
            if not _is_platform_screening_only_fragment(fragment)
            and not _is_deterministic_core_fragment(fragment, runtime)
        ]
        report = self.resolver.resolve_prompt("\n".join(capability_fragments))
        explicit_timeframes = _explicit_timeframes(
            " ".join([runtime.accumulated_setup, runtime.context.request_text])
        )
        default_timeframe = args.default_timeframe
        scanner_default_timeframe = (
            runtime.context.setup_mode == "scanner" and default_timeframe == "15m"
        )
        timeframe_authorized = (
            default_timeframe is None
            or default_timeframe in explicit_timeframes
            or scanner_default_timeframe
        )
        if default_timeframe and not timeframe_authorized:
            return AgentToolResult(
                status="validation_error",
                tool_name="resolve_trading_capabilities",
                call_id=call_id,
                warnings=["The model cannot choose a timeframe the user did not provide."],
                allowed_next_actions=["answer_clarification"],
            )
        candidates = {
            candidate.capability_key for item in report.fragments for candidate in item.candidates
        }
        sources = {_normalized(item.fragment) for item in report.fragments}
        runtime.policy_state.candidate_capability_keys.update(candidates)
        runtime.policy_state.candidate_source_fragments.update(sources)
        unresolved = [item for item in report.fragments if item.status != "matched"]
        missing_timeframe = (
            not explicit_timeframes
            and not runtime.chat.draft_schema_json
            and runtime.context.setup_mode != "scanner"
        )
        runtime.policy_state.resolution_complete = not unresolved and not missing_timeframe
        clarifications = [
            {
                "key": f"capability_meaning_{index}",
                "question": item.clarification_question
                or f"Which registered meaning should Hilal Markets use for '{item.fragment}'?",
                "reason": "Hilal Markets will not silently substitute a different market mechanic.",
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
                    "question": "Which candle timeframe should Hilal Markets evaluate?",
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
            "assumptions": (
                [
                    (
                        "Scanner uses the platform's 15m default and current market snapshot "
                        "because no timeframe or closed-candle requirement was stated."
                    )
                ]
                if runtime.context.setup_mode == "scanner" and not explicit_timeframes
                else []
            ),
            "ignored_model_fragments": [
                fragment
                for fragment in args.fragments
                if not _is_user_authored_fragment(fragment, runtime)
            ],
            "deterministic_fragments": [
                fragment
                for fragment in authored_mechanics
                if _is_deterministic_core_fragment(fragment, runtime)
            ],
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
            allowed_next_actions=(["answer_clarification"] if clarifications else ["review_draft"]),
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
                warnings=[f"{capability.label} does not support {args.direction} direction."],
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
                warnings=["The required flag conflicts with the user's required/optional wording."],
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
                    "Hilal Markets needs the user to provide numeric values for: "
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
                and _normalized(item["source_fragment"]) == _normalized(binding["source_fragment"])
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
            evidence_refs=[f"capability:{rule.capability_key}:v{rule.capability_version}"],
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

    async def _request_custom_capability(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        args = RequestCustomCapabilityArgs.model_validate(raw.model_dump())
        try:
            extension = await CapabilityExtensionService(self.settings).request(
                session,
                user_id=runtime.context.user_id,
                chat_session_id=runtime.chat.id,
                source_prompt=args.source_fragment,
                conversation_history=runtime.history,
            )
        except CapabilityExtensionScopeError as exc:
            return AgentToolResult(
                status="validation_error",
                tool_name="request_custom_capability",
                call_id=call_id,
                data={
                    "source_fragment": args.source_fragment,
                    "dependency_category": exc.dependency.category,
                    "matched_term": exc.dependency.matched_term,
                    "queued": False,
                },
                warnings=[str(exc)],
                allowed_next_actions=["answer_clarification"],
            )
        context = dict(runtime.chat.context_json or {})
        context["capability_extension_id"] = str(extension.id)
        context["capability_extension_status"] = extension.status
        context["capability_extension_source_fragment"] = args.source_fragment
        runtime.chat.context_json = context
        certified = bool(extension.certified_at and extension.artifact_hash)
        if not certified:
            runtime.chat.status = "building_mechanic"
        return AgentToolResult(
            status="success",
            tool_name="request_custom_capability",
            call_id=call_id,
            data={
                "extension_id": str(extension.id),
                "capability_key": extension.capability_key,
                "capability_version": extension.capability_version,
                "status": extension.status,
                "stage": extension.stage,
                "source_fragment": args.source_fragment,
                "certified": certified,
                "activation_is_external": True,
            },
            evidence_refs=[f"capability-extension:{extension.id}"],
            warnings=(
                []
                if certified
                else [
                    "The mechanic is queued for deterministic certification and is not "
                    "executable yet."
                ]
            ),
            allowed_next_actions=["review_custom_capability"],
        )

    async def _list_watch_plans(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        args = ListWatchPlansArgs.model_validate(raw.model_dump())
        query = select(Strategy).where(
            Strategy.user_id == runtime.context.user_id,
            Strategy.archived_at.is_(None),
        )
        status_map = {
            "draft": StrategyStatus.DRAFT,
            "active": StrategyStatus.ACTIVE,
            "paused": StrategyStatus.PAUSED,
        }
        if args.status != "all":
            query = query.where(Strategy.status == status_map[args.status])
        plans = list(
            (
                await session.scalars(
                    query.order_by(Strategy.updated_at.desc(), Strategy.id).limit(50)
                )
            ).all()
        )
        return AgentToolResult(
            status="success",
            tool_name="list_watch_plans",
            call_id=call_id,
            data={
                "filter": args.status,
                "count": len(plans),
                "watch_plans": [
                    {
                        "monitor_id": str(item.id),
                        "name": item.name,
                        "status": item.status.value,
                        "active_version_id": (
                            str(item.active_version_id) if item.active_version_id else None
                        ),
                        "activated_at": item.activated_at,
                        "paused_at": item.paused_at,
                    }
                    for item in plans
                ],
                "truncated": len(plans) == 50,
            },
            evidence_refs=[f"watch-plans:user:{runtime.context.user_id}"],
            allowed_next_actions=["open_monitor"],
        )

    async def _inspect_screened_watchlist(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        InspectScreenedWatchlistArgs.model_validate(raw.model_dump())
        watchlists = list(
            (
                await session.scalars(
                    select(ApprovedWatchlist)
                    .where(ApprovedWatchlist.user_id == runtime.context.user_id)
                    .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name)
                    .limit(20)
                )
            ).all()
        )
        watchlist_ids = [item.id for item in watchlists]
        assets = (
            list(
                (
                    await session.scalars(
                        select(ApprovedWatchlistAsset)
                        .where(ApprovedWatchlistAsset.watchlist_id.in_(watchlist_ids))
                        .order_by(ApprovedWatchlistAsset.added_at.desc())
                        .limit(500)
                    )
                ).all()
            )
            if watchlist_ids
            else []
        )
        assets_by_watchlist: dict[UUID, list[str]] = {}
        for asset in assets:
            assets_by_watchlist.setdefault(asset.watchlist_id, []).append(asset.canonical_asset)
        return AgentToolResult(
            status="success",
            tool_name="inspect_screened_watchlist",
            call_id=call_id,
            data={
                "watchlists": [
                    {
                        "watchlist_id": str(item.id),
                        "name": item.name,
                        "is_default": item.is_default,
                        "assets": assets_by_watchlist.get(item.id, []),
                    }
                    for item in watchlists
                ],
                "watchlist_count": len(watchlists),
                "asset_count": len(assets),
                "truncated": len(assets) == 500,
                "policy_note": (
                    "Saved assets remain subject to the current Sharia policy and exact "
                    "exchange-market availability."
                ),
            },
            evidence_refs=[f"screened-watchlist:user:{runtime.context.user_id}"],
        )

    async def _get_recent_scanner_result(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        del session
        GetRecentScannerResultArgs.model_validate(raw.model_dump())
        result = dict((runtime.chat.context_json or {}).get("scanner_result") or {})
        if not result:
            return AgentToolResult(
                status="unavailable",
                tool_name="get_recent_scanner_result",
                call_id=call_id,
                warnings=["This chat has no persisted Scanner result to explain."],
                allowed_next_actions=["run_scan"],
            )
        bounded = dict(result)
        bounded["results"] = list(result.get("results") or [])[:30]
        bounded["common_missing_reasons"] = list(result.get("common_missing_reasons") or [])[:10]
        evidence_refs = [
            str(reference)[:300]
            for reference in list(result.get("evidence_refs") or [])[:30]
            if str(reference).strip()
        ]
        if not evidence_refs and result.get("evidence_ref"):
            evidence_refs = [str(result["evidence_ref"])[:300]]
        if not evidence_refs:
            evidence_refs = [f"scanner-chat:{runtime.chat.id}"]
        return AgentToolResult(
            status="success",
            tool_name="get_recent_scanner_result",
            call_id=call_id,
            data=bounded,
            evidence_refs=evidence_refs,
            allowed_next_actions=["review_draft", "run_scan"],
        )

    async def _get_custom_capability_status(
        self,
        session: AsyncSession,
        runtime: AgentToolRuntime,
        call_id: str,
        raw: BaseModel,
    ) -> AgentToolResult:
        args = GetCustomCapabilityStatusArgs.model_validate(raw.model_dump())
        extension = await session.get(CapabilityExtension, UUID(args.extension_id))
        if extension is None or extension.user_id != runtime.context.user_id:
            return AgentToolResult(
                status="blocked",
                tool_name="get_custom_capability_status",
                call_id=call_id,
                warnings=["Custom capability status is unavailable for this user."],
            )
        report = dict(extension.validation_report or {})
        return AgentToolResult(
            status="success",
            tool_name="get_custom_capability_status",
            call_id=call_id,
            data={
                "extension_id": str(extension.id),
                "capability_key": extension.capability_key,
                "capability_version": extension.capability_version,
                "status": extension.status,
                "stage": extension.stage,
                "certified": bool(extension.certified_at and extension.artifact_hash),
                "validation_score": extension.validation_score,
                "failure_classification": extension.failure_classification,
                "validation_summary": {
                    key: report.get(key)
                    for key in (
                        "passed",
                        "candidate_rate",
                        "symbols_scanned",
                        "holdout_passed",
                        "replay_consistent",
                    )
                    if key in report
                },
                "pending_strategy_version_id": (
                    str(extension.pending_strategy_version_id)
                    if extension.pending_strategy_version_id
                    else None
                ),
                "requires_user_approval": bool(extension.pending_strategy_version_id),
                "last_error": extension.last_error[:500] if extension.last_error else None,
            },
            evidence_refs=[f"capability-extension:{extension.id}"],
            warnings=(
                []
                if extension.certified_at and extension.artifact_hash
                else ["This capability is not executable unless certification succeeds."]
            ),
            allowed_next_actions=["review_custom_capability"],
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
        if name == "get_custom_capability_status" and context.current_capability_extension_id:
            schema["properties"]["extension_id"]["enum"] = [
                str(context.current_capability_extension_id)
            ]
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
        "Resolve exact user-authored trading fragments against Hilal Markets' immutable capability "
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
    "list_watch_plans": (
        ListWatchPlansArgs,
        "List the authenticated user's persisted Watchlists with a server-enforced status "
        "filter. This tool is read-only and cannot activate, pause, edit, or delete a plan.",
    ),
    "inspect_screened_watchlist": (
        InspectScreenedWatchlistArgs,
        "Read the authenticated user's My Screened Watchlist assets. Saved assets do not "
        "override current Sharia policy or market availability.",
    ),
    "get_recent_scanner_result": (
        GetRecentScannerResultArgs,
        "Read the latest authoritative Scanner result already persisted in this setup chat. "
        "This never runs a new scan.",
    ),
    "request_custom_capability": (
        RequestCustomCapabilityArgs,
        "After explicit current-turn user consent, enqueue one unresolved OHLCV-computable "
        "fragment in the existing deterministic certification pipeline. This does not certify, "
        "approve, activate, or execute the mechanic.",
    ),
    "get_custom_capability_status": (
        GetCustomCapabilityStatusArgs,
        "Read the current deterministic certification status for the authenticated chat's "
        "custom capability request. This cannot approve, activate, repair, or execute it.",
    ),
}


#: Keywords a strict wire schema does not need to carry, because the Pydantic model the
#: answer is parsed into checks every one of them again. Stripping them is a size change,
#: never a guarantee change: a value outside a bound is refused exactly as before.
#:
#: ``enum`` is deliberately absent. An enum tells the model which values exist, which is
#: meaning rather than a bound.
_REDUNDANT_WIRE_KEYWORDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "maxLength",
        "minLength",
        "maxItems",
        "minItems",
        "pattern",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minimum",
        "maximum",
    }
)


class StrictSchemaError(TypeError):
    """A model that cannot be expressed as a strict provider schema.

    The case this catches is an open map — ``dict[str, X]``. A strict schema requires
    ``additionalProperties: false`` on every object, so an open map becomes an object
    that accepts **nothing at all**. The model is asked for a value it has no legal way
    to supply, and whatever it tries is dropped without a word.

    ``ConditionNodeV2.capability_parameters`` was exactly this, and it means the previous
    Setup Chat contract could never carry a capability's parameters. The fix is a list of
    name/value pairs, which a strict schema can express; the compact planner contract uses
    that shape.
    """


def strict_json_schema(
    model: type[BaseModel],
    *,
    compact: bool | None = None,
    allow_open_maps: bool = False,
) -> dict[str, Any]:
    """The provider-facing schema for one model.

    ``compact`` strips the keywords above. It defaults to whatever the model class
    declares in ``compact_wire_schema``, so the decision lives with the contract rather
    than with each call site — two call sites disagreeing about the same model would send
    one schema and price another.

    ``allow_open_maps`` keeps the old, broken behaviour for one caller. See
    :class:`StrictSchemaError` for what is broken about it.
    """

    if compact is None:
        compact = bool(getattr(model, "compact_wire_schema", False))
    return _built_schema(model, compact, allow_open_maps)


@lru_cache(maxsize=64)
def _built_schema(
    model: type[BaseModel],
    compact: bool,
    allow_open_maps: bool,
) -> dict[str, Any]:
    """Build once per model. Rebuilding measured ~18 ms, three times a turn."""

    return deepcopy(
        _build_strict_schema(model, compact=compact, allow_open_maps=allow_open_maps)
    )


def _build_strict_schema(
    model: type[BaseModel],
    *,
    compact: bool,
    allow_open_maps: bool,
) -> dict[str, Any]:
    schema = model.model_json_schema()

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if compact:
                for keyword in _REDUNDANT_WIRE_KEYWORDS:
                    node.pop(keyword, None)
            if node.get("type") == "object" or "properties" in node:
                open_map = node.get("additionalProperties")
                if (
                    isinstance(open_map, dict)
                    and not node.get("properties")
                    and not allow_open_maps
                ):
                    raise StrictSchemaError(
                        f"{model.__name__}.{path or '<root>'} is an open map, which a "
                        "strict schema cannot express; use a list of name/value pairs"
                    )
                properties = node.setdefault("properties", {})
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for key, value in node.items():
                visit(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for item in node:
                visit(item, path)

    visit(schema, "")
    return schema


def _authoritative_mechanic_fragments(runtime: AgentToolRuntime) -> list[str]:
    """Return exact user-authored mechanics, never model paraphrases."""

    result: list[str] = []
    seen: set[str] = set()
    turns = [*runtime.setup_fragments, runtime.context.request_text]
    for turn in turns:
        collapsed = " ".join(str(turn).split())
        if not collapsed:
            continue
        if _is_deterministic_core_fragment(collapsed, runtime):
            candidates = [collapsed]
        else:
            candidates = [fragment.text for fragment in classify_turn(collapsed).trading_conditions]
        for candidate in candidates:
            fingerprint = _normalized(candidate)
            if fingerprint and fingerprint not in seen:
                seen.add(fingerprint)
                result.append(candidate)
    return result


def _is_deterministic_core_fragment(
    fragment: str,
    runtime: AgentToolRuntime,
) -> bool:
    """Whether the compiler can represent the fragment without registry search."""

    stored = (runtime.chat.context_json or {}).get("strategy_state")
    resolved = StrategyDraftState.from_dict(stored if isinstance(stored, dict) else None).resolved()
    timeframe = str(resolved.get("base_timeframe") or "15m")
    try:
        direction = StrategyDirection(str(resolved.get("direction") or "long"))
    except ValueError:
        direction = StrategyDirection.LONG
    return bool(
        compile_explicit_formula_group(fragment, timeframe=timeframe)
        or parse_percentage_formula(
            fragment,
            default_timeframe=timeframe,
            default_direction=direction,
        )
    )


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


def _is_platform_screening_only_fragment(fragment: str) -> bool:
    lowered = _normalized(fragment)
    if not re.search(r"\b(?:halal|shariah?|screened)\b", lowered):
        return False
    remainder = re.sub(
        r"\b(?:all|only|the|our|platform|hilalmarkets|hilal markets|currently|"
        r"halal|shariah?|screened|approved|eligible|spot|market|markets|"
        r"coin|coins|asset|assets|token|tokens|pair|pairs|universe|"
        r"find|show|scan|bring|me|in|on|from)\b",
        " ",
        lowered,
    )
    return not " ".join(remainder.split())


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
    authored = " ".join([runtime.context.request_text, runtime.accumulated_setup]).casefold()
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
    return _sha256_json({"tool_name": tool_name, "arguments": arguments.model_dump(mode="json")})
