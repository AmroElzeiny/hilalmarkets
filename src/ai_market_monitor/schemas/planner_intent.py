"""Compact model-facing meaning for the authenticated Setup Chat.

Only trader-controlled semantics cross the model boundary. Canonical operations,
database identities, source offsets, versions, hashes, provenance records, Sharia
policy records, and workflow state are assigned or resolved by the server afterward.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, cast

from pydantic import AfterValidator, Field, model_validator

from ai_market_monitor.schemas.setup_agent import SegmentKind
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    ConditionUnit,
    DraftMode,
    FormulaKind,
    MovementDirection,
    StrategyBias,
)
from ai_market_monitor.schemas.strict_mode import StrictModel
from ai_market_monitor.schemas.timeframes import normalize_timeframe_alias

_QUOTE_MAX = STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH
CapabilityParameterScalar = float | str | bool
CapabilityParameterIntentValue = (
    CapabilityParameterScalar
    | list[CapabilityParameterScalar]
    | dict[str, CapabilityParameterScalar]
)


def _canonical_planner_timeframe(value: str) -> str:
    canonical = normalize_timeframe_alias(value)
    if canonical is None:
        raise ValueError("unsupported timeframe")
    return canonical


PlannerTimeframe = Annotated[
    str,
    Field(min_length=1, max_length=24),
    AfterValidator(_canonical_planner_timeframe),
]


class PlannerModel(StrictModel):
    compact_wire_schema: ClassVar[bool] = True


class SemanticAction(StrEnum):
    SET_MODE = "set_mode"
    SET_NAME = "set_name"
    SET_EXCHANGE = "set_exchange"
    SET_QUOTE_ASSET = "set_quote_asset"
    SET_MARKET_TYPE = "set_market_type"
    SET_SHARIA_PREFERENCES = "set_sharia_preferences"
    INCLUDE_SYMBOL = "include_symbol"
    EXCLUDE_SYMBOL = "exclude_symbol"
    REMOVE_INCLUDED_SYMBOL = "remove_included_symbol"
    REMOVE_EXCLUDED_SYMBOL = "remove_excluded_symbol"
    ADD_CONDITION = "add_condition"
    UPDATE_CONDITION = "update_condition"
    REMOVE_CONDITION = "remove_condition"
    REPLACE_BOOLEAN_STRUCTURE = "replace_boolean_structure"
    RESTORE_OWNED_VERSION = "restore_owned_version"


class CapabilityObjectFieldIntent(PlannerModel):
    """One scalar field inside a registry-declared object parameter.

    This is deliberately not a free-form JSON object.  The registry owns object
    property names and their schemas; the planner may only report the trader's
    scalar value for one of those properties.  Keeping this shallow prevents a
    canonical parameter map from leaking back across the model boundary.
    """

    name: str = Field(min_length=1, max_length=60)
    number_value: float | None = None
    string_value: str | None = Field(default=None, max_length=300)
    boolean_value: bool | None = None

    @model_validator(mode="after")
    def has_exactly_one_scalar_value(self) -> CapabilityObjectFieldIntent:
        if (
            sum(
                value is not None
                for value in (self.number_value, self.string_value, self.boolean_value)
            )
            != 1
        ):
            raise ValueError("object parameter fields need exactly one typed value")
        return self

    def semantic_value(self) -> float | str | bool:
        if self.number_value is not None:
            return self.number_value
        if self.string_value is not None:
            return self.string_value
        assert self.boolean_value is not None
        return self.boolean_value


class CapabilityParameterIntent(PlannerModel):
    """A shallow, typed capability parameter reported by the planner.

    There is intentionally no generic ``value``/``values`` container here.  A
    capability parameter is the only action-specific place where a trader can
    supply a scalar, a homogeneous list, or a registry-declared nested object.
    The compiler checks the selected branch against the actual registry schema
    before a canonical operation exists.
    """

    name: str = Field(min_length=1, max_length=60)
    number_value: float | None = None
    string_value: str | None = Field(default=None, max_length=300)
    boolean_value: bool | None = None
    # Strict structured-output schemas require every property to be returned. Unused
    # union branches therefore arrive as empty arrays; `minItems=1` made a valid scalar
    # branch impossible to deserialize. The model validator below still requires the
    # one selected list branch to be non-empty.
    number_items: list[float] = Field(default_factory=list, max_length=12)
    string_items: list[str] = Field(default_factory=list, max_length=12)
    boolean_items: list[bool] = Field(default_factory=list, max_length=12)
    object_fields: list[CapabilityObjectFieldIntent] = Field(
        default_factory=list, max_length=12
    )

    @model_validator(mode="after")
    def has_exactly_one_typed_value(self) -> CapabilityParameterIntent:
        choices = (
            self.number_value is not None,
            self.string_value is not None,
            self.boolean_value is not None,
            bool(self.number_items),
            bool(self.string_items),
            bool(self.boolean_items),
            bool(self.object_fields),
        )
        if sum(choices) != 1:
            raise ValueError("capability parameters need exactly one typed value")
        names = [item.name for item in self.object_fields]
        if len(names) != len(set(names)):
            raise ValueError("object parameter fields must be unique")
        return self

    def semantic_value(self) -> CapabilityParameterIntentValue:
        if self.number_value is not None:
            return self.number_value
        if self.string_value is not None:
            return self.string_value
        if self.boolean_value is not None:
            return self.boolean_value
        if self.number_items:
            return cast(CapabilityParameterIntentValue, list(self.number_items))
        if self.string_items:
            return cast(CapabilityParameterIntentValue, list(self.string_items))
        if self.boolean_items:
            return cast(CapabilityParameterIntentValue, list(self.boolean_items))
        return {item.name: item.semantic_value() for item in self.object_fields}


class ConditionIntent(PlannerModel):
    """One rule containing only values stated by the trader.

    Deliberately **not** recursive.  The earlier contract let one condition hold
    ``child_intents`` of its own type, which is a self-referencing JSON Schema. Strict
    structured output accepted the schema and then, across real provider calls in
    evaluator runs 20260802T232050Z and 20260803T000036Z, never used it: an explicit
    ``A AND (B OR C)`` came back as two unrelated flat rules and one unsupported note,
    and the stated grouping was silently lost. Boolean structure now travels in the
    flat :class:`BooleanStrategyIntent`, whose leaves wrap *this* model rather than
    redeclaring its fields — one definition on the wire, so a field can never be
    understood inside a group and not outside it.
    """

    target_reference: str | None = Field(default=None, max_length=40)
    source_quote: str | None = Field(default=None, max_length=_QUOTE_MAX)
    formula_key: FormulaKind | None = None
    movement_direction: MovementDirection | None = None
    strategy_bias: StrategyBias | None = None
    comparator: Comparator | None = None
    threshold: float | None = None
    unit: ConditionUnit | None = None
    trigger_timeframe: PlannerTimeframe | None = None
    context_timeframes: list[PlannerTimeframe] = Field(default_factory=list, max_length=6)
    confirmation_timeframes: list[PlannerTimeframe] = Field(default_factory=list, max_length=6)
    reference_timeframe: PlannerTimeframe | None = None
    reference_definition: str | None = Field(default=None, max_length=300)
    lookback: int | None = Field(default=None, ge=1, le=100000)
    capability_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    capability_parameters: list[CapabilityParameterIntent] = Field(
        default_factory=list, max_length=8
    )
    measured_price_field: Literal["open", "high", "low", "close"] | None = None
    required: bool | None = None
    condition_symbols: list[str] = Field(default_factory=list, max_length=12)

    @property
    def states_a_rule(self) -> bool:
        return any(
            getattr(self, name) not in (None, [], {})
            for name in ("formula_key", "comparator", "threshold", "capability_key")
        )


class ConditionLeafIntent(PlannerModel):
    """One operand of a Boolean expression, named by a turn-local reference.

    ``leaf_ref`` and ``segment_ref`` are temporary and belong to this turn only. They
    are never a database identity, never returned to the model as one, and never
    survive the turn: the server assigns canonical node ids afterward.

    The rule itself is a plain :class:`ConditionIntent`. A leaf inside ``(B OR C)`` is
    therefore validated, grounded and compiled by exactly the code that handles a rule
    written on its own.
    """

    leaf_ref: str = Field(min_length=1, max_length=40)
    segment_ref: str = Field(min_length=1, max_length=40)
    condition: ConditionIntent

    @model_validator(mode="after")
    def leaf_quotes_its_own_words(self) -> ConditionLeafIntent:
        # Per-leaf quotes are the role-separation boundary: without one, a timeframe
        # belonging to the neighbouring operand looks like this operand's own.
        if not (self.condition.source_quote or "").strip():
            raise ValueError("every rule inside a combination needs its own exact quote")
        return self

    @property
    def source_quote(self) -> str:
        return self.condition.source_quote or ""


class BooleanGroupIntent(PlannerModel):
    """One AND/OR/NOT node, naming its children by their turn-local references."""

    group_ref: str = Field(min_length=1, max_length=40)
    operator: Literal["and", "or", "not"]
    child_refs: list[str] = Field(min_length=1, max_length=8)
    source_quote: str = Field(min_length=1, max_length=_QUOTE_MAX)

    @model_validator(mode="after")
    def validate_arity(self) -> BooleanGroupIntent:
        if self.operator == "not" and len(self.child_refs) != 1:
            raise ValueError("not takes exactly one child")
        if self.operator in {"and", "or"} and len(self.child_refs) < 2:
            raise ValueError(f"{self.operator} needs at least two children")
        if len(self.child_refs) != len(set(self.child_refs)):
            raise ValueError("a group cannot name the same child twice")
        if self.group_ref in self.child_refs:
            raise ValueError("a group cannot be its own child")
        return self


#: How deep and how wide one turn's Boolean expression may be. Both are bounds on the
#: *trader's* expression, not on the schema: a person does not write eight levels of
#: nesting by hand, and an unbounded graph is a denial-of-service surface.
BOOLEAN_MAX_NODES: int = 24
BOOLEAN_MAX_DEPTH: int = 6


class BooleanStrategyIntent(PlannerModel):
    """A whole Boolean expression as a flat graph: leaves, groups, and one root.

    Structural validation (single root, every reference resolved exactly once, no
    cycles, bounded size) is deterministic and happens server-side in
    ``engine/boolean_topology.py``. What is checked here is only what a shape can
    check: references are unique, and the root names something in this turn.
    """

    condition_leaves: list[ConditionLeafIntent] = Field(min_length=1, max_length=12)
    boolean_groups: list[BooleanGroupIntent] = Field(default_factory=list, max_length=12)
    root_ref: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_references(self) -> BooleanStrategyIntent:
        leaf_refs = [item.leaf_ref for item in self.condition_leaves]
        group_refs = [item.group_ref for item in self.boolean_groups]
        every = [*leaf_refs, *group_refs]
        if len(every) != len(set(every)):
            raise ValueError("leaf and group references must be unique within one turn")
        if self.root_ref not in set(every):
            raise ValueError("the root names a node that is not in this expression")
        known = set(every)
        for group in self.boolean_groups:
            unknown = [ref for ref in group.child_refs if ref not in known]
            if unknown:
                raise ValueError(f"group {group.group_ref} names unknown children: {unknown}")
        if len(every) > BOOLEAN_MAX_NODES:
            raise ValueError("that expression has more parts than one turn may carry")
        return self


class SetModePayload(PlannerModel):
    action: Literal["set_mode"]
    mode: DraftMode


class SetNamePayload(PlannerModel):
    action: Literal["set_name"]
    name: str = Field(min_length=1, max_length=160)


class SetExchangePayload(PlannerModel):
    action: Literal["set_exchange"]
    exchange: str = Field(min_length=2, max_length=40)


class SetQuoteAssetPayload(PlannerModel):
    action: Literal["set_quote_asset"]
    quote_asset: str = Field(min_length=2, max_length=12)


class SetMarketTypePayload(PlannerModel):
    action: Literal["set_market_type"]
    market_type: Literal["spot"]


class ShariaPreferencePayload(PlannerModel):
    """User preference only; governed identity is resolved server-side."""

    action: Literal["set_sharia_preferences"]
    methodology_family: str | None = Field(default=None, max_length=180)
    methodology_identifier: str | None = Field(default=None, max_length=180)
    screened_assets_only: bool | None = None
    approved_watchlist_only: bool | None = None
    fail_closed_preference: bool | None = None

    @model_validator(mode="after")
    def has_preference(self) -> ShariaPreferencePayload:
        if all(
            value is None
            for value in (
                self.methodology_family,
                self.methodology_identifier,
                self.screened_assets_only,
                self.approved_watchlist_only,
                self.fail_closed_preference,
            )
        ):
            raise ValueError("set_sharia_preferences needs one explicit preference")
        return self


class SymbolPayload(PlannerModel):
    action: Literal[
        "include_symbol",
        "exclude_symbol",
        "remove_included_symbol",
        "remove_excluded_symbol",
    ]
    symbol: str = Field(min_length=2, max_length=40)


class AddConditionPayload(PlannerModel):
    action: Literal["add_condition"]
    condition: ConditionIntent


class UpdateConditionPayload(PlannerModel):
    action: Literal["update_condition"]
    target_reference: str = Field(min_length=1, max_length=40)
    condition: ConditionIntent


class RemoveConditionPayload(PlannerModel):
    action: Literal["remove_condition"]
    target_reference: str = Field(min_length=1, max_length=40)


class ReplaceBooleanPayload(PlannerModel):
    """The whole executable Boolean expression, replacing what is there now.

    Only used when the trader stated how rules combine. A single rule stays a single
    ``add_condition``; wrapping it in an artificial group would invent structure.
    """

    action: Literal["replace_boolean_structure"]
    boolean_structure: BooleanStrategyIntent


class RestoreSnapshotPayload(PlannerModel):
    action: Literal["restore_owned_version"]
    target_reference: str = Field(min_length=1, max_length=40)


# A structural discriminated union: every shallow branch owns a distinct literal
# ``action``.  We intentionally do not emit Pydantic's OpenAPI ``discriminator.mapping``
# hint because it duplicates every action/ref pair on the wire (774 bytes) while the
# strict JSON-Schema provider already selects branches from their action constants.
type IntentPayload = (
    SetModePayload
    | SetNamePayload
    | SetExchangePayload
    | SetQuoteAssetPayload
    | SetMarketTypePayload
    | ShariaPreferencePayload
    | SymbolPayload
    | AddConditionPayload
    | UpdateConditionPayload
    | RemoveConditionPayload
    | ReplaceBooleanPayload
    | RestoreSnapshotPayload
)


class SemanticIntent(PlannerModel):
    segment_ref: str = Field(min_length=1, max_length=40)
    payload: IntentPayload

    @property
    def action(self) -> SemanticAction:
        return SemanticAction(self.payload.action)


class PlannerSegment(PlannerModel):
    segment_ref: str = Field(min_length=1, max_length=40)
    exact_source_text: str = Field(min_length=1, max_length=_QUOTE_MAX)
    segment_kind: SegmentKind


class ClarificationAnswerIntent(PlannerModel):
    segment_ref: str = Field(min_length=1, max_length=40)
    clarification_ref: str = Field(min_length=1, max_length=40)
    answer_text: str = Field(min_length=1, max_length=_QUOTE_MAX)


class UnsupportedIntent(PlannerModel):
    segment_ref: str = Field(min_length=1, max_length=40)
    missing_contract: str = Field(min_length=1, max_length=400)


SupportedMissingField = Literal[
    "universe",
    "formula",
    "comparator",
    "threshold",
    "trigger_timeframe",
    "reference_point",
    "capability_parameter",
]


class SupportedIncompleteIntent(PlannerModel):
    """A known supported request that needs one or more user choices.

    This is intentionally separate from ``UnsupportedIntent``. Missing values in a
    registered/core mechanic should start a clarification flow, not become a permanent
    unsupported requirement on the draft.
    """

    segment_ref: str = Field(min_length=1, max_length=40)
    missing_fields: list[SupportedMissingField] = Field(min_length=1, max_length=6)
    capability_key: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$"
    )

    @model_validator(mode="after")
    def unique_missing_fields(self) -> "SupportedIncompleteIntent":
        if len(self.missing_fields) != len(set(self.missing_fields)):
            raise ValueError("missing fields must be unique")
        return self


class ReadOnlyPercentageScanIntent(PlannerModel):
    """A current-market percentage question that must not mutate Setup state.

    This is a routing contract, not an execution result.  The planner may report only
    values the trader stated in the current segment.  The server later checks the
    selected screened universe, entitlement, quota, provider data and durable run
    identity before any result is returned.
    """

    segment_ref: str = Field(min_length=1, max_length=40)
    movement_direction: Literal["up", "down"]
    threshold_percent: float = Field(gt=0, le=1000)
    measurement_window: Literal["24h"] | None = None


class ApprovalIntentSignal(PlannerModel):
    segment_ref: str = Field(min_length=1, max_length=40)


class PlannerIntentEnvelope(PlannerModel):
    """The complete model response with supported-incomplete kept distinct."""

    segments: list[PlannerSegment] = Field(min_length=1, max_length=12)
    semantic_intents: list[SemanticIntent] = Field(default_factory=list, max_length=12)
    clarification_answers: list[ClarificationAnswerIntent] = Field(
        default_factory=list, max_length=6
    )
    questions_to_answer: list[str] = Field(default_factory=list, max_length=6)
    supported_incomplete_intents: list[SupportedIncompleteIntent] = Field(
        default_factory=list, max_length=6
    )
    read_only_percentage_scans: list[ReadOnlyPercentageScanIntent] = Field(
        default_factory=list, max_length=1
    )
    unsupported_intents: list[UnsupportedIntent] = Field(default_factory=list, max_length=6)
    approval_intent: ApprovalIntentSignal | None = None
    overall_confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_references(self) -> PlannerIntentEnvelope:
        references = [item.segment_ref for item in self.segments]
        if len(references) != len(set(references)):
            raise ValueError("segment references must be unique within one turn")
        known = set(references)
        used = [
            *(item.segment_ref for item in self.semantic_intents),
            *(item.segment_ref for item in self.clarification_answers),
            *(item.segment_ref for item in self.supported_incomplete_intents),
            *(item.segment_ref for item in self.read_only_percentage_scans),
            *(item.segment_ref for item in self.unsupported_intents),
            *(self.questions_to_answer),
            *([self.approval_intent.segment_ref] if self.approval_intent else []),
        ]
        if any(reference not in known for reference in used):
            raise ValueError("an envelope item names a segment outside this turn")
        scan_refs = {item.segment_ref for item in self.read_only_percentage_scans}
        conflicting_scan_refs = scan_refs & {
            *(item.segment_ref for item in self.semantic_intents),
            *(item.segment_ref for item in self.supported_incomplete_intents),
            *(item.segment_ref for item in self.unsupported_intents),
            *self.questions_to_answer,
        }
        if conflicting_scan_refs:
            raise ValueError(
                "a read-only Scanner segment cannot also author or classify setup state"
            )
        return self

    @property
    def requires_tool(self) -> bool:
        return bool(
            self.semantic_intents
            or self.clarification_answers
            or self.supported_incomplete_intents
            or self.read_only_percentage_scans
            or self.unsupported_intents
        )


RepairKind = Literal[
    "remove_intent",
    "remove_field",
    "replace_with_grounded_value",
    "relink_source_segment",
    "inherit_existing_value",
    "correct_semantic_role",
    "replace_target_reference",
    "preserve_as_unsupported",
]


RepairReplacementKind = Literal[
    "number",
    "integer",
    "string",
    "enum",
    "boolean",
    "symbol",
    "timeframe",
    "number_list",
    "integer_list",
    "string_list",
    "boolean_list",
    "shallow_object",
]

type RepairSemanticValue = (
    float
    | int
    | str
    | bool
    | list[float]
    | list[int]
    | list[str]
    | list[bool]
    | dict[str, float | str | bool]
)


class RepairReplacementValue(PlannerModel):
    """One closed typed correction; never arbitrary model-authored JSON."""

    kind: RepairReplacementKind
    number_value: float | None = None
    integer_value: int | None = None
    string_value: str | None = Field(default=None, max_length=300)
    boolean_value: bool | None = None
    number_items: list[float] = Field(default_factory=list, max_length=12)
    integer_items: list[int] = Field(default_factory=list, max_length=12)
    string_items: list[str] = Field(default_factory=list, max_length=12)
    boolean_items: list[bool] = Field(default_factory=list, max_length=12)
    object_fields: list[CapabilityObjectFieldIntent] = Field(
        default_factory=list, max_length=12
    )

    @model_validator(mode="after")
    def matches_declared_kind(self) -> RepairReplacementValue:
        present = (
            self.number_value is not None,
            self.integer_value is not None,
            self.string_value is not None,
            self.boolean_value is not None,
            bool(self.number_items),
            bool(self.integer_items),
            bool(self.string_items),
            bool(self.boolean_items),
            bool(self.object_fields),
        )
        expected_present = {
            "number": present[0],
            "integer": present[1],
            "string": present[2],
            "enum": present[2],
            "boolean": present[3],
            "symbol": present[2],
            "timeframe": present[2],
            "number_list": present[4],
            "integer_list": present[5],
            "string_list": present[6],
            "boolean_list": present[7],
            "shallow_object": present[8],
        }
        if not expected_present[self.kind] or sum(present) != 1:
            raise ValueError("repair replacement must match exactly one declared kind")
        names = [item.name for item in self.object_fields]
        if len(names) != len(set(names)):
            raise ValueError("repair object fields must be unique")
        return self

    def semantic_value(self) -> RepairSemanticValue:
        if self.kind == "number":
            assert self.number_value is not None
            return self.number_value
        if self.kind == "integer":
            assert self.integer_value is not None
            return self.integer_value
        if self.kind in {"string", "enum", "symbol", "timeframe"}:
            assert self.string_value is not None
            return self.string_value
        if self.kind == "boolean":
            assert self.boolean_value is not None
            return self.boolean_value
        if self.kind == "number_list":
            return list(self.number_items)
        if self.kind == "integer_list":
            return list(self.integer_items)
        if self.kind == "string_list":
            return list(self.string_items)
        if self.kind == "boolean_list":
            return list(self.boolean_items)
        return {item.name: item.semantic_value() for item in self.object_fields}

    def capability_parameter_payload(self, name: str) -> dict[str, Any]:
        key = {
            "number": "number_value",
            "integer": "number_value",
            "string": "string_value",
            "enum": "string_value",
            "boolean": "boolean_value",
            "symbol": "string_value",
            "timeframe": "string_value",
            "number_list": "number_items",
            "integer_list": "number_items",
            "string_list": "string_items",
            "boolean_list": "boolean_items",
            "shallow_object": "object_fields",
        }[self.kind]
        if self.kind == "shallow_object":
            return {
                "name": name,
                key: [
                    item.model_dump(mode="json", exclude_none=True) for item in self.object_fields
                ],
            }
        return {"name": name, key: self.semantic_value()}


class SemanticIntentRepairDelta(PlannerModel):
    intent_ref: str = Field(min_length=1, max_length=40)
    target_path: str = Field(default="", max_length=100)
    repair_kind: RepairKind
    replacement_value: RepairReplacementValue | None = None
    source_segment_ref: str | None = Field(default=None, max_length=40)
    validation_code: str = Field(min_length=1, max_length=80)


class PlannerRepairEnvelope(PlannerModel):
    deltas: list[SemanticIntentRepairDelta] = Field(default_factory=list, max_length=8)
    cannot_repair: bool = False


class BooleanTopologyRepair(PlannerModel):
    """Rearrange rules that are already grounded. Nothing else.

    This contract exists because the failure it corrects is narrow: every rule and
    every connector is present and proved against the trader's words, and only their
    arrangement came back wrong. Resending the whole plan to fix that invites a second
    reading of the turn — new rules, new symbols, new thresholds — which is how a
    "repair" quietly becomes a different strategy.

    So the shape itself makes that impossible. There are no condition fields here: only
    references to leaves that already exist, and the groups joining them.
    """

    existing_leaf_refs: list[str] = Field(min_length=1, max_length=12)
    groups: list[BooleanGroupIntent] = Field(default_factory=list, max_length=12)
    root_ref: str = Field(min_length=1, max_length=40)
    cannot_repair: bool = False

    @model_validator(mode="after")
    def references_resolve(self) -> BooleanTopologyRepair:
        if self.cannot_repair:
            return self
        group_refs = [item.group_ref for item in self.groups]
        if len(group_refs) != len(set(group_refs)):
            raise ValueError("group references must be unique")
        known = set(self.existing_leaf_refs) | set(group_refs)
        if self.root_ref not in known:
            raise ValueError("the root names a node that is not in this arrangement")
        for group in self.groups:
            unknown = [ref for ref in group.child_refs if ref not in known]
            if unknown:
                raise ValueError(f"group {group.group_ref} names unknown children: {unknown}")
        return self


def compact_json_schema(model: type[Any]) -> dict[str, Any]:
    """The exact schema used by the structured provider call."""

    from ai_market_monitor.services.agent_tools import strict_json_schema

    return strict_json_schema(model, compact=True)


def schema_complexity(schema: dict[str, Any]) -> dict[str, int]:
    """Measure the exact wire schema, following definitions for object depth."""

    definitions = schema.get("$defs") or {}
    optional_fields = 0
    union_branches = 0

    def walk_counts(node: Any) -> None:
        nonlocal optional_fields, union_branches
        if isinstance(node, dict):
            branches = node.get("anyOf") or node.get("oneOf")
            if isinstance(branches, list):
                union_branches += len(branches)
                if any(isinstance(item, dict) and item.get("type") == "null" for item in branches):
                    optional_fields += 1
            for value in node.values():
                walk_counts(value)
        elif isinstance(node, list):
            for item in node:
                walk_counts(item)

    walk_counts(schema)

    def object_depth(node: Any, seen: frozenset[str]) -> int:
        if isinstance(node, list):
            return max((object_depth(item, seen) for item in node), default=0)
        if not isinstance(node, dict):
            return 0
        reference = node.get("$ref")
        if isinstance(reference, str):
            name = reference.rsplit("/", 1)[-1]
            if name in seen or name not in definitions:
                return 0
            return object_depth(definitions[name], seen | {name})
        properties = node.get("properties")
        if isinstance(properties, dict) and properties:
            return 1 + max(
                (object_depth(value, seen) for value in properties.values()),
                default=0,
            )
        return max(
            (
                object_depth(value, seen)
                for key, value in node.items()
                if key not in {"$defs", "required", "enum", "const"}
            ),
            default=0,
        )

    import json

    return {
        "minified_schema_bytes": len(json.dumps(schema, separators=(",", ":"))),
        "definition_count": len(definitions),
        "maximum_nesting_depth": object_depth(schema, frozenset()),
        "optional_field_count": optional_fields,
        "union_branch_count": union_branches,
    }


# The action-specific union is larger than the early aspirational 4,096-byte target.
#
# The previous 9,303-byte / depth-6 contract met this ceiling and did not work. Its
# Boolean support was a self-referencing ``child_intents`` array, and across real
# provider calls in evaluator runs 20260802T232050Z and 20260803T000036Z the model
# never once used it: an explicit ``A AND (B OR C)`` came back as flat, unrelated
# rules. A schema that fits a byte budget while losing the trader's meaning is not the
# cheaper option.
#
# The flat Boolean contract costs about 700 bytes and two levels more. It costs no
# duplicated condition definition: ``ConditionLeafIntent`` wraps ``ConditionIntent``
# rather than restating its fields, so there is still exactly one rule definition on
# the wire. Provider acceptance and the measured effect are recorded in
# SETUP_CHAT_LATENCY_FINDINGS.md.
# Y5.2 adds one typed read-only current-market intent (about 524 compact bytes).
# The cap remains deliberately tight and is asserted against the exact provider schema.
PLANNER_SCHEMA_BYTE_BUDGET = 11200
PLANNER_SCHEMA_DEPTH_BUDGET = 8

FORBIDDEN_SCHEMA_MODELS: frozenset[str] = frozenset(
    {
        "AuthorizedPatchOperation",
        "ConditionNodeV2",
        "UnresolvedFieldV2",
        "ShariaPolicyV2",
        "OperandV2",
        "DraftFieldPatch",
        "StrategyDraftV2",
        "StrategyPatch",
        "ApprovalBindingV2",
        "RequirementStateV2",
        "SourceProvenanceV2",
    }
)

FORBIDDEN_SERVER_FIELDS: frozenset[str] = frozenset(
    {
        "source_turn_id",
        "intent_id",
        "operation_id",
        "node_id",
        "unresolved_id",
        "start_offset",
        "end_offset",
        "methodology_version",
        "approved_watchlist_version",
        "registry_version",
        "executable_version",
        "workflow_revision",
        "hash",
        "values",
        "semantic_target",
    }
)
