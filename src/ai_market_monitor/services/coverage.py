from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import ScanJob, ScanResult, Strategy, StrategyVersion
from ai_market_monitor.engine.quality import market_coverage_score


async def market_coverage_for_user(session: AsyncSession, user_id: UUID) -> dict:
    """Return latest-cycle coverage without summing repeated historical scans."""

    jobs = (
        await session.scalars(
            select(ScanJob)
            .join(StrategyVersion, ScanJob.strategy_version_id == StrategyVersion.id)
            .join(Strategy, StrategyVersion.strategy_id == Strategy.id)
            .where(Strategy.user_id == user_id, ScanJob.completed_at.is_not(None))
            .order_by(ScanJob.completed_at.desc())
            .limit(250)
        )
    ).all()
    latest_by_version: dict[UUID, ScanJob] = {}
    for job in jobs:
        latest_by_version.setdefault(job.strategy_version_id, job)
    latest_jobs = list(latest_by_version.values())
    if not latest_jobs:
        return market_coverage_score(
            symbols_eligible=0,
            symbols_scanned=0,
            timeframes_required=1,
            timeframes_covered=0,
        )

    job_ids = [job.id for job in latest_jobs]
    rows = (
        await session.execute(
            select(
                ScanResult.symbol,
                ScanResult.timeframe,
                ScanResult.error_code,
            ).where(ScanResult.scan_job_id.in_(job_ids))
        )
    ).all()
    unique_symbols = {_canonical_symbol(row.symbol) for row in rows if row.symbol}
    timeframes = {row.timeframe for row in rows if row.timeframe}
    failures = sum(1 for row in rows if row.error_code)
    scanned = len(unique_symbols)
    largest_planned_universe = max((job.symbols_planned for job in latest_jobs), default=0)
    eligible = max(scanned, largest_planned_universe)
    return market_coverage_score(
        symbols_eligible=eligible,
        symbols_scanned=scanned,
        symbols_skipped=max(0, eligible - scanned),
        data_failures=failures,
        timeframes_required=max(1, len(timeframes) or 1),
        timeframes_covered=len(timeframes),
        last_scan_at=max(
            (job.completed_at for job in latest_jobs if job.completed_at is not None),
            default=None,
        ),
    )


def _canonical_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace("-", "/").strip()
    return normalized.split(":", 1)[0]
