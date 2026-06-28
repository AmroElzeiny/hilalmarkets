from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import Alert, AlertDelivery, DashboardPreference
from ai_market_monitor.db.models.enums import AlertType, DeliveryChannel


@dataclass(frozen=True, slots=True)
class NotificationPreference:
    channels: set[DeliveryChannel]
    near_miss_enabled: bool = True
    near_miss_threshold: int = 70
    lifecycle_enabled: bool = True
    maximum_alerts_per_hour: int = 50
    timezone: str = "UTC"
    alert_days: set[str] | None = None
    alert_hours: set[str] | None = None
    providers: set[str] | None = None
    muted_symbols: set[str] | None = None
    muted_strategy_ids: set[str] | None = None
    muted_strategy_until: dict[str, str] | None = None
    muted_setup_instance_ids: set[str] | None = None


class NotificationPreferenceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def current(self, user_id: UUID) -> NotificationPreference:
        row = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        data = row.notification_preferences if row else {}
        channel_values = data.get("alert_channels", data.get("channels", ["telegram"]))
        channels = {
            DeliveryChannel(value)
            for value in channel_values
            if value in {channel.value for channel in DeliveryChannel}
        }
        return NotificationPreference(
            channels=channels or {DeliveryChannel.TELEGRAM},
            near_miss_enabled=bool(data.get("near_miss_enabled", True)),
            near_miss_threshold=max(1, min(100, int(data.get("near_miss_threshold", 70)))),
            lifecycle_enabled=bool(data.get("lifecycle_enabled", True)),
            maximum_alerts_per_hour=max(
                1,
                min(1000, int(data.get("maximum_alerts_per_hour", 50))),
            ),
            timezone=str(data.get("timezone") or (row.default_timezone if row else "UTC")),
            alert_days=set(map(str, data.get("alert_days", ["Every Day"]))),
            alert_hours=set(map(str, data.get("alert_hours", []))),
            providers=set(map(str.lower, data.get("providers", ["binance", "bybit"]))),
            muted_symbols=set(map(str.upper, data.get("muted_symbols", []))),
            muted_strategy_ids=set(map(str, data.get("muted_strategy_ids", []))),
            muted_strategy_until={
                str(key): str(value)
                for key, value in (data.get("muted_strategy_until", {}) or {}).items()
            },
            muted_setup_instance_ids=set(map(str, data.get("muted_setup_instance_ids", []))),
        )

    async def allowed_channels(
        self,
        user_id: UUID,
        requested: set[DeliveryChannel],
        *,
        alert: Alert,
    ) -> set[DeliveryChannel]:
        preference = await self.current(user_id)
        proof = alert.proof_receipt or {}
        setup_state = str(proof.get("setup_state") or "")
        incomplete_states = {"candidate_detected", "detected", "forming", "near_confirmation"}
        if not preference.near_miss_enabled and (
            alert.alert_type in {AlertType.NEAR_MISS, AlertType.FORMING}
            or (alert.alert_type == AlertType.LIFECYCLE and setup_state in incomplete_states)
        ):
            return set()
        score = proof.get("setup_completion_score", proof.get("completion_score"))
        if alert.alert_type in {AlertType.NEAR_MISS, AlertType.FORMING} and score is not None:
            try:
                if float(score) < preference.near_miss_threshold:
                    return set()
            except (TypeError, ValueError):
                return set()
        if alert.alert_type == AlertType.LIFECYCLE and not preference.lifecycle_enabled:
            return set()
        symbol = str(proof.get("symbol") or "").upper()
        if symbol and symbol in (preference.muted_symbols or set()):
            return set()
        if alert.strategy_version_id is not None and str(alert.strategy_version_id) in (
            preference.muted_strategy_ids or set()
        ):
            return set()
        if alert.strategy_version_id is not None:
            muted_until = (preference.muted_strategy_until or {}).get(
                str(alert.strategy_version_id)
            )
            if muted_until:
                try:
                    expiry = datetime.fromisoformat(muted_until)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=UTC)
                    if expiry > datetime.now(UTC):
                        return set()
                except ValueError:
                    return set()
        if alert.setup_instance_id is not None and str(alert.setup_instance_id) in (
            preference.muted_setup_instance_ids or set()
        ):
            return set()
        if not self._inside_schedule(preference):
            return set()
        since = datetime.now(UTC) - timedelta(hours=1)
        hourly_count = await self.session.scalar(
            select(func.count(func.distinct(Alert.id)))
            .join(AlertDelivery, AlertDelivery.alert_id == Alert.id)
            .where(Alert.user_id == user_id, AlertDelivery.created_at >= since)
        )
        if (hourly_count or 0) >= preference.maximum_alerts_per_hour:
            return set()
        return set(requested) & preference.channels

    @staticmethod
    def _inside_schedule(preference: NotificationPreference) -> bool:
        try:
            zone = ZoneInfo(preference.timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        local_now = datetime.now(UTC).astimezone(zone)
        days = preference.alert_days or {"Every Day"}
        if "Every Day" not in days and local_now.strftime("%A") not in days:
            return False
        hours = preference.alert_hours or set()
        return not hours or local_now.strftime("%H:00") in hours
