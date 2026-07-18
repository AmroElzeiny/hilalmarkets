from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentToolName = Literal[
    "resolve_trading_capabilities",
    "validate_capability_selection",
    "compile_strategy_draft",
    "get_market_snapshot",
    "run_one_time_scan",
    "inspect_current_draft",
    "get_monitor_status",
    "list_watch_plans",
    "inspect_screened_watchlist",
    "get_recent_scanner_result",
    "request_custom_capability",
    "get_custom_capability_status",
]
AgentToolStatus = Literal[
    "success",
    "unavailable",
    "blocked",
    "validation_error",
    "requires_confirmation",
]
AgentFinalIntent = Literal[
    "clarify",
    "explain",
    "draft_ready",
    "scan_result",
    "market_snapshot",
    "monitor_status",
    "refusal",
    "unavailable",
    "error",
]
AgentFinalStatus = Literal["completed", "needs_user_input", "blocked", "failed"]
AgentActionType = Literal[
    "answer_clarification",
    "review_draft",
    "run_scan",
    "open_monitor",
    "retry",
    "start_revision",
    "review_custom_capability",
]

AgentConversationStage = Literal[
    "DISCOVER_INTENT",
    "CLARIFY_SETUP",
    "RESOLVE_CAPABILITIES",
    "BUILD_DRAFT",
    "REVIEW_TRANSLATION",
    "RUN_MARKET_CHECK",
    "EXPLAIN_RESULTS",
    "REQUEST_APPROVAL",
    "MANAGE_EXISTING_PLAN",
    "CREATE_CUSTOM_CAPABILITY",
    "RECOVER_FROM_FAILURE",
    "GENERAL_PRODUCT_HELP",
]

ClauseCoverageStatus = Literal[
    "COVERED",
    "NEEDS_CLARIFICATION",
    "PROVIDER_UNAVAILABLE",
    "INTENTIONALLY_OPTIONAL",
    "NON_EXECUTABLE_CONTEXT",
    "REJECTED_BY_USER",
    "CONFLICTING",
]


class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgentParameterValue(StrictAgentModel):

    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    value: str | int | float | bool

    @field_validator("value")
    @classmethod
    def bound_string_values(cls, value: str | int | float | bool) -> str | int | float | bool:
        if isinstance(value, str) and len(value) > 200:
            raise ValueError("String parameter values cannot exceed 200 characters")
        return value


class ResolveTradingCapabilitiesArgs(StrictAgentModel):

    fragments: list[str] = Field(min_length=1, max_length=12)
    default_timeframe: str | None = Field(max_length=10)

    @field_validator("fragments")
    @classmethod
    def validate_fragments(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 1000 for item in cleaned):
            raise ValueError("Each source fragment must contain 1-1000 characters")
        return cleaned


class ValidateCapabilitySelectionArgs(StrictAgentModel):

    capability_key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    parameters: list[AgentParameterValue] = Field(max_length=20)
    timeframe: str = Field(min_length=2, max_length=10)
    direction: Literal["bullish", "bearish", "neutral"] | None
    required: bool
    source_fragment: str = Field(min_length=1, max_length=1000)
    comparator: str | None = Field(max_length=40)


class CompileStrategyDraftArgs(StrictAgentModel):
    pass


class GetMarketSnapshotArgs(StrictAgentModel):

    exchange: Literal["binance", "bybit"] | None
    quote_currency: Literal["USDT", "USDC"] | None


class RunOneTimeScanArgs(StrictAgentModel):

    expected_draft_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class InspectCurrentDraftArgs(StrictAgentModel):
    pass


class GetMonitorStatusArgs(StrictAgentModel):

    monitor_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        ),
    )

    @field_validator("monitor_id")
    @classmethod
    def canonicalize_monitor_id(cls, value: str) -> str:
        return str(UUID(value))


class ListWatchPlansArgs(StrictAgentModel):

    status: Literal["all", "draft", "active", "paused"]


class InspectScreenedWatchlistArgs(StrictAgentModel):
    pass


class GetRecentScannerResultArgs(StrictAgentModel):
    pass


class RequestCustomCapabilityArgs(StrictAgentModel):

    source_fragment: str = Field(min_length=3, max_length=1000)
    confirmed_by_user: Literal[True]

    @field_validator("source_fragment")
    @classmethod
    def clean_source_fragment(cls, value: str) -> str:
        return " ".join(value.split())


class GetCustomCapabilityStatusArgs(StrictAgentModel):

    extension_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
            r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
        ),
    )

    @field_validator("extension_id")
    @classmethod
    def canonicalize_extension_id(cls, value: str) -> str:
        return str(UUID(value))


class AgentToolResult(StrictAgentModel):

    status: AgentToolStatus
    tool_name: str = Field(min_length=1, max_length=80)
    call_id: str = Field(min_length=1, max_length=160)
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    allowed_next_actions: list[AgentActionType] = Field(default_factory=list, max_length=10)
    authoritative: bool = True


class AgentSuggestedAction(StrictAgentModel):

    type: AgentActionType
    label: str = Field(min_length=1, max_length=120)


class AgentClarification(StrictAgentModel):

    key: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=500)
    options: list[str] = Field(default_factory=list, max_length=8)
    allows_free_text: bool = True


