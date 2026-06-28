from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.platforms import Platform
from ai_market_monitor.core.security import (
    DashboardLinkTokenService,
    InvalidContinuationToken,
    opaque_token,
    token_digest,
)
from ai_market_monitor.db.models import TelegramDashboardLink, User
from ai_market_monitor.services.web_auth import WebAuthService


class DashboardLinkError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DashboardLinkService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def create(
        self,
        *,
        user_id: UUID,
        source_platform: Platform,
        source_subject: str,
        target_path: str,
        ttl_minutes: int = 15,
    ) -> str:
        user = await self.session.get(User, user_id)
        if user is None:
            raise DashboardLinkError("user_missing", "User was not found.")
        raw = opaque_token()
        path = target_path if target_path.startswith("/") else f"/{target_path}"
        link = TelegramDashboardLink(
            user_id=user_id,
            telegram_user_id=f"{source_platform.value}:{source_subject}",
            token_digest=token_digest(raw),
            target_path=path,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link)
        await self.session.flush()
        signed = DashboardLinkTokenService(self.settings).issue(link.id, raw)
        base = str(self.settings.public_base_url).rstrip("/")
        return f"{base}/dashboard/link/{signed}"

    async def consume(self, token: str) -> tuple[User, str, str]:
        try:
            payload = DashboardLinkTokenService(self.settings).decode(token)
            link_id = UUID(payload["link_id"])
            digest = token_digest(payload["token"])
        except (InvalidContinuationToken, KeyError, ValueError) as exc:
            raise DashboardLinkError(
                "dashboard_link_invalid",
                "Dashboard link is invalid or expired.",
            ) from exc
        link = await self.session.get(TelegramDashboardLink, link_id)
        now = datetime.now(UTC)
        if link is None or link.token_digest != digest:
            raise DashboardLinkError("dashboard_link_invalid", "Dashboard link is invalid.")
        expires_at = _as_aware(link.expires_at)
        if link.consumed_at is not None:
            raise DashboardLinkError("dashboard_link_used", "Dashboard link has already been used.")
        if expires_at <= now:
            raise DashboardLinkError("dashboard_link_expired", "Dashboard link has expired.")
        user = await self.session.get(User, link.user_id)
        if user is None:
            raise DashboardLinkError("user_missing", "The linked user no longer exists.")
        link.consumed_at = now
        cookie = await WebAuthService(self.session, self.settings).create_session(user)
        return user, link.target_path, cookie


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
