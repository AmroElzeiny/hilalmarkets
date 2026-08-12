from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.request_identity import (
    CLIENT_MESSAGE_ID_MAX_LENGTH,
    CLIENT_MESSAGE_ID_MIN_LENGTH,
    CLIENT_MESSAGE_ID_PATTERN,
)
from ai_market_monitor.schemas.setup_change_review import SetupDraftDiff
from ai_market_monitor.schemas.setup_chat_evaluation import SetupChatEvaluationContract
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH,
    StrategyDraftV2,
)

SETUP_CHAT_MESSAGE_MAX_LENGTH = 5000
SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH = STRATEGY_SOURCE_FRAGMENT_MAX_LENGTH


def setup_chat_source_excerpt(value: str) -> str:
    """Bound non-authoritative audit text without changing the canonical message."""

    normalized = str(value or "").strip()
    if len(normalized) <= SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH:
        return normalized
    return normalized[: SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH - 3].rstrip() + "..."


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

    text: str = Field(min_length=1, max_length=SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH)
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

    message: str = Field(default="", max_length=SETUP_CHAT_MESSAGE_MAX_LENGTH)
    option_key: str | None = Field(default=None, max_length=80)
    option_value: str | None = Field(default=None, max_length=500)
    option_label: str | None = Field(default=None, max_length=120)
    #: The client's own name for this attempt. **Required.**
    #:
    #: Every turn costs money and changes the user's setup, so the server has to be
    #: able to tell "the user asked again" from "the browser retried". Without this the
    #: two are indistinguishable and a dropped response becomes a second paid mutation.
    #: The client generates it before sending, reuses it for every automatic retry of
    #: the same attempt, and generates a new one when the user edits and resends.
    client_message_id: str = Field(
        min_length=CLIENT_MESSAGE_ID_MIN_LENGTH,
        max_length=CLIENT_MESSAGE_ID_MAX_LENGTH,
        pattern=CLIENT_MESSAGE_ID_PATTERN,
    )
    #: The question this message was written under, as the client saw it.
    #:
    #: Required as a pair whenever a question is open — typed answers included. A typed
    #: answer written against a screen that has since moved on is exactly as unsafe as a
    #: stale button click: it lands on whatever field is current now, which is a field
    #: the trader was never asked about. The two travel together, and one without the
    #: other is refused rather than half-trusted.
    question_id: str | None = Field(default=None, max_length=120)
    step_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_message_or_option(self) -> "SetupChatMessageRequest":
        if not self.message.strip() and not (self.option_key and self.option_value):
            raise ValueError("message or option selection is required")
        if (self.question_id is None) != (self.step_revision is None):
            raise ValueError("question_id and step_revision must be sent together")
        return self

    @property
    def carries_question_identity(self) -> bool:
        return self.question_id is not None and self.step_revision is not None


class SetupChatApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: Literal[True]
    expected_schema_hash: str = Field(min_length=64, max_length=64)
    expected_executable_version: int | None = Field(default=None, ge=1)
    expected_executable_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    # Read compatibility for clients deployed before executable/workflow separation.
    expected_draft_version: int | None = Field(default=None, ge=1)
    expected_semantic_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )
    confirmed_low_confidence_rule_keys: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def canonical_identity_required(self) -> "SetupChatApprovalRequest":
        if self.expected_executable_version is None:
            self.expected_executable_version = self.expected_draft_version
        if self.expected_executable_hash is None:
            self.expected_executable_hash = self.expected_semantic_hash
        if self.expected_executable_version is None or self.expected_executable_hash is None:
            raise ValueError("approval requires executable version and hash")
        return self


class SetupChatScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)


#: What the user asked the server to do to their own draft history. Each of these is a
#: canonical, audited operation the server builds itself — never a sentence a model is
#: asked to turn back into an old state.
SetupChatDraftAction = Literal[
    "undo_last_material_change",
    "restore_snapshot",
    "reset_current_draft",
    "confirm_pending_change",
    "cancel_pending_change",
]


