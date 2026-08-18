"""One answer to "may this person send another support message right now".

Two doors take support messages: the public ``/contact`` form and the dashboard's own
support form. Before this module neither counted anything, so one script could open a
thousand tickets, and a person who was told "two per email" at one door could send two
more at the other.

The rule is a property of the **person and of the product**, never of the door:

* ``per_email`` — how many messages one address may send inside the window.
* ``per_client`` — how many one browser session or address may send inside the window.
* ``per_hour`` — how many the product accepts in total inside the window, whoever
  sent them. This is the flood ceiling; it exists so a crowd of fresh addresses cannot
  do what one address cannot.

All three read their numbers from settings, so the operator changes a limit in the
environment rather than in the code.

**Counted from one ledger.** ``support_intake_records`` holds one row per accepted
message, written by whichever door accepted it, holding only salted hashes of the
address and the session. That is what makes "two per email" mean two across the whole
product rather than two per form. Counting the two message tables instead would have
meant a JSON query that behaves differently on SQLite and PostgreSQL, and a count that
silently changed whenever either table was tidied.

**Checked before the work, recorded after it.** A refused message must not cost a
database write, an email send or a file upload; an accepted one must be counted before
the next request can be answered. :meth:`SupportIntakeGuard.check` does the first and
:meth:`SupportIntakeGuard.record` does the second.

**Fail closed on a bad clock or a broken count.** If the ledger cannot be read the
message is refused, because the alternative is an unmetered door.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import SupportIntakeRecord

__all__ = [
    "SUPPORT_INTAKE_DOORS",
    "SupportIntakeDecision",
    "SupportIntakeDoor",
    "SupportIntakeGuard",
    "SupportIntakeLimits",
    "support_intake_limits",
]

#: Which form a message arrived through. Named here so a third door cannot invent a
#: fourth spelling of an existing one.
SupportIntakeDoor = Literal["contact", "dashboard"]
SUPPORT_INTAKE_DOORS: Final[frozenset[str]] = frozenset({"contact", "dashboard"})


@dataclass(frozen=True, slots=True)
class SupportIntakeLimits:
    """How many messages are accepted, and over how long."""

    per_email: int
    per_client: int
    per_hour: int
    window_seconds: int

    @property
    def window(self) -> timedelta:
        return timedelta(seconds=self.window_seconds)

    @property
    def window_hours(self) -> float:
        return self.window_seconds / 3600


def support_intake_limits(settings: Settings) -> SupportIntakeLimits:
    """The configured limits. The only place settings are turned into the rule."""

    return SupportIntakeLimits(
        per_email=settings.support_intake_max_per_email,
        per_client=settings.support_intake_max_per_client,
        per_hour=settings.support_intake_max_per_hour,
        window_seconds=settings.support_intake_window_seconds,
    )


@dataclass(frozen=True, slots=True)
class SupportIntakeDecision:
    """Whether one message is accepted, and what to tell the person if it is not."""

    allowed: bool
    #: Which of the three limits stopped it. ``None`` when the message is accepted.
    reason: str | None
    #: How many this address may still send. Counted from what is already stored, so
    #: the same number is correct whether it is read before a message or after one —
    #: there is no "and one more that is about to happen" hiding in it.
    remaining_for_email: int
    #: How many this browser session may still send, on the same basis.
    remaining_for_client: int
    #: When the oldest counted message leaves the window, so the person can be told
    #: when to come back rather than only that they cannot send now.
    retry_after_seconds: int
    limits: SupportIntakeLimits

    @property
    def code(self) -> str:
        return f"support_intake_{self.reason}" if self.reason else "support_intake_accepted"

    def message(self) -> str:
        """What the person reads. Plain words, and always a next step."""

        minutes = max(1, round(self.retry_after_seconds / 60))
        wait = f"about {minutes} minute{'s' if minutes != 1 else ''}"
        if self.reason == "per_email":
            return (
                f"This email address has already sent {self.limits.per_email} messages. "
                f"You can send another in {wait}. If it is urgent, reply to the email we "
                "sent you instead of starting a new message."
            )
        if self.reason == "per_client":
            return (
                f"You have already sent {self.limits.per_client} messages from this "
                f"device. You can send another in {wait}. If it is urgent, reply to the "
                "email we sent you instead of starting a new message."
            )
        if self.reason == "per_hour":
            return (
                "We are receiving an unusual number of messages right now, so new ones "
                f"are paused for a short time. Please try again in {wait}."
            )
        return "Your message can be sent."


class SupportIntakeGuard:
    """Counts support messages, and decides whether one more is allowed.

    One instance answers for one request. Both doors use it the same way::

        guard = SupportIntakeGuard(session, settings)
        decision = await guard.check(email=..., client_fingerprint=...)
        if not decision.allowed:
            ...refuse, having written nothing...
        ...do the work...
        await guard.record(door="contact", email=..., client_fingerprint=...)
    """

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.limits = support_intake_limits(settings)

    async def check(
        self,
        *,
        email: str,
        client_fingerprint: str,
        now: datetime | None = None,
    ) -> SupportIntakeDecision:
        """Whether one more message is accepted, without writing anything.

        Also the way to read the remaining allowance after a message was accepted: the
        counts come from the ledger, so the answer is simply "what is left now".
        """

        moment = now or datetime.now(UTC)
        since = moment - self.limits.window
        email_key = self.email_hash(email)

        by_email = await self._count(since, email_hash=email_key)
        by_client = await self._count(since, client_hash=client_fingerprint)
        overall = await self._count(since)

        # Checked narrowest first. Telling somebody "we are busy" when the real reason
        # is their own second message would be a true sentence about the wrong thing.
        reason: str | None = None
        if by_email >= self.limits.per_email:
            reason = "per_email"
        elif by_client >= self.limits.per_client:
            reason = "per_client"
        elif overall >= self.limits.per_hour:
            reason = "per_hour"

        return SupportIntakeDecision(
            allowed=reason is None,
            reason=reason,
            remaining_for_email=max(0, self.limits.per_email - by_email),
            remaining_for_client=max(0, self.limits.per_client - by_client),
            retry_after_seconds=await self._retry_after(
                moment,
                reason,
                email_hash=email_key,
                client_hash=client_fingerprint,
            ),
            limits=self.limits,
        )

    async def record(
        self,
        *,
        door: SupportIntakeDoor,
        email: str,
        client_fingerprint: str,
        now: datetime | None = None,
    ) -> None:
        """Count one accepted message. Flushed, so the next request sees it."""

        if door not in SUPPORT_INTAKE_DOORS:
            raise ValueError(f"Unknown support door: {door!r}")
        self.session.add(
            SupportIntakeRecord(
                door=door,
                email_hash=self.email_hash(email),
                client_hash=client_fingerprint,
                accepted_at=now or datetime.now(UTC),
            )
        )
        await self.session.flush()

    def email_hash(self, email: str) -> str:
        """One address, as one opaque value.

        Salted with the application secret so the ledger cannot be turned back into a
        list of addresses, and case-folded first so ``A@x.com`` and ``a@x.com`` are the
        same person — which is the whole point of a per-email limit.
        """

        normalized = email.strip().casefold()
        material = f"{self.settings.app_secret_key.get_secret_value()}:support:{normalized}"
        return hashlib.sha256(material.encode()).hexdigest()

    async def _count(
        self,
        since: datetime,
        *,
        email_hash: str | None = None,
        client_hash: str | None = None,
    ) -> int:
        query = select(func.count()).select_from(SupportIntakeRecord).where(
            SupportIntakeRecord.accepted_at > since
        )
        if email_hash is not None:
            query = query.where(SupportIntakeRecord.email_hash == email_hash)
        if client_hash is not None:
            query = query.where(SupportIntakeRecord.client_hash == client_hash)
        return int(await self.session.scalar(query) or 0)

    async def _retry_after(
        self,
        moment: datetime,
        reason: str | None,
        *,
        email_hash: str,
        client_hash: str,
    ) -> int:
        """How long until the limit that fired has room again.

        The oldest message still inside the window is the one that will leave it first,
        so that is what decides the wait. A person is told a real time rather than a
        fixed guess.
        """

        if reason is None:
            return 0
        query = select(func.min(SupportIntakeRecord.accepted_at)).where(
            SupportIntakeRecord.accepted_at > moment - self.limits.window
        )
        if reason == "per_email":
            query = query.where(SupportIntakeRecord.email_hash == email_hash)
        elif reason == "per_client":
            query = query.where(SupportIntakeRecord.client_hash == client_hash)
        oldest = await self.session.scalar(query)
        if oldest is None:
            return self.limits.window_seconds
        # A row read back from SQLite carries no timezone. Treating it as UTC is
        # correct — that is what was written — and it stops the subtraction below from
        # raising while the compiler is reporting a limit.
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        elapsed = (moment - oldest).total_seconds()
        return max(1, int(self.limits.window_seconds - elapsed))