class AgentClauseCoverage(StrictAgentModel):

    source_fragment: str = Field(min_length=1, max_length=1000)
    source_message_id: str | None = Field(default=None, max_length=64)
    status: ClauseCoverageStatus
    compiled_field: str | None = Field(default=None, max_length=160)
    capability_key: str | None = Field(default=None, max_length=120)
    capability_version: str | None = Field(default=None, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    required: bool | None = None
    explanation: str = Field(min_length=1, max_length=500)


class SetupConversationRequirement(StrictAgentModel):

    field: str = Field(min_length=1, max_length=160)
    value: Any
    source_message_ids: list[str] = Field(default_factory=list, max_length=20)
    confirmed: bool = False


class SetupChatConversationState(StrictAgentModel):
    """Application-owned memory; executable authority remains StrategyDefinition."""

    version: int = 1
    original_request: str = Field(default="", max_length=5000)
    confirmed_requirements: list[SetupConversationRequirement] = Field(
        default_factory=list,
        max_length=100,
    )
    rejected_interpretations: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    latest_correction_by_field: dict[str, Any] = Field(default_factory=dict)
    required_conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    optional_conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    exclusion_conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    logical_structure: dict[str, Any] = Field(default_factory=dict)
    timeframes: list[str] = Field(default_factory=list, max_length=20)
    direction: str | None = Field(default=None, max_length=40)
    universe: dict[str, Any] = Field(default_factory=dict)
    selected_methodology: dict[str, Any] = Field(default_factory=dict)
    delivery_preferences: list[str] = Field(default_factory=list, max_length=20)
    alert_timing: dict[str, Any] = Field(default_factory=dict)
    invalidation_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    setup_mode: Literal["scanner", "monitor"] = "monitor"
    custom_terminology: dict[str, str] = Field(default_factory=dict)
    unresolved_contradictions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    capability_bindings: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    user_message_ids: list[str] = Field(default_factory=list, max_length=200)
    tool_results: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    draft_version: dict[str, Any] = Field(default_factory=dict)
    clause_coverage: list[AgentClauseCoverage] = Field(default_factory=list, max_length=100)


class AgentFinalResponse(StrictAgentModel):

    message: str = Field(min_length=1, max_length=1800)
    intent: AgentFinalIntent
    status: AgentFinalStatus
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    suggested_actions: list[AgentSuggestedAction] = Field(default_factory=list, max_length=6)
    requires_user_confirmation: bool
    stage: AgentConversationStage = "DISCOVER_INTENT"
    user_intent: str = Field(default="unknown", min_length=1, max_length=120)
    assistant_message: str = Field(default="", max_length=1800)
    proposed_tool_calls: list[AgentToolName] = Field(default_factory=list, max_length=8)
    clarification: AgentClarification | None = None
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    unresolved_conflicts: list[str] = Field(default_factory=list, max_length=30)
    referenced_user_message_ids: list[str] = Field(default_factory=list, max_length=30)
    source_clause_coverage: list[AgentClauseCoverage] = Field(
        default_factory=list,
        max_length=100,
    )
    can_continue: bool = True
    refusal_reason: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("message")
    @classmethod
    def disallow_model_authored_urls(cls, value: str) -> str:
        if re.search(r"(?:https?://|www\.)", value, flags=re.IGNORECASE):
            raise ValueError("Agent messages cannot contain model-authored URLs")
        return value.strip()

    @field_validator("assistant_message")
    @classmethod
    def disallow_urls_in_assistant_message(cls, value: str) -> str:
        if re.search(r"(?:https?://|www\.)", value, flags=re.IGNORECASE):
            raise ValueError("Agent messages cannot contain model-authored URLs")
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def bound_evidence_refs(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 300 for item in value):
            raise ValueError("Evidence references must contain 1-300 characters")
        return value


class AgentUsageTotals(StrictAgentModel):

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)


class AgentBudgetState(StrictAgentModel):

    max_steps: int = Field(ge=1, le=12)
    max_tool_calls: int = Field(ge=1, le=20)
    max_repeated_calls: int = Field(ge=0, le=3)
    timeout_seconds: int = Field(ge=1, le=180)
    tool_timeout_seconds: int = Field(ge=1, le=120)
    max_output_tokens: int = Field(ge=128, le=8000)
    max_estimated_cost_usd: float = Field(gt=0, le=5)


AGENT_TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "resolve_trading_capabilities": ResolveTradingCapabilitiesArgs,
    "validate_capability_selection": ValidateCapabilitySelectionArgs,
    "compile_strategy_draft": CompileStrategyDraftArgs,
    "get_market_snapshot": GetMarketSnapshotArgs,
    "run_one_time_scan": RunOneTimeScanArgs,
    "inspect_current_draft": InspectCurrentDraftArgs,
    "get_monitor_status": GetMonitorStatusArgs,
    "list_watch_plans": ListWatchPlansArgs,
    "inspect_screened_watchlist": InspectScreenedWatchlistArgs,
    "get_recent_scanner_result": GetRecentScannerResultArgs,
    "request_custom_capability": RequestCustomCapabilityArgs,
    "get_custom_capability_status": GetCustomCapabilityStatusArgs,
}