class SetupChatDraftActionRequest(BaseModel):
    """One explicit draft-history action, with the identity it acts on.

    ``client_message_id`` is required here for the same reason it is required on a
    message: a double-clicked Undo must undo once, not twice.
    """

    model_config = ConfigDict(extra="forbid")

    action: SetupChatDraftAction
    client_message_id: str = Field(
        min_length=CLIENT_MESSAGE_ID_MIN_LENGTH,
        max_length=CLIENT_MESSAGE_ID_MAX_LENGTH,
        pattern=CLIENT_MESSAGE_ID_PATTERN,
    )
    #: `restore_snapshot` — which immutable snapshot, and the version it must still be.
    snapshot_id: str | None = Field(default=None, max_length=80)
    expected_executable_version: int | None = Field(default=None, ge=1)
    #: `confirm_pending_change`, `cancel_pending_change` — which proposal.
    proposal_id: str | None = Field(default=None, max_length=64)
    #: Required for anything that discards work. A client that does not send it is
    #: refused and told what it is about to lose, rather than being taken at its word.
    confirmed: bool = False

    @model_validator(mode="after")
    def action_carries_its_target(self) -> "SetupChatDraftActionRequest":
        if self.action == "restore_snapshot" and not self.snapshot_id:
            raise ValueError("restore_snapshot requires snapshot_id")
        if self.action in {"confirm_pending_change", "cancel_pending_change"} and (
            not self.proposal_id
        ):
            raise ValueError(f"{self.action} requires proposal_id")
        return self


#: Everything the Guided Builder can do to a draft. Each one is a server-owned canonical
#: operation: the values come from fields the server drew, the operations are built by
#: the server, and they go through the same authority a chat turn does.
SetupBuilderAction = Literal[
    "select_mode",
    "rename_plan",
    "select_universe",
    "select_watchlist",
    "set_explicit_assets",
    "select_methodology",
    "add_condition",
    "update_condition",
    "remove_condition",
    "arrange_conditions",
    # Boolean structure, edited by stable node id. ``arrange_conditions`` can only
    # produce one flat root join, so on its own it silently flattened "A and (B or C)"
    # into "A and B and C" — a different strategy that still compiles and still fires.
    "group_conditions",
    "ungroup_conditions",
    "set_group_operator",
    "move_condition",
    "apply_starter",
]


