"""Scan history must stay bounded, and must only ever lose rows that are safe to lose.

Production held 167 queued scan jobs for 57 days. Nothing could run them — their dispatch
messages were long gone, and ``ScanScheduler.recover_stale_or_retryable`` only rescues
queued rows that carry a retry time — and nothing could remove them either, because no
retention pass existed at all. Meanwhile every finished job and all of its results stayed
forever, so the disk a new customer needs was held by evidence of scans nobody can act on.

These tests assert the two rules rather than that one case. They sweep *every* member of
``ScanJobStatus``, so a status added later is classified by this file rather than falling
silently into "never cleaned" or, far worse, "deleted while a worker is still writing".

The database here enforces foreign keys. SQLite does not do that by default, and without
it the cascade test would pass while proving nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.base import Base
from ai_market_monitor.db.models import ScanJob, ScanResult, Strategy, StrategyVersion, User
from ai_market_monitor.db.models.enums import ScanJobStatus, ScanOutcome, StrategyVersionStatus
from ai_market_monitor.services.scan_retention import (
    ABANDONED_ERROR_CODE,
    PENDING_STATUSES,
    TERMINAL_STATUSES,
    ScanRetentionService,
)

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@dataclass
class Env:
    session: AsyncSession
    settings: Settings
    version_id: UUID

    def add_job(
        self,
        *,
        status: ScanJobStatus,
        created_at: datetime,
        key: str | None = None,
    ) -> ScanJob:
        job = ScanJob(
            strategy_version_id=self.version_id,
            idempotency_key=key or f"job-{uuid4()}",
            status=status,
            scheduled_for=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        self.session.add(job)
        return job

    def add_result(self, job: ScanJob) -> ScanResult:
        row = ScanResult(
            scan_job_id=job.id,
            strategy_version_id=job.strategy_version_id,
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            direction="long",
            outcome=ScanOutcome.CONFIRMED,
            completion_score=Decimal("100.000"),
            candle_closed_at=job.scheduled_for,
            evaluated_at=job.scheduled_for,
            data_freshness_ms=10,
            is_candle_complete=True,
        )
        self.session.add(row)
        return row

    async def count(self, model: Any) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(model)) or 0)


@pytest_asyncio.fixture
async def env() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
    )

    async with factory() as session:
        user = User()
        session.add(user)
        await session.flush()
        strategy = Strategy(user_id=user.id, name="Retention monitor")
        session.add(strategy)
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="template",
            source_text="retention test",
            schema_json={},
            schema_hash="retention-test",
        )
        session.add(version)
        await session.flush()
        yield Env(session=session, settings=settings, version_id=version.id)

    await engine.dispose()


def test_every_status_is_classified_exactly_once() -> None:
    """No status may be forgotten, and none may be in both groups.

    A status in neither set is a row nothing will ever clean up. A status in both would
    let the purge delete a job while a worker is still writing to it.
    """

    covered = TERMINAL_STATUSES | PENDING_STATUSES
    missing = set(ScanJobStatus) - covered
    assert not missing, f"unclassified scan statuses: {missing}"
    assert not (TERMINAL_STATUSES & PENDING_STATUSES)


@pytest.mark.parametrize("status", sorted(PENDING_STATUSES))
async def test_old_pending_jobs_are_expired_with_a_stated_reason(
    env: Env, status: ScanJobStatus
) -> None:
    old = env.add_job(status=status, created_at=NOW - timedelta(days=5))
    await env.session.flush()

    expired = await ScanRetentionService(env.session, env.settings).expire_abandoned(now=NOW)

    assert expired == 1
    await env.session.refresh(old)
    assert old.status is ScanJobStatus.FAILED
    assert old.error_code == ABANDONED_ERROR_CODE
    assert old.error_detail, "an expired job must record why it was expired"
    assert old.completed_at is not None


@pytest.mark.parametrize("status", sorted(PENDING_STATUSES))
async def test_a_pending_job_inside_the_window_is_left_alone(
    env: Env, status: ScanJobStatus
) -> None:
    fresh = env.add_job(status=status, created_at=NOW - timedelta(minutes=5))
    await env.session.flush()

    expired = await ScanRetentionService(env.session, env.settings).expire_abandoned(now=NOW)

    assert expired == 0
    await env.session.refresh(fresh)
    assert fresh.status is status


def test_a_window_shorter_than_the_claim_timeout_is_refused() -> None:
    """Expiry must never be able to fire on a job a worker could still be holding.

    Each setting has a legitimate range, and the ranges overlap: a claim timeout may be a
    whole day, an abandonment window as little as an hour. Neither bound can catch that on
    its own, so the relationship is enforced, and startup refuses rather than quietly
    failing scans that were still allowed to be running.
    """

    with pytest.raises(ValidationError, match="SCAN_JOB_ABANDONED_AFTER_HOURS"):
        Settings(
            _env_file=None,
            app_env="test",
            app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
            scan_job_claim_timeout_seconds=7200,
            scan_job_abandoned_after_hours=2,
        )


def test_a_window_longer_than_the_claim_timeout_is_accepted() -> None:
    """The guard must not be so eager that it refuses a sound configuration."""

    settings = Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        scan_job_claim_timeout_seconds=7200,
        scan_job_abandoned_after_hours=3,
    )
    assert settings.scan_job_abandoned_after_hours == 3


def test_the_shipped_defaults_satisfy_their_own_rule() -> None:
    """The values a deployment gets without touching anything must be a valid pair."""

    settings = Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
    )
    assert (
        settings.scan_job_abandoned_after_hours * 3600 > settings.scan_job_claim_timeout_seconds
    )


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
async def test_old_finished_jobs_are_deleted(env: Env, status: ScanJobStatus) -> None:
    env.add_job(status=status, created_at=NOW - timedelta(days=400))
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    assert result["purged_jobs"] == 1
    assert await env.count(ScanJob) == 0


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
async def test_recent_finished_jobs_are_kept(env: Env, status: ScanJobStatus) -> None:
    env.add_job(status=status, created_at=NOW - timedelta(days=2))
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    assert result["purged_jobs"] == 0
    assert await env.count(ScanJob) == 1


@pytest.mark.parametrize("status", sorted(PENDING_STATUSES))
async def test_the_purge_never_deletes_a_pending_job(env: Env, status: ScanJobStatus) -> None:
    """Age alone must not be enough. A running job is not rubbish, however old it looks."""

    env.add_job(status=status, created_at=NOW - timedelta(days=400))
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    assert result["purged_jobs"] == 0
    assert await env.count(ScanJob) == 1


async def test_deleting_a_job_takes_its_results_with_it(env: Env) -> None:
    """Retention is written against the job; results must follow through the cascade.

    If this fails, results become orphans that no retention pass looks at — the very
    unbounded growth this service exists to stop, hidden one table deeper.
    """

    job = env.add_job(status=ScanJobStatus.SUCCEEDED, created_at=NOW - timedelta(days=400))
    await env.session.flush()
    env.add_result(job)
    await env.session.flush()
    assert await env.count(ScanResult) == 1

    await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)
    await env.session.flush()

    assert await env.count(ScanResult) == 0


async def test_one_run_clears_the_whole_backlog_however_many_batches_that_takes(
    env: Env,
) -> None:
    """The batch bounds a transaction. It must not bound a night's work.

    This test asserted the opposite until 24 August 2026: that one run removed exactly one
    batch and reported the rest as ``remaining``. That reads like a safety property and is
    really a leak. The purge runs once a night, so a batch-per-run means the table can
    never lose more rows in a day than fit in one transaction, whatever arrived that day.
    At today's 204 jobs a day against a batch of 5000 it is invisible; fifty monitors on
    one-minute candles write about 72,000 a day, and the deletion would fall 67,000 rows
    behind every night until the disk filled.

    The short transaction is still the rule — it is asserted below, by the number of
    batches the run needed — but finishing the work is no longer optional.
    """

    env.settings.scan_history_purge_batch = 2
    for index in range(5):
        env.add_job(
            status=ScanJobStatus.SUCCEEDED,
            created_at=NOW - timedelta(days=400 + index),
            key=f"batch-{index}",
        )
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    assert result["purged_jobs"] == 5
    assert result["remaining"] == 0
    assert await env.count(ScanJob) == 0


async def test_a_run_stops_at_its_batch_ceiling_and_reports_what_is_left(env: Env) -> None:
    """The loop has an end, and it says plainly what it could not reach.

    A run that could go on for ever is its own kind of outage on a one-worker server: every
    other background task waits behind it. The ceiling is deliberately far above any real
    day's work, so reaching it means something is wrong and ``remaining`` is how anyone
    finds out.
    """

    env.settings.scan_history_purge_batch = 2
    env.settings.scan_history_purge_max_batches = 1
    for index in range(5):
        env.add_job(
            status=ScanJobStatus.SUCCEEDED,
            created_at=NOW - timedelta(days=400 + index),
            key=f"capped-{index}",
        )
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    assert result["purged_jobs"] == 2
    assert result["remaining"] == 3
    assert await env.count(ScanJob) == 3


async def test_the_oldest_rows_go_first(env: Env) -> None:
    """A capped run must make progress from the far end, not sample the middle."""

    env.settings.scan_history_purge_batch = 1
    env.settings.scan_history_purge_max_batches = 1
    oldest = env.add_job(
        status=ScanJobStatus.SUCCEEDED,
        created_at=NOW - timedelta(days=500),
        key="oldest",
    )
    env.add_job(
        status=ScanJobStatus.SUCCEEDED,
        created_at=NOW - timedelta(days=100),
        key="newer",
    )
    await env.session.flush()
    doomed_id = oldest.id

    await ScanRetentionService(env.session, env.settings).purge_expired(now=NOW)

    survivors = list((await env.session.scalars(select(ScanJob.id))).all())
    assert doomed_id not in survivors
    assert len(survivors) == 1


async def test_run_expires_then_purges_in_the_same_pass(env: Env) -> None:
    """Expiry must feed the purge immediately, not a day later.

    A job old enough for both is the common case on a first run against a neglected
    database — exactly the 167 rows this was written for.
    """

    env.add_job(status=ScanJobStatus.QUEUED, created_at=NOW - timedelta(days=400))
    await env.session.flush()

    result = await ScanRetentionService(env.session, env.settings).run(now=NOW)

    assert result["expired"] == 1
    assert result["purged_jobs"] == 1
    assert await env.count(ScanJob) == 0


async def test_a_clean_database_is_a_no_op(env: Env) -> None:
    """The nightly task runs forever on healthy deployments; it must cost nothing there."""

    result = await ScanRetentionService(env.session, env.settings).run(now=NOW)

    assert result == {"expired": 0, "purged_jobs": 0, "remaining": 0}


def test_the_nightly_task_is_scheduled_and_not_gated_on_scanning() -> None:
    """History outlives the switch that produced it.

    Every other scan task returns early when scanning is off. Cleanup must not: the
    deployment most likely to run out of disk is the one that paused everything and
    stopped looking at it.
    """

    from ai_market_monitor.worker import _cleanup_scan_history, app

    entry = app.conf.beat_schedule["cleanup-scan-history-nightly"]
    assert entry["task"] == "ai_market_monitor.cleanup_scan_history"
    assert entry["schedule"] == 24 * 60 * 60

    # The compiled names, not the source text: reading the source matched this rule's own
    # explanation of itself in the docstring, which is a test that passes on prose.
    assert "scanning_enabled" not in _cleanup_scan_history.__code__.co_names, (
        "scan cleanup must run even when scanning is disabled"
    )
