from sqlalchemy import func, select

from ai_market_monitor.db.models import User, UserIdentity
from ai_market_monitor.schemas.onboarding import IdentityInput
from ai_market_monitor.services.identity import IdentityService


async def test_duplicate_provider_identity_reuses_user(test_context):
    identity = IdentityInput(
        provider="telegram",
        provider_subject="stable-telegram-id",
        display_identifier="changeable_username",
    )
    async with test_context["session_factory"]() as session:
        service = IdentityService(session)
        first_user, _, created = await service.resolve_or_create(identity)
        await session.commit()
        second_user, _, created_again = await service.resolve_or_create(
            identity.model_copy(update={"display_identifier": "new_username"})
        )
        await session.commit()
        assert created is True
        assert created_again is False
        assert second_user.id == first_user.id
        assert await session.scalar(select(func.count(User.id))) == 1


async def test_verified_discord_email_links_existing_email_user(test_context):
    async with test_context["session_factory"]() as session:
        service = IdentityService(session)
        email_user, _, _ = await service.resolve_or_create(
            IdentityInput(
                provider="email",
                provider_subject="person@example.com",
                email="person@example.com",
                verified=True,
            ),
            trusted_provider_assertion=True,
        )
        discord_user, discord_identity, created = await service.resolve_or_create(
            IdentityInput(
                provider="discord",
                provider_subject="discord-9988",
                email="PERSON@example.com",
                display_identifier="trader#1234",
                verified=True,
            ),
            trusted_provider_assertion=True,
        )
        await session.commit()
        assert created is False
        assert discord_user.id == email_user.id
        assert discord_identity.user_id == email_user.id
        assert await session.scalar(select(func.count(UserIdentity.id))) == 2