class SetupBuilderActionRequest(BaseModel):
    """One guided change, with only the fields that change carry.

    ``client_message_id`` is required for the same reason it is on every other write: a
    double-clicked button must act once.
    """

    model_config = ConfigDict(extra="forbid")

    action: SetupBuilderAction
    client_message_id: str = Field(
        min_length=CLIENT_MESSAGE_ID_MIN_LENGTH,
        max_length=CLIENT_MESSAGE_ID_MAX_LENGTH,
        pattern=CLIENT_MESSAGE_ID_PATTERN,
    )
    #: `select_mode`, `rename_plan`, `select_universe`, `select_watchlist`,
    #: `set_explicit_assets`, `select_methodology`, `apply_starter` — the chosen value.
    value: str | None = Field(default=None, max_length=2000)
    #: `add_condition`, `update_condition` — which kind of rule, and its filled fields.
    mechanic_key: str | None = Field(default=None, max_length=160)
    values: dict[str, Any] = Field(default_factory=dict)
    #: `update_condition`, `remove_condition` — the rule this acts on.
    node_id: str | None = Field(default=None, max_length=120)
    #: `add_condition`, `update_condition` — must-have rule, or an optional confirmation.
    required: bool = True
    #: `arrange_conditions` — every rule id in its new order, and how they join.
    order: list[str] = Field(default_factory=list, max_length=100)
    join: Literal["and", "or"] | None = None
    #: `group_conditions` — the rules to wrap in a new group. They must share a parent;
    #: grouping across branches would move rules out of the logic their author chose.
    node_ids: list[str] = Field(default_factory=list, max_length=100)
    #: `group_conditions`, `set_group_operator` — which grouping to apply. "not" is here
    #: and not in `join` because a negation takes exactly one child, so it can never be a
    #: root join over a list.
    operator: Literal["and", "or", "not"] | None = None
    #: `ungroup_conditions`, `set_group_operator`, `move_condition` — the group acted on.
    group_id: str | None = Field(default=None, max_length=120)
    #: `move_condition` — where in the target group the rule lands. None appends.
    position: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def action_carries_its_target(self) -> "SetupBuilderActionRequest":
        """A change with no target is a change nobody can check.

        Refused here rather than deep inside the service, so a malformed request never
        reaches the code that writes a draft.
        """

        needs_value = {
            "select_mode",
            "rename_plan",
            "select_universe",
            "select_watchlist",
            "set_explicit_assets",
            "select_methodology",
            "apply_starter",
        }
        if self.action in needs_value and not (self.value or "").strip():
            raise ValueError(f"{self.action} requires value")
        if self.action in {"add_condition", "update_condition"} and not self.mechanic_key:
            raise ValueError(f"{self.action} requires mechanic_key")
        if self.action in {"update_condition", "remove_condition"} and not self.node_id:
            raise ValueError(f"{self.action} requires node_id")
        if self.action == "arrange_conditions" and not (self.order and self.join):
            raise ValueError("arrange_conditions requires order and join")
        if self.action == "group_conditions" and not (self.node_ids and self.operator):
            raise ValueError("group_conditions requires node_ids and operator")
        if self.action == "set_group_operator" and not (self.group_id and self.operator):
            raise ValueError("set_group_operator requires group_id and operator")
        if self.action == "ungroup_conditions" and not self.group_id:
            raise ValueError("ungroup_conditions requires group_id")
        if self.action == "move_condition" and not (self.node_id and self.group_id):
            raise ValueError("move_condition requires node_id and group_id")
        return self


class SetupChatSnapshotSummary(BaseModel):
    """One restorable version, described from its own stored facts."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID
    executable_version: int
    executable_hash: str
    created_at: datetime
    #: Plain sentences about what this version contains. Built from the draft, not from
    #: whatever the assistant said at the time.
    summary_lines: list[str] = Field(default_factory=list)
    is_current: bool = False


class SetupChatPendingChangeResponse(BaseModel):
    """A proposal the user has not answered yet."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    status: Literal["pending", "confirmed", "cancelled", "stale", "applied"]
    reasons: list[str] = Field(default_factory=list)
    summary_lines: list[str] = Field(default_factory=list)
    governance_notes: list[str] = Field(default_factory=list)
    invalidates_approval: bool = False
    diff: SetupDraftDiff
    expires_at: datetime
    #: True when the draft moved after this was offered. A stale proposal can only be
    #: regenerated, never applied.
    stale: bool = False


class SetupChatTurnState(BaseModel):
    """What the session's own turn machinery is doing right now.

    The composer reads this to know whether to accept typing. Without it the client
    guessed from whether a reply had arrived, which is why a double-click could send
    two paid turns.
    """

    model_config = ConfigDict(extra="forbid")

    active: bool = False
    #: The stage name, for display. Never an internal failure detail.
    stage: str | None = None
    client_message_id: str | None = None
    #: True when the client may send another mutating message.
    accepts_input: bool = True
    #: Set when this turn has been running long enough to be worth explaining.
    slow: bool = False


