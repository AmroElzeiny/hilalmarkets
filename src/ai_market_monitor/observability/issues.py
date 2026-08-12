"""Writing and moving rows in the operational issue queue.

One method records an occurrence. It either creates the row for a problem seen for
the first time or increments the one already there, and that decision is made by
the ``dedupe_key`` alone. Callers never choose between "create" and "update",
because that choice is precisely what produces four thousand rows for one outage.

State changes go through :meth:`OperationalIssueService.transition`, which refuses a
move the state machine does not allow and appends an event for every move it does.
An issue that was resolved and came back reopens the original row: the history of a
recurring problem is the thing worth keeping, and a fresh row throws it away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models.operations import (
    OPERATIONAL_ISSUE_STATES,
    OPERATIONAL_ISSUE_TRANSITIONS,
    OperationalIssue,
    OperationalIssueEvent,
)
from ai_market_monitor.observability.alerts import FiredAlert
from ai_market_monitor.observability.labels import assert_no_sensitive_content

__all__ = [
    "IssueQueueError",
    "OperationalIssueService",
    "IssueSummary",
    "dedupe_key_for_alert",
]

_SCOPE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_DEDUPE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,159}$")
_EVIDENCE_REF_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z_]+:[A-Za-z0-9_.#/-]{1,120}$")
_MAX_SUMMARY_LENGTH: Final[int] = 240


class IssueQueueError(ValueError):
    """A write the issue queue refused."""


@dataclass(frozen=True, slots=True)
class IssueSummary:
    """Counts by state, for the admin surface and the release gate."""

    open: int
    acknowledged: int
    mitigated: int
    suppressed: int
    resolved: int

    @property
    def needs_attention(self) -> int:
        """Everything a person still has to do something about."""

        return self.open + self.acknowledged + self.mitigated


def dedupe_key_for_alert(alert: FiredAlert) -> str:
    """The stable key for one alert rule firing about one service.

    Built from the rule name and the service it watches, and from nothing else. A
    timestamp, a measured value or a message would all make the key unique per
    occurrence, which is the same as having no key at all.
    """

    return f"alert:{alert.rule.name}:{alert.rule.watched_service}".casefold()


class OperationalIssueService:
    """Read and write the operational issue queue.

    Never touches strategy, Passport, entitlement or approval state. The only tables
    it writes are its own two.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_occurrence(
        self,
        *,
        dedupe_key: str,
        category: str,
        severity: str,
        summary: str,
        affected_scope: str,
        evidence_refs: tuple[str, ...] = (),
        runbook_anchor: str | None = None,
        source: str = "alert_rule",
        definition_version: str | None = None,
        now: datetime | None = None,
    ) -> OperationalIssue:
        """Create the issue, or add one occurrence to the one already open."""

        self._validate_payload(
            dedupe_key=dedupe_key,
            summary=summary,
            affected_scope=affected_scope,
            evidence_refs=evidence_refs,
        )
        moment = now or datetime.now(UTC)
        existing = await self.session.scalar(
            select(OperationalIssue).where(OperationalIssue.dedupe_key == dedupe_key)
        )
        if existing is None:
            issue = OperationalIssue(
                dedupe_key=dedupe_key,
                category=category,
                severity=severity,
                state="open",
                summary=summary,
                affected_scope=affected_scope,
                occurrence_count=1,
                first_seen_at=moment,
                last_seen_at=moment,
                evidence_refs=list(evidence_refs),
                runbook_anchor=runbook_anchor,
                source=source,
                definition_version=definition_version,
            )
            self.session.add(issue)
            await self.session.flush()
            self._append_event(
                issue,
                from_state=None,
                to_state="open",
                actor="system",
                reason="First occurrence recorded.",
                now=moment,
            )
            await self.session.flush()
            return issue

        existing.occurrence_count += 1
        existing.last_seen_at = moment
        existing.summary = summary
        existing.severity = severity
        existing.definition_version = definition_version or existing.definition_version
        if runbook_anchor:
            existing.runbook_anchor = runbook_anchor
        # A suppression that has run out stops suppressing. Left alone, an expired
        # window would keep a live problem invisible for exactly as long as nobody
        # thought to look at the row.
        if existing.state == "suppressed" and (
            existing.suppressed_until is None
            or _aware(existing.suppressed_until) <= moment
        ):
            self._move(
                existing,
                to_state="open",
                actor="system",
                reason="Suppression expired and the problem is still occurring.",
                now=moment,
            )
        elif existing.state == "resolved":
            self._move(
                existing,
                to_state="open",
                actor="system",
                reason="Recurred after being resolved.",
                now=moment,
            )
        await self.session.flush()
        return existing

    async def record_fired_alert(
        self,
        alert: FiredAlert,
        *,
        now: datetime | None = None,
    ) -> OperationalIssue:
        """Record one firing alert as an occurrence on its own issue row."""

        evidence: list[str] = [f"alert:{alert.rule.name}"]
        if alert.rule.runbook_anchor:
            evidence.append(f"runbook:{alert.rule.runbook_anchor}")
        trigger_slo = getattr(alert.rule.trigger, "slo_name", None)
        if trigger_slo:
            evidence.append(f"slo:{trigger_slo}")
        return await self.record_occurrence(
            dedupe_key=dedupe_key_for_alert(alert),
            category=alert.rule.watched_service,
            severity=alert.rule.severity,
            summary=alert.rule.what_broke[:_MAX_SUMMARY_LENGTH],
            affected_scope=alert.rule.watched_service,
            evidence_refs=tuple(evidence),
            runbook_anchor=alert.rule.runbook_anchor,
            source="alert_rule",
            definition_version=alert.rules_version,
            now=now,
        )

    async def transition(
        self,
        *,
        issue_id: UUID,
        to_state: str,
        actor: str,
        reason: str | None = None,
        suppressed_for: timedelta | None = None,
        now: datetime | None = None,
    ) -> OperationalIssue:
        """Move an issue, or raise naming why the move is not allowed."""

        issue = await self.session.get(OperationalIssue, issue_id)
        if issue is None:
            raise IssueQueueError("Operational issue not found.")
        if to_state not in OPERATIONAL_ISSUE_STATES:
            raise IssueQueueError(f"Unknown issue state {to_state!r}.")
        allowed = OPERATIONAL_ISSUE_TRANSITIONS[issue.state]
        if to_state not in allowed:
            raise IssueQueueError(
                f"An issue cannot move from {issue.state!r} to {to_state!r}. "
                f"Allowed: {sorted(allowed)}."
            )
        if to_state == "suppressed" and suppressed_for is None:
            raise IssueQueueError(
                "A suppression must state when it ends. An open-ended suppression "
                "hides a problem permanently."
            )
        if reason is not None:
            assert_no_sensitive_content(reason, field="issue.reason")
            if len(reason) > _MAX_SUMMARY_LENGTH:
                raise IssueQueueError("An issue reason must be a short sentence.")
        assert_no_sensitive_content(actor, field="issue.actor")
        moment = now or datetime.now(UTC)
        if to_state == "suppressed" and suppressed_for is not None:
            issue.suppressed_until = moment + suppressed_for
        else:
            issue.suppressed_until = None
        self._move(issue, to_state=to_state, actor=actor, reason=reason, now=moment)
        await self.session.flush()
        return issue

    async def list_issues(
        self,
        *,
        states: tuple[str, ...] = ("open", "acknowledged", "mitigated"),
        limit: int = 100,
    ) -> list[OperationalIssue]:
        rows = await self.session.scalars(
            select(OperationalIssue)
            .where(OperationalIssue.state.in_(states))
            .order_by(
                OperationalIssue.severity.asc(),
                OperationalIssue.last_seen_at.desc(),
            )
            .limit(limit)
        )
        return list(rows)

    async def summary(self) -> IssueSummary:
        rows = (
            await self.session.execute(
                select(OperationalIssue.state, func.count(OperationalIssue.id)).group_by(
                    OperationalIssue.state
                )
            )
        ).all()
        counts = {str(state): int(count) for state, count in rows}
        return IssueSummary(
            open=counts.get("open", 0),
            acknowledged=counts.get("acknowledged", 0),
            mitigated=counts.get("mitigated", 0),
            suppressed=counts.get("suppressed", 0),
            resolved=counts.get("resolved", 0),
        )

    async def events(self, issue_id: UUID, *, limit: int = 50) -> list[OperationalIssueEvent]:
        rows = await self.session.scalars(
            select(OperationalIssueEvent)
            .where(OperationalIssueEvent.issue_id == issue_id)
            .order_by(OperationalIssueEvent.created_at.asc())
            .limit(limit)
        )
        return list(rows)

    # -- internals ---------------------------------------------------------

    def _move(
        self,
        issue: OperationalIssue,
        *,
        to_state: str,
        actor: str,
        reason: str | None,
        now: datetime,
    ) -> None:
        from_state = issue.state
        issue.state = to_state
        issue.resolved_at = now if to_state == "resolved" else None
        self._append_event(
            issue,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            reason=reason,
            now=now,
        )

    def _append_event(
        self,
        issue: OperationalIssue,
        *,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str | None,
        now: datetime,
    ) -> None:
        self.session.add(
            OperationalIssueEvent(
                issue_id=issue.id,
                from_state=from_state,
                to_state=to_state,
                actor=actor,
                reason=reason,
                details={"occurrence_count": issue.occurrence_count},
                created_at=now,
            )
        )

    @staticmethod
    def _validate_payload(
        *,
        dedupe_key: str,
        summary: str,
        affected_scope: str,
        evidence_refs: tuple[str, ...],
    ) -> None:
        if not _DEDUPE_KEY_PATTERN.match(dedupe_key):
            raise IssueQueueError(
                f"Issue dedupe key {dedupe_key!r} is not a stable low-cardinality key."
            )
        if not _SCOPE_PATTERN.match(affected_scope):
            raise IssueQueueError(
                f"Issue scope {affected_scope!r} must name a component, not an instance."
            )
        if not summary.strip():
            raise IssueQueueError("An issue must say what broke.")
        if len(summary) > _MAX_SUMMARY_LENGTH:
            raise IssueQueueError(
                f"An issue summary must be at most {_MAX_SUMMARY_LENGTH} characters. "
                "Longer than that is a log line, not a summary."
            )
        assert_no_sensitive_content(summary, field="issue.summary")
        assert_no_sensitive_content(affected_scope, field="issue.affected_scope")
        for ref in evidence_refs:
            if not _EVIDENCE_REF_PATTERN.match(ref):
                raise IssueQueueError(
                    f"Evidence reference {ref!r} must be a pointer such as "
                    "'slo:api_availability', never a payload."
                )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
