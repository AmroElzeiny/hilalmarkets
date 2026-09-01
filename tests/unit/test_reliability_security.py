from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import WHATSAPP_TEMPLATE_EVENTS, Settings
from ai_market_monitor.core.logging import redact_sensitive_values
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, plan_offer
from ai_market_monitor.core.startup import (
    RuntimeConfigurationError,
    validate_runtime_configuration,
)
from ai_market_monitor.db.models import (
    AlertDelivery,
    AuditEvent,
    MarketDataHealth,
    User,
)
from ai_market_monitor.db.models.enums import (
    DeliveryChannel,
    DeliveryStatus,
    HealthStatus,
    IncidentSeverity,
    IncidentStatus,
)
from ai_market_monitor.services.reliability import ReliabilityError, ReliabilityService
from ai_market_monitor.services.security_review import SecurityReviewError, SecurityReviewService


async def test_market_data_health_blocks_stale_or_incomplete_confirmations(test_context):
    async with test_context["session_factory"]() as session:
        service = ReliabilityService(session)
        health = await service.record_market_data_health(
            provider="ccxt",
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            latest_candle_at=datetime.now(UTC) - timedelta(minutes=10),
            retrieved_at=datetime.now(UTC),
            candle_count=29,
            expected_candle_count=30,
        )
        assert health.status == HealthStatus.DEGRADED
        with pytest.raises(ReliabilityError) as stale_error:
            await service.assert_confirmation_allowed(
                provider="ccxt",
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                is_candle_complete=True,
            )
        assert stale_error.value.code == "stale_market_data"

        await service.record_market_data_health(
            provider="ccxt",
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="15m",
            latest_candle_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            candle_count=30,
            expected_candle_count=30,
        )
        with pytest.raises(ReliabilityError) as candle_error:
            await service.assert_confirmation_allowed(
                provider="ccxt",
                exchange="binance",
                symbol="ETH/USDT",
                timeframe="15m",
                is_candle_complete=False,
            )
        assert candle_error.value.code == "incomplete_candle"
        await service.assert_confirmation_allowed(
            provider="ccxt",
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="15m",
            is_candle_complete=True,
        )
        assert await session.scalar(select(func.count(MarketDataHealth.id))) == 2


