"""Make the measurements survive a restart, and add up across processes.

The recorder in :mod:`ai_market_monitor.observability.metrics` is fast because it is
in memory. That is also its whole weakness: the API, the workers and the scheduler
each held their own private numbers, every restart threw them away, and an objective
read from one process was an objective about one process. A dashboard built on that
answers "is the product healthy" with "the last few minutes of whichever web process
happened to serve this page".

This module is the durable half. It does three things and nothing else.

**Write.** Each process periodically writes down only what *it* added since its own
last write, into a row keyed by its own writer identity. Nothing is ever read,
modified and written back, so two processes flushing at the same instant cannot
overwrite each other, and a retried write cannot count twice — the delta is only
marked as written after the transaction commits, and it is marked against the exact
reading it was taken from.

**Read.** :func:`load_recorder` rebuilds a recorder from the stored rows for a time
window, so the objectives, the alert rules and the customer banners read the whole
deployment through the same code they already used for one process.

**Bound.** Rows do not accumulate for ever. Old windows are folded into one row per
series by :meth:`DurableMetricsStore.compact`, and older ones are deleted. Both
limits are configured, and both run from a scheduled task next to the other
housekeeping tasks.

Nothing here writes product state. It reads and writes its own table only.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models.operations import (
    METRIC_ROLLUP_WRITER,
    OperationalMetricDelta,
)
from ai_market_monitor.observability.metrics import (
    HISTOGRAM_BUCKETS,
    METRICS,
    MetricDelta,
    MetricRetentionPolicy,
    MetricsRecorder,
    get_metrics_recorder,
)

__all__ = [
    "DurableMetricsStore",
    "MetricRetentionPolicy",
    "flush_metrics_once",
    "label_signature",
    "load_recorder",
    "window_start_for",
    "writer_identity",
]

#: Length of the ``label_signature`` column. A signature longer than this cannot be
#: stored, and silently truncating it would merge two different series into one.
_MAX_SIGNATURE_LENGTH: Final[int] = 500

_BUCKET_SLOTS: Final[int] = len(HISTOGRAM_BUCKETS) + 1


class MetricSignatureTooLong(ValueError):
    """A label set whose stored form would not fit, and so could not be told apart."""


def label_signature(labels: Mapping[str, str]) -> str:
    """The comparable form of one label set: sorted ``key=value`` pairs.

    Sorted so that the same labels supplied in a different order produce the same
    signature. Without that, one series would quietly become two, and every ratio
    built on it would read half its own traffic.
    """

    signature = "|".join(f"{key}={value}" for key, value in sorted(labels.items()))
    if len(signature) > _MAX_SIGNATURE_LENGTH:
        raise MetricSignatureTooLong(
            f"Label signature is {len(signature)} characters, over the "
            f"{_MAX_SIGNATURE_LENGTH} the store can tell apart: {signature[:80]}…"
        )
    return signature


def window_start_for(moment: datetime, *, width_seconds: int) -> datetime:
    """The start of the fixed window ``moment`` falls in.

    Fixed windows on an absolute clock, not "the last N seconds from now". Every
    process must agree on which window a measurement belongs to without talking to
    the others, and only a clock-aligned boundary gives that.
    """

    if width_seconds <= 0:
        raise ValueError("A metric window must be at least one second wide.")
    aligned = int(moment.timestamp()) // width_seconds * width_seconds
    return datetime.fromtimestamp(aligned, tz=UTC)


_PROCESS_STARTED: Final[int] = int(time.time())


def writer_identity() -> str:
    """Who is writing: host, process id, and when this process started.

    The start time matters. A restarted process on the same host can be handed the
    same process id, and without it the new process would continue the dead one's row
    — turning a restart into a jump in the numbers that nothing could explain.
    """

    host = socket.gethostname()[:60]
    return f"{host}:{os.getpid()}:{_PROCESS_STARTED}"[:120]


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(float(value), 6)))


class DurableMetricsStore:
    """Reads and writes :class:`OperationalMetricDelta` rows. Nothing else."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: MetricRetentionPolicy,
        writer: str | None = None,
    ) -> None:
        self.session = session
        self.policy = policy
        self.writer = writer or writer_identity()

    async def flush(
        self,
        recorder: MetricsRecorder | None = None,
        *,
        now: datetime | None = None,
    ) -> int:
        """Write this process's outstanding movement. Returns the number of series.

        Marks the deltas as written only after the commit succeeds. If the database
        is unreachable the numbers stay in memory and go out with the next pass, so a
        blip costs a delayed reading rather than a missing one.
        """

        recorder = recorder if recorder is not None else get_metrics_recorder()
        deltas = recorder.pending_deltas()
        if not deltas:
            return 0
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        window = window_start_for(moment, width_seconds=self.policy.window_seconds)
        written: list[MetricDelta] = []
        for delta in deltas:
            try:
                signature = label_signature(delta.label_map)
            except MetricSignatureTooLong:
                # Refusing one unstorable series must never stop the rest from being
                # written. A diagnostic that takes the other diagnostics down with it
                # is worse than the gap it was reporting.
                continue
            await self._apply(delta, signature=signature, window=window, moment=moment)
            written.append(delta)
        await self.session.commit()
        recorder.mark_flushed(tuple(written))
        return len(written)

    async def _apply(
        self,
        delta: MetricDelta,
        *,
        signature: str,
        window: datetime,
        moment: datetime,
    ) -> None:
        row = await self.session.scalar(
            select(OperationalMetricDelta).where(
                OperationalMetricDelta.metric_name == delta.name,
                OperationalMetricDelta.label_signature == signature,
                OperationalMetricDelta.window_start == window,
                OperationalMetricDelta.writer == self.writer,
            )
        )
        # No upsert and no locking on purpose: the unique key contains ``writer``, so
        # this row belongs to this process alone. There is no other writer to race.
        if row is None:
            self.session.add(
                OperationalMetricDelta(
                    metric_name=delta.name,
                    kind=delta.kind,
                    label_signature=signature,
                    labels=delta.label_map,
                    window_start=window,
                    writer=self.writer,
                    total=_decimal(delta.value),
                    observations=max(delta.observations, 0),
                    buckets=list(delta.buckets) if delta.buckets is not None else None,
                    updated_at=moment,
                )
            )
            return
        if delta.kind == "gauge":
            row.total = _decimal(delta.value)
        else:
            row.total = _decimal(float(row.total) + delta.value)
            if delta.buckets is not None:
                existing = list(row.buckets or [0] * _BUCKET_SLOTS)
                row.buckets = [
                    existing[index] + count for index, count in enumerate(delta.buckets)
                ]
        row.observations += max(delta.observations, 0)
        row.updated_at = moment

    async def load(
        self,
        *,
        minutes: int,
        now: datetime | None = None,
    ) -> MetricsRecorder:
        """Rebuild a recorder from every process's stored rows for the last ``minutes``."""

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        since = moment - timedelta(minutes=minutes)
        rows = list(
            (
                await self.session.scalars(
                    select(OperationalMetricDelta)
                    .where(OperationalMetricDelta.window_start >= since)
                    .order_by(OperationalMetricDelta.updated_at.asc())
                )
            ).all()
        )
        return _recorder_from_rows(rows)

    async def compact(self, *, now: datetime | None = None) -> dict[str, int]:
        """Fold old per-process rows into one, then delete rows past retention.

        Growth is bounded by this and by nothing else. Without it the table grows with
        every process, every window, for ever — and the first symptom would be the
        health page timing out during the incident it exists to explain.
        """

        moment = (now or datetime.now(UTC)).astimezone(UTC)
        rollup_before = moment - timedelta(hours=self.policy.rollup_after_hours)
        delete_before = moment - timedelta(hours=self.policy.retention_hours)

        deleted = await self.session.execute(
            delete(OperationalMetricDelta).where(
                OperationalMetricDelta.window_start < delete_before
            )
        )
        rows = list(
            (
                await self.session.scalars(
                    select(OperationalMetricDelta).where(
                        OperationalMetricDelta.window_start >= delete_before,
                        OperationalMetricDelta.window_start < rollup_before,
                        OperationalMetricDelta.writer != METRIC_ROLLUP_WRITER,
                    )
                )
            ).all()
        )
        folded = 0
        grouped: dict[tuple[str, str, datetime], list[OperationalMetricDelta]] = {}
        for row in rows:
            key = (row.metric_name, row.label_signature, row.window_start)
            grouped.setdefault(key, []).append(row)
        for (metric_name, signature, window), group in grouped.items():
            merged = _merge_rows(group)
            target = OperationalMetricDelta(
                metric_name=metric_name,
                kind=group[0].kind,
                label_signature=signature,
                labels=dict(group[0].labels or {}),
                window_start=window,
                writer=METRIC_ROLLUP_WRITER,
                total=merged["total"],
                observations=merged["observations"],
                buckets=merged["buckets"],
                updated_at=merged["updated_at"],
            )
            existing = await self.session.scalar(
                select(OperationalMetricDelta).where(
                    OperationalMetricDelta.metric_name == metric_name,
                    OperationalMetricDelta.label_signature == signature,
                    OperationalMetricDelta.window_start == window,
                    OperationalMetricDelta.writer == METRIC_ROLLUP_WRITER,
                )
            )
            if existing is not None:
                # A rollup that ran before is folded in as well, so running the task
                # twice over the same window neither loses nor doubles anything.
                if group[0].kind == "gauge":
                    if merged["updated_at"] >= existing.updated_at:
                        existing.total = merged["total"]
                        existing.updated_at = merged["updated_at"]
                else:
                    existing.total = _decimal(
                        float(existing.total) + float(merged["total"])
                    )
                    if merged["buckets"] is not None:
                        base = list(existing.buckets or [0] * _BUCKET_SLOTS)
                        existing.buckets = [
                            base[index] + count
                            for index, count in enumerate(merged["buckets"])
                        ]
                    existing.updated_at = max(existing.updated_at, merged["updated_at"])
                existing.observations += merged["observations"]
            else:
                self.session.add(target)
            for row in group:
                await self.session.delete(row)
            folded += len(group)
        await self.session.commit()
        return {
            "deleted": int(getattr(deleted, "rowcount", 0) or 0),
            "folded": folded,
            "rollup_rows": len(grouped),
        }


