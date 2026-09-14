"""Putting a Telegram account on a Hilal Markets account — one rule, two doors.

A person connects Telegram through one of two doors. They press **Connect Telegram** on
the Connections page and confirm in the bot, or they press a button in the bot and finish
on the sign-up or sign-in form. Until now each door decided for itself who was allowed to
hold a Telegram account: the dashboard path moved a Telegram from *any* account to any
other without a word, and the bot path refused to move it from *any* account, including
the throwaway one the bot itself had made for that same person. Two owners of one rule is
how a person was told their own Telegram "already belongs to another account", and then
kept being asked to confirm it.

:meth:`TelegramAccountLinkService.attach` is the rule now, and both doors ask it. The
newest confirmed link always wins:

* **(a) move** — the Telegram is held by another account: the shell the bot creates the
  moment somebody presses Start, or a different account with a sign-in of its own. Both
  doors prove the person controls this Telegram — the confirm is pressed inside that
  chat, and the sign-in link was issued by the bot to that chat — so the identity, the
  connection and the conversation go to the account being linked. Everything else the
  other account holds stays where it is, and an audit event names both accounts. Alerts
  already queued for that chat by the other account are never sent: delivery checks
  the chat still belongs to the alert's owner.
* **(b) replace** — the account being signed into already holds a different Telegram. The
  person was told the prompt would do this, so the old one is taken off first and the new
  one attached, in one transaction and with no uniqueness error.
* **(c) already there** — the same Telegram is already on this same account: success, and
  no second row of anything.

Every row this rule reads is read with ``with_for_update()``. SQLite ignores it and
PostgreSQL does not, so the same code that is testable offline is the code that serialises
two taps that arrive at the same moment in production.

Nothing in this module decides what a person is *told*: :class:`TelegramAccountLinkError`
carries a code and one plain sentence, and the caller — the bot, or the sign-in page —
puts it in front of the person.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import CONNECTIONS_PATH
from ai_market_monitor.core.platforms import Platform
from ai_market_monitor.core.security import (
    DashboardLinkTokenService,
    InvalidContinuationToken,
    opaque_token,
    token_digest,
)
from ai_market_monitor.db.models import (
    AuditEvent,
    DisclaimerAcceptance,
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


@dataclass(frozen=True, slots=True)
class TelegramLinkOffer:
    """What a ``/start link_...`` is offering, before the person has answered."""

    user: User
    email: str | None
    #: A Telegram already connected to that account, which confirming would replace.
    replaces: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramLinkCompletion:
    """The answer to a confirmed ``/start link_...``."""

    user: User
    email: str | None
    #: True when this Telegram was already on this account, so nothing had to change.
    already_connected: bool = False


@dataclass(frozen=True, slots=True)
class TelegramOwnership:
    """What :meth:`TelegramAccountLinkService.attach` did."""

    identity: UserIdentity
    connection: TelegramConnection | None
    #: Accounts that gave this Telegram up, if any.
    moved_from: tuple[UUID, ...] = field(default_factory=tuple)
    #: The Telegram that was taken off the account, if this confirm replaced one.
    replaced: str | None = None
    already_connected: bool = False


class TelegramAccountLinkService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    # ------------------------------------------------------------------
    # Doors into the rule
    # ------------------------------------------------------------------

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
            # The page this link came from. It was the old Integrations address, which
            # only survives as a redirect to this one.
            target_path=CONNECTIONS_PATH,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link)
        await self.session.flush()
        return f"https://t.me/{bot_username}?start=link_{raw}"

    async def pending_dashboard_start_link(
        self,
        raw_token: str,
        *,
        telegram_user_id: str | None = None,
    ) -> TelegramLinkOffer:
        """What a ``/start link_...`` is offering, before the person has answered.

        ``telegram_user_id`` is the chat that asked, when it is already known: the offer
        then leaves that account alone and only warns about a *different* Telegram the
        dashboard account holds.
        """

        link = await self._pending_link_from_raw_token(raw_token)
        user = await self.session.get(User, link.user_id)
        if user is None:
            raise TelegramAccountLinkError("user_missing", "The linked user no longer exists.")
        email = await self._primary_email(user.id)
        previous = await self._replaced_connection(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
        )
        return TelegramLinkOffer(
            user=user,
            email=email,
            replaces=_connection_label(previous),
        )

    async def complete_dashboard_start_link(
        self,
        *,
        raw_token: str,
        telegram_user_id: str,
        chat_id: str,
        username: str | None,
    ) -> TelegramLinkCompletion:
        """The bot's door: the person confirmed a ``/start link_...`` in Telegram."""

        link = await self._pending_link_from_raw_token(
            raw_token,
            for_update=True,
            refuse_spent=False,
        )
        user = await self.session.get(User, link.user_id)
        if user is None:
            raise TelegramAccountLinkError("user_missing", "The linked user no longer exists.")
        if link.consumed_at is not None:
            # Read *after* the lock was taken. When the link was already spent by this
            # Telegram onto this account, the answer is the success the first tap already
            # got: a second row, a second audit event or an exception would all be wrong.
            if await self._spent_by_this_telegram(link=link, telegram_user_id=telegram_user_id):
                return TelegramLinkCompletion(
                    user=user,
                    email=await self._primary_email(user.id),
                    already_connected=True,
                )
            raise TelegramAccountLinkError("telegram_link_used", "Telegram link was already used.")
        ownership = await self.attach(
            target=user,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=username,
        )
        link.telegram_user_id = f"{Platform.TELEGRAM.value}:{telegram_user_id}"
        link.consumed_at = datetime.now(UTC)
        if not ownership.already_connected:
            self._record_linked(
                user=user,
                telegram_user_id=telegram_user_id,
                link=link,
                source="telegram_start",
            )
        await self.session.flush()
        return TelegramLinkCompletion(
            user=user,
            email=await self._primary_email(user.id),
            already_connected=ownership.already_connected,
        )

    async def complete(self, token: str, *, user: User) -> str:
        """The dashboard's door: a sign-up or sign-in finished with a bot-issued link."""

        link = await self._link_from_token(token, for_update=True, refuse_spent=False)
        telegram_user_id = link.telegram_user_id.split(":", 1)[-1]
        if link.consumed_at is not None:
            # The same answer for the same reason: a form posted twice must not connect
            # twice, report a failure, or spend a second audit event.
            if await self._spent_by_this_telegram(link=link, telegram_user_id=telegram_user_id):
                return telegram_user_id
            raise TelegramAccountLinkError("telegram_link_used", "Telegram link was already used.")
        ownership = await self.attach(
            target=user,
            telegram_user_id=telegram_user_id,
            chat_id=None,
            username=None,
        )
        if not ownership.already_connected:
            self._record_linked(
                user=user,
                telegram_user_id=telegram_user_id,
                link=link,
                source="dashboard_form",
            )
        link.consumed_at = datetime.now(UTC)
        await self.session.flush()
        return telegram_user_id

    async def disconnect_dashboard(self, *, user_id: UUID) -> str | None:
        """Remove the Telegram rows owned by one signed-in account.

        Both dashboard entry points call this method. Keeping deletion here beside the
        attach rule prevents the JSON endpoint and the native form fallback from
        disagreeing about what "unlinked" means.
        """

        connection = await self.session.scalar(
            select(TelegramConnection)
            .where(TelegramConnection.user_id == user_id)
            .with_for_update()
        )
        if connection is None:
            return None

        telegram_user_id = connection.telegram_user_id
        await self._remove_telegram(
            connection,
            owner_id=user_id,
            keep_subject=None,
            action="telegram.disconnected",
            metadata={"source": "dashboard"},
        )
        await self.session.flush()
        return telegram_user_id

    # ------------------------------------------------------------------
    # The rule
    # ------------------------------------------------------------------

    async def attach(
        self,
        *,
        target: User,
        telegram_user_id: str,
        chat_id: str | None,
        username: str | None,
    ) -> TelegramOwnership:
        """Put this Telegram account on ``target``. The only place that decision is made.

        Never refuses because another account holds the Telegram: the newest confirmed
        link takes it, and the Telegram this account held before is taken off.
        """

        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == telegram_user_id,
            )
            .with_for_update()
        )
        connection = await self.session.scalar(
            select(TelegramConnection)
            .where(TelegramConnection.telegram_user_id == telegram_user_id)
            .with_for_update()
        )

        holders = {
            holder
            for holder in (
                identity.user_id if identity is not None else None,
                connection.user_id if connection is not None else None,
            )
            if holder is not None and holder != target.id
        }
        # Read before anything is written: this Telegram was already on this account, so
        # the confirm changes no rows and must not be reported as a second connection.
        was_already_connected = (
            identity is not None
            and identity.user_id == target.id
            and connection is not None
            and connection.user_id == target.id
        )
        stale_chat_owner: TelegramConnection | None = None
        if chat_id:
            stale_chat_owner = await self.session.scalar(
                select(TelegramConnection)
                .where(
                    TelegramConnection.chat_id == chat_id,
                    TelegramConnection.telegram_user_id != telegram_user_id,
                )
                .with_for_update()
            )
            if stale_chat_owner is not None:
                holders.add(stale_chat_owner.user_id)
        # (a) Whoever held this Telegram gives it up, whether or not that account has a
        # sign-in of its own. The newest confirmed link wins; see the module docstring.
        moved_from = tuple(sorted(holders, key=str))

        # (b) The account being signed into may hold a different Telegram. It was promised
        # in the prompt; taking it off first is what makes the attach fit the constraints.
        previous = await self._replaced_connection(
            user_id=target.id, telegram_user_id=telegram_user_id
        )
        replaced: str | None = None
        if previous is not None:
            replaced = previous.telegram_user_id
            await self._take_off(
                previous,
                target=target,
                incoming_telegram_user_id=telegram_user_id,
            )
            # The old Telegram's chat id may be the one about to be written (the same
            # person, a new Telegram account): the row is gone, so the value is free.
            await self.session.flush()
        if stale_chat_owner is not None:
            # A shell account holding this chat under a different Telegram id can only be
            # stale data — a Telegram chat belongs to the one account speaking in it. The
            # chat is the incoming account's; the placeholder gives it up.
            stale_chat_owner.chat_id = None
            await self.session.flush()

        if identity is None:
            identity = UserIdentity(
                user_id=target.id,
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
            if identity.user_id != target.id:
                # An acceptance the previous holder gave through this sign-in stays theirs.
                # Its pointer is cleared rather than left naming a row that now belongs to
                # another account; the record keeps its own copy of which sign-in gave it.
                await self.session.execute(
                    update(DisclaimerAcceptance)
                    .where(
                        DisclaimerAcceptance.identity_id == identity.id,
                        DisclaimerAcceptance.user_id != target.id,
                    )
                    .values(identity_id=None)
                )
            identity.user_id = target.id
            identity.display_identifier = username or identity.display_identifier
            identity.is_verified = True
            identity.verified_at = identity.verified_at or datetime.now(UTC)
            identity.profile_data = {
                **(identity.profile_data or {}),
                **({"username": username} if username else {}),
            }

        if connection is None:
            # The dashboard's door has no chat to write: the person is not speaking to the
            # bot right now. The row is made without one and the bot fills it in the first
            # time the person messages, rather than an alert being promised a chat nobody
            # named.
            connection = TelegramConnection(
                user_id=target.id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                username=username,
                status=ConnectionStatus.ACTIVE,
                connected_at=datetime.now(UTC),
            )
            self.session.add(connection)
        else:
            connection.user_id = target.id
            if chat_id is not None:
                connection.chat_id = chat_id
            if username is not None:
                connection.username = username
            connection.status = ConnectionStatus.ACTIVE
            connection.connected_at = connection.connected_at or datetime.now(UTC)

        conversation = await self.session.scalar(
            select(TelegramConversationState)
            .where(TelegramConversationState.telegram_user_id == telegram_user_id)
            .with_for_update()
        )
        if conversation is not None:
            state = dict(conversation.state_data or {})
            state["dashboard_linked_at"] = datetime.now(UTC).isoformat()
            conversation.user_id = target.id
            conversation.state_data = state

        if moved_from:
            # (a) The shell gave the Telegram up. Naming both accounts is the point of the
            # event: without it, a moved identity looks like an identity that appeared.
            self.session.add(
                AuditEvent(
                    actor_user_id=target.id,
                    actor_type="dashboard_user",
                    action="telegram.identity_moved",
                    target_type="telegram_identity",
                    target_id=telegram_user_id,
                    metadata_redacted={
                        "from_user_ids": [str(item) for item in moved_from],
                        "to_user_id": str(target.id),
                    },
                    created_at=datetime.now(UTC),
                )
            )
        await self.session.flush()
        return TelegramOwnership(
            identity=identity,
            connection=connection,
            moved_from=moved_from,
            replaced=replaced,
            already_connected=was_already_connected,
        )

    async def account_holding(self, telegram_user_id: str) -> UUID | None:
        """Which account, if any, this Telegram is on right now.

        The bot asks this when a tap arrives for a request it has already finished, so it
        can tell "already connected" from "that link is gone" instead of guessing.
        """

        return await self.session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == telegram_user_id,
            )
        )

    async def _replaced_connection(
        self, *, user_id: UUID, telegram_user_id: str | None
    ) -> TelegramConnection | None:
        """The other Telegram this account holds, which a confirm would replace."""

        clauses = [TelegramConnection.user_id == user_id]
        if telegram_user_id is not None:
            clauses.append(TelegramConnection.telegram_user_id != telegram_user_id)
        return await self.session.scalar(
            select(TelegramConnection).where(*clauses).with_for_update()
        )

    async def _take_off(
        self,
        connection: TelegramConnection,
        *,
        target: User,
        incoming_telegram_user_id: str,
    ) -> None:
        """Take a Telegram off an account, exactly as unlinking it from the page does.

        The connection, the sign-in row and the bot conversation all go: leaving any of
        them behind keeps a chat that is no longer connected acting as this account, and
        ``user_id`` is unique on the connection, so a half-removed row also blocks the
        next one from being written at all.
        """

        await self._remove_telegram(
            connection,
            owner_id=target.id,
            keep_subject=incoming_telegram_user_id,
            action="telegram.connection_replaced",
            metadata={"replaced_by": "telegram_start", "user_id": str(target.id)},
        )

    async def _remove_telegram(
        self,
        connection: TelegramConnection,
        *,
        owner_id: UUID,
        keep_subject: str | None,
        action: str,
        metadata: dict[str, str],
    ) -> None:
        """Take every Telegram trace off one account. Unlink and replace both use this.

        The connection, the bot conversation, and **every** Telegram sign-in the account
        holds go — not only the one matching this connection — so an older Telegram left
        behind by an earlier link cannot keep acting as the account. ``keep_subject`` is
        the Telegram being linked right now, when there is one.

        A risk-note acceptance given through a removed sign-in stays: the database clears
        its pointer and the record keeps its own copy of which sign-in gave it. Alerts
        already queued for the old chat are stopped when they are due to send, because
        the chat no longer belongs to the account.
        """

        telegram_user_id = connection.telegram_user_id
        clauses = [
            UserIdentity.user_id == owner_id,
            UserIdentity.provider == IdentityProvider.TELEGRAM,
        ]
        if keep_subject is not None:
            clauses.append(UserIdentity.provider_subject != keep_subject)
        identities = (await self.session.scalars(select(UserIdentity).where(*clauses))).all()
        conversations = (
            await self.session.scalars(
                select(TelegramConversationState).where(
                    TelegramConversationState.telegram_user_id == telegram_user_id
                )
            )
        ).all()
        for conversation in conversations:
            await self.session.delete(conversation)
        for identity in identities:
            await self.session.delete(identity)
        await self.session.delete(connection)
        self.session.add(
            AuditEvent(
                actor_user_id=owner_id,
                actor_type="dashboard_user",
                action=action,
                target_type="telegram_connection",
                target_id=telegram_user_id,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _record_linked(
        self,
        *,
        user: User,
        telegram_user_id: str,
        link: TelegramDashboardLink,
        source: str,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user.id,
                actor_type="dashboard_user",
                action="telegram.account_linked",
                target_type="telegram_connection",
                target_id=telegram_user_id,
                metadata_redacted={"source_link_id": str(link.id), "source": source},
                created_at=datetime.now(UTC),
            )
        )

    # ------------------------------------------------------------------
    # The link rows both doors read
    # ------------------------------------------------------------------

    async def _link_from_token(
        self,
        token: str,
        *,
        for_update: bool = False,
        refuse_spent: bool = True,
    ) -> TelegramDashboardLink:
        try:
            payload = DashboardLinkTokenService(self.settings).decode(token)
            link_id = UUID(payload["link_id"])
            digest = token_digest(payload["token"])
        except (InvalidContinuationToken, KeyError, ValueError) as exc:
            raise TelegramAccountLinkError(
                "telegram_link_invalid",
                "Telegram account link is invalid or expired.",
            ) from exc
        statement = select(TelegramDashboardLink).where(TelegramDashboardLink.id == link_id)
        if for_update:
            statement = statement.with_for_update()
        link = await self.session.scalar(statement)
        if link is None or link.token_digest != digest:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        if for_update:
            # The lock is held now, so what is read next is what the other writer left.
            await self.session.refresh(link)
        self._refuse_an_unusable_link(link, refuse_spent=refuse_spent)
        return link

    async def _pending_link_from_raw_token(
        self,
        raw_token: str,
        *,
        for_update: bool = False,
        refuse_spent: bool = True,
    ) -> TelegramDashboardLink:
        if not raw_token or len(raw_token) > 128:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        statement = select(TelegramDashboardLink).where(
            TelegramDashboardLink.token_digest == token_digest(raw_token)
        )
        if for_update:
            statement = statement.with_for_update()
        link = await self.session.scalar(statement)
        if link is None:
            raise TelegramAccountLinkError("telegram_link_invalid", "Telegram link is invalid.")
        if for_update:
            # The lock is held now, so what is read next is what the other writer left.
            await self.session.refresh(link)
        self._refuse_an_unusable_link(link, refuse_spent=refuse_spent)
        return link

    @staticmethod
    def _refuse_an_unusable_link(
        link: TelegramDashboardLink, *, refuse_spent: bool = True
    ) -> None:
        """Refuse a link that cannot be answered: spent, or out of its own lifetime.

        ``refuse_spent=False`` is for the doors that hold the row locked and decide the
        spent case themselves — a second tap of a link that already connected this person
        answers with the success the first tap got, not with an error.
        """

        if link.consumed_at is not None and refuse_spent:
            raise TelegramAccountLinkError("telegram_link_used", "Telegram link was already used.")
        expires_at = (
            link.expires_at.replace(tzinfo=UTC)
            if link.expires_at.tzinfo is None
            else link.expires_at
        )
        if link.consumed_at is None and expires_at <= datetime.now(UTC):
            raise TelegramAccountLinkError("telegram_link_expired", "Telegram link has expired.")

    async def _spent_by_this_telegram(
        self, *, link: TelegramDashboardLink, telegram_user_id: str
    ) -> bool:
        """Was the spent link spent by this Telegram, onto the account it names?

        Asked of the database rather than of anything held in memory, because the answer
        is what makes a double tap, a redelivered update or two taps that raced each other
        resolve to one connection instead of two decisions.
        """

        owner = await self.session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.TELEGRAM,
                UserIdentity.provider_subject == telegram_user_id,
            )
        )
        return owner is not None and owner == link.user_id

    async def _primary_email(self, user_id: UUID) -> str | None:
        identity = await self.session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
            .limit(1)
        )
        if identity is None:
            return None
        return identity.display_identifier or identity.normalized_identifier


def _connection_label(connection: TelegramConnection | None) -> str | None:
    """How to call the Telegram a person is being warned about, in the words they know."""

    if connection is None:
        return None
    if connection.username:
        return f"@{connection.username}"
    return connection.telegram_user_id
