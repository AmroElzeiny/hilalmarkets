from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AISetupChatSession, Strategy
from ai_market_monitor.schemas.agent_control import (
    AGENT_TOOL_ARGUMENT_MODELS,
    GetMonitorStatusArgs,
    RunOneTimeScanArgs,
    ValidateCapabilitySelectionArgs,
)
from ai_market_monitor.services.entitlements import EntitlementService

ToolClassification = Literal["safe", "guarded", "confirmation_required", "forbidden"]

TOOL_CLASSIFICATIONS: dict[str, ToolClassification] = {
    "resolve_trading_capabilities": "safe",
    "validate_capability_selection": "guarded",
    "compile_strategy_draft": "guarded",
    "get_market_snapshot": "safe",
    "run_one_time_scan": "confirmation_required",
    "inspect_current_draft": "safe",
    "get_monitor_status": "guarded",
}

FORBIDDEN_AGENT_TOOLS = frozenset(
    {
        "approve_strategy",
        "activate_monitor",
        "create_monitor",
        "modify_billing",
        "change_entitlements",
        "send_notification",
        "execute_code",
        "execute_python",
        "execute_sql",
        "execute_shell",
        "http_request",
        "modify_capability_registry",
        "create_dynamic_mechanic",
        "repair_dynamic_mechanic",
        "place_trade",
        "simulate_trade",
    }
)


