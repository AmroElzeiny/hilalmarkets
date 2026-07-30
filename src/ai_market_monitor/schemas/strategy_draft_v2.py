from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_market_monitor.schemas.strategy import Comparator, Timeframe

STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH = 500


class SetupIntent(StrEnum):
    CONVERSATION = "CONVERSATION"
    PRODUCT_QUESTION = "PRODUCT_QUESTION"
    STRATEGY_PATCH = "STRATEGY_PATCH"
    APPROVAL_ACTION = "APPROVAL_ACTION"
    EXPLANATION_REQUEST = "EXPLANATION_REQUEST"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


class DraftMode(StrEnum):
    SCANNER = "scanner"
    MONITOR = "monitor"


class MovementDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    NOT_APPLICABLE = "not_applicable"


class StrategyBias(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class DraftDirection(StrEnum):
    """Legacy overloaded direction accepted only by migration readers."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class FormulaKind(StrEnum):
    OPEN_TO_CLOSE_PERCENTAGE = "open_to_close_percentage"
    CLOSE_TO_CLOSE_PERCENTAGE = "close_to_close_percentage"
    REFERENCE_TO_CURRENT_PERCENTAGE = "reference_to_current_percentage"
    HIGH_TO_LOW_PERCENTAGE = "high_to_low_percentage"
    LOW_TO_HIGH_PERCENTAGE = "low_to_high_percentage"
    PREVIOUS_CANDLE_REFERENCE = "previous_candle_reference"
    FIXED_REFERENCE_LEVEL = "fixed_reference_level"
    LOOKBACK_REFERENCE_LEVEL = "lookback_reference_level"
    CROSS = "cross"
    SWEEP_AND_RECLAIM = "sweep_and_reclaim"
    CAPABILITY = "capability"


class ConditionNodeType(StrEnum):
    CONDITION = "condition"
    AND = "and"
    OR = "or"
    NOT = "not"


ConditionUnit = Literal[
    "percent",
    "price",
    "ratio",
    "count",
    "index",
    "boolean",
    "none",
]

_LEVEL_OPERATORS = frozenset(
    {
        Comparator.GREATER_THAN,
        Comparator.GREATER_THAN_OR_EQUAL,
        Comparator.LESS_THAN,
        Comparator.LESS_THAN_OR_EQUAL,
        Comparator.EQUAL,
    }
)
_CROSS_OPERATORS = frozenset({Comparator.CROSSES_ABOVE, Comparator.CROSSES_BELOW})
_BOOLEAN_OPERATORS = frozenset({Comparator.IS_TRUE, Comparator.IS_FALSE})
_EVERY_OPERATOR = frozenset(Comparator)
_EVERY_UNIT: frozenset[str] = frozenset(
    {"percent", "price", "ratio", "count", "index", "boolean", "none"}
)


@dataclass(frozen=True, slots=True)
class FormulaContract:
    """What one formula is allowed to carry.

    Without this table a node could say ``cross`` and compare with ``gte``, or say
    ``sweep_and_reclaim`` and carry a percentage. Both serialize, both compile, and
    both monitor something the trader never asked for — the substitutions section 6
    forbids. Each field is checked, so a wrong combination is refused rather than
    silently executed.
    """

    #: Comparisons this formula can express. Anything else changes its meaning.
    operators: frozenset[Comparator]
    #: What the threshold counts. A percentage move measured in price is a
    #: different rule from the same number measured in percent.
    units: frozenset[str]
    #: Sides this formula cannot measure. A high-to-low move is a fall; calling it
    #: long inverts the alert.
    #:
    #: A *signed* threshold is not listed here on purpose. `-2%` with a long bias is
    #: a legitimate dip rule, and the trader's own sign is never overruled.
    forbidden_directions: frozenset[MovementDirection] = frozenset()


_PERCENTAGE_CONTRACT = FormulaContract(
    operators=_LEVEL_OPERATORS,
    units=frozenset({"percent"}),
)
_PRICE_LEVEL_CONTRACT = FormulaContract(
    operators=_LEVEL_OPERATORS,
    units=frozenset({"price"}),
)

#: Every formula in the launch grammar and the exact contract it must satisfy.
FORMULA_CONTRACTS: dict[FormulaKind, FormulaContract] = {
    FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: _PERCENTAGE_CONTRACT,
    FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: _PERCENTAGE_CONTRACT,
    FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: _PERCENTAGE_CONTRACT,
    FormulaKind.HIGH_TO_LOW_PERCENTAGE: FormulaContract(
        operators=_LEVEL_OPERATORS,
        units=frozenset({"percent"}),
        forbidden_directions=frozenset({MovementDirection.UP}),
    ),
    FormulaKind.LOW_TO_HIGH_PERCENTAGE: FormulaContract(
        operators=_LEVEL_OPERATORS,
        units=frozenset({"percent"}),
        forbidden_directions=frozenset({MovementDirection.DOWN}),
    ),
    FormulaKind.PREVIOUS_CANDLE_REFERENCE: FormulaContract(
        operators=_LEVEL_OPERATORS | _CROSS_OPERATORS,
        units=frozenset({"price"}),
    ),
    FormulaKind.FIXED_REFERENCE_LEVEL: _PRICE_LEVEL_CONTRACT,
    FormulaKind.LOOKBACK_REFERENCE_LEVEL: FormulaContract(
        operators=_LEVEL_OPERATORS | _CROSS_OPERATORS,
        units=frozenset({"price"}),
    ),
    FormulaKind.CROSS: FormulaContract(
        operators=_CROSS_OPERATORS,
        units=frozenset({"price"}),
    ),
    FormulaKind.SWEEP_AND_RECLAIM: FormulaContract(
        operators=_BOOLEAN_OPERATORS,
        units=frozenset({"boolean"}),
    ),
    # A registered capability carries its own contract in the capability registry;
    # this table cannot narrow it without duplicating that definition.
    FormulaKind.CAPABILITY: FormulaContract(
        operators=_EVERY_OPERATOR,
        units=_EVERY_UNIT,
    ),
}


class OperandV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=80)
    kind: Literal["price", "constant", "market_metric", "indicator", "reference"]
    field: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    value: float | str | bool | None = None
    unit: ConditionUnit | None = None
    parameters: dict[
        str,
        int | float | str | bool | list[int | float | str | bool],
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_value(self) -> OperandV2:
        if self.kind == "constant" and self.value is None:
            raise ValueError("constant operands require a value")
        if self.kind != "constant" and not (self.field or self.name):
            raise ValueError(f"{self.kind} operands require a field or name")
        return self


CapabilityParameterValue = int | float | str | bool | list[int | float | str | bool]


class ConditionNodeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(default_factory=lambda: f"condition_{uuid4().hex[:16]}")
    node_type: ConditionNodeType
    source_turn_id: str | None = Field(default=None, max_length=80)
    source_fragment: str | None = Field(
        default=None,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
    required: bool = True
    movement_direction: MovementDirection = MovementDirection.NEUTRAL
    strategy_bias: StrategyBias = StrategyBias.NEUTRAL
    formula: FormulaKind | None = None
    operands: list[OperandV2] = Field(default_factory=list, max_length=12)
    operator: Comparator | None = None
    threshold: float | None = None
    unit: ConditionUnit = "none"
    trigger_timeframe: Timeframe | None = None
    context_timeframes: list[Timeframe] = Field(default_factory=list, max_length=10)
    confirmation_timeframes: list[Timeframe] = Field(default_factory=list, max_length=10)
    reference_timeframe: Timeframe | None = None
    reference_definition: str | None = Field(default=None, max_length=500)
    lookback: int | None = Field(default=None, ge=1, le=100000)
    capability_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    capability_version: str | None = Field(default=None, max_length=40)
    capability_parameters: dict[str, CapabilityParameterValue] = Field(
        default_factory=dict
    )
    condition_symbols: list[str] = Field(default_factory=list, max_length=1000)
    children: list[ConditionNodeV2] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def migrate_direction(cls, data: object) -> object:
        if not isinstance(data, dict) or "direction" not in data:
            return data
        migrated = dict(data)
        old = str(migrated.pop("direction") or "neutral").casefold()
        migrated.setdefault(
            "movement_direction",
            "up" if old in {"long", "bullish", "up"} else "down" if old in {
                "short",
                "bearish",
                "down",
            } else "neutral",
        )
        # Old direction encoded price movement, not a user-authored trade instruction.
        migrated.setdefault("strategy_bias", "neutral")
        return migrated

    @model_validator(mode="after")
    def validate_shape(self) -> ConditionNodeV2:
        if self.node_type == ConditionNodeType.CONDITION:
            if self.children:
                raise ValueError("condition nodes cannot have children")
            if not self.source_turn_id or not self.source_fragment:
                raise ValueError("every executable condition requires source provenance")
            if self.formula is None:
                raise ValueError("condition nodes require an exact formula")
            if self.formula == FormulaKind.CAPABILITY and not self.capability_key:
                raise ValueError("capability conditions require capability_key")
            if self.operator is None:
                raise ValueError("condition nodes require an operator")
            if (
                self.operator not in {Comparator.IS_TRUE, Comparator.IS_FALSE}
                and self.threshold is None
                and not any(operand.kind != "constant" for operand in self.operands[1:])
            ):
                raise ValueError("numerical conditions require a threshold or right operand")
        else:
            if self.formula is not None or self.operator is not None or self.operands:
                raise ValueError("boolean groups cannot contain condition fields")
            if not self.children:
                raise ValueError("boolean groups require children")
            if self.node_type == ConditionNodeType.NOT and len(self.children) != 1:
                raise ValueError("NOT requires exactly one child")
        return self

    def walk(self) -> list[ConditionNodeV2]:
        return [self, *(item for child in self.children for item in child.walk())]

    @property
    def direction(self) -> DraftDirection:
        """Deprecated read alias for old serializers and diagnostics."""

        return {
            MovementDirection.UP: DraftDirection.LONG,
            MovementDirection.DOWN: DraftDirection.SHORT,
            MovementDirection.NEUTRAL: DraftDirection.NEUTRAL,
            MovementDirection.NOT_APPLICABLE: DraftDirection.NEUTRAL,
        }[self.movement_direction]


ConditionNodeV2.model_rebuild()


class StrategyUniverseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    included_symbols: list[str] = Field(default_factory=list, max_length=100000)
    excluded_symbols: list[str] = Field(default_factory=list, max_length=100000)

    @field_validator("included_symbols", "excluded_symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_canonical_symbol(item) for item in values if item.strip()))

    @model_validator(mode="after")
    def disjoint(self) -> StrategyUniverseV2:
        overlap = set(self.included_symbols) & set(self.excluded_symbols)
        if overlap:
            raise ValueError(f"included and excluded symbols overlap: {sorted(overlap)}")
        return self


class MarketScopeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str = Field(default="binance", min_length=2, max_length=40)
    quote_asset: str = Field(default="USDT", min_length=2, max_length=12)
    market_type: Literal["spot"] = "spot"

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("quote_asset")
    @classmethod
    def normalize_quote(cls, value: str) -> str:
        return value.strip().upper()


UnresolvedTargetType = Literal[
    "draft_field",
    "condition_field",
    "condition_creation",
    "universe",
    "market_scope",
    "boolean_structure",
    "capability_parameter",
    "reference_definition",
    "unsupported_resolution",
]


class UnresolvedFieldV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unresolved_id: str = Field(min_length=1, max_length=120)
    source_turn_id: str | None = Field(default=None, max_length=80)
    source_fragment: str = Field(
        min_length=1,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
    target_type: UnresolvedTargetType
    target_field: str | None = Field(default=None, max_length=120)
    target_condition_id: str | None = Field(default=None, max_length=120)
    expected_answer_schema: dict[str, object] = Field(default_factory=dict)
    allowed_options: list[str] = Field(default_factory=list, max_length=12)
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    blocking: bool = True
    created_workflow_revision: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_unresolved(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        key = str(migrated.pop("key", "") or migrated.get("unresolved_id") or "")
        migrated.setdefault("unresolved_id", key)
        target_type = migrated.get("target_type")
        if target_type is None:
            target_type = "condition_creation" if key == "conditions" else (
                "universe" if key in {"universe", "symbols"} else "draft_field"
            )
            migrated["target_type"] = target_type
        if target_type == "draft_field":
            migrated.setdefault("target_field", key)
        migrated.setdefault("expected_answer_schema", {"type": "string"})
        migrated.setdefault("reason", "This value is required to compile the setup exactly.")
        migrated.setdefault("created_workflow_revision", 1)
        return migrated

    @model_validator(mode="after")
    def validate_target(self) -> UnresolvedFieldV2:
        if (
            self.target_type in {"draft_field", "condition_field", "capability_parameter"}
            and not self.target_field
        ):
            raise ValueError(f"{self.target_type} requires target_field")
        if self.target_type == "condition_field" and not self.target_condition_id:
            raise ValueError("condition_field requires target_condition_id")
        return self

    @property
    def key(self) -> str:
        """Deprecated read alias while old UI payloads are migrated."""

        return self.unresolved_id


class UnsupportedRequirementV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    source_turn_id: str | None = Field(default=None, max_length=80)
    source_fragment: str = Field(
        min_length=1,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
    missing_contract: str = Field(min_length=1, max_length=500)
    blocking: bool = True


class ProviderRequirementV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=120)
    source_turn_id: str | None = Field(default=None, max_length=80)
    source_fragment: str = Field(
        min_length=1,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
    capability_version: str | None = Field(default=None, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def remove_runtime_availability(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        migrated.pop("available", None)
        return migrated


class ProviderRuntimeStatusV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=120)
    status: Literal["available", "unavailable", "unknown"] = "unknown"
    checked_at: datetime | None = None
    safe_error: str | None = Field(default=None, max_length=500)


class DraftRuntimeStateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_status: list[ProviderRuntimeStatusV2] = Field(default_factory=list, max_length=100)
    screening_available: bool | None = None
    circuit_breaker_state: Literal["CLOSED", "OPEN", "HALF_OPEN"] = "CLOSED"
    last_execution_result: dict[str, object] | None = None
    runtime_health: Literal["healthy", "degraded", "unavailable", "unknown"] = "unknown"


class ApprovalBindingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool = False
    user_id: UUID | None = None
    executable_version: int | None = Field(default=None, ge=1)
    executable_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    conversation_snapshot_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    approved_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_binding(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "draft_version" in migrated:
            migrated.setdefault("executable_version", migrated.pop("draft_version"))
        if "semantic_hash" in migrated:
            migrated.setdefault("executable_hash", migrated.pop("semantic_hash"))
        return migrated

    @model_validator(mode="after")
    def complete_binding(self) -> ApprovalBindingV2:
        bound = (
            self.user_id,
            self.executable_version,
            self.executable_hash,
            self.conversation_snapshot_hash,
            self.approved_at,
        )
        if self.approved and any(value is None for value in bound):
            raise ValueError("approved drafts require a complete approval binding")
        if not self.approved and any(value is not None for value in bound):
            raise ValueError("unapproved drafts cannot retain approval bindings")
        return self

    @property
    def draft_version(self) -> int | None:
        return self.executable_version

    @property
    def semantic_hash(self) -> str | None:
        return self.executable_hash


class SourceProvenanceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=80)
    fragment: str = Field(
        min_length=1,
        max_length=STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    )
    applied_fields: list[str] = Field(default_factory=list, max_length=100)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StrategyDraftV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.1"] = "2.1"
    draft_id: UUID = Field(default_factory=uuid4)
    executable_version: int = Field(default=1, ge=1)
    workflow_revision: int = Field(default=1, ge=1)
    mode: DraftMode = DraftMode.MONITOR
    name: str = Field(default="Untitled Monitor", min_length=1, max_length=160)
    universe: StrategyUniverseV2 = Field(default_factory=StrategyUniverseV2)
    market_scope: MarketScopeV2 = Field(default_factory=MarketScopeV2)
    condition_ast: ConditionNodeV2 | None = None
    unresolved_fields: list[UnresolvedFieldV2] = Field(default_factory=list, max_length=100)
    unsupported_requirements: list[UnsupportedRequirementV2] = Field(
        default_factory=list, max_length=100
    )
    static_provider_requirements: list[ProviderRequirementV2] = Field(
        default_factory=list, max_length=100
    )
    runtime_state: DraftRuntimeStateV2 = Field(default_factory=DraftRuntimeStateV2)
    approval: ApprovalBindingV2 = Field(default_factory=ApprovalBindingV2)
    source_provenance: list[SourceProvenanceV2] = Field(default_factory=list, max_length=1000)
    executable_hash: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")
    workflow_state_hash: str = Field(default="", pattern=r"^$|^[a-f0-9]{64}$")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_identity(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        legacy_identity = (
            migrated.get("schema_version") != "2.1"
            or "version" in migrated
            or "semantic_hash" in migrated
        )
        migrated["schema_version"] = "2.1"
        if "version" in migrated:
            migrated.setdefault("executable_version", migrated.pop("version"))
        migrated.setdefault("workflow_revision", migrated.get("executable_version", 1))
        if "semantic_hash" in migrated:
            migrated.pop("semantic_hash")
        if "provider_requirements" in migrated:
            legacy_requirements = list(migrated.pop("provider_requirements") or [])
            migrated.setdefault("static_provider_requirements", legacy_requirements)
            statuses = [
                {
                    "provider": item.get("provider", ""),
                    "capability": item.get("capability", ""),
                    "status": "available" if item.get("available") else "unavailable",
                }
                for item in legacy_requirements
                if isinstance(item, dict) and item.get("provider") and item.get("capability")
            ]
            migrated.setdefault("runtime_state", {"provider_status": statuses})
        if legacy_identity:
            # The old hash mixed authoring and runtime fields, so it cannot prove the
            # new executable identity. Migrate deterministically and fail closed.
            migrated["executable_hash"] = ""
            migrated["workflow_state_hash"] = ""
            migrated["approval"] = ApprovalBindingV2().model_dump(mode="json")
        return migrated

    @model_validator(mode="after")
    def validate_identities(self) -> StrategyDraftV2:
        executable = self.calculate_executable_hash()
        workflow = self.calculate_workflow_state_hash()
        if self.executable_hash and self.executable_hash != executable:
            raise ValueError("executable_hash does not match executable state")
        if self.workflow_state_hash and self.workflow_state_hash != workflow:
            raise ValueError("workflow_state_hash does not match authoring state")
        self.executable_hash = executable
        self.workflow_state_hash = workflow
        if self.approval.approved and (
            self.approval.executable_version != self.executable_version
            or self.approval.executable_hash != self.executable_hash
        ):
            raise ValueError("approval is not bound to this executable version and hash")
        return self

    @property
    def authoring_blocking(self) -> bool:
        return (
            self.condition_ast is None
            or any(item.blocking for item in self.unresolved_fields)
            or any(item.blocking for item in self.unsupported_requirements)
        )

    @property
    def blocking(self) -> bool:
        runtime_by_contract = {
            (item.provider, item.capability): item.status
            for item in self.runtime_state.provider_status
        }
        return (
            self.authoring_blocking
            or any(
                runtime_by_contract.get(
                    (requirement.provider, requirement.capability),
                    "unknown",
                )
                != "available"
                for requirement in self.static_provider_requirements
            )
        )

    @property
    def approval_eligible(self) -> bool:
        return not self.blocking

    def calculate_executable_hash(self) -> str:
        payload = {
            "mode": self.mode.value,
            "universe": self.universe.model_dump(mode="json"),
            "market_scope": self.market_scope.model_dump(mode="json"),
            "condition_ast": (
                _executable_condition(self.condition_ast) if self.condition_ast else None
            ),
            "static_provider_requirements": [
                {
                    "provider": item.provider,
                    "capability": item.capability,
                    "capability_version": item.capability_version,
                }
                for item in self.static_provider_requirements
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def calculate_workflow_state_hash(self) -> str:
        payload = {
            "name": self.name,
            "unresolved_fields": [
                item.model_dump(mode="json") for item in self.unresolved_fields
            ],
            "unsupported_requirements": [
                item.model_dump(mode="json") for item in self.unsupported_requirements
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def version(self) -> int:
        return self.executable_version

    @property
    def semantic_hash(self) -> str:
        return self.executable_hash

    @property
    def provider_requirements(self) -> list[ProviderRequirementV2]:
        return self.static_provider_requirements


def _executable_condition(node: ConditionNodeV2) -> dict[str, object]:
    """Canonical condition semantics without authoring/provenance metadata."""

    payload = node.model_dump(
        mode="json",
        exclude={"node_id", "source_turn_id", "source_fragment", "children"},
    )
    payload["children"] = [_executable_condition(child) for child in node.children]
    return payload


class DraftFieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: DraftMode | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    exchange: str | None = Field(default=None, min_length=2, max_length=40)
    quote_asset: str | None = Field(default=None, min_length=2, max_length=12)
    market_type: Literal["spot"] | None = None


class ConditionUpdateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=120)
    replacement: ConditionNodeV2


class CorrectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=500)


class ReversionV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_version: int = Field(ge=1)


class StrategyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _absent_nulls_use_defaults(cls, data: object) -> object:
        """A strict schema requires every key, so `null` here means "nothing to set".

        The provider cannot omit a property under a strict schema. Sending `null` for
        `set_fields` is how it says it has no field changes, and rejecting that failed
        otherwise-correct patches.
        """
        from ai_market_monitor.schemas.strict_mode import drop_absent_nulls

        return drop_absent_nulls(cls, data)

    source_turn_id: str = Field(min_length=1, max_length=80)
    set_fields: DraftFieldPatch = Field(default_factory=DraftFieldPatch)
    add_conditions: list[ConditionNodeV2] = Field(default_factory=list, max_length=100)
    update_conditions: list[ConditionUpdateV2] = Field(default_factory=list, max_length=100)
    remove_conditions: list[str] = Field(default_factory=list, max_length=100)
    replace_groups: ConditionNodeV2 | None = None
    add_inclusions: list[str] = Field(default_factory=list, max_length=100000)
    add_exclusions: list[str] = Field(default_factory=list, max_length=100000)
    remove_inclusions: list[str] = Field(default_factory=list, max_length=100000)
    remove_exclusions: list[str] = Field(default_factory=list, max_length=100000)
    correction: CorrectionV2 | None = None
    reversion: ReversionV2 | None = None
    unresolved_references: list[UnresolvedFieldV2] = Field(default_factory=list, max_length=100)
    unsupported_requirements: list[UnsupportedRequirementV2] = Field(
        default_factory=list, max_length=100
    )
    #: Open items to close, named by their exact key.
    #:
    #: Closing one used to depend on `CorrectionV2.target` happening to equal an
    #: internal key, so a correction phrased in prose could clear the wrong item or
    #: none at all. Naming the key makes the operation explicit and checkable.
    remove_unresolved_keys: list[str] = Field(default_factory=list, max_length=100)
    remove_unsupported_keys: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def one_mutation_mode(self) -> StrategyPatch:
        if self.reversion is not None and any(
            (
                self.add_conditions,
                self.update_conditions,
                self.remove_conditions,
                self.replace_groups is not None,
                self.add_inclusions,
                self.add_exclusions,
                self.remove_inclusions,
                self.remove_exclusions,
                self.correction is not None,
                self.unresolved_references,
                self.unsupported_requirements,
                self.remove_unresolved_keys,
                self.remove_unsupported_keys,
                self.set_fields != DraftFieldPatch(),
            )
        ):
            raise ValueError("reversion cannot be combined with other mutations")
        return self


class StrategyPatchExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: SetupIntent
    patch: StrategyPatch | None = None
    answer: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def patch_matches_intent(self) -> StrategyPatchExtraction:
        if self.intent == SetupIntent.STRATEGY_PATCH and self.patch is None:
            raise ValueError("STRATEGY_PATCH requires a patch")
        if self.intent != SetupIntent.STRATEGY_PATCH and self.patch is not None:
            raise ValueError("non-strategy intents cannot carry a patch")
        return self


def _canonical_symbol(value: str) -> str:
    compact = re.sub(r"[\s/_-]", "", value).upper()
    for quote in ("USDT", "USDC", "FDUSD", "BTC", "ETH", "USD"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[:-len(quote)]}/{quote}"
    return compact
