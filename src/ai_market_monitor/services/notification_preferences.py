from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.db.models import Alert, AlertDelivery, DashboardPreference
from ai_market_monitor.db.models.enums import AlertType, DeliveryChannel
from ai_market_monitor.services.email_delivery import email_delivery_available


@dataclass(frozen=True, slots=True)
class NotificationPreference:
    channels: set[DeliveryChannel]
    near_miss_enabled: bool = True
    near_miss_threshold: int = 70
    lifecycle_enabled: bool = True
    maximum_alerts_per_hour: int = 50
    maximum_alerts_per_day: int = 500
    timezone: str = "UTC"
    alert_days: set[str] | None = None
    alert_hours: set[str] | None = None
    providers: set[str] | None = None
    muted_symbols: set[str] | None = None
    muted_strategy_ids: set[str] | None = None
    muted_strategy_until: dict[str, str] | None = None
    muted_strategy_symbols: dict[str, set[str]] | None = None
    muted_setup_instance_ids: set[str] | None = None
    compliance_alerts_enabled: bool = True
    compliance_alert_channels: set[DeliveryChannel] | None = None
    compliance_alert_digest: str = "immediate"
    qualification_change_alerts: bool = True
    under_review_alerts: bool = True
    exclusion_alerts: bool = True


def symbol_is_muted(symbol: str, muted: set[str] | None) -> bool:
    """Did this person ask not to hear about this market?

    A recorded market symbol is a pair — ``BTC/USDT``. A person silencing something
    means the coin: they do not want to hear about Bitcoin, on any pair. So a stored
    ``BTC`` silences ``BTC/USDT`` and ``BTC/USDC`` alike, while a stored ``BTC/USDT``
    still silences only that one pair.

    One owner for the comparison, because the moment a second place answers "is this
    coin silenced?" the two will disagree about whether the quote currency counts.
    """

    if not symbol or not muted:
        return False
    normalized = symbol.strip().upper().replace("-", "/")
    if not normalized:
        return False
    return normalized in muted or normalized.partition("/")[0] in muted


def offered_channels(settings: Settings) -> set[DeliveryChannel]:
    """Every way this platform can actually deliver an alert right now.

    One owner for the answer. ``DeliveryChannel`` still carries ``discord`` because
    historical alert and delivery rows point at it (see
    `docs/RETIRED_DISCORD_COMPATIBILITY.md`), so "every value of the enum" is not the
    same question as "every channel we offer" — and anything that treats them as the
    same offers a retired channel to a customer.

    That is exactly what happened: the canvas built its own list from the alert schema's
    accepted values and offered Discord as a way to be told about a monitor. Two places
    answering one question, and one of them wrong. Both import this now.

    Email is offered only when a sender is really configured. The same rule as WhatsApp,
    for the same reason: a channel a person can switch on but that nothing delivers is
    worse than a channel that is honestly missing, because the silence looks like "there
    was nothing to tell you".
    """

    channels = {DeliveryChannel.TELEGRAM, DeliveryChannel.WEB}
    if settings.whatsapp_enabled:
        channels.add(DeliveryChannel.WHATSAPP)
    if email_delivery_available(settings):
        channels.add(DeliveryChannel.EMAIL)
    return channels


