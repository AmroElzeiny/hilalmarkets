from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import AccountBan, User
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.schemas.onboarding import IdentityInput
from ai_market_monitor.services.account_admin import account_identifier_hash
from ai_market_monitor.services.identity import IdentityConflictError, IdentityService


async def test_duplicate_provider_identity_reuses_user(test_context):
    identity = IdentityInput(
        provider="telegram",
        provider_subject="stable-telegram-id",
        display_identifier="changeable_username",
    )
    async with test_context["session_factory"]() as session:
        service = IdentityService(session, test_context["settings"])
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


async def test_banned_email_cannot_create_an_alternate_provider_identity(test_context):
    email = "blocked-alternate-provider@example.com"
    async with test_context["session_factory"]() as session:
        administrator = User(display_name="Administrator", role=UserRole.ADMIN)
        session.add(administrator)
        await session.flush()
        session.add(
            AccountBan(
                identifier_hash=account_identifier_hash(
                    test_context["settings"],
                    email,
                ),
                banned_user_id=None,
                banned_by_user_id=administrator.id,
                reason="Blocked identity must remain blocked across providers.",
                is_active=True,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

        service = IdentityService(session, test_context["settings"])
        with pytest.raises(IdentityConflictError, match="Your profile is banned"):
            await service.resolve_or_create(
                IdentityInput(
                    provider="telegram",
                    provider_subject="new-telegram-identity",
                    email=email,
                    display_identifier="@blocked",
                    verified=True,
                ),
                trusted_provider_assertion=True,
            )

        assert await session.scalar(select(func.count(User.id))) == 1