class AgentPolicyViolation(ValueError):
    def __init__(self, code: str, message: str, *, fatal: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.fatal = fatal


@dataclass(slots=True)
class AgentServerContext:
    user_id: UUID
    chat_id: UUID
    request_text: str
    chat_status: str
    setup_mode: Literal["scanner", "monitor"]
    has_draft: bool
    draft_hash: str | None
    has_pending_clarification: bool
    explicit_scan_request: bool
    explicit_revision_request: bool
    market_question: bool
    monitor_question: bool
    setup_language: bool
    scan_entitled: bool
    owned_monitor_ids: frozenset[UUID] = frozenset()


@dataclass(slots=True)
class AgentRuntimePolicyState:
    successful_tools: set[str] = field(default_factory=set)
    candidate_capability_keys: set[str] = field(default_factory=set)
    candidate_source_fragments: set[str] = field(default_factory=set)
    resolution_complete: bool = False
    compiled_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AgentPolicyDecision:
    tool_name: str
    classification: ToolClassification
    arguments: BaseModel


class AgentPolicyService:
    """Builds a small tool surface from server-owned state for every model step."""

    async def build_context(
        self,
        session: AsyncSession,
        *,
        chat: AISetupChatSession,
        request_text: str,
    ) -> AgentServerContext:
        text = " ".join(request_text.split())
        lowered = text.casefold()
        setup_mode: Literal["scanner", "monitor"] = (
            "scanner" if (chat.context_json or {}).get("setup_mode") == "scanner" else "monitor"
        )
        explicit_scan = bool(
            re.search(
                r"\b(?:run|start|repeat|rerun|execute|launch)\b.{0,24}\bscan\b|\bscan\s+now\b",
                lowered,
            )
        )
        market_question = bool(
            re.search(
                r"\b(?:market|markets)\b.{0,32}\b(?:today|now|looks?|snapshot|doing|status)\b|"
                r"\bhow\s+(?:is|are)\s+(?:the\s+)?market",
                lowered,
            )
        )
        monitor_question = bool(
            re.search(
                r"\b(?:my\s+)?monitor\b.{0,40}\b(?:status|working|healthy|paused|active|scan)",
                lowered,
            )
        )
        explicit_revision = bool(
            re.search(
                r"\b(?:change|correct|replace|revise|edit|instead|not\s+the|like\s+i\s+said)\b",
                lowered,
            )
        )
        setup_language = _looks_like_setup_language(lowered)
        owned_ids = frozenset(
            (
                await session.scalars(
                    select(Strategy.id).where(
                        Strategy.user_id == chat.user_id,
                        Strategy.archived_at.is_(None),
                    )
                )
            ).all()
        )
        scan_entitled = False
        if explicit_scan and setup_mode == "scanner":
            entitlement = await EntitlementService(session).current(chat.user_id)
            scan_entitled = entitlement.feature_enabled("light_prompt_scan")
        context = dict(chat.context_json or {})
        return AgentServerContext(
            user_id=chat.user_id,
            chat_id=chat.id,
            request_text=text,
            chat_status=chat.status,
            setup_mode=setup_mode,
            has_draft=bool(chat.draft_schema_json),
            draft_hash=context.get("schema_hash"),
            has_pending_clarification=bool(context.get("awaiting_clarification")),
            explicit_scan_request=explicit_scan,
            explicit_revision_request=explicit_revision,
            market_question=market_question,
            monitor_question=monitor_question,
            setup_language=setup_language,
            scan_entitled=scan_entitled,
            owned_monitor_ids=owned_ids,
        )

    def allowed_tools(
        self,
        context: AgentServerContext,
        runtime: AgentRuntimePolicyState,
    ) -> tuple[str, ...]:
        allowed: list[str] = []
        mutating_state = context.chat_status not in {"approved", "building_mechanic"}

        if context.has_draft or context.has_pending_clarification or context.setup_language:
            allowed.append("inspect_current_draft")
        if context.market_question:
            allowed.append("get_market_snapshot")
        if context.monitor_question and context.owned_monitor_ids:
            allowed.append("get_monitor_status")
        if context.setup_language and mutating_state:
            allowed.append("resolve_trading_capabilities")
        if runtime.candidate_capability_keys and mutating_state:
            allowed.append("validate_capability_selection")
        if runtime.resolution_complete and mutating_state:
            allowed.append("compile_strategy_draft")
        draft_hash = runtime.compiled_hash or context.draft_hash
        if (
            context.explicit_scan_request
            and context.setup_mode == "scanner"
            and context.scan_entitled
            and draft_hash
        ):
            allowed.append("run_one_time_scan")
        return tuple(dict.fromkeys(allowed))

    def validate_call(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        offered_tools: tuple[str, ...],
        context: AgentServerContext,
        runtime: AgentRuntimePolicyState,
    ) -> AgentPolicyDecision:
        if tool_name in FORBIDDEN_AGENT_TOOLS:
            raise AgentPolicyViolation(
                "forbidden_tool",
                f"The agent is not authorized to call {tool_name}.",
            )
        model = AGENT_TOOL_ARGUMENT_MODELS.get(tool_name)
        if model is None:
            raise AgentPolicyViolation("unknown_tool", "The requested agent tool does not exist.")
        if tool_name not in offered_tools:
            raise AgentPolicyViolation(
                "tool_not_offered",
                f"{tool_name} was not available in this execution step.",
            )
        try:
            arguments = model.model_validate(raw_arguments)
        except ValidationError as exc:
            raise AgentPolicyViolation(
                "invalid_tool_arguments",
                f"Arguments for {tool_name} failed strict server validation.",
            ) from exc

        if tool_name == "validate_capability_selection":
            if not isinstance(arguments, ValidateCapabilitySelectionArgs):
                raise AgentPolicyViolation(
                    "invalid_tool_arguments",
                    "Capability selection arguments had the wrong server type.",
                )
            capability_key = str(arguments.capability_key)
            source_fragment = " ".join(str(arguments.source_fragment).split())
            if capability_key not in runtime.candidate_capability_keys:
                raise AgentPolicyViolation(
                    "capability_not_shortlisted",
                    "The selected capability was not returned by the current registry shortlist.",
                )
            if source_fragment.casefold() not in runtime.candidate_source_fragments:
                raise AgentPolicyViolation(
                    "source_fragment_not_resolved",
                    "The capability source fragment was not resolved in this turn.",
                )
        elif tool_name == "get_monitor_status":
            if not isinstance(arguments, GetMonitorStatusArgs):
                raise AgentPolicyViolation(
                    "invalid_tool_arguments",
                    "Monitor status arguments had the wrong server type.",
                )
            if UUID(arguments.monitor_id) not in context.owned_monitor_ids:
                raise AgentPolicyViolation(
                    "monitor_not_owned",
                    "The requested monitor is not owned by the authenticated user.",
                )
        elif tool_name == "run_one_time_scan":
            if not isinstance(arguments, RunOneTimeScanArgs):
                raise AgentPolicyViolation(
                    "invalid_tool_arguments",
                    "Scanner arguments had the wrong server type.",
                )
            if not context.explicit_scan_request:
                raise AgentPolicyViolation(
                    "scan_confirmation_required",
                    "A one-time scan requires an explicit request in the current user turn.",
                )
            if not context.scan_entitled:
                raise AgentPolicyViolation(
                    "scan_not_entitled",
                    "The current plan does not permit Scanner.",
                )
            current_hash = runtime.compiled_hash or context.draft_hash
            if arguments.expected_draft_hash != current_hash:
                raise AgentPolicyViolation(
                    "draft_hash_mismatch",
                    "The scan request does not reference the current validated draft.",
                )

        return AgentPolicyDecision(
            tool_name=tool_name,
            classification=TOOL_CLASSIFICATIONS[tool_name],
            arguments=arguments,
        )


def _looks_like_setup_language(value: str) -> bool:
    if not value:
        return False
    if re.fullmatch(r"(?:hi|hello|hey|thanks|thank you|how are you)[.!? ]*", value):
        return False
    setup_terms = (
        "alert",
        "breakout",
        "candle",
        "confirmation",
        "cross",
        "ema",
        "eth",
        "fvg",
        "filter",
        "find coin",
        "high",
        "invalidat",
        "low",
        "macd",
        "monitor",
        "pdh",
        "pdl",
        "price",
        "rsi",
        "rvol",
        "scan",
        "setup",
        "spot pair",
        "sweep",
        "timeframe",
        "volume",
        "vwap",
    )
    return any(term in value for term in setup_terms)
