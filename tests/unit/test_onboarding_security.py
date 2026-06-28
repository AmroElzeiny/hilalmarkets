import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.security import IdentityAssertionTokenService
from ai_market_monitor.schemas.onboarding import StartOnboardingRequest
from ai_market_monitor.services.onboarding import OnboardingError, OnboardingService


def production_settings() -> Settings:
    return Settings(
        app_env="production",
        app_secret_key="production-test-secret-with-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        disclaimer_version="test-2026-06",
    )


def start_request(assertion: str | None = None) -> StartOnboardingRequest:
    return StartOnboardingRequest.model_validate(
        {
            "identity": {
                "provider": "telegram",
                "provider_subject": "telegram-signed-123",
                "display_identifier": "verified_trader",
                "verified": True,
            },
            "entry_channel": "telegram",
            "identity_assertion": assertion,
        }
    )


async def test_production_rejects_unsigned_provider_identity(test_context):
    async with test_context["session_factory"]() as session:
        with pytest.raises(OnboardingError) as error:
            await OnboardingService(session, production_settings()).start(start_request())
        assert error.value.code == "identity_assertion_required"


async def test_production_accepts_matching_short_lived_assertion(test_context):
    settings = production_settings()
    assertion = IdentityAssertionTokenService(settings).issue("telegram", "telegram-signed-123")
    async with test_context["session_factory"]() as session:
        result = await OnboardingService(session, settings).start(start_request(assertion))
        assert result.user_id
        assert result.current_step.value == "disclaimer"
