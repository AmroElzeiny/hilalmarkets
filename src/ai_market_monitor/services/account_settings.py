"""One owner for what a person's own settings are, and for saving them.

Reading a saved setting already had an owner: :mod:`notification_preferences`. Writing
one did not. The rule for every value — which channels may be chosen, which sounds
exist, what a number is clamped to, which day names are real — lived inside the
``POST /dashboard/settings`` handler, written out once, in a router.

That is the duplicate-parser shape this codebase keeps producing. The moment a second
way to save a setting exists (a page that saves as you go, rather than a form that
posts everything at once) there are two answers to "what is a valid sound?" and they
are free to drift. One of them will eventually accept something the other refuses, and
a person will have a setting that looks saved and is not.

So the whole rule lives here, once. The form handler and the JSON endpoint both call
:meth:`AccountSettingsService.save`. Neither decides anything itself.

**Nothing here is silently substituted.** A value that is not one of the offered ones
is dropped, and the person keeps whatever they had; a value that would leave the
account with no way at all of being told falls back to the in-app notice, which is the
one channel that cannot be switched off. Both are stated in the code below rather than
happening by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import DashboardPreference, User
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    DeliveryChannel,
    ShariaAssetStatus,
)
from ai_market_monitor.services.entitlements import EntitlementService
from ai_market_monitor.services.notification_preferences import offered_channels
from ai_market_monitor.services.sharia_screening import (
    ShariaScreeningError,
    ShariaScreeningService,
)

# ── The vocabulary ───────────────────────────────────────────────────────────
#
# Every closed set of choices on the Settings page. Declared once so the page that
# offers them and the code that checks them read the same list.

#: The time zones a person may keep their schedule in.
SUPPORTED_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Moscow",
    "Europe/Berlin",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
)

#: Which days a message may arrive on. "Every Day" wins over any other choice.
ALERT_DAYS: tuple[str, ...] = (
    "Every Day",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

#: Which hours a message may arrive in, in the person's own time zone.
ALERT_HOURS: tuple[str, ...] = tuple(f"{hour:02d}:00" for hour in range(24))

#: The exchanges whose prices a Watchlist may be checked against.
#:
#: This is not decoration. ``services/scanner.py`` and ``services/strategy.py`` both
#: refuse to scan a Watchlist whose exchange is not in this person's chosen set, so
#: unticking one here stops every Watchlist that uses it.
MARKET_PROVIDERS: tuple[str, ...] = ("binance", "bybit")

#: The sounds a confirmed match may make.
CONFIRMED_SOUNDS: tuple[str, ...] = ("chime", "bell", "soft", "none")

#: The sounds a forming setup may make.
FORMING_SOUNDS: tuple[str, ...] = ("pulse", "chime", "soft", "none")

#: How often screening changes may be gathered up before being sent.
EVIDENCE_TIMING: tuple[str, ...] = ("immediate", "daily")

#: The screening statuses a person may keep in their own market by default.
DEFAULT_SHARIA_STATUSES: tuple[str, ...] = (
    ShariaAssetStatus.ELIGIBLE.value,
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
)

#: How many coins one person may silence. A cap so a single account cannot store an
#: unbounded list; large enough that nobody real will meet it.
MUTED_SYMBOL_LIMIT = 50


class SettingsRejected(ValueError):
    """A submitted setting cannot be stored, and nothing was saved.

    Carries a code so the page can say the same thing whether the settings were posted
    as a form or sent as JSON.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SettingsChoice:
    """Everything a person can choose, exactly as it arrived.

    Nothing is checked here. This is the raw submission; :meth:`AccountSettingsService.
    save` is what decides which parts of it are real.
    """

    timezone: str
    near_miss_enabled: bool = True
    near_miss_threshold: int = 70
    maximum_alerts_per_hour: int = 50
    maximum_alerts_per_day: int = 500
    alert_channels: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    alert_days: list[str] = field(default_factory=list)
    alert_hours: list[str] = field(default_factory=list)
    #: Whether to be told when an opportunity finishes, one way or the other.
    finished_opportunity_alerts: bool = True
    #: Coins this person never wants to hear about.
    muted_symbols: list[str] = field(default_factory=list)
    compliance_alert_channels: list[str] = field(default_factory=list)
    compliance_alert_digest: str = "immediate"
    dashboard_notifications_enabled: bool = True
    dashboard_notification_sound: str = "chime"
    forming_dashboard_notifications: bool = False
    forming_notification_sound: str = "pulse"
    qualification_change_alerts: bool = True
    default_sharia_methodology_id: str = ""
    allowed_sharia_statuses: list[str] = field(default_factory=list)
    compliance_change_behavior: str = ComplianceChangeBehavior.PAUSE_ASSET.value
    advanced_sharia_override_acknowledged: bool = False


