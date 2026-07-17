from sqlalchemy import func, select

from ai_market_monitor.db.models import User
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
