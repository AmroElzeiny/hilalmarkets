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

import hashlib
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
from ai_market_monitor.observability.labels import (
    MAX_RECORD_VALUE_LENGTH,
    SensitiveValueError,
    assert_no_sensitive_content,
)

__all__ = [
    "IssueQueueError",
    "OperationalIssueService",
    "IssueSummary",
    "dedupe_key_for_alert",
    "sanitize_dedupe_key",
]

_SCOPE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_DEDUPE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,159}$")
#: The longest key the pattern above can hold: one start character plus 159 others.
_MAX_DEDUPE_KEY_LENGTH: Final[int] = 160
_DEDUPE_KEY_CHAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_.:/-]")
#: The tail an evidence pointer may hold. Named, because the rule that decides whether
#: a ref is stored or reduced has to truncate to exactly this allowance: a real id
#: longer than the queue can hold is cut, not refused.
_EVIDENCE_REF_MAX_TAIL_LENGTH: Final[int] = 120
_EVIDENCE_REF_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z_]+:[A-Za-z0-9_.#/-]{{1,{_EVIDENCE_REF_MAX_TAIL_LENGTH}}}$"
)
_EVIDENCE_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z_]+$")
#: What a tail must look like to **be** an identifier rather than to be **made** one.
#: The queue's own pattern above forbids ``:``, so this shape is wider by exactly that
#: one character: a NOWPayments id holds colons inside itself, and the stored form
#: rewrites them to ``-``. Everything else the shape allows is what the stored pattern
#: allows, so a tail that passes here always survives the rewrite inside the pattern.
#: A tail outside it — a space, an ``@``, a ``?``, a quote, a brace — is not a name at
#: all, and is never repaired into looking like one.
_EVIDENCE_TAIL_SHAPE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.:/#-]+")
#: A ref that is not an identifier shape is replaced by this many hex characters of its
#: own digest: enough to tell two payloads apart and to recognise the same payload
#: again, short enough to always fit behind a usable prefix, and made only of characters
#: the stored pattern allows.
_EVIDENCE_POINTER_HASH_LENGTH: Final[int] = 16
#: A summary or a reason is held to the one limit every operational record has. The
#: queue once allowed 240 while the content check it then ran refused anything over 200,
#: so a summary of 201 to 240 characters passed one rule and failed the next — and an
#: alert whose text was cut to 240 would have failed at the moment it fired.
_MAX_SUMMARY_LENGTH: Final[int] = MAX_RECORD_VALUE_LENGTH
#: Evidence refs are pointers, not payloads. A 13-character prefix such as
#: ``billing_event`` plus one colon leaves 120 characters for the tail, matching the
#: evidence-ref pattern. Anything longer is truncated at the owner, not left to callers.
_MAX_EVIDENCE_REF_LENGTH: Final[int] = 134


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


def sanitize_dedupe_key(key: str) -> str:
    """Make one caller's raw string into a dedupe key the queue can always store.

    Providers write event ids in their own shapes: Stripe uses uppercase
    (``evt_01JABCdefGHI``), NOWPayments uses ``:``, and some ids run past the 160
    characters the column holds. Those are facts about providers, not reasons to lose
    a critical billing alert, so what a key may look like is decided here — once —
    instead of by each caller's string. A key is casefolded, characters the pattern
    forbids become ``-``, the first character is made a lowercase letter or digit, and
    the result is cut to the length the pattern allows. A key that already fits is
    returned unchanged, so a write and the later read for the same problem agree, and
    cleaning twice is the same as cleaning once.

    A key that cannot be made safe keeps raising: an empty string, anything with
    whitespace (a key with spaces is prose or a timestamp, not a stable identifier —
    exactly the volatile keys the queue has always refused), or a key with no
    lowercase letter or digit anywhere left in it.
    """

    error = IssueQueueError(
        f"Issue dedupe key {key!r} is not a stable low-cardinality key."
    )
    if not isinstance(key, str) or not key:
        raise error
    if any(character.isspace() for character in key):
        raise error
    safe = "".join(
        character if _DEDUPE_KEY_CHAR_PATTERN.match(character) else "-"
        for character in key.casefold()
    )
    # The pattern wants the first character to be [a-z0-9]. `. : / _ -` are allowed
    # anywhere else but never there, so a key that starts with them loses that
    # leading junk and keeps everything meaningful.
    safe = safe.lstrip("_.:/-")[:_MAX_DEDUPE_KEY_LENGTH]
    if not safe or not _DEDUPE_KEY_PATTERN.match(safe):
        raise error
    return safe


