from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.platforms import Platform
from ai_market_monitor.core.security import (
    DashboardLinkTokenService,
    InvalidContinuationToken,
    opaque_token,
    token_digest,
)
from ai_market_monitor.db.models import (
    AuditEvent,
    TelegramConnection,
    TelegramConversationState,
    TelegramDashboardLink,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import ConnectionStatus, IdentityProvider


class TelegramAccountLinkError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TelegramAccountLinkService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def create(
        self,
        *,
        user_id: UUID,
        telegram_user_id: str,
        target: str,
        ttl_minutes: int = 30,
    ) -> str:
        if target not in {"signup", "signin"}:
            raise TelegramAccountLinkError("invalid_target", "Unsupported account link target.")
        if await self.session.get(User, user_id) is None:
            raise TelegramAccountLinkError("user_missing", "User was not found.")
        raw = opaque_token()
        link = TelegramDashboardLink(
            user_id=user_id,
            telegram_user_id=f"{Platform.TELEGRAM.value}:{telegram_user_id}",
            token_digest=token_digest(raw),
            target_path=f"/{target}",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link)
        await self.session.flush()
        signed = DashboardLinkTokenService(self.settings).issue(link.id, raw)
        base = str(self.settings.public_base_url).rstrip("/")
        return f"{base}/{target}?telegram_link={signed}"

    async def create_dashboard_start_link(
        self,
        *,
        user_id: UUID,
        ttl_minutes: int = 30,
    ) -> str:
        if await self.session.get(User, user_id) is None:
            raise TelegramAccountLinkError("user_missing", "User was not found.")
        bot_username = (
            self.settings.telegram_bot_username.lstrip("@").strip()
            if self.settings.telegram_bot_username
            else ""
        )
        if not bot_username:
            raise TelegramAccountLinkError(
                "telegram_bot_missing",
                "Telegram bot username is not configured.",
            )
        raw = opaque_token()
        link = TelegramDashboardLink(
            user_id=user_id,
            telegram_user_id="pending",
            token_digest=token_digest(raw),
            target_path="/dashboard/integrations",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link)
        await self.session.flush()
        return f"https://t.me/{bot_username}?start=link_{raw}"

    async def pending_dashboard_start_link(
        self,
        raw_token: str,
    ) -> tuple[User, str | None]:
        link = await self._pending_link_from_raw_token(raw_token)
        user = await self.session.get(User, link.user_id)
        if user is None:
            raise TelegramAccountLinkError("user_missing", "The linked user no longer exists.")
        return user, await self._primary_email(user.id)

    async def complete_dashboard_start_link(
        self,
        *,
        raw_token: str,
        telegram_user_id: str,
        chat_id: str,
        username: str | None,
    ) -> tuple[User, str | None]:
        link = await self._pending_link_from_raw_token(raw_token)
        user = await self.session.get(User, link.user_id)
        if user is None:
            raise TelegramAccountLinkError("user_missing", "The linked user no longer exists.")
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == telegram_user_id,
            )
        )
        if identity is not None and identity.user_id != user.id:
            raise TelegramAccountLinkError(
                "telegram_already_linked",
                "This Telegram account is already linked to another dashboard user.",
            )
        if identity is None:
            identity = UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.TELEGRAM,
                provider_subject=telegram_user_id,
                display_identifier=username or telegram_user_id,
                is_verified=True,
                is_primary=False,
                verified_at=datetime.now(UTC),
                profile_data={"username": username} if username else {},
            )
            self.session.add(identity)
        else:
            identity.user_id = user.id
            identity.display_identifier = username or identity.display_identifier
            identity.is_verified = True
            identity.verified_at = identity.verified_at or datetime.now(UTC)
            identity.profile_data = {
                **(identity.profile_data or {}),
                **({"username": username} if username else {}),
            }
        connection = await self.session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.telegram_user_id == telegram_user_id
            )
        )
        if connection is not None and connection.user_id != user.id:
            raise TelegramAccountLinkError(
                "telegram_already_linked",
                "This Telegram account is already connected to another dashboard user.",
            )
        if connection is None:
            connection = TelegramConnection(
                user_id=user.id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                username=username,
                status=ConnectionStatus.ACTIVE,
                connected_at=datetime.now(UTC),
            )
            self.session.add(connection)
        else:
            connection.user_id = user.id
            connection.chat_id = chat_id
            connection.username = username
            connection.status = ConnectionStatus.ACTIVE
            connection.connected_at = connection.connected_at or datetime.now(UTC)
        conversation = await self.session.scalar(
            select(TelegramConversationState).where(
                TelegramConversationState.telegram_user_id == telegram_user_id
            )
        )
        if conversation is not None:
            state = dict(conversation.state_data or {})
            state["dashboard_linked_at"] = datetime.now(UTC).isoformat()
            conversation.user_id = user.id
            conversation.state_data = state
        link.telegram_user_id = f"{Platform.TELEGRAM.value}:{telegram_user_id}"
        link.consumed_at = datetime.now(UTC)
        self.session.add(
            AuditEvent(
                actor_user_id=user.id,
                actor_type="dashboard_user",
                action="telegram.account_linked",
                target_type="telegram_connection",
                target_id=telegram_user_id,
                metadata_redacted={"source_link_id": str(link.id), "source": "telegram_start"},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return user, await self._primary_email(user.id)

    async def complete(self, token: str, *, user: User) -> str:
        link = await self._link_from_token(token)
        telegram_user_id = link.telegram_user_id.split(":", 1)[-1]
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == telegram_user_id,
            )
        )
        if identity is None:
            identity = UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.TELEGRAM,
                provider_subject=telegram_user_id,
                display_identifier=telegram_user_id,
                is_verified=True,
                is_primary=False,
                verified_at=datetime.now(UTC),
                profile_data={},
            )
            self.session.add(identity)
        else:
            identity.user_id = user.id
            identity.is_verified = True
            identity.verified_at = identity.verified_at or datetime.now(UTC)
        connection = await self.session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.telegram_user_id == telegram_user_id
            )
        )
        if connection is not None:
            connection.user_id = user.id
            connection.status = ConnectionStatus.ACTIVE
        conversation = await self.session.scalar(
            select(TelegramConversationState).where(
                TelegramConversationState.telegram_user_id == telegram_user_id
            )
        )
        if conversation is not None:
            state = dict(conversation.state_data or {})
            state["dashboard_linked_at"] = datetime.now(UTC).isoformat()
            conversation.user_id = user.id
            conversation.state_data = state
        link.consumed_at = datetime.now(UTC)
        self.session.add(
            AuditEvent(
                actor_user_id=user.id,
                actor_type="dashboard_user",
                action="telegram.account_linked",
                target_type="telegram_connection",
                target_id=telegram_user_id,
                metadata_redacted={"source_link_id": str(link.id)},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return telegram_user_id

    async def _link_from_token(self, token: str) -> TelegramDashboardLink:
        try:
            payload = DashboardLinkTokenService(self.settings).decode(token)
            link_id = UUID(payload["link_id"])
            digest = token_digest(payload["token"])
        except (InvalidContinuationToken, KeyError, ValueError) as exc:
            raise TelegramAccountLinkError(
                "telegram_link_invalid",
                "Telegram account link is invalid or expired.",
            ) from exc
        link = await self.session.get(TelegramDashboardLink, link_id)
        now = datetime.now(UTC)
        if link is None or link.token_digest != digest:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        if link.consumed_at is not None:
            raise TelegramAccountLinkError("telegram_link_used", "Telegram link was already used.")
        expires_at = (
            link.expires_at.replace(tzinfo=UTC)
            if link.expires_at.tzinfo is None
            else link.expires_at
        )
        if expires_at <= now:
            raise TelegramAccountLinkError("telegram_link_expired", "Telegram link has expired.")
        return link

    async def _pending_link_from_raw_token(self, raw_token: str) -> TelegramDashboardLink:
        if not raw_token or len(raw_token) > 128:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        link = await self.session.scalar(
            select(TelegramDashboardLink).where(
                TelegramDashboardLink.token_digest == token_digest(raw_token)
            )
        )
        now = datetime.now(UTC)
        if link is None:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        if link.consumed_at is not None:
            raise TelegramAccountLinkError("telegram_link_used", "Telegram link was already used.")
        expires_at = (
            link.expires_at.replace(tzinfo=UTC)
            if link.expires_at.tzinfo is None
            else link.expires_at
        )
        if expires_at <= now:
            raise TelegramAccountLinkError("telegram_link_expired", "Telegram link has expired.")
        return link

    async def _primary_email(self, user_id: UUID) -> str | None:
        identity = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            .limit(1)
        )
        if identity is None:
            return None
        return identity.display_identifier or identity.normalized_identifier
