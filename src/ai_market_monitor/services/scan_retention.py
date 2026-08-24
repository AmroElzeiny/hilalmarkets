"""Keep the scan tables bounded, so capacity belongs to customers rather than history.

Scan rows are the fastest-growing thing the product writes. Every monitor produces a job
per interval, and every job produces a result per symbol x timeframe x direction. Nothing
deleted any of it, so the tables only ever grew: the disk a new customer needs was being
held by evidence of scans from months ago that nobody can act on.

Two jobs, deliberately separate because they answer different questions:

``expire_abandoned``
    Which rows are *lying* about being pending? A queued job whose dispatch message is
    gone can never run. Nothing re-sends it, and ``ScanScheduler.recover_stale_or_retryable``
    only rescues queued rows that carry a ``next_retry_at``. Production held 167 such rows
    for 57 days, counted as pending work that would never happen.

``purge_expired``
    Which finished rows are old enough to remove? Deletion is expressed on the job alone:
    ``scan_results`` and the monitor evaluation cycles fall with it through their existing
    ``ON DELETE CASCADE``, and incident records and capability-extension rows keep their
    history through ``ON DELETE SET NULL``.

Both are bounded per run. A first run against years of history must be a series of short
transactions, never one long lock on a table the scanner is still writing to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ScanJob
from ai_market_monitor.db.models.enums import ScanJobStatus
from ai_market_monitor.services.monitor_scan_state import (
    CHECK_IN_FLIGHT_STATUSES,
    CHECK_TERMINAL_STATUSES,
)

logger = structlog.get_logger(__name__)

#: A job in one of these states is finished. Nothing will write to it again, so it is
#: eligible for removal once it is old enough.
#:
#: Taken from `services/monitor_scan_state.py` rather than listed here. This file wrote
#: the same two lists out by hand, and one of them — "still claimed by the system" — is
#: the same set the dashboard reads to say a check is running now. Two copies is how the
#: two drift.
TERMINAL_STATUSES: frozenset[ScanJobStatus] = frozenset(CHECK_TERMINAL_STATUSES)

#: A job in one of these states is still claimed by the system. Only age proves otherwise.
PENDING_STATUSES: frozenset[ScanJobStatus] = frozenset(CHECK_IN_FLIGHT_STATUSES)

#: Written onto a job that aged out of pending, so the reason survives in the row itself
#: rather than only in a log line that rotates away.
ABANDONED_ERROR_CODE = "scan_job_abandoned"


class ScanRetentionService:
    """Expire scan jobs that can never run, then delete finished ones that are old."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def run(self, *, now: datetime | None = None) -> dict[str, int]:
        """Both passes, in the only order that works.

        Expiry runs first on purpose. It moves abandoned rows into a terminal state, which
        is what makes them visible to the purge in the same run rather than a day later.
        """

        current_time = now or datetime.now(UTC)
        expired = await self.expire_abandoned(now=current_time)
        purged = await self.purge_expired(now=current_time)
        result = {"expired": expired, **purged}
        if any(result.values()):
            logger.info("scan_retention_completed", **result)
        return result

    async def expire_abandoned(self, *, now: datetime | None = None) -> int:
        """Mark pending jobs too old to ever run as failed, and say why.

        The cutoff is deliberately far above ``scan_job_claim_timeout_seconds``: a job
        being worked on right now must never be caught here. Between the claim timeout and
        this cutoff sits the recovery task, which re-dispatches what can still be saved.
        """

        current_time = now or datetime.now(UTC)
        cutoff = current_time - timedelta(hours=self.settings.scan_job_abandoned_after_hours)
        result = await self.session.execute(
            update(ScanJob)
            .where(
                ScanJob.status.in_(tuple(PENDING_STATUSES)),
                ScanJob.created_at < cutoff,
            )
            .values(
                status=ScanJobStatus.FAILED,
                completed_at=current_time,
                error_code=ABANDONED_ERROR_CODE,
                error_detail=(
                    "Scan job was never dispatched and is older than the abandonment "
                    "window; no worker can still claim it."
                ),
            )
        )
        return _rowcount(result)

    async def purge_expired(self, *, now: datetime | None = None) -> dict[str, int]:
        """Delete finished jobs past the retention window, one bounded batch at a time.

        Results and evaluation cycles are not deleted here by name. They hang off the job
        with ``ON DELETE CASCADE``, so removing the job removes them — and writing a second
        delete against ``scan_results`` would be a parallel rule that could disagree with
        the first one about what "old" means.

        **It keeps going until the backlog is gone.** It used to delete one batch and stop,
        and the batch is deliberately small so that no single transaction holds a long lock
        on a live table. Those two facts together meant this job could only ever remove
        ``scan_history_purge_batch`` rows a night, however many had arrived that day — and
        it ran nightly. It even measured what it had failed to reach, returned it as
        ``remaining``, and nothing read it.

        Today that is invisible: 204 jobs a day against a batch of 5000. It stops being
        invisible the moment the product grows. Fifty monitors watching one-minute candles
        write about 72,000 jobs a day, so the deletion would fall 67,000 rows behind every
        night, for ever, and the first symptom would be a full disk.

        Each batch is still its own short transaction, committed before the next begins, so
        the lock behaviour the batch size was chosen for is unchanged. What changed is that
        finishing the work is no longer optional.
        """

        current_time = now or datetime.now(UTC)
        cutoff = current_time - timedelta(days=self.settings.scan_history_retention_days)
        batch = self.settings.scan_history_purge_batch
        expired_jobs = (
            ScanJob.status.in_(tuple(TERMINAL_STATUSES)),
            ScanJob.created_at < cutoff,
        )

        purged = 0
        for _ in range(self.settings.scan_history_purge_max_batches):
            doomed = (
                select(ScanJob.id)
                .where(*expired_jobs)
                .order_by(ScanJob.created_at)
                .limit(batch)
            )
            ids = list((await self.session.scalars(doomed)).all())
            if not ids:
                break
            await self.session.execute(delete(ScanJob).where(ScanJob.id.in_(ids)))
            # Committed per batch, which is what keeps each lock short. Without it every
            # batch would join one transaction that grows for the whole run, which is the
            # long lock the batching exists to avoid.
            await self.session.commit()
            purged += len(ids)

        remaining = await self.session.scalar(
            select(func.count()).select_from(ScanJob).where(*expired_jobs)
        )
        return {"purged_jobs": purged, "remaining": int(remaining or 0)}


def _rowcount(result: Any) -> int:
    """A portable affected-row count for SQLAlchemy DML results."""

    return int(getattr(result, "rowcount", 0) or 0)
