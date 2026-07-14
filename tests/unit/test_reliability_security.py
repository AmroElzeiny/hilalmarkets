from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.logging import redact_sensitive_values
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
        database_url="postgresql+asyncpg://user:password@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        scanning_enabled=False,
        ai_interpreter_provider="rules",
        telegram_enabled=False,
        discord_enabled=False,
        billing_enabled=False,
    )
    validate_runtime_configuration(settings)


def test_deployed_runtime_rejects_fixture_market_data_and_unwired_provider_flags():
    settings = Settings(
        app_env="staging",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:password@database/monitor",
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
    assert "COINGECKO_ENABLED" in message
    assert "ALTERNATIVE_ME_ENABLED" in message
    assert "FRED_ENABLED" in message
    assert "BINANCE_DERIVATIVES_ENABLED" in message


def test_enabled_production_integrations_require_real_adapters_and_secrets():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-key-with-at-least-thirty-two-characters",
        database_url="postgresql+asyncpg://user:password@database/monitor",
        public_base_url="https://monitor.example.com",
        allow_mock_providers=False,
        ai_interpreter_provider="rules",
        telegram_enabled=True,
        telegram_adapter="none",
        discord_enabled=True,
        billing_enabled=True,
        billing_provider="static",
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    message = str(error.value)
    assert "TELEGRAM_ADAPTER=http" in message
    assert "NoopDiscordGateway" in message
    assert "StaticBillingProvider" in message


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