async def test_incident_lifecycle_delivery_failure_and_status_summary(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Affected")
        session.add(user)
        await session.flush()
        service = ReliabilityService(session)
        incident = await service.open_incident(
            actor_user_id=None,
            title="Binance candles delayed",
            description="15m candles lagged behind freshness policy.",
            incident_type="market_data",
            severity=IncidentSeverity.MAJOR,
            affected_users=[user.id],
        )
        assert incident.status == IncidentStatus.INVESTIGATING
        summary = await service.status_summary()
        assert summary.overall_status == HealthStatus.DOWN
        await service.resolve_incident(
            incident_id=incident.id,
            actor_user_id=user.id,
            resolution="Freshness recovered.",
        )
        assert incident.status == IncidentStatus.RESOLVED

        delivery = AlertDelivery(
            alert_id=user.id,
            channel=DeliveryChannel.DISCORD,
            destination_key="dm:discord",
            status=DeliveryStatus.PENDING,
        )
        session.add(delivery)
        await session.flush()
        await service.mark_delivery_failure(
            delivery,
            retryable=False,
            error_code="Forbidden",
            detail="Bot was removed.",
        )
        assert delivery.status == DeliveryStatus.FAILED_PERMANENT
        assert await session.scalar(select(func.count(AuditEvent.id))) >= 2


def test_security_review_blocks_ssrf_unsafe_uploads_and_code_execution():
    service = SecurityReviewService()
    service.validate_external_url("https://example.com/chart.png")
    with pytest.raises(SecurityReviewError):
        service.validate_external_url("http://127.0.0.1/latest/meta-data")
    with pytest.raises(SecurityReviewError):
        service.validate_upload(
            filename="payload.exe",
            content_type="application/octet-stream",
            size_bytes=100,
        )
    with pytest.raises(SecurityReviewError):
        service.validate_strategy_source("Run eval('2+2') before scanning")
    redacted = service.redact({"telegram_bot_token": "secret", "nested": {"api_key": "key"}})
    assert redacted["telegram_bot_token"] == "[redacted]"
    assert redacted["nested"]["api_key"] == "[redacted]"
    redacted_value = service.redact({"openai_api_key": "sk-test-secret-value-with-many-characters"})
    assert redacted_value["openai_api_key"] == "[redacted]"
    event = redact_sensitive_values(
        None,
        None,
        {
            "event": "provider configured",
            "binance_api_secret": "real-looking-secret-with-123456",
        },
    )
    assert event["binance_api_secret"] == "[redacted]"
    assert service.dependency_inventory().package_count > 0


def test_production_runtime_rejects_defaults_and_mock_providers():
    settings = Settings(
        app_env="production",
        app_secret_key="development-only-change-me-32-characters",
        database_url="sqlite+aiosqlite:///unsafe.db",
        public_base_url="http://localhost:8000",
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    message = str(error.value)
    assert "APP_SECRET_KEY" in message
    assert "ALLOW_MOCK_PROVIDERS" in message
    assert "PostgreSQL" in message
    assert "HTTPS" in message


def test_production_runtime_accepts_disabled_integrations_with_safe_core_config():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        ai_agent_control_enabled=False,
        capability_extension_enabled=False,
        public_chat_enabled=True,
        public_chat_ai_enabled=True,
        ai_setup_evaluator_enabled=False,
        ai_setup_evaluator_faults_enabled=False,
        openai_api_key="production-openai-key",
        email_adapter="smtp",
        smtp_host="smtp.example.com",
        smtp_username="production-smtp-user",
        smtp_password="production-smtp-password",
        smtp_from_email="no-reply@example.com",
    )
    validate_runtime_configuration(settings)


def test_deployed_public_chat_requires_real_smtp_configuration():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        ai_agent_control_enabled=False,
        capability_extension_enabled=False,
        public_chat_enabled=True,
        public_chat_ai_enabled=True,
        openai_api_key="production-openai-key",
        email_adapter="none",
        smtp_host=None,
        smtp_username=None,
        smtp_password=None,
        smtp_from_email=None,
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    message = str(error.value)
    assert "EMAIL_ADAPTER=smtp" in message
    assert "SMTP_HOST" in message
    assert "SMTP_USERNAME" in message
    assert "SMTP_PASSWORD" in message
    assert "SMTP_FROM_EMAIL" in message


def test_agent_kill_switch_keeps_certified_capabilities_bootable() -> None:
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        public_chat_enabled=False,
        # A deployment with no public surface also has no public forms. Leaving the
        # default on made this test fail for a reason that has nothing to do with the
        # kill switch it is about: public forms need real SMTP, and none is configured
        # here. The startup guard is right; the settings were incomplete.
        public_forms_enabled=False,
        ai_agent_control_enabled=False,
        capability_extension_enabled=True,
        ai_setup_evaluator_enabled=False,
        ai_setup_evaluator_faults_enabled=False,
        capability_extension_preflight_exchange="binance",
        openai_api_key="production-openai-key",
    )

    validate_runtime_configuration(settings)


def test_test_sharia_market_switch_is_not_a_runtime_setting():
    assert "sharia_test_market_enabled" not in Settings.model_fields


def test_deployed_runtime_requires_fail_closed_api_rate_limits():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        api_rate_limiting_enabled=False,
        api_rate_limit_fail_closed=False,
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    message = str(error.value)
    assert "API_RATE_LIMITING_ENABLED" in message
    assert "API_RATE_LIMIT_FAIL_CLOSED" in message


