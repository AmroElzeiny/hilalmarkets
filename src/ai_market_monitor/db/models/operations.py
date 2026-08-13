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
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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


#: The writer identity used for a row that has been folded together after the fact.
#: Reserved, so a real process can never be mistaken for a rollup or overwrite one.
METRIC_ROLLUP_WRITER: str = "rollup"


class OperationalMetricDelta(UUIDPrimaryKeyMixin, Base):
    """One process's movement on one metric during one time window.

    Sits beside :class:`~ai_market_monitor.db.models.monitoring.OperationalMetric`
    rather than inside it. That table records a *health measurement*: one component,
    one reading, one ``HealthStatus``, taken at a moment. This one records a
    *counter's movement*: no health status exists for "the API answered 4,102
    requests", and a histogram's bucket counts have nowhere to live in a single
    ``value`` column. Forcing both meanings into one table would have meant inventing
    a health status for every counter and reading distributions out of a JSON blob.
    Same database, same session, same migrations — one more table, not a second store.

    **Why concurrent writers cannot lose or double-count.** The unique key includes
    ``writer``, which is unique per process. No two processes ever touch the same row,
    so there is no read-modify-write to race on. Each process writes only what *it*
    added since its own last write, and the reader sums the rows. A process that dies
    mid-window leaves its counts behind; a process that restarts gets a new ``writer``
    and starts a new row instead of overwriting the dead one's.
    """

    __tablename__ = "operational_metric_deltas"
    __table_args__ = (
        UniqueConstraint(
            "metric_name",
            "label_signature",
            "window_start",
            "writer",
            name="uq_operational_metric_delta_series",
        ),
        Index("ix_operational_metric_delta_window", "window_start", "metric_name"),
        Index("ix_operational_metric_delta_name_window", "metric_name", "window_start"),
    )

    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: ``counter``, ``gauge`` or ``histogram``. Stored so a reader can add up the rows
    #: correctly without having to trust that the code's registry still says the same.
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: ``label=value`` pairs, sorted and joined. The comparable form of ``labels``;
    #: a JSON column cannot carry a unique constraint on every backend.
    label_signature: Mapped[str] = mapped_column(String(500), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Start of the window this row belongs to, floored to the configured width.
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Which process wrote it: ``host:pid:started``. Low-cardinality by deployment,
    #: bounded over time by the rollup that folds old rows into one.
    writer: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Counter and histogram: the amount added in this window. Gauge: the last reading.
    total: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    observations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Histogram only: counts per shared bucket edge, plus one overflow slot.
    buckets: Mapped[list[int] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
