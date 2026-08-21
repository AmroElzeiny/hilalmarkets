"""What a monitor's scanning has actually done, answered in one place.

Two different questions get asked of the same table, and they have two different
answers:

* **"When did this monitor last look at the market?"** — the newest job that *finished
  a check*.
* **"Is it looking right now?"** — a job that is queued or running.

Reading the newest row and asking it both questions gets the first one wrong nearly
always. A live monitor is scheduled again every interval, so the newest row is almost
always the one that has not finished yet, and its ``completed_at`` is empty. Every page
that read that row said "Not looked yet" about a monitor that had already completed
dozens of checks, and Home said the market had never been looked at at all.

The second half of the trap is the opposite mistake. ``completed_at`` is also stamped on
a job that **failed** and on one that was **canceled** before it read anything, so
"is ``completed_at`` set?" answers yes for a job that produced no reading at all. A
finished *row* is not a finished *check*.

So the terminal states are named here once, and the two questions are answered by two
separate fields on one object. Nothing outside this module may order ``ScanJob`` rows to
work out when a monitor last looked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import ScanJob
from ai_market_monitor.db.models.enums import ScanJobStatus

#: The terminal states in which a job really read the market.
#:
#: ``PARTIAL`` counts: some markets were read, so the monitor did look. ``FAILED`` and
#: ``CANCELED`` do not — both stamp ``completed_at`` on the way out, and neither one is
#: a check a person can be told about.
CHECK_FINISHED_STATUSES: Final[tuple[ScanJobStatus, ...]] = (
    ScanJobStatus.SUCCEEDED,
    ScanJobStatus.PARTIAL,
)

#: The states in which a check has been made but has not finished.
CHECK_IN_FLIGHT_STATUSES: Final[tuple[ScanJobStatus, ...]] = (
    ScanJobStatus.QUEUED,
    ScanJobStatus.RUNNING,
)

#: The states in which nothing will ever write to the row again.
#:
#: A *different* question from `CHECK_FINISHED_STATUSES`, and the difference is the whole
#: bug: a failed or canceled job is finished as a **row** and is not a finished
#: **check**. Retention needs the first meaning — an untouchable row can be deleted once
#: it is old — and every screen needs the second. Both live here so the two can never be
#: confused for one another again.
CHECK_TERMINAL_STATUSES: Final[tuple[ScanJobStatus, ...]] = (
    *CHECK_FINISHED_STATUSES,
    ScanJobStatus.FAILED,
    ScanJobStatus.CANCELED,
)


@dataclass(frozen=True)
class MonitorScanState:
    """Everything a screen may honestly say about one monitor's market checks."""

    #: The newest check that finished and produced readings, or ``None``.
    last_completed: ScanJob | None = None
    #: A check that is queued or running for this monitor right now, or ``None``.
    in_flight: ScanJob | None = None
    #: The newest job row of any state. For "what is it doing", never for "has it
    #: looked" — that is what the two fields above are for.
    latest: ScanJob | None = None

    @property
    def last_checked_at(self) -> datetime | None:
        """The moment this monitor last finished looking at the market."""

        return self.last_completed.completed_at if self.last_completed is not None else None

    @property
    def has_ever_checked(self) -> bool:
        """Whether a check has ever finished for this monitor."""

        return self.last_completed is not None

    @property
    def is_checking_now(self) -> bool:
        """Whether a check is queued or running for this monitor at this moment."""

        return self.in_flight is not None


#: A monitor with no approved version has no checks and cannot have any.
NO_SCANS: Final[MonitorScanState] = MonitorScanState()


async def scan_state_for_version(
    session: AsyncSession,
    strategy_version_id: UUID | None,
) -> MonitorScanState:
    """Read the three facts about one version's market checks.

    Three small queries against ``ix_scan_job_version_created`` rather than one row that
    every caller then has to interpret. The interpretation is where this went wrong.
    """

    if strategy_version_id is None:
        return NO_SCANS

    last_completed = await session.scalar(
        select(ScanJob)
        .where(
            ScanJob.strategy_version_id == strategy_version_id,
            ScanJob.status.in_(CHECK_FINISHED_STATUSES),
            ScanJob.completed_at.is_not(None),
        )
        .order_by(ScanJob.completed_at.desc())
        .limit(1)
    )
    in_flight = await session.scalar(
        select(ScanJob)
        .where(
            ScanJob.strategy_version_id == strategy_version_id,
            ScanJob.status.in_(CHECK_IN_FLIGHT_STATUSES),
        )
        .order_by(ScanJob.created_at.desc())
        .limit(1)
    )
    latest = await session.scalar(
        select(ScanJob)
        .where(ScanJob.strategy_version_id == strategy_version_id)
        .order_by(ScanJob.created_at.desc())
        .limit(1)
    )
    return MonitorScanState(
        last_completed=last_completed,
        in_flight=in_flight,
        latest=latest,
    )