class NotificationPreferenceService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def _supported_channels(self) -> set[DeliveryChannel]:
        return offered_channels(self.settings)

    async def current(self, user_id: UUID) -> NotificationPreference:
        row = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        data = row.notification_preferences if row else {}
        channel_values = data.get(
            "alert_channels",
            data.get("channels", ["web", "telegram"]),
        )
        channels = {
            DeliveryChannel(value)
            for value in channel_values
            if value in {channel.value for channel in DeliveryChannel}
        } & self._supported_channels()
        return NotificationPreference(
            channels=channels or {DeliveryChannel.WEB},
            near_miss_enabled=bool(data.get("near_miss_enabled", True)),
            near_miss_threshold=max(1, min(100, int(data.get("near_miss_threshold", 70)))),
            lifecycle_enabled=bool(data.get("lifecycle_enabled", True)),
            maximum_alerts_per_hour=max(
                1,
                min(1000, int(data.get("maximum_alerts_per_hour", 50))),
            ),
            maximum_alerts_per_day=max(
                1,
                min(10000, int(data.get("maximum_alerts_per_day", 500))),
            ),
            timezone=str(data.get("timezone") or (row.default_timezone if row else "UTC")),
            alert_days=set(map(str, data.get("alert_days", ["Every Day"]))),
            alert_hours=set(map(str, data.get("alert_hours", []))),
            providers=set(map(str.lower, data.get("providers", ["binance", "bybit"]))),
            # Read into the one shape `symbol_is_muted` compares against, so a record
            # written before that shape existed still silences what it was meant to.
            muted_symbols={
                str(value).strip().upper().replace("-", "/")
                for value in data.get("muted_symbols", [])
                if str(value).strip()
            },
            muted_strategy_ids=set(map(str, data.get("muted_strategy_ids", []))),
            muted_strategy_until={
                str(key): str(value)
                for key, value in (data.get("muted_strategy_until", {}) or {}).items()
            },
            muted_strategy_symbols={
                str(key): set(map(str.upper, value or []))
                for key, value in (data.get("muted_strategy_symbols", {}) or {}).items()
            },
            muted_setup_instance_ids=set(map(str, data.get("muted_setup_instance_ids", []))),
            compliance_alerts_enabled=bool(data.get("compliance_alerts_enabled", True)),
            compliance_alert_channels={
                DeliveryChannel(value)
                for value in data.get("compliance_alert_channels", ["web"])
                if value in {channel.value for channel in DeliveryChannel}
            }
            & self._supported_channels()
            or {DeliveryChannel.WEB},
            compliance_alert_digest=str(data.get("compliance_alert_digest", "immediate")),
            qualification_change_alerts=bool(data.get("qualification_change_alerts", True)),
            under_review_alerts=bool(data.get("under_review_alerts", True)),
            exclusion_alerts=bool(data.get("exclusion_alerts", True)),
        )

    async def allowed_channels(
        self,
        user_id: UUID,
        requested: set[DeliveryChannel],
        *,
        alert: Alert,
    ) -> set[DeliveryChannel]:
        preference = await self.current(user_id)
        mandatory_in_app = (
            {DeliveryChannel.WEB}
            if alert.alert_type == AlertType.COMPLIANCE
            and DeliveryChannel.WEB in requested
            else set()
        )
        if alert.alert_type == AlertType.COMPLIANCE:
            if not preference.compliance_alerts_enabled:
                return mandatory_in_app
            return (
                requested
                & (preference.compliance_alert_channels or {DeliveryChannel.WEB})
            ) | mandatory_in_app
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
        if symbol_is_muted(symbol, preference.muted_symbols):
            return set()
        if alert.strategy_version_id is not None and str(alert.strategy_version_id) in (
            preference.muted_strategy_ids or set()
        ):
            return set()
        if alert.strategy_version_id is not None and symbol:
            muted_strategy_symbols = preference.muted_strategy_symbols or {}
            if symbol in muted_strategy_symbols.get(str(alert.strategy_version_id), set()):
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
        try:
            zone = ZoneInfo(preference.timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        local_now = datetime.now(UTC).astimezone(zone)
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = local_start.astimezone(UTC)
        daily_count = await self.session.scalar(
            select(func.count(func.distinct(Alert.id)))
            .join(AlertDelivery, AlertDelivery.alert_id == Alert.id)
            .where(Alert.user_id == user_id, AlertDelivery.created_at >= day_start_utc)
        )
        if (daily_count or 0) >= preference.maximum_alerts_per_day:
            return set()
        return (set(requested) & preference.channels) | mandatory_in_app

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
