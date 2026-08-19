from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import get_args
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


async def deliverable_channels(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
) -> set[DeliveryChannel]:
    """Every way this **person** can really be told, right now.

    Not the same question as :func:`offered_channels`, which is about the platform.
    This one is about one account: Telegram has to be connected, WhatsApp has to be
    connected, email needs a verified address. The dashboard needs nothing — a notice
    waiting on the page is always deliverable, which is why it is the one channel the
    product can always fall back on.

    One owner, because the activation gate used to answer it with its own rule: "an
    active Telegram **or** WhatsApp connection", and nothing else counted. So somebody
    who chose "In the dashboard" — or, later, email — was told to connect a channel,
    with no channel to connect and no way forward. Every way the platform can deliver
    has to count as a way of being told, or the gate is refusing something that works.
    """

    from ai_market_monitor.db.models import TelegramConnection, WhatsAppConnection
    from ai_market_monitor.db.models.enums import ConnectionStatus
    from ai_market_monitor.services.alert_emails import alert_email_address

    offered = offered_channels(settings)
    reachable: set[DeliveryChannel] = set()
    if DeliveryChannel.WEB in offered:
        reachable.add(DeliveryChannel.WEB)
    if DeliveryChannel.EMAIL in offered and await alert_email_address(session, user_id):
        reachable.add(DeliveryChannel.EMAIL)
    if DeliveryChannel.TELEGRAM in offered:
        connected = await session.scalar(
            select(TelegramConnection.id).where(
                TelegramConnection.user_id == user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                TelegramConnection.alerts_enabled.is_(True),
            )
        )
        if connected is not None:
            reachable.add(DeliveryChannel.TELEGRAM)
    if DeliveryChannel.WHATSAPP in offered:
        connected = await session.scalar(
            select(WhatsAppConnection.id).where(
                WhatsAppConnection.user_id == user_id,
                WhatsAppConnection.status == ConnectionStatus.ACTIVE,
                WhatsAppConnection.alerts_enabled.is_(True),
            )
        )
        if connected is not None:
            reachable.add(DeliveryChannel.WHATSAPP)
    return reachable


#: The words a person reads for each way of being told.
#:
#: Only the words. Which ways exist is not decided here, and no screen writes its own
#: label for one — a channel named "Email" on one page and "E-mail" on another is two
#: things as far as a reader is concerned.
CHANNEL_WORDS: dict[str, tuple[str, str]] = {
    "web": ("In the dashboard", "A notice waiting for you the next time you sign in."),
    "telegram": ("Telegram", "A message from the Hilal Markets bot."),
    "whatsapp": ("WhatsApp", "A message on WhatsApp."),
    "email": ("Email", "A message to the address you signed up with."),
}


def alert_channel_choices(
    settings: Settings,
    *,
    whatsapp_allowed: bool = True,
    reachable: set[DeliveryChannel] | None = None,
) -> list[dict[str, str]]:
    """Every way a monitor may really be told to send, in the words a person reads.

    Three questions, each answered by its owner and then met in the middle:

    * *Would the compiler accept it?* — ``AlertPolicy``'s own accepted values, so no
      screen can offer something the alert schema would refuse;
    * *Can we actually deliver it?* — :func:`offered_channels`, so a retired channel is
      never offered however long its value lingers in the schema for old rows;
    * *Does this plan include it?* — WhatsApp is a paid feature, and offering it to a
      plan that does not have it is a promise the product will not keep.

    One owner, because two of these were already answered in two places. The canvas
    built its own list from the alert schema alone and offered a retired channel; it
    then offered WhatsApp to plans without it, while the Connections page did not. The
    canvas page, the Connections page and the canvas's own activation route all call
    this now, so what is offered and what is accepted cannot drift apart.
    """

    from ai_market_monitor.schemas.strategy import AlertPolicy

    annotation = AlertPolicy.model_fields["channels"].annotation
    literal = get_args(annotation)[0] if get_args(annotation) else None
    accepted = [str(value) for value in get_args(literal)] if literal is not None else []
    deliverable = {channel.value for channel in offered_channels(settings)}
    if not whatsapp_allowed:
        deliverable.discard(DeliveryChannel.WHATSAPP.value)
    # Whether each one can reach *this person* today, if the caller has asked. A page
    # that only knows what the platform delivers will happily let somebody choose
    # Telegram before connecting it, and the refusal then arrives at the very last step
    # — after they have built the whole thing.
    ready = None if reachable is None else {item.value for item in reachable}
    choices: list[dict[str, str]] = []
    for value in accepted:
        if value not in deliverable:
            continue
        label, explanation = CHANNEL_WORDS.get(
            value, (value.replace("_", " ").capitalize(), "")
        )
        choice: dict[str, str] = {
            "value": value,
            "label": label,
            "explanation": explanation,
        }
        if ready is not None:
            choice["ready"] = "true" if value in ready else "false"
            if value not in ready:
                choice["not_ready_reason"] = CHANNEL_NOT_READY.get(
                    value, "This one is not set up on your account yet."
                )
        choices.append(choice)
    return choices


#: Why one way of being told cannot reach somebody yet, and what to do about it.
CHANNEL_NOT_READY: dict[str, str] = {
    "telegram": "Telegram is not connected yet. Connect it on the Connections page.",
    "whatsapp": "WhatsApp is not connected yet. Connect it on the Connections page.",
    "email": "There is no confirmed email address on this account yet.",
}


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