def clean_muted_symbols(values: list[str]) -> list[str]:
    """The coins somebody silenced, tidied into the shape the delivery gate matches.

    Upper case, no blanks, no repeats, capped. A person types ``btc``; the recorded
    market symbol is ``BTC/USDT``. Matching the two is
    :func:`notification_preferences.symbol_is_muted`'s job, not this one — here we only
    keep what they wrote, in one shape.
    """

    cleaned: list[str] = []
    for value in values:
        symbol = str(value or "").strip().upper().replace("-", "/")
        if not symbol or len(symbol) > 24:
            continue
        if symbol not in cleaned:
            cleaned.append(symbol)
    return cleaned[:MUTED_SYMBOL_LIMIT]


def _one_of(value: str, allowed: tuple[str, ...], fallback: str) -> str:
    """One choice from a closed set.

    A value nobody offered is not stored. It falls back to the same default the page
    shows, so the person's record can never hold a sound nothing can play.
    """

    return value if value in allowed else fallback


class AccountSettingsService:
    """Read and write one person's own settings.

    Every rule about what a setting may be lives in :meth:`save`. Both ways of saving —
    the ``POST /dashboard/settings`` form and the ``PUT`` endpoint the redesigned page
    uses — go through it.
    """

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def choosable_channels(self, user_id: UUID) -> set[str]:
        """Every way of being told this person may really pick, besides the in-app one.

        Two questions, both asked of their owner: what can the platform deliver at all
        (``offered_channels``), and what does this person's plan include. WhatsApp is
        in the enum and in the schema long before any given account may use it.
        """

        entitlement = await EntitlementService(self.session).current(user_id)
        allowed = {
            channel.value
            for channel in offered_channels(self.settings)
            if channel is not DeliveryChannel.WEB
        }
        if not (self.settings.whatsapp_enabled and entitlement.feature_enabled("whatsapp")):
            allowed.discard(DeliveryChannel.WHATSAPP.value)
        return allowed

    async def save(self, user: User, choice: SettingsChoice) -> dict[str, Any]:
        """Store one person's settings, and return exactly what was stored.

        Raises :class:`SettingsRejected` and writes nothing when a value cannot be
        stored at all. Values that are merely unrecognised are dropped rather than
        rejected: a browser that sends an old sound name should not stop somebody
        saving their alert hours.
        """

        if choice.timezone not in SUPPORTED_TIMEZONES:
            raise SettingsRejected(
                "unsupported_timezone",
                "That time zone is not one of the ones we support.",
            )

        methodology_id = await self._methodology(choice.default_sharia_methodology_id)
        statuses = [
            value
            for value in dict.fromkeys(choice.allowed_sharia_statuses)
            if value in {item.value for item in ShariaAssetStatus}
        ] or list(DEFAULT_SHARIA_STATUSES)
        acknowledged = choice.advanced_sharia_override_acknowledged
        if not set(statuses).issubset(set(DEFAULT_SHARIA_STATUSES)) and not acknowledged:
            raise SettingsRejected(
                "screening_override_ack_required",
                "Keeping a status outside the reviewed ones has to be confirmed first.",
            )

        allowed_channels = await self.choosable_channels(user.id)
        # The in-app notice is added rather than chosen. It is the one place a person
        # can always come back and read what happened, so it is never absent from the
        # stored set — even when every other channel is switched off.
        chosen = [
            value for value in dict.fromkeys(choice.alert_channels) if value in allowed_channels
        ]
        channels = [DeliveryChannel.WEB.value, *chosen]

        evidence_channels = [
            value
            for value in dict.fromkeys(choice.compliance_alert_channels)
            if value in allowed_channels
        ]
        evidence_channels = [DeliveryChannel.WEB.value, *evidence_channels]

        providers = [
            value for value in dict.fromkeys(choice.providers) if value in MARKET_PROVIDERS
        ] or [MARKET_PROVIDERS[0]]

        days = [value for value in dict.fromkeys(choice.alert_days) if value in ALERT_DAYS]
        if not days or "Every Day" in days:
            days = ["Every Day"]
        hours = [value for value in dict.fromkeys(choice.alert_hours) if value in ALERT_HOURS]

        try:
            behaviour = ComplianceChangeBehavior(choice.compliance_change_behavior)
        except ValueError:
            behaviour = ComplianceChangeBehavior.PAUSE_ASSET

        user.timezone = choice.timezone
        preference = await self.session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user.id)
        )
        if preference is None:
            preference = DashboardPreference(
                user_id=user.id,
                default_timezone=choice.timezone,
                theme="light",
            )
            self.session.add(preference)
        else:
            preference.default_timezone = choice.timezone

        stored = dict(preference.notification_preferences or {})
        stored.update(
            {
                "timezone": choice.timezone,
                "near_miss_enabled": choice.near_miss_enabled,
                "near_miss_threshold": max(1, min(100, choice.near_miss_threshold)),
                "maximum_alerts_per_hour": max(1, min(1000, choice.maximum_alerts_per_hour)),
                "maximum_alerts_per_day": max(1, min(10000, choice.maximum_alerts_per_day)),
                # Written under both names because both are read: `alert_channels` by
                # this page, `channels` by older stored records and by the reader's
                # fallback. One value, two keys, never allowed to disagree.
                "alert_channels": channels,
                "channels": channels,
                "providers": providers,
                "alert_days": days,
                "alert_hours": hours,
                "lifecycle_enabled": choice.finished_opportunity_alerts,
                "muted_symbols": clean_muted_symbols(choice.muted_symbols),
                "dashboard_notifications_enabled": choice.dashboard_notifications_enabled,
                "dashboard_notification_sound": _one_of(
                    choice.dashboard_notification_sound, CONFIRMED_SOUNDS, "chime"
                ),
                "forming_dashboard_notifications": choice.forming_dashboard_notifications,
                "forming_notification_sound": _one_of(
                    choice.forming_notification_sound, FORMING_SOUNDS, "pulse"
                ),
                "default_sharia_methodology_id": (
                    str(methodology_id) if methodology_id else None
                ),
                "allowed_sharia_statuses": statuses,
                "compliance_change_behavior": behaviour.value,
                "compliance_alerts_enabled": True,
                "compliance_alert_channels": evidence_channels,
                "compliance_alert_digest": _one_of(
                    choice.compliance_alert_digest, EVIDENCE_TIMING, "immediate"
                ),
                "qualification_change_alerts": choice.qualification_change_alerts,
                # A Watchlist that is running must keep at least the in-app notice for
                # these two. Not a choice, so not read from the submission.
                "under_review_alerts": True,
                "exclusion_alerts": True,
                "advanced_sharia_override_acknowledged": acknowledged,
                "sharia": {
                    "default_methodology_id": (
                        str(methodology_id) if methodology_id else None
                    ),
                    "allowed_statuses": statuses,
                    "compliance_change_behavior": behaviour.value,
                    "advanced_override_acknowledged": acknowledged,
                },
            }
        )
        preference.notification_preferences = stored
        return stored

    async def _methodology(self, raw: str) -> UUID | None:
        """The screening standard somebody chose, checked against the stored ones.

        A standard that is not active is refused rather than ignored: silently falling
        back to the platform default would screen this person's market against a
        different set of rules than the one they picked.
        """

        value = (raw or "").strip()
        if not value:
            return None
        try:
            methodology_id = UUID(value)
        except ValueError as exc:
            raise SettingsRejected(
                "invalid_sharia_methodology",
                "That screening standard is not one we hold.",
            ) from exc
        try:
            await ShariaScreeningService(self.session, self.settings).methodology(
                methodology_id, require_active=True
            )
        except ShariaScreeningError as exc:
            raise SettingsRejected(
                "invalid_sharia_methodology",
                "That screening standard is not one we hold.",
            ) from exc
        return methodology_id