def _merge_rows(rows: Sequence[OperationalMetricDelta]) -> dict:
    kind = rows[0].kind
    buckets: list[int] | None = None
    observations = 0
    updated_at = rows[0].updated_at
    if kind == "gauge":
        newest = max(rows, key=lambda row: row.updated_at)
        total = _decimal(float(newest.total))
        updated_at = newest.updated_at
        observations = sum(row.observations for row in rows)
    else:
        total = _decimal(sum(float(row.total) for row in rows))
        observations = sum(row.observations for row in rows)
        updated_at = max(row.updated_at for row in rows)
        if any(row.buckets for row in rows):
            buckets = [0] * _BUCKET_SLOTS
            for row in rows:
                for index, count in enumerate(row.buckets or []):
                    if index < _BUCKET_SLOTS:
                        buckets[index] += int(count)
    return {
        "total": total,
        "observations": observations,
        "buckets": buckets,
        "updated_at": updated_at,
    }


def _recorder_from_rows(rows: Sequence[OperationalMetricDelta]) -> MetricsRecorder:
    recorder = MetricsRecorder()
    # Gauges are a reading, not a running total: the answer is the newest one, not the
    # sum of every process's. Rows arrive oldest first, so the last one seen wins.
    for row in rows:
        spec = METRICS.get(row.metric_name)
        if spec is None:
            # A metric that no longer exists in the registry. Its history stays in the
            # table until retention removes it, but nothing can read it as anything.
            continue
        labels = {str(key): str(value) for key, value in (row.labels or {}).items()}
        if spec.kind == "gauge":
            recorder.merge_stored(
                row.metric_name,
                labels,
                value=float(row.total),
                observations=max(row.observations, 0),
            )
            continue
        recorder.merge_stored(
            row.metric_name,
            labels,
            value=float(row.total),
            observations=max(row.observations, 0),
            buckets=tuple(int(count) for count in (row.buckets or [])) or None,
        )
    return recorder


async def load_recorder(
    session: AsyncSession,
    *,
    policy: MetricRetentionPolicy,
    minutes: int,
    now: datetime | None = None,
) -> MetricsRecorder:
    """Every process's stored measurements for the last ``minutes``, as one recorder."""

    return await DurableMetricsStore(session, policy=policy).load(minutes=minutes, now=now)


#: Guards the flush inside one process. Two overlapping flushes would each compute a
#: delta against the same last-written mark and write the same movement twice.
_FLUSH_LOCK: Final[asyncio.Lock] = asyncio.Lock()


async def flush_metrics_once(
    session: AsyncSession,
    *,
    policy: MetricRetentionPolicy,
    recorder: MetricsRecorder | None = None,
    now: datetime | None = None,
) -> int:
    """Write this process's outstanding measurements down, once."""

    async with _FLUSH_LOCK:
        return await DurableMetricsStore(session, policy=policy).flush(recorder, now=now)