def _sanitize_evidence_ref(ref: str) -> str:
    """Keep an evidence ref a pointer, or reduce it to one. Never store a payload.

    A ref is ``prefix:identifier`` — a name the reviewer can follow, not the content
    found next to the failure. Some provider ids genuinely hold a character the stored
    pattern reserves for the separator, such as the ``:`` in
    ``nowpayments:90313222:finished``; some are longer than the pattern can hold. Those
    are facts about providers, not payloads, so a tail that has an identifier's shape
    (``[A-Za-z0-9_.:/#-]+``: no whitespace, no ``@``, no ``?``, no ``=``, no quote, no
    brace, no backslash) is kept — its colons rewritten to ``-`` so the stored form
    still matches :data:`_EVIDENCE_REF_PATTERN`, and its length cut to that pattern's
    allowance rather than refused.

    A tail outside that shape is prose, a response body, a stack line, an address or a
    URL. Repairing it character by character was the old bug: a sentence written behind
    ``billing_event:`` survived as the same sentence with ``-`` where its spaces were,
    a payload wearing a prefix. Such a ref is replaced by the prefix plus
    :data:`_EVIDENCE_POINTER_HASH_LENGTH` hex characters of its own SHA-256: the same
    content always yields the same pointer, so a reviewer can see that two occurrences
    carried the same body, while the body itself is never stored. A ref whose prefix is
    usable therefore never raises here — a diagnostic must not become the failure.

    What does still raise is a ref that cannot name anything: no colon, an empty tail,
    an empty ref, or a prefix outside ``[a-z_]+``. A long prefix is not one of them: the
    tail allowance shrinks behind it, and a prefix so long that even the digest would
    not fit keeps the digest, because losing a usable pointer is worse than a ref that
    runs past the usual budget.

    An identifier-shaped tail can still be a credential (``billing_event:sk-…``, a JWT).
    It is kept only when :func:`assert_no_sensitive_content` finds nothing in it;
    otherwise it takes the same digest route as prose. It used to be kept and then
    refused by ``_validate_payload``, and that refusal threw away the whole issue — a
    critical billing alert lost because one of its pointers looked like a key. The
    secret is still never stored; the alert is still written.

    The refusals that remain are all about the prefix, and a prefix is written in our own
    code (``f"billing_event:{event.id}"``), never taken from a provider. They are caught
    by the first test that runs that caller, not by a customer's webhook.
    """

    error = (
        f"Evidence reference {ref!r} must be a pointer such as "
        "'slo:api_availability', never a payload."
    )
    if not isinstance(ref, str) or not ref:
        raise IssueQueueError(error)
    if ":" not in ref:
        raise IssueQueueError(error)
    prefix, tail = ref.split(":", 1)
    if not _EVIDENCE_PREFIX_PATTERN.match(prefix):
        raise IssueQueueError(error)
    if not tail:
        raise IssueQueueError(error)
    # Three numbers meet here. The stored pattern allows at most
    # ``_EVIDENCE_REF_MAX_TAIL_LENGTH`` tail characters; the pointer's usual total
    # budget leaves ``_MAX_EVIDENCE_REF_LENGTH - len(prefix) - 1`` of them to this
    # prefix; and the reduction needs ``_EVIDENCE_POINTER_HASH_LENGTH`` to stay
    # distinguishable. The smaller of the first two is taken, but never below the third,
    # so a ref with a usable prefix always has room for its own pointer — which is why
    # this line cannot raise.
    max_tail = min(
        _EVIDENCE_REF_MAX_TAIL_LENGTH,
        max(_EVIDENCE_POINTER_HASH_LENGTH, _MAX_EVIDENCE_REF_LENGTH - len(prefix) - 1),
    )
    if _EVIDENCE_TAIL_SHAPE_PATTERN.fullmatch(tail):
        kept = f"{prefix}:{tail.replace(':', '-')[:max_tail]}"
        if not _carries_sensitive_content(kept):
            return kept
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    # Shorter than a digest only in the corner above, where the prefix has already used
    # the budget; normally this is exactly ``_EVIDENCE_POINTER_HASH_LENGTH`` characters.
    return f"{prefix}:{digest[:min(_EVIDENCE_POINTER_HASH_LENGTH, max_tail)]}"


def _carries_sensitive_content(ref: str) -> bool:
    """Ask the one content check whether a ref holds a secret, without raising."""

    try:
        assert_no_sensitive_content(ref, field="issue.evidence_ref")
    except SensitiveValueError:
        return True
    return False


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

        # Normalise the key once, here, and use that one value for the lookup, the
        # insert and the update below. Sanitising at the owner keeps a provider-shaped
        # key (uppercase, colons, too long) writable, and keeps the same raw id
        # pointing at the same row every time.
        safe_key = sanitize_dedupe_key(dedupe_key)
        evidence_refs = self._validate_payload(
            dedupe_key=safe_key,
            summary=summary,
            affected_scope=affected_scope,
            evidence_refs=evidence_refs,
        )
        moment = now or datetime.now(UTC)
        existing = await self.session.scalar(
            select(OperationalIssue).where(OperationalIssue.dedupe_key == safe_key)
        )
        if existing is None:
            issue = OperationalIssue(
                dedupe_key=safe_key,
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
            if len(reason) > _MAX_SUMMARY_LENGTH:
                raise IssueQueueError("An issue reason must be a short sentence.")
            assert_no_sensitive_content(reason, field="issue.reason")
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
    ) -> tuple[str, ...]:
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
        safe_refs: list[str] = []
        for raw_ref in evidence_refs:
            # ``_sanitize_evidence_ref`` already reduced prose and anything the content
            # check objects to, so this check sees a pointer and cannot fire on data. It
            # stays as the last line of defence on the value that is actually stored.
            ref = _sanitize_evidence_ref(raw_ref)
            if not _EVIDENCE_REF_PATTERN.match(ref):
                raise IssueQueueError(
                    f"Evidence reference {ref!r} must be a pointer such as "
                    "'slo:api_availability', never a payload."
                )
            assert_no_sensitive_content(ref, field="issue.evidence_ref")
            safe_refs.append(ref)
        return tuple(safe_refs)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
