from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.strategy import StrategyDefinition


class SetupChatOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=300)
    action: Literal["answer", "explain", "other", "build_mechanic"] = "answer"


class SetupChatClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    options: list[SetupChatOption] = Field(default_factory=list, max_length=8)


class SetupChatInterviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["greeting", "setup", "market_snapshot", "out_of_scope", "unsafe"]
    assistant_message: str = Field(min_length=1, max_length=1800)
    ready_to_compile: bool = False
    setup_summary: str | None = Field(default=None, max_length=3000)
    clarifications: list[SetupChatClarification] = Field(default_factory=list, max_length=8)
    suggestions: list[str] = Field(default_factory=list, max_length=8)


class SetupChatTurnSegment(BaseModel):
    """Auditable classification of one user-authored part of a chat turn."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    category: Literal[
        "human_conversation",
        "product_question",
        "option_question",
        "technical_instruction",
        "clarification_answer",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    ]


class SetupChatTurnClassification(BaseModel):
    """AI routing result used before any text can enter the strategy compiler."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "conversation",
        "product_question",
        "option_question",
        "clarification_answer",
        "setup_instruction",
        "setup_revision",
        "mixed",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    ]
    assistant_message: str = Field(default="", max_length=1800)
    technical_fragments: list[str] = Field(default_factory=list, max_length=12)
    clarification_answer: str | None = Field(default=None, max_length=1000)
    segments: list[SetupChatTurnSegment] = Field(default_factory=list, max_length=16)
    preserve_pending_question: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SetupChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=5000)
    option_key: str | None = Field(default=None, max_length=80)
    option_value: str | None = Field(default=None, max_length=500)
    option_label: str | None = Field(default=None, max_length=120)
    client_message_id: str | None = Field(default=None, min_length=8, max_length=80)

    @model_validator(mode="after")
    def require_message_or_option(self) -> "SetupChatMessageRequest":
        if not self.message.strip() and not (self.option_key and self.option_value):
            raise ValueError("message or option selection is required")
        return self


class SetupChatApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: Literal[True]
    expected_schema_hash: str = Field(min_length=64, max_length=64)
    confirmed_low_confidence_rule_keys: list[str] = Field(default_factory=list, max_length=100)


class SetupChatMessageResponse(BaseModel):
    id: UUID
    role: Literal["user", "assistant", "system"]
    message_type: str
    content: str
    payload: dict[str, Any]
    client_message_id: str | None = None
    created_at: datetime


class SetupChatSessionResponse(BaseModel):
    id: UUID
    status: str
    title: str
    original_idea: str | None
    messages: list[SetupChatMessageResponse]
    draft_strategy: StrategyDefinition | None = None
    schema_hash: str | None = None
    translation_sheet: dict[str, Any]
    lint_warnings: list[dict[str, Any]]
    rule_confidence: list[dict[str, Any]]
    assumptions: list[str]
    ambiguities: list[dict[str, Any]]
    unsupported_conditions: list[dict[str, Any]]
    setup_mode: Literal["scanner", "monitor"] | None = None
    can_approve: bool
    can_scan: bool = False
    scanner_result: dict[str, Any] | None = None
    approved_strategy_id: UUID | None
    approved_strategy_version_id: UUID | None
    next_url: str | None = None
    updated_at: datetime


class MarketSnapshotMover(BaseModel):
    symbol: str
    percentage_24h: float


class MarketSnapshotAssetStatus(BaseModel):
    symbol: str
    percentage_24h: float
    direction: Literal["advancing", "declining", "unchanged"]


class MarketSnapshotResponse(BaseModel):
    status: Literal["available", "unavailable"]
    exchange: str
    quote_currency: str
    captured_at: datetime
    provider_name: str
    symbols_checked: int = 0
    advancing: int = 0
    declining: int = 0
    unchanged: int = 0
    average_change_24h: float | None = None
    volatility_label: str | None = None
    dispersion_24h: float | None = None
    btc_status: MarketSnapshotAssetStatus | None = None
    eth_status: MarketSnapshotAssetStatus | None = None
    top_movers: list[MarketSnapshotMover] = Field(default_factory=list)
    bottom_movers: list[MarketSnapshotMover] = Field(default_factory=list)
    data_source: str | None = None
    unavailable_reason: str | None = None
    message: str
    disclaimer: str = "Market context only, not financial advice."
