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

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeV2,
    DraftFieldPatch,
    ShariaPolicyV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.schemas.strict_mode import StrictModel

#: What one authorised operation does. Each kind maps to exactly one mutation of the
#: canonical draft, so the applied diff can be reported per operation.
OperationKind = Literal[
    "set_fields",
    "set_sharia_policy",
    "add_condition",
    "update_condition",
    "remove_condition",
    "replace_groups",
    "add_inclusion",
    "add_exclusion",
    "remove_inclusion",
    "remove_exclusion",
    "add_unresolved",
    "update_unresolved",
    "add_unsupported",
    "resolve_unresolved_key",
    "remove_unsupported_key",
    "restore_snapshot",
]


class AuthorizedPatchOperation(StrictModel):
    """One change, and the exact words that permit it.

    Exactly one of the payload fields is set, matching ``kind``. Anything else is a
    malformed operation and is refused rather than partially applied.
    """

    operation_id: str = Field(min_length=1, max_length=80)
    #: The segment whose text authorises this change. Never optional: an operation with
    #: no author is an operation nobody asked for.
    authorizing_segment_id: str = Field(min_length=1, max_length=80)
    kind: OperationKind

    #: `set_fields`
    fields: DraftFieldPatch | None = None
    #: `set_sharia_policy`
    sharia_policy: ShariaPolicyV2 | None = None
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
    #: `add_unresolved`, `update_unresolved`
    unresolved: UnresolvedFieldV2 | None = None
    #: `restore_snapshot`; the server resolves and verifies both values against
    #: immutable session-owned history.
    target_snapshot_id: str | None = Field(default=None, max_length=80)
    target_executable_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_missing_operation_id(cls, data: object) -> object:
        """Give pre-migration server-built operations a stable evidence identity.

        The strict model schema still requires ``operation_id`` from the planner.
        This adapter is only for trusted Python producers and persisted legacy plans
        that predate operation-level attribution.
        """

        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        # Some planners attach the unsupported requirement's `blocking` flag to
        # the operation. Blocking is deliberately server-owned and always true for
        # launch-path unsupported mechanics, so discard this redundant model field
        # rather than accepting a model-authored false value or rejecting an
        # otherwise fail-closed unsupported record.
        if migrated.get("kind") == "add_unsupported":
            migrated.pop("blocking", None)
        kind = migrated.get("kind")
        unresolved = migrated.get("unresolved")
        if (
            kind == "update_unresolved"
            and not migrated.get("target_key")
            and isinstance(unresolved, dict)
            and unresolved.get("unresolved_id")
        ):
            # The payload already names the exact unresolved contract. Reuse that
            # identity instead of rejecting the whole turn because the planner did
            # not repeat it in target_key.
            migrated["target_key"] = unresolved["unresolved_id"]

        allowed_payloads: dict[object, frozenset[str]] = {
            "set_fields": frozenset({"fields"}),
            "set_sharia_policy": frozenset({"sharia_policy"}),
            "add_condition": frozenset({"condition"}),
            "update_condition": frozenset({"condition", "target_condition_id"}),
            "remove_condition": frozenset({"target_condition_id"}),
            "replace_groups": frozenset({"condition"}),
            "add_inclusion": frozenset({"symbol"}),
            "add_exclusion": frozenset({"symbol"}),
            "remove_inclusion": frozenset({"symbol"}),
            "remove_exclusion": frozenset({"symbol"}),
            "add_unresolved": frozenset({"unresolved"}),
            "update_unresolved": frozenset({"unresolved", "target_key"}),
            "add_unsupported": frozenset({"missing_contract"}),
            "resolve_unresolved_key": frozenset({"target_key"}),
            "remove_unsupported_key": frozenset({"target_key"}),
            "restore_snapshot": frozenset({"target_snapshot_id", "target_executable_version"}),
        }
        payload_fields = {
            "fields",
            "sharia_policy",
            "condition",
            "target_condition_id",
            "symbol",
            "missing_contract",
            "target_key",
            "unresolved",
            "target_snapshot_id",
            "target_executable_version",
        }
        if kind in allowed_payloads:
            for field_name in payload_fields - allowed_payloads[kind]:
                migrated.pop(field_name, None)
        if migrated.get("operation_id"):
            return migrated
        encoded = json.dumps(
            {
                key: (value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
                for key, value in migrated.items()
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        migrated["operation_id"] = f"legacy_{hashlib.sha256(encoded).hexdigest()[:24]}"
        return migrated

    @model_validator(mode="after")
    def validate_payload(self) -> AuthorizedPatchOperation:
        required: dict[str, tuple[str, ...]] = {
            "set_fields": ("fields",),
            "set_sharia_policy": ("sharia_policy",),
            "add_condition": ("condition",),
            "update_condition": ("condition", "target_condition_id"),
            "remove_condition": ("target_condition_id",),
            "replace_groups": ("condition",),
            "add_inclusion": ("symbol",),
            "add_exclusion": ("symbol",),
            "remove_inclusion": ("symbol",),
            "remove_exclusion": ("symbol",),
            "add_unresolved": ("unresolved",),
            "update_unresolved": ("unresolved", "target_key"),
            "add_unsupported": ("missing_contract",),
            "resolve_unresolved_key": ("target_key",),
            "remove_unsupported_key": ("target_key",),
            "restore_snapshot": (
                "target_snapshot_id",
                "target_executable_version",
            ),
        }
        for name in required[self.kind]:
            if getattr(self, name) is None:
                raise ValueError(f"{self.kind} requires {name}")
        payload_fields = {
            "fields",
            "sharia_policy",
            "condition",
            "target_condition_id",
            "symbol",
            "missing_contract",
            "target_key",
            "unresolved",
            "target_snapshot_id",
            "target_executable_version",
        }
        allowed = set(required[self.kind])
        unexpected = sorted(
            name for name in payload_fields - allowed if getattr(self, name) is not None
        )
        if unexpected:
            raise ValueError(f"{self.kind} cannot carry payload fields: {', '.join(unexpected)}")
        return self


#: What kind of thing a clarification is asking about, so an answer can be checked
#: against the slot it claims to fill.
ClarificationTargetType = Literal[
    "conversational",
    "draft_field",
    "condition_field",
    "condition_creation",
    "universe",
    "market_scope",
    "sharia_policy",
    "boolean_structure",
    "capability_parameter",
    "reference_definition",
    "unsupported_requirement",
    "unsupported_resolution",
]


class CancellationPolicy(StrEnum):
    """What ``cancel`` must do to the canonical state behind one question.

    Cancelling used to mean only "stop showing the question". The blocker that caused
    it stayed in the draft, so the setup was still blocked for a reason nothing on
    screen mentioned any more — hidden blocked state a trader could not see or clear.

    So every question now says, when it is created, what cancelling it means. There are
    exactly three honest answers and none of them is "clear the question and say
    nothing about the blocker".
    """

    #: The trader is abandoning a rule they themselves started. The canonical blocker
    #: goes with it, and no half-built condition is created in its place.
    REMOVE_PENDING_REQUIREMENT = "remove_pending_requirement"
    #: The platform requires this before the setup can run — a screened universe, an
    #: approved methodology. It is put down, not thrown away: the blocker stays, and the
    #: reply says plainly that the setup is still incomplete and how to come back to it.
    PAUSE_PENDING_REQUIREMENT = "pause_pending_requirement"
    #: Nothing canonical is behind it. A read-only Scanner question is the whole family:
    #: dropping it changes no draft, no rule and no governed policy.
    CANCEL_CONVERSATION_ONLY = "cancel_conversation_only"


#: What cancelling means for a question that did not say. Chosen per target type and
#: deliberately fail-closed: anything the platform itself requires is *paused*, so a
#: cancellation can never quietly claim to have removed a requirement that is still
#: blocking the draft. Only questions whose blocker the trader authored are removable.
DEFAULT_CANCELLATION_POLICY: dict[str, CancellationPolicy] = {
    "conversational": CancellationPolicy.CANCEL_CONVERSATION_ONLY,
    "condition_creation": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "condition_field": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "boolean_structure": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "capability_parameter": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "reference_definition": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "unsupported_requirement": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "unsupported_resolution": CancellationPolicy.REMOVE_PENDING_REQUIREMENT,
    "draft_field": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "universe": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "market_scope": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
    "sharia_policy": CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
}


class ClarificationContract(StrictModel):
    """A question the server authorised, and what would count as answering it.

    Before this existed a clarification was a sentence. The next turn could clear it by
    claiming to, without changing anything the question was about, so an open item
    disappeared while the draft stayed blocked for the same reason.
    """

    #: Unique to **this step**. A multi-step workflow that reused one id across steps
    #: could not tell "answered step one" from "answered step two", so a late or
    #: repeated reply landed on the wrong field.
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
    #: The multi-step question this step belongs to, stable for the whole workflow.
    #: Separate from ``question_id`` on purpose: the workflow names the thing being
    #: built, the question names one step of building it.
    workflow_id: str | None = Field(default=None, max_length=120)
    #: Bumped on every step. An answer carrying an older revision is stale — it was
    #: written against a question that is no longer the one on screen.
    step_revision: int = Field(default=0, ge=0)
    #: The canonical values an answer may resolve to. Wider than what is displayed:
    #: buttons are a shortlist, this is everything the step can actually execute.
    canonical_values: list[str] = Field(default_factory=list, max_length=64)
    #: What ``cancel`` must do to the canonical state behind this question. Decided when
    #: the question is created, never guessed at cancellation time — by then the code
    #: that knew whether the blocker was the trader's or the platform's is long gone.
    cancellation_policy: CancellationPolicy = CancellationPolicy.CANCEL_CONVERSATION_ONLY

    @model_validator(mode="before")
    @classmethod
    def derive_cancellation_policy(cls, data: object) -> object:
        """Give a question written before this field existed the right policy.

        Reconstructed from ``target_type``, which is the same fact the policy is chosen
        from in the first place. A stored session therefore keeps working, and it keeps
        working *correctly* rather than defaulting every old question to "drop it".
        """

        if not isinstance(data, dict) or data.get("cancellation_policy"):
            return data
        migrated = dict(data)
        migrated["cancellation_policy"] = DEFAULT_CANCELLATION_POLICY.get(
            str(migrated.get("target_type") or ""),
            CancellationPolicy.PAUSE_PENDING_REQUIREMENT,
        )
        return migrated

    @model_validator(mode="after")
    def validate_target(self) -> ClarificationContract:
        if self.mutating and self.target_type == "conversational":
            raise ValueError("a conversational question cannot be mutating")
        if (
            self.target_type
            in {
                "condition_field",
                "capability_parameter",
                "reference_definition",
            }
            and not self.target_condition_id
        ):
            raise ValueError("a condition question must name its condition")
        if (
            self.target_type
            in {
                "draft_field",
                "condition_field",
                "capability_parameter",
                "sharia_policy",
            }
            and not self.target_field
        ):
            raise ValueError("a field question must name its field")
        return self
