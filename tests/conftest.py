from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.api.dependencies import get_market_previewer
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.base import Base
from ai_market_monitor.main import create_app
from ai_market_monitor.schemas.onboarding import MarketPreviewResponse


class SuccessfulPreviewer:
    async def run(self, strategy) -> MarketPreviewResponse:
        return MarketPreviewResponse(
            status="succeeded",
            symbols_checked=2,
            candles_checked=600,
            sample_matches=[
                {
                    "exchange": strategy.universe.exchange,
                    "symbol": "SOL/USDT",
                    "completion_score": 100,
                }
            ],
            warnings=[],
            data_as_of="2026-06-14T12:00:00+00:00",
        )


@pytest_asyncio.fixture
async def test_context() -> AsyncIterator[dict]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        billing_provider="static",
        openai_explanation_enabled=False,
        ai_agent_control_enabled=False,
        capability_extension_enabled=False,
        public_chat_ai_enabled=False,
        email_adapter="memory",
        sharia_default_methodology_code=None,
        openai_model="gpt-5.4-nano",
        openai_reasoning_effort="low",
        openai_model_pricing_usd_per_million={
            "gpt-5.4-nano": {
                "input": 0.20,
                "cached_input": 0.02,
                "output": 1.25,
            },
            "gpt-5.4-mini": {
                "input": 0.75,
                "cached_input": 0.075,
                "output": 4.50,
            },
        },
        disclaimer_version="test-2026-06",
        trial_days=14,
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_market_previewer] = lambda: SuccessfulPreviewer()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield {
            "client": client,
            "session_factory": session_factory,
            "settings": settings,
            "app": app,
        }

    app.dependency_overrides.clear()
    await engine.dispose()