class SetupBuilderCondition(BaseModel):
    """One rule as the Builder shows it: readable, and editable when it can be."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    mechanic_key: str | None = None
    label: str
    sentence: str
    values: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    editable: bool = True
    not_editable_reason: str | None = None


class SetupBuilderStep(BaseModel):
    """One stop in the guided flow, and whether it is done."""

    model_config = ConfigDict(extra="forbid")

    key: Literal["mode", "assets", "conditions", "logic", "review", "market", "approval"]
    label: str
    complete: bool = False
    #: What is still needed here, in plain words. Empty when the step is done.
    todo: str | None = None


class SetupBuilderState(BaseModel):
    """The whole guided Builder, drawn from the canonical draft and nothing else.

    Both surfaces read this. It is what makes an edit made in chat appear in the
    Builder immediately, and an edit made in the Builder appear in the next chat turn:
    there is one state, described once.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["scanner", "monitor"]
    name: str
    universe_mode: str | None = None
    universe_summary: str | None = None
    methodology_summary: str | None = None
    conditions: list[SetupBuilderCondition] = Field(default_factory=list)
    #: ``and`` when every rule must match, ``or`` when any one is enough, ``""`` when
    #: there is only one rule and nothing to join.
    join: str = ""
    #: The shape of the logic: one row per node, each naming its parent and its depth.
    #: ``join`` describes only the outermost join, so on its own it made nested groups
    #: invisible in the Builder — and invisible structure is structure the next rearrange
    #: destroys without anybody noticing.
    structure: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[SetupBuilderStep] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    unsupported: list[dict[str, Any]] = Field(default_factory=list)
    provider_requirements: list[dict[str, Any]] = Field(default_factory=list)
    #: True when this draft could be edited by hand right now.
    editable: bool = True


class SetupChatErrorEnvelope(BaseModel):
    """Sanitized description of a turn that failed inside the backend.

    The original exception is logged internally under the same ``request_id``; none
    of it reaches the client. A populated envelope always travels with an assistant
    message, so a failed turn is never an empty response.
    """

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=64)
    stage: Literal[
        "intent",
        "extract",
        "patch",
        "interpret",
        "compile",
        "serialize",
        "provider",
    ]
    retryable: bool = False
    message: str = Field(min_length=1, max_length=500)
    field: str | None = Field(default=None, max_length=200)
    draft_id: UUID | None = None
    executable_version: int | None = Field(default=None, ge=1)
    executable_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )


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
    #: Explicit lifecycle position. ``status`` alone cannot distinguish a session that
    #: is still gathering requirements from one holding an inactive compiled draft,
    #: which is why clients could not tell when a turn had finished.
    lifecycle_state: Literal[
        "collecting",
        "needs_clarification",
        "ready_for_confirmation",
        "awaiting_approval",
        "approved",
        "compiled",
        "activated",
    ] = "collecting"
    #: True when this turn reached a state that waits on the user rather than on more
    #: assistant output. Clients stop waiting on this, not on a heuristic.
    turn_complete: bool = False
    title: str
    original_idea: str | None
    messages: list[SetupChatMessageResponse]
    draft_strategy: StrategyDefinition | None = None
    draft_v2: StrategyDraftV2 | None = None
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
    evaluation_contract: SetupChatEvaluationContract | None = None
    error: SetupChatErrorEnvelope | None = None
    next_url: str | None = None
    replayed_client_message_id: str | None = None
    turn_execution_result: dict[str, Any] | None = None
    #: What the last turn actually changed, compared between canonical drafts. The
    #: client renders this instead of reading the assistant's sentence, so what it shows
    #: and what the server did cannot disagree.
    last_diff: SetupDraftDiff | None = None
    #: A change waiting for the user to confirm or cancel. While this is set, nothing in
    #: the draft has moved.
    pending_change: SetupChatPendingChangeResponse | None = None
    #: Whether a turn is in flight, so the composer knows to wait rather than to send.
    turn_state: SetupChatTurnState = Field(default_factory=SetupChatTurnState)
    #: Versions the user can go back to, newest last.
    snapshots: list[SetupChatSnapshotSummary] = Field(default_factory=list)
    #: True when there is a material change this session can undo.
    can_undo: bool = False
    #: The guided Builder's view of this exact draft. Present on every response, so a
    #: change made by the assistant is visible in the Builder without another request.
    builder: SetupBuilderState | None = None
    #: Where this setup is in its life, named the same way on every surface.
    lifecycle: dict[str, str] = Field(default_factory=dict)
    #: Which surfaces can be used right now, and one sentence when the assistant cannot.
    #: The client reads this to close the composer instead of guessing from an error.
    ai_availability: dict[str, Any] = Field(default_factory=dict)
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
