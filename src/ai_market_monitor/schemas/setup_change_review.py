"""What changed, and what is waiting to be confirmed — as server-owned facts.

The dashboard used to work out whether a turn had broken something by reading the
assistant's sentence. That is the model describing its own work, so a turn that
replaced every rule could be shown as "updated your setup" and the user would only
find out later.

Two records live here, and neither is authored by a model:

* :class:`SetupDraftDiff` — the exact difference between two canonical drafts, in a
  shape the frontend can render without interpreting prose.
* :class:`PendingDestructiveChange` — a change that has **not** been applied, its
  operations stored verbatim, waiting for the user to confirm or cancel it.

A pending change is not a draft state. Nothing in the canonical draft moves while one
exists; that is the whole point of it.

Only the records live here. What *builds* them needs the diff engine, and the public
request model imports this file, so the builders live in
:mod:`ai_market_monitor.engine.change_review` instead. Importing the engine from here
would drag the whole strategy evaluator into every schema import.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

#: How long a proposal stays valid. Long enough to read a diff and think, short enough
#: that a forgotten tab cannot apply yesterday's change to today's draft.
PENDING_CHANGE_TTL_MINUTES = 30

#: Where the pending proposal lives on the chat session's JSON context. One key, one
#: writer — a second home is how two copies start disagreeing.
PENDING_CHANGE_CONTEXT_KEY = "pending_destructive_change"


class DiffFieldChange(BaseModel):
    """One field that moved, with the values a person can read."""

    model_config = ConfigDict(extra="forbid")

    #: Stable identity of the thing that changed (a condition id, a field path, a symbol).
    target: str = Field(min_length=1, max_length=200)
    #: Plain label for the field. Never an internal name on its own.
    label: str = Field(default="", max_length=200)
    before: str | None = Field(default=None, max_length=500)
    after: str | None = Field(default=None, max_length=500)


class DiffConditionEntry(BaseModel):
    """One rule that was added or removed, named the way the user would say it."""

    model_config = ConfigDict(extra="forbid")

    condition_id: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=300)


class SetupDraftDiff(BaseModel):
    """Every difference between two canonical drafts, ready to render.

    Built only by :func:`build_draft_diff`. The frontend reads this instead of the
    assistant's wording, so what it shows and what the server did cannot disagree.
    """

    model_config = ConfigDict(extra="forbid")

    added_conditions: list[DiffConditionEntry] = Field(default_factory=list)
    removed_conditions: list[DiffConditionEntry] = Field(default_factory=list)
    changed_fields: list[DiffFieldChange] = Field(default_factory=list)
    #: How the rules join, before and after. Empty strings when there is no join yet.
    boolean_topology_before: str = ""
    boolean_topology_after: str = ""
    boolean_topology_changed: bool = False
    universe_changes: list[DiffFieldChange] = Field(default_factory=list)
    methodology_changes: list[DiffFieldChange] = Field(default_factory=list)
    market_scope_changes: list[DiffFieldChange] = Field(default_factory=list)
    unresolved_added: list[str] = Field(default_factory=list)
    unresolved_resolved: list[str] = Field(default_factory=list)
    unresolved_advanced: list[str] = Field(default_factory=list)
    unsupported_added: list[str] = Field(default_factory=list)
    unsupported_removed: list[str] = Field(default_factory=list)
    provider_requirement_changes: list[DiffFieldChange] = Field(default_factory=list)
    approval_invalidated: bool = False
    #: True when the setup could be approved. Derived from the draft's own blocking
    #: state, never from the session status, which lags a turn behind.
    ready_before: bool = False
    ready_after: bool = False
    executable_version_before: int = 0
    executable_version_after: int = 0
    executable_hash_before: str = ""
    executable_hash_after: str = ""
    workflow_state_hash_before: str = ""
    workflow_state_hash_after: str = ""
    #: True when nothing at all differs. Lets a client say "no change" without having
    #: to check eleven empty lists itself.
    empty: bool = True



class PendingDestructiveChange(BaseModel):
    """A change that is proposed and **not** applied, waiting on the user.

    The operations are stored exactly as they were authorized. Confirming does not
    re-plan and does not re-read the user's words: it replays this stored list against
    the same draft it was built for, or refuses because that draft has moved.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=8, max_length=64)
    #: The turn that proposed it, so the audit trail joins up.
    source_turn_id: str = Field(min_length=1, max_length=80)
    client_message_id: str = Field(default="", max_length=80)
    #: The draft this proposal was built against. Both hashes, because a blocker moving
    #: is a different kind of staleness from an executable change.
    executable_hash: str = Field(min_length=1, max_length=64)
    workflow_state_hash: str = Field(min_length=1, max_length=64)
    executable_version: int = Field(ge=0)
    operations: list[AuthorizedPatchOperation] = Field(default_factory=list, max_length=64)
    #: The exact difference confirming would produce, computed before it was offered.
    diff: SetupDraftDiff
    reasons: list[str] = Field(default_factory=list, max_length=16)
    summary_lines: list[str] = Field(default_factory=list, max_length=16)
    invalidates_approval: bool = False
    #: What the user should know about Sharia and market-data effects, in plain words.
    #: Empty when the change touches neither.
    governance_notes: list[str] = Field(default_factory=list, max_length=8)
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "confirmed", "cancelled", "stale", "applied"] = "pending"

    @model_validator(mode="after")
    def expiry_after_creation(self) -> PendingDestructiveChange:
        if self.expires_at <= self.created_at:
            raise ValueError("a proposal must expire after it was created")
        return self

    @property
    def operation_payload_hash(self) -> str:
        """Identity of exactly this operation list.

        Confirmation checks this, so a proposal whose stored operations were edited
        between offering and confirming is refused rather than applied.
        """

        encoded = json.dumps(
            [item.model_dump(mode="json") for item in self.operations],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return moment >= expires

    def matches_draft(self, draft: StrategyDraftV2) -> bool:
        """True only when the draft is still exactly the one this was built against."""

        return (
            draft.executable_hash == self.executable_hash
            and draft.workflow_state_hash == self.workflow_state_hash
        )


