from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AlertDelivery,
    AuditEvent,
    IntegrationHealth,
    MarketDataHealth,
    ScanJob,
    Strategy,
    SupportRequest,
    TelegramConnection,
)
from ai_market_monitor.db.models.enums import SupportRequestStatus
from ai_market_monitor.services.entitlements import EntitlementService


class SupportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SupportEscalationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def diagnostic_context(
        self,
        *,
        user_id: UUID,
        strategy_id: UUID | None = None,
        alert_id: UUID | None = None,
        scan_job_id: UUID | None = None,
    ) -> dict:
        entitlement = await EntitlementService(self.session).current(user_id)
        telegram = await self.session.scalar(
            select(TelegramConnection).where(TelegramConnection.user_id == user_id)
        )
        strategy = await self.session.get(Strategy, strategy_id) if strategy_id else None
        scan_job = await self.session.get(ScanJob, scan_job_id) if scan_job_id else None
        deliveries = []
        if alert_id:
            deliveries = [
                {
                    "channel": row.channel.value,
                    "status": row.status.value,
                    "attempt_count": row.attempt_count,
                    "last_error_code": row.last_error_code,
                    "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
                }
                for row in (
                    await self.session.scalars(
                        select(AlertDelivery).where(AlertDelivery.alert_id == alert_id)
                    )
                ).all()
            ]
        market_health = [
            {
                "provider": row.provider,
                "exchange": row.exchange,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "status": row.status.value,
                "checked_at": row.checked_at.isoformat(),
                "data_age_seconds": row.data_age_seconds,
                "missing_candle_count": row.missing_candle_count,
            }
            for row in (
                await self.session.scalars(
                    select(MarketDataHealth).order_by(MarketDataHealth.checked_at.desc()).limit(5)
                )
            ).all()
        ]
        integrations = [
            {
                "integration": row.integration,
                "scope_key": row.scope_key,
                "status": row.status.value,
                "last_error_code": row.last_error_code,
                "checked_at": row.checked_at.isoformat(),
            }
            for row in (
                await self.session.scalars(
                    select(IntegrationHealth).order_by(IntegrationHealth.checked_at.desc()).limit(5)
                )
            ).all()
        ]
        return {
            "tier": "diagnostic",
            "user_id": str(user_id),
            "plan": entitlement.plan.code,
            "entitlement_source": entitlement.source,
            "telegram_connection": bool(telegram),
            "strategy": {
                "id": str(strategy.id),
                "name": strategy.name,
                "status": strategy.status.value,
            }
            if strategy
            else None,
            "scan_job": {
                "id": str(scan_job.id),
                "status": scan_job.status.value,
                "error_code": scan_job.error_code,
            }
            if scan_job
            else None,
            "alert_id": str(alert_id) if alert_id else None,
            "delivery_logs": deliveries,
            "market_health": market_health,
            "integration_health": integrations,
        }

    async def create_ticket(
        self,
        *,
        user_id: UUID,
        category: str,
        description: str,
        strategy_id: UUID | None = None,
        alert_id: UUID | None = None,
        scan_job_id: UUID | None = None,
        source: str = "web",
    ) -> SupportRequest:
        context = await self.diagnostic_context(
            user_id=user_id,
            strategy_id=strategy_id,
            alert_id=alert_id,
            scan_job_id=scan_job_id,
        )
        context["source"] = source
        priority = "high" if category in {"missing_alert", "billing", "bug_report"} else "normal"
        ticket = SupportRequest(
            user_id=user_id,
            category=category,
            priority=priority,
            status=SupportRequestStatus.OPEN,
            subject=f"{category.replace('_', ' ').title()} support request",
            description=description,
            context=context,
        )
        self.session.add(ticket)
        self._audit(
            user_id,
            "support.ticket_created",
            "support_request",
            None,
            {"category": category, "source": source},
        )
        await self.session.flush()
        return ticket

    async def escalate(
        self,
        *,
        support_request_id: UUID,
        actor_user_id: UUID | None,
        tier: int,
        reason: str,
    ) -> SupportRequest:
        ticket = await self.session.get(SupportRequest, support_request_id)
        if ticket is None:
            raise SupportError("support_missing", "Support request not found.")
        if tier not in {1, 2, 3}:
            raise SupportError("invalid_tier", "Support tier must be 1, 2 or 3.")
        ticket.status = SupportRequestStatus.IN_PROGRESS
        ticket.priority = "urgent" if tier == 3 else "high"
        ticket.context = {
            **ticket.context,
            "escalation_tier": tier,
            "escalation_reason": reason,
            "escalated_at": datetime.now(UTC).isoformat(),
        }
        self._audit(
            actor_user_id,
            "support.escalated",
            "support_request",
            ticket.id,
            {"tier": tier, "reason": reason},
        )
        await self.session.flush()
        return ticket

    async def resolve(
        self,
        *,
        support_request_id: UUID,
        actor_user_id: UUID,
        resolution: str,
    ) -> SupportRequest:
        ticket = await self.session.get(SupportRequest, support_request_id)
        if ticket is None:
            raise SupportError("support_missing", "Support request not found.")
        ticket.status = SupportRequestStatus.RESOLVED
        ticket.resolved_at = datetime.now(UTC)
        ticket.context = {
            **ticket.context,
            "resolution": resolution,
            "resolved_by": str(actor_user_id),
        }
        self._audit(
            actor_user_id,
            "support.resolved",
            "support_request",
            ticket.id,
            {"resolution": resolution},
        )
        await self.session.flush()
        return ticket

    def _audit(
        self,
        actor_user_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="user" if actor_user_id else "system",
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )
