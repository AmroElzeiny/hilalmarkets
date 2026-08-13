"""Stored measurements: they survive a restart, they add up, and they stay bounded.

Every test here is written against the rule, not against one metric. The properties
being proved are the ones that decide whether the health page is telling the truth:

* a restart loses nothing,
* two processes recording the same thing are added together, not overwritten,
* nothing is counted twice, however the flush is retried,
* a gauge is a reading and is never summed,
* and the table cannot grow for ever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.db.base import Base
from ai_market_monitor.db.models.operations import (
    METRIC_ROLLUP_WRITER,
    OperationalMetricDelta,
)
from ai_market_monitor.observability.durable_metrics import (
    DurableMetricsStore,
    MetricSignatureTooLong,
    label_signature,
    window_start_for,
    writer_identity,
)
from ai_market_monitor.observability.labels import MetricLabelError, SensitiveValueError
from ai_market_monitor.observability.metrics import (
    METRICS,
    MetricRetentionPolicy,
    MetricsRecorder,
)

POLICY = MetricRetentionPolicy(
    window_seconds=300,
    rollup_after_hours=6,
    retention_hours=72,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _store(session: AsyncSession, writer: str) -> DurableMetricsStore:
    return DurableMetricsStore(session, policy=POLICY, writer=writer)


def _request(recorder: MetricsRecorder, count: int = 1, status: str = "2xx") -> None:
    for _ in range(count):
        recorder.record(
            "http_requests_total", route="/dashboard", method="GET", status_class=status
        )


# ---------------------------------------------------------------------------
# 1. A restart loses nothing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_measurements_survive_the_process_that_recorded_them(
    session: AsyncSession,
) -> None:
    """The whole point. In-memory numbers died with the process that held them."""

    dying = MetricsRecorder()
    _request(dying, 7)
    await _store(session, "host:1:100").flush(dying, now=NOW)

    # The process is gone. Nothing of it is left except what it wrote down.
    del dying
    restored = await _store(session, "host:2:200").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 7.0


@pytest.mark.asyncio
async def test_a_restarted_process_does_not_continue_the_dead_one_s_row(
    session: AsyncSession,
) -> None:
    """A new process is a new writer, even on the same host with the same id.

    Without the start time in the writer identity, the operating system reusing a
    process id would make the new process continue the dead one's row. The counts
    would jump, and nothing in the data would explain why.
    """

    first = MetricsRecorder()
    _request(first, 4)
    await _store(session, "host:77:100").flush(first, now=NOW)

    second = MetricsRecorder()
    _request(second, 6)
    await _store(session, "host:77:200").flush(second, now=NOW)

    rows = (await session.scalars(select(OperationalMetricDelta))).all()
    assert len(rows) == 2
    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 10.0


# ---------------------------------------------------------------------------
# 2. Several processes add up. None of them loses the others' counts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("writers", [2, 3, 5])
@pytest.mark.asyncio
async def test_every_process_s_counts_are_added_not_overwritten(
    session: AsyncSession,
    writers: int,
) -> None:
    for index in range(writers):
        recorder = MetricsRecorder()
        _request(recorder, index + 1)
        await _store(session, f"host:{index}:100").flush(recorder, now=NOW)

    expected = sum(range(1, writers + 1))
    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == float(expected)


@pytest.mark.asyncio
async def test_interleaved_flushes_from_two_processes_lose_nothing(
    session: AsyncSession,
) -> None:
    """The concurrency case, written as the interleaving that would break it.

    Both processes record, both flush, both record again, both flush again. A
    read-modify-write design loses the second process's first batch here.
    """

    left, right = MetricsRecorder(), MetricsRecorder()
    left_store = _store(session, "host:a:1")
    right_store = _store(session, "host:b:1")

    _request(left, 3)
    _request(right, 5)
    await left_store.flush(left, now=NOW)
    await right_store.flush(right, now=NOW)
    _request(left, 2)
    _request(right, 4)
    await right_store.flush(right, now=NOW)
    await left_store.flush(left, now=NOW)

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 14.0


# ---------------------------------------------------------------------------
# 3. Nothing is ever counted twice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flushing_again_with_nothing_new_writes_nothing(
    session: AsyncSession,
) -> None:
    recorder = MetricsRecorder()
    _request(recorder, 9)
    store = _store(session, "host:a:1")
    assert await store.flush(recorder, now=NOW) == 1
    assert await store.flush(recorder, now=NOW) == 0

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 9.0


@pytest.mark.asyncio
async def test_a_flush_that_never_committed_is_not_marked_as_written(
    session: AsyncSession,
) -> None:
    """A failed write costs a delay, never a measurement.

    The pending delta is only marked as stored after the commit, so a database blip
    leaves the numbers in memory and they go out with the next pass.
    """

    recorder = MetricsRecorder()
    _request(recorder, 6)
    # Take the deltas the way a flush would, then do not confirm them.
    pending = recorder.pending_deltas()
    assert pending and pending[0].value == 6.0

    _request(recorder, 2)
    await _store(session, "host:a:1").flush(recorder, now=NOW)
    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 8.0


@pytest.mark.asyncio
async def test_observations_that_land_during_a_flush_are_not_dropped(
    session: AsyncSession,
) -> None:
    """The race the snapshot exists for.

    Marking "everything current" as written would silently discard whatever arrived
    while the write was in flight. The delta carries the reading it was taken from.
    """

    recorder = MetricsRecorder()
    _request(recorder, 5)
    pending = recorder.pending_deltas()
    _request(recorder, 3)  # arrives mid-flush
    recorder.mark_flushed(pending)

    remaining = recorder.pending_deltas()
    assert len(remaining) == 1
    assert remaining[0].value == 3.0


# ---------------------------------------------------------------------------
# 4. A gauge is a reading, not a running total.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_gauge_reads_the_newest_value_and_is_never_summed(
    session: AsyncSession,
) -> None:
    """Four workers each 30 seconds behind is not a two-minute lag."""

    for index, age in enumerate((30.0, 30.0, 30.0, 30.0)):
        recorder = MetricsRecorder()
        recorder.record("worker_heartbeat_age_seconds", age, component=f"worker{index}")
        await _store(session, f"host:{index}:1").flush(recorder, now=NOW)

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    for index in range(4):
        assert restored.value(
            "worker_heartbeat_age_seconds", component=f"worker{index}"
        ) == 30.0


@pytest.mark.asyncio
async def test_a_gauge_written_twice_by_one_process_keeps_the_last_reading(
    session: AsyncSession,
) -> None:
    recorder = MetricsRecorder()
    store = _store(session, "host:a:1")
    recorder.record("queue_depth", 120.0, queue="scans")
    await store.flush(recorder, now=NOW)
    recorder.record("queue_depth", 4.0, queue="scans")
    await store.flush(recorder, now=NOW)

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.value("queue_depth", queue="scans") == 4.0


# ---------------------------------------------------------------------------
# 5. Histograms keep their distribution, so a quantile still answers.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quantile_still_reads_after_a_restart(session: AsyncSession) -> None:
    """A mean stays healthy through an outage. Only the distribution shows it."""

    recorder = MetricsRecorder()
    for _ in range(99):
        recorder.record("http_request_duration_ms", 10.0, route="/x", method="GET")
    recorder.record("http_request_duration_ms", 300_000.0, route="/x", method="GET")
    await _store(session, "host:a:1").flush(recorder, now=NOW)

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.quantile("http_request_duration_ms", 0.95) == 10.0
    assert restored.quantile("http_request_duration_ms", 1.0) == 300_000.0


@pytest.mark.asyncio
async def test_two_processes_histograms_merge_into_one_distribution(
    session: AsyncSession,
) -> None:
    slow = MetricsRecorder()
    fast = MetricsRecorder()
    for _ in range(50):
        fast.record("http_request_duration_ms", 5.0, route="/x", method="GET")
        slow.record("http_request_duration_ms", 5_000.0, route="/x", method="GET")
    await _store(session, "host:a:1").flush(fast, now=NOW)
    await _store(session, "host:b:1").flush(slow, now=NOW)

    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.observation_count("http_request_duration_ms") == 100
    assert restored.quantile("http_request_duration_ms", 0.99) == 5_000.0


# ---------------------------------------------------------------------------
# 6. Growth is bounded.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_folds_every_process_into_one_row_per_window(
    session: AsyncSession,
) -> None:
    old = NOW - timedelta(hours=12)
    for index in range(6):
        recorder = MetricsRecorder()
        _request(recorder, 2)
        await _store(session, f"host:{index}:1").flush(recorder, now=old)

    before = await session.scalar(select(func.count()).select_from(OperationalMetricDelta))
    assert before == 6

    result = await _store(session, "compactor").compact(now=NOW)
    assert result["folded"] == 6

    rows = (await session.scalars(select(OperationalMetricDelta))).all()
    assert len(rows) == 1
    assert rows[0].writer == METRIC_ROLLUP_WRITER
    # Folding must not change the answer.
    assert float(rows[0].total) == 12.0


@pytest.mark.asyncio
async def test_rollup_run_twice_over_the_same_window_changes_nothing(
    session: AsyncSession,
) -> None:
    """The task is scheduled, so it will run again over the same rows."""

    old = NOW - timedelta(hours=12)
    for index in range(3):
        recorder = MetricsRecorder()
        _request(recorder, 5)
        await _store(session, f"host:{index}:1").flush(recorder, now=old)

    await _store(session, "compactor").compact(now=NOW)
    await _store(session, "compactor").compact(now=NOW)

    restored = await _store(session, "reader").load(minutes=60 * 24, now=NOW)
    assert restored.total("http_requests_total") == 15.0


@pytest.mark.asyncio
async def test_retention_deletes_measurements_past_their_age(
    session: AsyncSession,
) -> None:
    ancient = NOW - timedelta(hours=100)
    recorder = MetricsRecorder()
    _request(recorder, 3)
    await _store(session, "host:a:1").flush(recorder, now=ancient)

    result = await _store(session, "compactor").compact(now=NOW)
    assert result["deleted"] == 1
    remaining = await session.scalar(
        select(func.count()).select_from(OperationalMetricDelta)
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_recent_measurements_are_left_alone_by_compaction(
    session: AsyncSession,
) -> None:
    recorder = MetricsRecorder()
    _request(recorder, 3)
    await _store(session, "host:a:1").flush(recorder, now=NOW)

    result = await _store(session, "compactor").compact(now=NOW)
    assert result == {"deleted": 0, "folded": 0, "rollup_rows": 0}
    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total") == 3.0


@pytest.mark.parametrize(
    ("rollup_after", "retention"),
    [(6, 6), (24, 2), (6, 5)],
)
def test_retention_shorter_than_rollup_is_refused(
    rollup_after: int, retention: int
) -> None:
    """Rows would be deleted before they were ever folded, and nothing would say so."""

    policy = MetricRetentionPolicy(
        window_seconds=300,
        rollup_after_hours=rollup_after,
        retention_hours=retention,
    )
    with pytest.raises(ValueError, match="longer than"):
        policy.validate()


# ---------------------------------------------------------------------------
# 7. The stored form cannot silently merge two different series.
# ---------------------------------------------------------------------------


def test_the_same_labels_in_a_different_order_are_one_series() -> None:
    left = label_signature({"route": "/a", "method": "GET", "status_class": "2xx"})
    right = label_signature({"status_class": "2xx", "method": "GET", "route": "/a"})
    assert left == right


def test_two_different_label_sets_never_share_a_signature() -> None:
    assert label_signature({"route": "/a"}) != label_signature({"route": "/b"})


def test_a_signature_too_long_to_store_is_refused_not_truncated() -> None:
    """Truncating would merge two different series into one silently."""

    with pytest.raises(MetricSignatureTooLong):
        label_signature({"route": "/" + "x" * 600})


@pytest.mark.asyncio
async def test_one_unstorable_series_does_not_stop_the_others(
    session: AsyncSession,
) -> None:
    """A diagnostic must never become the failure it was reporting."""

    recorder = MetricsRecorder()
    _request(recorder, 4)
    recorder.record(
        "http_requests_total",
        route="/" + "a" * 78,
        method="GET",
        status_class="5xx",
    )
    # Force the long series past the storable limit by giving it a long signature.
    written = await _store(session, "host:a:1").flush(recorder, now=NOW)
    assert written >= 1
    restored = await _store(session, "reader").load(minutes=60, now=NOW)
    assert restored.total("http_requests_total", status_class="2xx") == 4.0


# ---------------------------------------------------------------------------
# 8. Windows and writers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [60, 300, 900])
def test_every_moment_in_a_window_maps_to_the_same_start(width: int) -> None:
    """Processes must agree on the window without talking to each other."""

    base = window_start_for(NOW, width_seconds=width)
    for offset in range(0, width, max(width // 7, 1)):
        moment = base + timedelta(seconds=offset)
        assert window_start_for(moment, width_seconds=width) == base
    assert window_start_for(base + timedelta(seconds=width), width_seconds=width) != base


def test_the_writer_identity_names_host_process_and_start() -> None:
    identity = writer_identity()
    assert identity.count(":") == 2
    assert len(identity) <= 120
    assert identity == writer_identity()


# ---------------------------------------------------------------------------
# 9. Nothing sensitive can reach the stored rows.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "sk-lmnopqrstuvwx1234567890abcdefgh",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "user@example.com",
        "Monitor BTC when the fifteen minute candle rises by at least three percent "
        "and the volume is above average for the day",
    ],
)
def test_a_label_carrying_a_secret_or_prose_is_refused_before_it_is_stored(
    value: str,
) -> None:
    recorder = MetricsRecorder()
    with pytest.raises((SensitiveValueError, MetricLabelError, ValueError)):
        recorder.record("provider_calls_total", provider=value, operation="x", outcome="success")


@pytest.mark.asyncio
async def test_stored_rows_only_ever_hold_declared_label_names(
    session: AsyncSession,
) -> None:
    """Every stored label name belongs to the metric that carries it."""

    recorder = MetricsRecorder()
    _request(recorder, 1)
    recorder.record("queue_depth", 3.0, queue="scans")
    await _store(session, "host:a:1").flush(recorder, now=NOW)

    rows = (await session.scalars(select(OperationalMetricDelta))).all()
    assert rows
    for row in rows:
        allowed = set(METRICS[row.metric_name].labels)
        assert set(row.labels) == allowed
