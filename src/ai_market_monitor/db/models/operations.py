"""The durable operational issue queue.

Separate from :class:`~ai_market_monitor.db.models.monitoring.Incident`, which
records a *declared* customer-facing incident with impact rows, updates and a
published timeline. An operational issue is the layer underneath: the recurring
problem an alert keeps firing about, before anybody has decided it is an incident.

The two properties that make it useful are both enforced by the schema.

``dedupe_key`` is unique. A provider that fails four thousand times overnight
produces one row with a count of four thousand, not four thousand rows. Without
that, the queue becomes unreadable exactly when it matters most, and the honest
signal — how long has this been happening — is the one that gets lost.

Nothing here holds customer content. There is no strategy text column, no religious
status column and no free-text field a caller could put a prompt in. The service
that writes these rows refuses such content as well, but the schema is the part
that cannot be bypassed by a new caller.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: The states an issue can hold. ``suppressed`` is deliberately time-bound in the
#: row itself: a suppression with no expiry is how a known problem stops being
#: reported and then stops being known.
OPERATIONAL_ISSUE_STATES: frozenset[str] = frozenset(
    {"open", "acknowledged", "mitigated", "resolved", "suppressed"}
)

#: Which state may follow which. Resolved is not terminal — the same dedupe key
#: recurring after a fix is the single most valuable thing this queue can tell you,
#: so it reopens rather than starting a fresh row with no history.
OPERATIONAL_ISSUE_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"acknowledged", "mitigated", "resolved", "suppressed"}),
    "acknowledged": frozenset({"mitigated", "resolved", "suppressed", "open"}),
    "mitigated": frozenset({"resolved", "open", "suppressed"}),
    "resolved": frozenset({"open"}),
    "suppressed": frozenset({"open", "acknowledged", "resolved"}),
}


class OperationalIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operational_issues"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_operational_issue_dedupe_key"),
        Index("ix_operational_issue_state_last_seen", "state", "last_seen_at"),
        Index("ix_operational_issue_severity_state", "severity", "state"),
        Index("ix_operational_issue_category", "category", "last_seen_at"),
    )

    #: Stable across every occurrence of the same problem. Built by the service from
    #: the rule and the scope, never from a timestamp or a message, so the same
    #: failure tomorrow lands on the same row.
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    #: A short operational sentence: what broke. Length-capped and content-checked by
    #: the service so it can never grow into a pasted log line or a model reply.
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    #: Which part of the system, in low-cardinality terms such as ``provider:openai``.
    #: Never a user, a symbol or a strategy.
    affected_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: An operator handle, not an email address. The queue names who is holding a
    #: problem; it is not a contact list.
    assignee: Mapped[str | None] = mapped_column(String(60))
    #: Pointers, never payloads: ``slo:api_availability``, ``runbook:#api-latency``.
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    runbook_anchor: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40), default="alert_rule", nullable=False)
    #: When a suppression stops. Always set while suppressed, always cleared otherwise.
    suppressed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The alert-rule and objective version that produced this row, so an issue raised
    #: under an older threshold stays readable after the threshold moves.
    definition_version: Mapped[str | None] = mapped_column(String(40))


class OperationalIssueEvent(UUIDPrimaryKeyMixin, Base):
    """One state change on an issue. Append-only; nothing here is ever updated.

    The audit trail is what separates "this was fixed" from "somebody closed it".
    """

    __tablename__ = "operational_issue_events"
    __table_args__ = (
        Index("ix_operational_issue_event_issue", "issue_id", "created_at"),
    )

    issue_id: Mapped[UUID] = mapped_column(
        ForeignKey("operational_issues.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    #: ``system`` for automatic transitions, otherwise the operator handle.
    actor: Mapped[str] = mapped_column(String(60), default="system", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
