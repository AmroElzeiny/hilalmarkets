from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ai_market_monitor.schemas.strategy import StrategyDefinition


class EvaluationCondition(BaseModel):
    """Canonical, evaluator-facing view of one validated strategy condition."""

    model_config = ConfigDict(extra="forbid")

    key: str
    path: str
    parent_group_key: str
    label: str
    condition_type: str
    capability_key: str | None = None
    capability_version: str | None = None
    timeframe: str
    comparator: str
    threshold: float | str | bool | dict[str, Any] | None = None
    required: bool
    source_fragment: str | None = None
    confidence: float | None = None
    provider_required: bool
    availability: str


class EvaluationConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    path: str
    parent_group_key: str | None = None
    operator: str
    child_keys: list[str]


class EvaluationCanvasNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    key: str
    node_type: Literal["group", "condition"]
    label: str
    parent_id: str | None = None
    operator: str | None = None
    required: bool | None = None
    capability_key: str | None = None


class EvaluationCanvasEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    order: int = Field(ge=0)


class EvaluationCanvasPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_node_id: str
    nodes: list[EvaluationCanvasNode]
    groups: list[EvaluationCanvasNode]
    edges: list[EvaluationCanvasEdge]


class EvaluationApprovalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_status: str
    #: Explicit position in the setup lifecycle. ``session_status`` is coarse and does
    #: not distinguish an inactive compiled draft from a session still gathering
    #: requirements, which is what made turn completion undetectable.
    lifecycle_state: Literal[
        "collecting",
        "needs_clarification",
        "ready_for_confirmation",
        "awaiting_approval",
        "approved",
        "compiled",
        "activated",
    ] = "collecting"
    #: True only after authenticated approval reaches a final lifecycle state.
    #: Session ``turn_complete`` separately reports transport/UI completion.
    terminal: bool = False
    eligible: bool
    approved: bool
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    strategy_version_number: int | None = Field(default=None, ge=1)
    immutable_version_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )


class SetupChatEvaluationContract(BaseModel):
    """Read-only contract derived from the production-validated strategy draft."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    requires_explicit_approval: Literal[True] = True
    must_not_assign_sharia_status: Literal[True] = True
    sharia_status_assignment_authorized: Literal[False] = False
    strategy: StrategyDefinition
    canonical_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbols: list[str]
    exclusions: list[str]
    direction: str
    timeframes: list[str]
    operators: list[str]
    thresholds: list[float | str | bool | dict[str, Any]]
    condition_groups: list[EvaluationConditionGroup]
    conditions: list[EvaluationCondition]
    filters: list[EvaluationCondition]
    alerts: dict[str, Any]
    assumptions: list[str]
    confidence: list[dict[str, Any]]
    unsupported_capabilities: list[dict[str, Any]]
    provider_required_capabilities: list[EvaluationCondition]
    approval: EvaluationApprovalState
    canvas: EvaluationCanvasPayload
