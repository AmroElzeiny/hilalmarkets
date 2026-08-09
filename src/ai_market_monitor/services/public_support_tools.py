from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaAssessment,
    CanonicalAsset,
    PublishedAssetAssessment,
    Strategy,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    DeliveryStatus,
    StrategyStatus,
)
from ai_market_monitor.schemas.public_chat import (
    PublicSupportToolName,
    PublicSupportToolResult,
)
from ai_market_monitor.services.entitlements import EntitlementService, UsageService


class PublicSupportReadTools:
    """Read-only account support adapters with server-derived ownership."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        tool_name: PublicSupportToolName,
        *,
        user_id: UUID | None,
        question: str = "",
    ) -> PublicSupportToolResult:
        try:
            if tool_name == "public_passport":
                return await self._public_passport(question)
            if user_id is None:
                return PublicSupportToolResult(
                    tool_name=tool_name,
                    status="blocked",
                    data={},
                    evidence_refs=[],
                    route_id="dashboard_entry",
                )
            user = await self.session.get(User, user_id)
            if user is None or str(user.status.value) == "suspended":
                return PublicSupportToolResult(
                    tool_name=tool_name,
                    status="blocked",
                    data={},
                    evidence_refs=[],
                    route_id="dashboard_entry",
                )
            handlers = {
                "account_state": self._account_state,
                "telegram_status": self._telegram_status,
                "watch_plan_summary": self._watch_plan_summary,
                "recent_alerts": self._recent_alerts,
                "entitlement_usage": self._entitlement_usage,
                "screened_watchlist": self._screened_watchlist,
            }
            return await handlers[tool_name](user_id)
        except Exception:
            return PublicSupportToolResult(
                tool_name=tool_name,
                status="unavailable",
                data={
                    "reason": (
                        "The authoritative account or Passport record could not be read "
                        "right now. No value was inferred."
                    )
                },
                evidence_refs=[],
                # Passport and account failures both send the reader to the Help Center.
                # They used to split, with the Passport branch pointing at How We Screen;
                # that page is no longer linked from anywhere on the site.
                route_id="help",
            )

    async def _public_passport(self, question: str) -> PublicSupportToolResult:
        candidates = {
            item.upper()
            for item in re.findall(r"\b[A-Za-z][A-Za-z0-9]{1,11}\b", question)
        }
        for name, symbol in {
            "bitcoin": "BTC",
            "ethereum": "ETH",
            "solana": "SOL",
        }.items():
            if re.search(rf"\b{name}\b", question, flags=re.IGNORECASE):
                candidates.add(symbol)
        ignored = {
            "ABOUT",
            "ASSET",
            "COIN",
            "CURRENT",
            "DOES",
            "HALAL",
            "HARAM",
            "PASSPORT",
            "STATUS",
            "THE",
            "TOKEN",
            "WHAT",
        }
        candidates.difference_update(ignored)
        if not candidates:
            return PublicSupportToolResult(
                tool_name="public_passport",
                status="unavailable",
                data={"reason": "No asset symbol was identified in the question."},
                evidence_refs=[],
                route_id="help",
            )
        asset = await self.session.scalar(
            select(CanonicalAsset)
            .where(func.upper(CanonicalAsset.symbol).in_(sorted(candidates)))
            .order_by(CanonicalAsset.symbol)
            .limit(1)
        )
        if asset is None:
            return PublicSupportToolResult(
                tool_name="public_passport",
                status="unavailable",
                data={"reason": "No published asset identity matched the supplied symbol."},
                evidence_refs=[],
                route_id="help",
            )
        publication = await self.session.scalar(
            select(PublishedAssetAssessment)
            .where(
                PublishedAssetAssessment.canonical_asset_id == asset.id,
                PublishedAssetAssessment.is_active.is_(True),
                PublishedAssetAssessment.publication_state == "published",
            )
            .order_by(PublishedAssetAssessment.version.desc())
            .limit(1)
        )
        if publication is None:
            return PublicSupportToolResult(
                tool_name="public_passport",
                status="unavailable",
                data={
                    "asset": asset.symbol,
                    "reason": "This asset has no current published Passport.",
                },
                evidence_refs=[],
                route_id="help",
            )
        assessment = await self.session.get(
            AssetShariaAssessment,
            publication.asset_assessment_id,
        )
        if assessment is None:
            return PublicSupportToolResult(
                tool_name="public_passport",
                status="unavailable",
                data={"reason": "The published Passport record is incomplete."},
                evidence_refs=[],
                route_id="help",
            )
        snapshot = dict(publication.passport_snapshot or {})
        methodology = dict(snapshot.get("methodology_result") or {})
        return PublicSupportToolResult(
            tool_name="public_passport",
            status="success",
            data={
                "asset": asset.symbol,
                "asset_name": asset.name,
                "recorded_status": assessment.status.value,
                "summary": assessment.summary,
                "qualifications": list(assessment.qualifications or []),
                "methodology_code": methodology.get("methodology_code"),
                "methodology_version": methodology.get("methodology_version"),
                "reviewed_at": assessment.reviewed_at,
                "publication_version": publication.version,
                "published_at": publication.published_at,
                "wording_boundary": (
                    "This is the recorded status under the published methodology Passport, "
                    "not a new religious ruling by the assistant."
                ),
            },
            evidence_refs=[f"published-passport:{publication.id}"],
            route_id="help",
        )

    async def _account_state(self, user_id: UUID) -> PublicSupportToolResult:
        user = await self.session.get(User, user_id)
        assert user is not None
        return self._success(
            "account_state",
            user_id,
            {
                "status": user.status.value,
                "onboarding_complete": user.onboarding_completed_at is not None,
                "timezone": user.timezone,
            },
            "dashboard_entry",
        )

    async def _telegram_status(self, user_id: UUID) -> PublicSupportToolResult:
        connection = await self.session.scalar(
            select(TelegramConnection).where(TelegramConnection.user_id == user_id)
        )
        data = {
            "connected": bool(
                connection is not None and connection.status == ConnectionStatus.ACTIVE
            ),
            "status": connection.status.value if connection else "not_connected",
            "alerts_enabled": bool(connection and connection.alerts_enabled),
            "last_delivery_at": connection.last_delivery_at if connection else None,
            "last_error_code": connection.last_error_code if connection else None,
        }
        return self._success("telegram_status", user_id, data, "dashboard_entry")

    async def _watch_plan_summary(self, user_id: UUID) -> PublicSupportToolResult:
        plans = list(
            (
                await self.session.scalars(
                    select(Strategy)
                    .where(Strategy.user_id == user_id, Strategy.archived_at.is_(None))
                    .order_by(Strategy.updated_at.desc())
                    .limit(20)
                )
            ).all()
        )
        counts = {status.value: 0 for status in StrategyStatus}
        for plan in plans:
            counts[plan.status.value] = counts.get(plan.status.value, 0) + 1
        return self._success(
            "watch_plan_summary",
            user_id,
            {
                "counts": counts,
                "plans": [
                    {"name": item.name, "status": item.status.value}
                    for item in plans[:10]
                ],
                "truncated": len(plans) > 10,
            },
            "dashboard_entry",
        )

    async def _recent_alerts(self, user_id: UUID) -> PublicSupportToolResult:
        alerts = list(
            (
                await self.session.scalars(
                    select(Alert)
                    .where(Alert.user_id == user_id)
                    .order_by(Alert.created_at.desc())
                    .limit(10)
                )
            ).all()
        )
        alert_ids = [item.id for item in alerts]
        failures = 0
        if alert_ids:
            failures = int(
                await self.session.scalar(
                    select(func.count(AlertDelivery.id)).where(
                        AlertDelivery.alert_id.in_(alert_ids),
                        AlertDelivery.status.in_(
                            {
                                DeliveryStatus.FAILED,
                                DeliveryStatus.FAILED_RETRYABLE,
                                DeliveryStatus.FAILED_PERMANENT,
                            }
                        ),
                    )
                )
                or 0
            )
        return self._success(
            "recent_alerts",
            user_id,
            {
                "count": len(alerts),
                "delivery_failure_count": failures,
                "alerts": [
                    {
                        "title": item.title,
                        "alert_type": item.alert_type.value,
                        "created_at": item.created_at,
                        "suppressed_reason": item.suppressed_reason,
                    }
                    for item in alerts
                ],
            },
            "dashboard_entry",
        )

    async def _entitlement_usage(self, user_id: UUID) -> PublicSupportToolResult:
        entitlement = await EntitlementService(self.session).current(user_id)
        period_end = datetime.now(UTC)
        period_start = period_end - timedelta(days=30)
        usage = await UsageService(self.session).summary(user_id, period_start, period_end)
        return self._success(
            "entitlement_usage",
            user_id,
            {
                "plan_code": entitlement.plan.code,
                "plan_name": entitlement.plan.name,
                "source": entitlement.source,
                "ends_at": entitlement.ends_at,
                "limits": entitlement.plan.limits,
                "usage_last_30_days": usage,
            },
            "pricing",
        )

    async def _screened_watchlist(self, user_id: UUID) -> PublicSupportToolResult:
        watchlists = list(
            (
                await self.session.scalars(
                    select(ApprovedWatchlist)
                    .where(ApprovedWatchlist.user_id == user_id)
                    .order_by(ApprovedWatchlist.is_default.desc(), ApprovedWatchlist.name)
                    .limit(20)
                )
            ).all()
        )
        ids = [item.id for item in watchlists]
        assets = (
            list(
                (
                    await self.session.scalars(
                        select(ApprovedWatchlistAsset)
                        .where(ApprovedWatchlistAsset.watchlist_id.in_(ids))
                        .order_by(ApprovedWatchlistAsset.added_at.desc())
                        .limit(300)
                    )
                ).all()
            )
            if ids
            else []
        )
        names = {item.id: item.name for item in watchlists}
        return self._success(
            "screened_watchlist",
            user_id,
            {
                "watchlist_count": len(watchlists),
                "asset_count": len(assets),
                "assets": [
                    {
                        "asset": item.canonical_asset,
                        "watchlist": names.get(item.watchlist_id),
                    }
                    for item in assets[:100]
                ],
                "truncated": len(assets) > 100,
                "policy_note": (
                    "Saved assets never override current Sharia eligibility or market mapping."
                ),
            },
            "dashboard_entry",
        )

    @staticmethod
    def _success(
        tool_name: PublicSupportToolName,
        _user_id: UUID,
        data: dict,
        route_id: str,
    ) -> PublicSupportToolResult:
        return PublicSupportToolResult(
            tool_name=tool_name,
            status="success",
            data=data,
            evidence_refs=[f"support-tool:{tool_name}:current-user"],
            route_id=route_id,
        )
