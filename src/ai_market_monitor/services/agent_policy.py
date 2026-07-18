from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AISetupChatSession, CapabilityExtension, Strategy
from ai_market_monitor.schemas.agent_control import (
    AGENT_TOOL_ARGUMENT_MODELS,
    GetCustomCapabilityStatusArgs,
    GetMonitorStatusArgs,
    RequestCustomCapabilityArgs,
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
    "list_watch_plans": "guarded",
    "inspect_screened_watchlist": "guarded",
    "get_recent_scanner_result": "guarded",
    "request_custom_capability": "confirmation_required",
    "get_custom_capability_status": "guarded",
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
    capability_extension_enabled: bool = False
    explicit_custom_capability_consent: bool = False
    custom_capability_source_fragments: frozenset[str] = frozenset()
    custom_capability_requests_today: int = 0
    custom_capability_daily_limit: int = 0
    current_capability_extension_id: UUID | None = None
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

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

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
        capability_resolution = dict(context.get("capability_resolution") or {})
        pending = dict(context.get("awaiting_clarification") or {})
        consented_source = _custom_capability_consent_source(
            text,
            pending=pending,
            capability_resolution=capability_resolution,
        )
        custom_sources = {consented_source} if consented_source else set()
        extension_enabled = bool(
            self.settings
            and self.settings.capability_extension_enabled
            and self.settings.openai_api_key is not None
        )
        requests_today = 0
        daily_limit = (
            self.settings.capability_extension_daily_limit if self.settings else 0
        )
        if extension_enabled:
            day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            requests_today = int(
                await session.scalar(
                    select(func.count(CapabilityExtension.id)).where(
                        CapabilityExtension.user_id == chat.user_id,
                        CapabilityExtension.created_at >= day_start,
                    )
                )
                or 0
            )
        explicit_custom_consent = bool(consented_source)
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
            capability_extension_enabled=extension_enabled,
            explicit_custom_capability_consent=explicit_custom_consent,
            custom_capability_source_fragments=frozenset(custom_sources),
            custom_capability_requests_today=requests_today,
            custom_capability_daily_limit=daily_limit,
            current_capability_extension_id=_optional_uuid(
                context.get("capability_extension_id")
            ),
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
        if _asks_about_watch_plans(context.request_text):
            allowed.append("list_watch_plans")
        if _asks_about_screened_watchlist(context.request_text):
            allowed.append("inspect_screened_watchlist")
        if _asks_about_scanner_results(context.request_text) and (
            context.has_draft or context.setup_mode == "scanner"
        ):
            allowed.append("get_recent_scanner_result")
        if context.setup_language and mutating_state:
            allowed.append("resolve_trading_capabilities")
        if runtime.candidate_capability_keys and mutating_state:
            allowed.append("validate_capability_selection")
        if runtime.resolution_complete and mutating_state:
            allowed.append("compile_strategy_draft")
        if (
            mutating_state
            and context.capability_extension_enabled
            and context.explicit_custom_capability_consent
            and context.custom_capability_source_fragments
            and context.custom_capability_requests_today
            < context.custom_capability_daily_limit
        ):
            allowed.append("request_custom_capability")
        if context.current_capability_extension_id is not None:
            allowed.append("get_custom_capability_status")
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
        elif tool_name == "request_custom_capability":
            if not isinstance(arguments, RequestCustomCapabilityArgs):
                raise AgentPolicyViolation(
                    "invalid_tool_arguments",
                    "Custom capability arguments had the wrong server type.",
                )
            if not context.capability_extension_enabled:
                raise AgentPolicyViolation(
                    "custom_capability_disabled",
                    "Custom capability creation is not enabled.",
                )
            if not context.explicit_custom_capability_consent:
                raise AgentPolicyViolation(
                    "custom_capability_confirmation_required",
                    "Custom capability creation requires explicit consent in this user turn.",
                )
            if context.custom_capability_requests_today >= context.custom_capability_daily_limit:
                raise AgentPolicyViolation(
                    "custom_capability_daily_limit",
                    "The custom capability daily limit has been reached.",
                )
            normalized_source = " ".join(arguments.source_fragment.casefold().split())
            allowed_sources = {
                " ".join(item.casefold().split())
                for item in context.custom_capability_source_fragments
            }
            if normalized_source not in allowed_sources:
                raise AgentPolicyViolation(
                    "custom_capability_source_mismatch",
                    "The proposed mechanic does not match the unresolved user-authored fragment.",
                )
        elif tool_name == "get_custom_capability_status":
            if not isinstance(arguments, GetCustomCapabilityStatusArgs):
                raise AgentPolicyViolation(
                    "invalid_tool_arguments",
                    "Custom capability status arguments had the wrong server type.",
                )
            if (
                context.current_capability_extension_id is None
                or UUID(arguments.extension_id) != context.current_capability_extension_id
            ):
                raise AgentPolicyViolation(
                    "custom_capability_not_owned",
                    "The requested custom capability is not part of this authenticated chat.",
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
    arabic_setup_terms = (
        "\u0623\u0631\u0627\u0642\u0628",
        "\u0627\u0631\u0627\u0642\u0628",
        "\u0631\u0627\u0642\u0628",
        "\u062a\u0646\u0628\u064a\u0647",
        "\u0646\u0628\u0647",
        "\u0643\u0633\u0631",
        "\u0645\u0642\u0627\u0648\u0645\u0629",
        "\u062f\u0639\u0645",
        "\u0634\u0645\u0639\u0629",
        "\u0625\u063a\u0644\u0627\u0642",
        "\u0627\u063a\u0644\u0627\u0642",
        "\u062d\u062c\u0645",
        "\u0633\u064a\u0648\u0644\u0629",
        "\u0642\u0645\u0629",
        "\u0642\u0627\u0639",
        "\u0633\u0639\u0631",
        "\u0639\u0645\u0644\u0627\u062a",
        "\u0639\u0645\u0644\u0629",
        "\u0623\u0632\u0648\u0627\u062c",
        "\u0627\u0632\u0648\u0627\u062c",
        "\u0641\u0631\u064a\u0645",
        "\u0633\u0628\u0648\u062a",
    )
    arabizi_setup_terms = (
        "kasr",
        "moqawma",
        "mokawma",
        "da3m",
        "sham3a",
        "eghlak",
        "se3r",
        "seyola",
        "tanbeeh",
        "nabeh",
        "ra2eb",
        "rakeb",
    )
    return any(
        term in value
        for term in (*setup_terms, *arabic_setup_terms, *arabizi_setup_terms)
    )


def _has_explicit_custom_capability_consent(value: str) -> bool:
    lowered = value.casefold().strip()
    if re.search(
        r"\b(?:yes\s*[,.-]?\s*)?(?:build|create|certify|make)\s+"
        r"(?:(?:this|that|the)\s+)?(?:custom\s+)?(?:mechanic|capability|rule)\b",
        lowered,
    ):
        return True
    if lowered in {
        "yes",
        "yes, build it",
        "build it",
        "create it",
        "ah ebniha",
        "ebniha",
        "e3mlo",
    }:
        return True
    return lowered in {
        "\u0646\u0639\u0645",
        "\u0623\u0646\u0634\u0626\u0647\u0627",
        "\u0627\u0646\u0634\u0626\u0647\u0627",
        "\u0627\u0628\u0646\u0647\u0627",
        "\u0627\u0639\u0645\u0644\u0647\u0627",
    }


def _custom_capability_consent_source(
    value: str,
    *,
    pending: dict[str, Any],
    capability_resolution: dict[str, Any],
) -> str | None:
    """Bind short consent to the exact active custom-mechanic question."""
    if not _has_explicit_custom_capability_consent(value):
        return None
    clarification = pending.get("clarification")
    if not isinstance(clarification, dict):
        return None
    options = clarification.get("options")
    if not isinstance(options, list) or not any(
        isinstance(option, dict)
        and (
            option.get("action") == "build_mechanic"
            or option.get("value") == "__build_mechanic__"
        )
        for option in options
    ):
        return None
    pending_key = str(pending.get("key") or clarification.get("key") or "")
    for fragment in capability_resolution.get("fragments") or []:
        if not isinstance(fragment, dict):
            continue
        source = " ".join(str(fragment.get("fragment") or "").split())
        if not source:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", source.casefold()).strip("_")[:48]
        if pending_key == f"capability_meaning_{slug or 'unknown'}":
            return source
    return None


def _asks_about_watch_plans(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:my\s+)?(?:watch\s+plans?|monitors?|setups?)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _asks_about_screened_watchlist(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:my\s+)?(?:screened\s+)?watchlist\b|\bsaved\s+(?:assets?|coins?)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _asks_about_scanner_results(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:scanner|scan)\b.{0,36}\b(?:result|found|match|forming|failed|why)\b|"
            r"\b(?:explain|show)\b.{0,24}\b(?:scanner|scan)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