def test_deployed_sharia_governance_requires_safe_operational_dependencies():
    settings = Settings(
        app_env="staging",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        sharia_screening_enforced=True,
        sharia_admin_telegram_chat_id=None,
        openai_api_key=None,
        sharia_ai_service_tier="default",
        sharia_scraper_obey_robots=False,
        sharia_scraper_download_delay_seconds=0.2,
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    message = str(error.value)
    assert "SHARIA_ADMIN_TELEGRAM_CHAT_ID" in message
    assert "TELEGRAM_ENABLED" in message
    assert "OPENAI_API_KEY" in message
    assert "SHARIA_AI_SERVICE_TIER" in message
    assert "SHARIA_SCRAPER_OBEY_ROBOTS" in message
    assert "SHARIA_SCRAPER_DOWNLOAD_DELAY_SECONDS" in message


def test_deployed_runtime_rejects_fixture_market_data_and_unwired_provider_flags():
    settings = Settings(
        _env_file=None,
        app_env="staging",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        ai_interpreter_provider="rules",
        tracedge_market_data_mode="fixture",
        tracedge_fixture_market_data_enabled=True,
        coingecko_enabled=True,
        alternative_me_enabled=True,
        fred_enabled=True,
        binance_derivatives_enabled=True,
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    message = str(error.value)
    assert "TRACEDGE_MARKET_DATA_MODE=fixture" in message
    assert "TRACEDGE_FIXTURE_MARKET_DATA_ENABLED" in message
    assert "ALTERNATIVE_ME_ENABLED" in message
    assert "FRED_ENABLED" in message
    assert "BINANCE_DERIVATIVES_ENABLED" in message


def test_enabled_production_integrations_require_real_adapters_and_secrets():
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=True,
        telegram_adapter="none",
        billing_enabled=True,
        billing_provider="static",
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    message = str(error.value)
    assert "TELEGRAM_ADAPTER=http" in message
    assert "StaticBillingProvider" in message


def _configuration_complaints(settings: Settings) -> list[str]:
    """Every line the startup check objects to, or an empty list when it is happy."""

    try:
        validate_runtime_configuration(settings)
    except RuntimeConfigurationError as error:
        return [line.strip("- ").strip() for line in str(error).splitlines()[1:]]
    return []


def _creem_production_settings(product_ids: dict[str, str]) -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        public_chat_enabled=False,
        billing_enabled=True,
        billing_card_provider="creem",
        creem_api_key="creem-production-key",
        creem_webhook_secret="creem-webhook-secret",
        creem_product_ids=product_ids,
        creem_api_base="https://api.creem.io",
    )


#: Everything the product could sell, and whether it is really on sale today. Read from
#: `core/plans.py`, so this test says what the rule *is* rather than what today's answer
#: happens to be.
CREEM_PRODUCT_KEYS = [
    pytest.param(
        f"{plan_code}_{cycle}",
        (
            plan_offer(plan_code).monthly_available
            if cycle == "monthly"
            else plan_offer(plan_code).annual_available
        ),
        id=f"{plan_code}_{cycle}",
    )
    for plan_code in PURCHASABLE_PLAN_CODES
    for cycle in ("monthly", "annual")
]


@pytest.mark.parametrize(("product_key", "on_sale"), CREEM_PRODUCT_KEYS)
def test_creem_needs_a_product_for_everything_on_sale_and_nothing_else(
    product_key: str, on_sale: bool
):
    """The gate asks what is on sale; it does not hold its own copy of the answer.

    It used to demand four named products — Monitor and Pro, monthly and yearly. Three of
    those are not on sale, so a server correctly configured for the one plan that *is* on
    sale refused to start, and card payments could not be switched on at all.
    """

    every_key = {key for key, _ in ((p.values[0], p.values[1]) for p in CREEM_PRODUCT_KEYS)}
    without_this_one = {
        key: f"prod_{key}" for key in sorted(every_key) if key != product_key
    }
    complaints = _configuration_complaints(_creem_production_settings(without_this_one))
    # Only what this rule says. These settings trip other production rules that have
    # nothing to do with Creem products, and those are other tests' business.
    about_products = [line for line in complaints if "CREEM_PRODUCT_IDS is missing" in line]

    if on_sale:
        assert about_products, f"{product_key} is on sale and was not required"
        assert product_key in about_products[0]
    else:
        # Nothing may be demanded for a plan or a period nobody can buy.
        assert not about_products, f"{product_key} is not on sale and was required anyway"


def test_creem_products_must_be_real_creem_identifiers():
    on_sale = {
        key
        for key, available in ((p.values[0], p.values[1]) for p in CREEM_PRODUCT_KEYS)
        if available
    }
    broken = {key: "monitor-monthly" for key in sorted(on_sale)}
    complaints = _configuration_complaints(_creem_production_settings(broken))

    assert any("invalid product IDs" in line for line in complaints)


def test_enabled_production_whatsapp_requires_complete_cloud_api_configuration():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        whatsapp_enabled=True,
        whatsapp_adapter="none",
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    message = str(error.value)
    assert "WHATSAPP_ADAPTER=http" in message
    assert "WHATSAPP_GRAPH_API_VERSION" in message
    assert "WHATSAPP_ACCESS_TOKEN" in message
    assert "WHATSAPP_APP_SECRET" in message
    assert "WHATSAPP_VERIFY_TOKEN" in message
    assert "WHATSAPP_PHONE_NUMBER_ID" in message
    assert "WHATSAPP_BUSINESS_ACCOUNT_ID" in message
    assert "WHATSAPP_BUSINESS_PHONE_E164" in message


def test_whatsapp_opportunity_delivery_requires_an_explicit_template():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        whatsapp_enabled=True,
        whatsapp_adapter="http",
        whatsapp_graph_api_version="v23.0",
        whatsapp_access_token="production-access-token-value",
        whatsapp_app_secret="production-app-secret-value",
        whatsapp_verify_token="production-webhook-verify-value",
        whatsapp_phone_number_id="phone-number-id",
        whatsapp_business_account_id="waba-id",
        whatsapp_business_phone_e164="+12025550123",
        whatsapp_opportunity_alerts_enabled=True,
        whatsapp_template_names={},
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    assert "confirmed_research_event" in str(error.value)


def test_deployed_whatsapp_requires_complete_non_placeholder_template_registry():
    templates = {event: f"hm_{event}_v1" for event in WHATSAPP_TEMPLATE_EVENTS}
    templates["connection_test"] = "replace_with_approved_template"
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:s3cure-deployment-secret@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        billing_enabled=False,
        whatsapp_enabled=True,
        whatsapp_adapter="http",
        whatsapp_graph_api_version="v23.0",
        whatsapp_access_token="production-access-token-value",
        whatsapp_app_secret="production-app-secret-value",
        whatsapp_verify_token="production-webhook-verify-value",
        whatsapp_phone_number_id="phone-number-id",
        whatsapp_business_account_id="waba-id",
        whatsapp_business_phone_e164="+12025550123",
        whatsapp_template_names=templates,
    )

    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)

    assert "connection_test must not use a placeholder" in str(error.value)


def test_whatsapp_template_locale_keys_are_validated():
    with pytest.raises(ValueError, match="invalid locale key"):
        Settings(
            app_env="test",
            app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
            whatsapp_template_names={
                "connection_test": {"english-US": "connection_test_v1"}
            },
        )


def test_production_runtime_rejects_placeholder_credentials():
    settings = Settings(
        app_env="production",
        app_secret_key="REPLACE_WITH_64_RANDOM_CHARACTERS",
        database_url="postgresql+asyncpg://user:REPLACE_WITH_PASSWORD@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        ai_interpreter_provider="openai",
        openai_api_key="REPLACE_WITH_OPENAI_API_KEY",
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    message = str(error.value)
    assert "APP_SECRET_KEY must not use a placeholder" in message
    assert "DATABASE_URL must not contain placeholder credentials" in message
    assert "OPENAI_API_KEY must not use a placeholder" in message
