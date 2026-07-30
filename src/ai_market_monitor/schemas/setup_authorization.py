"""Which of the user's words authorised each change, one change at a time.

A patch used to arrive as a bag of operations grounded against the whole message. That
is too coarse to be safe. In

    drop LTC from the list, and is 5% a lot for a 15m candle?

the ``5%`` and the ``15m`` belong to a *question*. Message-wide grounding let them
justify a condition, so a question could quietly author a rule.

So every state-changing operation names the one segment that authorised it, and the
server checks that segment: it must exist, it must quote the message exactly, and its
kind must be one that can act. Values are then grounded **only** in that segment's own
text, plus fields explicitly inherited from a named existing condition.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeV2,
    DraftFieldPatch,
)
from ai_market_monitor.schemas.strict_mode import StrictModel

#: What one authorised operation does. Each kind maps to exactly one mutation of the
#: canonical draft, so the applied diff can be reported per operation.
OperationKind = Literal[
    "set_fields",
    "add_condition",
    "update_condition",
    "remove_condition",
    "replace_groups",
    "add_inclusion",
    "add_exclusion",
    "remove_inclusion",
    "remove_exclusion",
    "add_unsupported",
    "resolve_unresolved_key",
    "remove_unsupported_key",
]


class AuthorizedPatchOperation(StrictModel):
    """One change, and the exact words that permit it.

    Exactly one of the payload fields is set, matching ``kind``. Anything else is a
    malformed operation and is refused rather than partially applied.
    """

    #: The segment whose text authorises this change. Never optional: an operation with
    #: no author is an operation nobody asked for.
    authorizing_segment_id: str = Field(min_length=1, max_length=80)
    kind: OperationKind

    #: `set_fields`
    fields: DraftFieldPatch | None = None
    #: `add_condition`, `update_condition`, `replace_groups`
    condition: ConditionNodeV2 | None = None
    #: `update_condition`, `remove_condition` — the existing node this acts on.
    target_condition_id: str | None = Field(default=None, max_length=120)
    #: `add_inclusion`, `add_exclusion`, `remove_inclusion`, `remove_exclusion`
    symbol: str | None = Field(default=None, max_length=40)
    #: `add_unsupported` — what the platform cannot express exactly.
    missing_contract: str | None = Field(default=None, max_length=500)
    #: `resolve_unresolved_key`, `remove_unsupported_key`
    #:
    #: Named explicitly rather than matched from free text. A correction whose target
    #: was prose could clear the wrong open item, or none at all.
    target_key: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_payload(self) -> AuthorizedPatchOperation:
        required: dict[str, tuple[str, ...]] = {
            "set_fields": ("fields",),
            "add_condition": ("condition",),
            "update_condition": ("condition", "target_condition_id"),
            "remove_condition": ("target_condition_id",),
            "replace_groups": ("condition",),
            "add_inclusion": ("symbol",),
            "add_exclusion": ("symbol",),
            "remove_inclusion": ("symbol",),
            "remove_exclusion": ("symbol",),
            "add_unsupported": ("missing_contract",),
            "resolve_unresolved_key": ("target_key",),
            "remove_unsupported_key": ("target_key",),
        }
        for name in required[self.kind]:
            if getattr(self, name) is None:
                raise ValueError(f"{self.kind} requires {name}")
        return self

    @property
    def mutates_executable_state(self) -> bool:
        """True when this operation changes what the monitor would fire on."""
        return self.kind not in {"resolve_unresolved_key", "remove_unsupported_key"}


#: What kind of thing a clarification is asking about, so an answer can be checked
#: against the slot it claims to fill.
ClarificationTargetType = Literal[
    "conversational",
    "draft_field",
    "condition_field",
    "universe",
    "unsupported_requirement",
]


class ClarificationContract(StrictModel):
    """A question the server authorised, and what would count as answering it.

    Before this existed a clarification was a sentence. The next turn could clear it by
    claiming to, without changing anything the question was about, so an open item
    disappeared while the draft stayed blocked for the same reason.
    """

    question_id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=400)
    target_type: ClarificationTargetType
    #: The draft or condition field the answer must fill, when there is one.
    target_field: str | None = Field(default=None, max_length=120)
    target_condition_id: str | None = Field(default=None, max_length=120)
    #: Plain description of a usable answer, for the model and for the user.
    expected_answer_schema: str = Field(min_length=1, max_length=200)
    #: True when answering it must change executable state. A mutating question cannot
    #: be closed by words alone.
    mutating: bool = True
    allowed_options: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_target(self) -> ClarificationContract:
        if self.mutating and self.target_type == "conversational":
            raise ValueError("a conversational question cannot be mutating")
        if self.target_type == "condition_field" and not self.target_condition_id:
            raise ValueError("a condition question must name its condition")
        if self.target_type in {"draft_field", "condition_field"} and not self.target_field:
            raise ValueError("a field question must name its field")
        return self
