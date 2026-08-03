from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SystemBrainAssistantHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class SystemBrainAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=2000)
    history: list[SystemBrainAssistantHistoryItem] = Field(
        default_factory=list,
        max_length=10,
    )


class SystemBrainAssistantFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=800)
    severity: Literal["information", "attention", "critical"]
    evidence_ref: str = Field(min_length=1, max_length=240)


class SystemBrainAssistantAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=500)


class SystemBrainAssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=6000)
    findings: list[SystemBrainAssistantFinding] = Field(
        default_factory=list,
        max_length=8,
    )
    suggested_actions: list[SystemBrainAssistantAction] = Field(
        default_factory=list,
        max_length=6,
    )
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    model: str
    reasoning_effort: str


AdminConversationSource = Literal["authenticated_setup_chat", "public_site_chat"]


class AdminConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    source_type: AdminConversationSource
    user_id: UUID | None = None
    user_email: str | None = None
    display_name: str
    anonymous: bool
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    message_count: int = Field(ge=0)
    title_or_topic: str
    lifecycle_or_stage: str
    last_message_excerpt: str | None = None
    has_error: bool
    last_error_code: str | None = None
    approval_state: str | None = None
    model: str | None = None
    estimated_ai_cost: Decimal = Decimal("0")
    unread: bool = False


class AdminConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    message_type: str
    content: str
    created_at: datetime
    lifecycle: str | None = None
    error_code: str | None = None
    model: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)


class AdminConversationTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: AdminConversationSummary
    messages: list[AdminConversationMessage]
    lifecycle_events: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    telemetry_available: bool = False
    transcript_complete: bool = True
    transcript_limitation: str | None = None
    related_links: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class AdminConversationPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminConversationSummary]
    next_cursor: str | None = None
    latest_event_id: int = Field(default=0, ge=0)


class SystemBrainConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="New analysis", min_length=1, max_length=160)


class SystemBrainConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None


class SystemBrainConversationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    title: str
    archived: bool
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class SystemBrainAgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=4000)
    client_message_id: str = Field(min_length=8, max_length=120)


class EvidenceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    freshness: str
    coverage: str
    limitations: list[str] = Field(default_factory=list, max_length=20)


class SystemBrainToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=500)
    user_id: UUID | None = None
    conversation_id: UUID | None = None
    target_id: str | None = Field(default=None, max_length=160)
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=500)
    source: AdminConversationSource | None = None
    lifecycle: str | None = Field(default=None, max_length=80)
    identity: Literal["authenticated", "anonymous"] | None = None
    approval_state: str | None = Field(default=None, max_length=40)
    error_only: bool = False
    path: str | None = Field(default=None, max_length=500)
    line: int = Field(default=1, ge=1, le=1_000_000)
    lines: int = Field(default=30, ge=1, le=80)


class GrowthQualityOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str
    affected_funnel_stage: str
    measured_evidence: str
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    sample_size: int = Field(ge=0)
    estimated_opportunity_range: str
    confidence: Literal["low", "medium", "high"]
    customer_impact: str
    revenue_impact_method: str
    recommended_experiment: str
    success_metric: str
    guardrail_metric: str
    required_human_action: str


class ActionProposalRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    action: str
    target: str
    exact_changes: dict[str, Any]
    reason: str
    evidence: list[str]
    expected_effect: str
    risks: list[str]
    rollback_path: str
    idempotency_key: str
    confirmation_token: str
    status: str
    expires_at: datetime


class ActionExactChanges(BaseModel):
    """Closed model-facing description; canonical services still own mutation payloads."""

    model_config = ConfigDict(extra="forbid")

    tier: str | None = Field(default=None, max_length=40)
    months: int | None = Field(default=None, ge=1, le=120)
    setting_name: str | None = Field(default=None, max_length=120)
    setting_value: str | int | float | bool | None = None
    email_subject: str | None = Field(default=None, max_length=200)
    job_type: str | None = Field(default=None, max_length=100)
    campaign_change: str | None = Field(default=None, max_length=1000)
    governance_decision: str | None = Field(default=None, max_length=1000)
    strategy_version_id: UUID | None = None


class ActionProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=80)
    target_type: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=120)
    exact_changes: ActionExactChanges
    reason: str = Field(min_length=3, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=50)
    expected_effect: str = Field(min_length=3, max_length=2000)
    risks: list[str] = Field(default_factory=list, max_length=20)
    rollback_path: str = Field(min_length=3, max_length=2000)
    idempotency_key: str = Field(min_length=12, max_length=160)


class ActionConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_token: str = Field(min_length=32, max_length=256)
    reason: str = Field(min_length=3, max_length=1000)


class InternalArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    target_id: str | None = Field(default=None, max_length=160)


class SystemBrainArtifactRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    conversation_id: UUID | None = None
    artifact_kind: str
    title: str
    content: str
    evidence_refs: list[str]
    target_id: str | None = None
    created_at: datetime
    updated_at: datetime
    archived: bool
    model_authored_draft: bool = True


class SystemBrainRunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID | None = None
    status: str
    step_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    assistant_message_id: UUID | None = None
    answer: str | None = None


class SystemBrainAgentModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=8000)
    findings: list[SystemBrainAssistantFinding] = Field(default_factory=list, max_length=12)
    opportunities: list[GrowthQualityOpportunity] = Field(default_factory=list, max_length=8)
    suggested_actions: list[SystemBrainAssistantAction] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class SystemBrainAgentTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    run_id: UUID
    status: str
    answer: str
    findings: list[SystemBrainAssistantFinding] = Field(default_factory=list)
    opportunities: list[GrowthQualityOpportunity] = Field(default_factory=list)
    suggested_actions: list[SystemBrainAssistantAction] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    reasoning_effort: str
    usage: dict[str, Any]
    latency_ms: int = Field(ge=0)
