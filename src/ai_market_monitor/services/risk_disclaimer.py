"""Whether somebody has accepted the risk note, and recording it when they do.

One owner, because the answer is a legal record and three places were writing it: the
onboarding flow, the Telegram bot, and now the canvas. A monitor cannot start without
it — ``StrategyService.activate`` refuses with ``disclaimer_required`` — so a path that
could not record one could never start a monitor at all. That is exactly what the
dashboard was: somebody who signed up on the website was never asked, and every monitor
they built was refused at the last step with a word from inside the machine.

Two rules this module exists to keep:

* **it is never recorded on somebody's behalf.** A caller passes ``source`` naming the
  thing the person actually did. Nothing here writes an acceptance because a request
  happened to arrive;
* **it is recorded once per version.** A new version of the note is a new question, and
  an old answer does not carry over to it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import DisclaimerAcceptance, UserIdentity


class DisclaimerIdentityMissing(RuntimeError):
    """The account has no identity to attach the acceptance to. Fail closed."""


async def has_accepted(session: AsyncSession, *, user_id: UUID, version: str) -> bool:
    """Has this person accepted **this** version of the risk note?"""

    found = await session.scalar(
        select(DisclaimerAcceptance.id).where(
            DisclaimerAcceptance.user_id == user_id,
            DisclaimerAcceptance.disclaimer_version == version,
        )
    )
    return found is not None


async def record_acceptance(
    session: AsyncSession,
    *,
    user_id: UUID,
    version: str,
    source: str,
    identity_id: UUID | None = None,
) -> None:
    """Write down that this person accepted this version, once.

    ``identity_id`` is for callers that have already checked which identity is doing the
    accepting. Where it is not given the account's primary identity is used, which is
    the one every other record on the account is attached to.
    """

    if await has_accepted(session, user_id=user_id, version=version):
        return
    resolved = identity_id
    if resolved is None:
        resolved = await session.scalar(
            select(UserIdentity.id)
            .where(UserIdentity.user_id == user_id)
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        )
    if resolved is None:
        raise DisclaimerIdentityMissing("Account identity was not found.")
    session.add(
        DisclaimerAcceptance(
            user_id=user_id,
            identity_id=resolved,
            disclaimer_version=version,
            acceptance_source=source,
            accepted_at=datetime.now(UTC),
        )
    )
    await session.flush()
