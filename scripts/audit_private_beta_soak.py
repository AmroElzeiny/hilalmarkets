from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select

from ai_market_monitor.core.database import SessionFactory, engine
from ai_market_monitor.db.models import (
    Alert,
    PaymentEmailDelivery,
    PublicInquiryEmailDelivery,
    ScanJob,
    ScanResult,
    SetupInstance,
)


async def _duplicate_groups(session, statement: Select[Any]) -> int:
    grouped = statement.subquery()
    return int(await session.scalar(select(func.count()).select_from(grouped)) or 0)


async def audit(*, days: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    async with SessionFactory() as session:
        scheduled_scan_duplicates = await _duplicate_groups(
            session,
            select(ScanJob.strategy_version_id, ScanJob.scheduled_for, func.count().label("n"))
            .where(ScanJob.created_at >= since, ScanJob.job_type == "live")
            .group_by(ScanJob.strategy_version_id, ScanJob.scheduled_for)
            .having(func.count() > 1),
        )
        scan_result_duplicates = await _duplicate_groups(
            session,
            select(
                ScanResult.scan_job_id,
                ScanResult.exchange,
                ScanResult.symbol,
                ScanResult.timeframe,
                ScanResult.direction,
                func.count().label("n"),
            )
            .where(ScanResult.evaluated_at >= since)
            .group_by(
                ScanResult.scan_job_id,
                ScanResult.exchange,
                ScanResult.symbol,
                ScanResult.timeframe,
                ScanResult.direction,
            )
            .having(func.count() > 1),
        )
        journey_duplicates = await _duplicate_groups(
            session,
            select(
                SetupInstance.strategy_version_id,
                SetupInstance.exchange,
                SetupInstance.symbol,
                SetupInstance.timeframe,
                SetupInstance.setup_key,
                func.count().label("n"),
            )
            .where(SetupInstance.created_at >= since)
            .group_by(
                SetupInstance.strategy_version_id,
                SetupInstance.exchange,
                SetupInstance.symbol,
                SetupInstance.timeframe,
                SetupInstance.setup_key,
            )
            .having(func.count() > 1),
        )
        alert_duplicates = await _duplicate_groups(
            session,
            select(
                Alert.setup_instance_id,
                Alert.alert_type,
                Alert.candle_timestamp,
                func.count().label("n"),
            )
            .where(
                Alert.created_at >= since,
                Alert.setup_instance_id.is_not(None),
                Alert.candle_timestamp.is_not(None),
            )
            .group_by(Alert.setup_instance_id, Alert.alert_type, Alert.candle_timestamp)
            .having(func.count() > 1),
        )
        payment_email_duplicates = await _duplicate_groups(
            session,
            select(PaymentEmailDelivery.event_key, func.count().label("n"))
            .where(PaymentEmailDelivery.created_at >= since)
            .group_by(PaymentEmailDelivery.event_key)
            .having(func.count() > 1),
        )
        inquiry_email_duplicates = await _duplicate_groups(
            session,
            select(PublicInquiryEmailDelivery.event_key, func.count().label("n"))
            .where(PublicInquiryEmailDelivery.created_at >= since)
            .group_by(PublicInquiryEmailDelivery.event_key)
            .having(func.count() > 1),
        )
        totals = {
            "scheduled_scan_jobs": int(
                await session.scalar(
                    select(func.count(ScanJob.id)).where(
                        ScanJob.created_at >= since, ScanJob.job_type == "live"
                    )
                )
                or 0
            ),
            "scan_results": int(
                await session.scalar(
                    select(func.count(ScanResult.id)).where(ScanResult.evaluated_at >= since)
                )
                or 0
            ),
            "opportunity_journeys": int(
                await session.scalar(
                    select(func.count(SetupInstance.id)).where(
                        SetupInstance.created_at >= since
                    )
                )
                or 0
            ),
            "alerts": int(
                await session.scalar(
                    select(func.count(Alert.id)).where(Alert.created_at >= since)
                )
                or 0
            ),
            "payment_emails": int(
                await session.scalar(
                    select(func.count(PaymentEmailDelivery.id)).where(
                        PaymentEmailDelivery.created_at >= since
                    )
                )
                or 0
            ),
            "public_inquiry_emails": int(
                await session.scalar(
                    select(func.count(PublicInquiryEmailDelivery.id)).where(
                        PublicInquiryEmailDelivery.created_at >= since
                    )
                )
                or 0
            ),
        }

    duplicates = {
        "scheduler_slots": scheduled_scan_duplicates,
        "scan_results": scan_result_duplicates,
        "opportunity_journeys": journey_duplicates,
        "alerts": alert_duplicates,
        "payment_email_events": payment_email_duplicates,
        "public_inquiry_email_events": inquiry_email_duplicates,
    }
    return {
        "generated_at": now.isoformat(),
        "window_start": since.isoformat(),
        "window_days": days,
        "totals": totals,
        "duplicate_groups": duplicates,
        "status": "pass" if not any(duplicates.values()) else "fail",
        "note": (
            "This audit detects duplicate persisted work. It does not prove provider delivery, "
            "market-data correctness, or scheduler availability; retain worker and provider "
            "telemetry with this result."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit persisted private-beta work for semantic duplicates."
    )
    parser.add_argument("--days", type=int, default=7, choices=range(1, 31), metavar="1-30")
    return parser.parse_args()


async def _main() -> int:
    args = parse_args()
    result = await audit(days=args.days)
    print(json.dumps(result, indent=2, sort_keys=True))
    await engine.dispose()
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
