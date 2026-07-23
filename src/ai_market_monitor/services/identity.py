from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserStatus
from ai_market_monitor.schemas.onboarding import IdentityInput
from ai_market_monitor.services.account_admin import identifier_is_banned


class IdentityConflictError(ValueError):
    pass


def normalize_identifier(identity: IdentityInput) -> str | None:
    if identity.provider == IdentityProvider.EMAIL and identity.email:
        return str(identity.email).strip().casefold()
    return None


class IdentityService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def resolve_or_create(
        self, identity: IdentityInput, *, trusted_provider_assertion: bool = False
    ) -> tuple[User, UserIdentity, bool]:
        normalized = normalize_identifier(identity)
        asserted_email = (
            str(identity.email).strip().casefold() if identity.email is not None else None
        )
        if asserted_email and await identifier_is_banned(
            self.session,
            self.settings,
            asserted_email,
        ):
            raise IdentityConflictError("Your profile is banned.")
        identity_matches = [
            and_(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_subject == identity.provider_subject,
            )
        ]
        if normalized:
            identity_matches.append(
                and_(
                    UserIdentity.provider == identity.provider,
                    UserIdentity.normalized_identifier == normalized,
                )
            )
        query = select(UserIdentity).where(or_(*identity_matches))
        matches = (await self.session.scalars(query)).all()
        if matches:
            user_ids = {match.user_id for match in matches}
            if len(user_ids) != 1:
                raise IdentityConflictError("Identity attributes resolve to different accounts")
            existing = matches[0]
            existing_user = await self.session.get(User, existing.user_id)
            if existing_user is None:
                raise IdentityConflictError("Identity references a missing account")
            if existing_user.status == UserStatus.SUSPENDED:
                raise IdentityConflictError("Your profile is banned.")
            if existing_user.status != UserStatus.ACTIVE:
                raise IdentityConflictError("This profile is not available.")
            existing_user.last_seen_at = datetime.now(UTC)
            return existing_user, existing, False

        # Cross-provider linking is allowed only for a verified assertion from a trusted OAuth or
        # magic-link adapter. Public request fields alone are never sufficient.
        user: User | None = None
        if trusted_provider_assertion and identity.verified and identity.email:
            normalized_email = str(identity.email).strip().casefold()
            email_identity = await self.session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == normalized_email,
                    UserIdentity.is_verified.is_(True),
                )
            )
            if email_identity:
                user = await self.session.get(User, email_identity.user_id)
                if user is None:
                    raise IdentityConflictError("Identity references a missing account")
                if user.status == UserStatus.SUSPENDED:
                    raise IdentityConflictError("Your profile is banned.")
                if user.status != UserStatus.ACTIVE:
                    raise IdentityConflictError("This profile is not available.")

        created = user is None
        if user is None:
            user = User(display_name=identity.display_name, last_seen_at=datetime.now(UTC))
            self.session.add(user)
            await self.session.flush()

        linked_identity = UserIdentity(
            user_id=user.id,
            provider=identity.provider,
            provider_subject=identity.provider_subject,
            normalized_identifier=normalized,
            display_identifier=identity.display_identifier
            or (str(identity.email) if identity.email else None),
            is_verified=identity.verified and trusted_provider_assertion,
            is_primary=created,
            verified_at=(
                datetime.now(UTC) if identity.verified and trusted_provider_assertion else None
            ),
            profile_data=identity.profile_data,
        )
        self.session.add(linked_identity)
        await self.session.flush()
        return user, linked_identity, created

    async def link_to_user(
        self,
        user: User,
        identity: IdentityInput,
        *,
        trusted_provider_assertion: bool,
    ) -> UserIdentity:
        if user.status == UserStatus.SUSPENDED:
            raise IdentityConflictError("Your profile is banned.")
        if user.status != UserStatus.ACTIVE:
            raise IdentityConflictError("This profile is not available.")
        existing = await self.session.scalar(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_subject == identity.provider_subject,
            )
        )
        if existing:
            if existing.user_id != user.id:
                raise IdentityConflictError("That identity belongs to another account")
            return existing
        if not trusted_provider_assertion:
            raise IdentityConflictError("Identity must be verified before linking")
        normalized = normalize_identifier(identity)
        asserted_email = (
            str(identity.email).strip().casefold() if identity.email is not None else None
        )
        if asserted_email and await identifier_is_banned(
            self.session,
            self.settings,
            asserted_email,
        ):
            raise IdentityConflictError("Your profile is banned.")
        linked = UserIdentity(
            user_id=user.id,
            provider=identity.provider,
            provider_subject=identity.provider_subject,
            normalized_identifier=normalized,
            display_identifier=identity.display_identifier,
            is_verified=identity.verified,
            verified_at=datetime.now(UTC) if identity.verified else None,
            profile_data=identity.profile_data,
        )
        self.session.add(linked)
        await self.session.flush()
        return linked
