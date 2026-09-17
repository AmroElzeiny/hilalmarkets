from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.api.dependencies import (
    get_market_data_provider,
    get_market_previewer,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.db.base import Base
from ai_market_monitor.main import create_app
from ai_market_monitor.schemas.onboarding import MarketPreviewResponse
from ai_market_monitor.services.fixture_market_data import FixtureMarketDataProvider


@pytest.fixture(autouse=True)
def isolate_settings_from_local_env_file() -> Iterator[None]:
    """No test result may depend on a developer's untracked ``.env``.

    ``Settings`` reads ``.env`` by default. A test that builds a production-like settings
    object and then checks the startup guard was therefore reading whatever the developer
    happened to have on disk: a local ``APP_BASE_URL=http://localhost:8000`` made two
    production-configuration tests fail on one machine and pass on another, and the
    reverse is worse — a real misconfiguration could be hidden by a local override.

    Autouse and repo-wide on purpose. Fixing the two tests that happened to fail would
    leave every other test, and every test written later, exposed to the same thing. A
    test that genuinely wants a file still passes ``_env_file=`` itself, which wins over
    this default.
    """

    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original


@pytest.fixture(autouse=True)
def reset_provider_runtime_between_tests() -> Iterator[None]:
    """Each test starts with a provider circuit that has never seen a failure.

    The breaker is deliberately *process* state in production: an outage one code path
    discovers is an outage every other code path already knows about. In a test run that
    same sharing becomes coupling — a test that makes the provider return 500 five times
    opens the circuit for every test that runs after it in the same process, and the
    victim is whichever test happens to come next. That is a test order dependency, not a
    finding.

    Reset between tests, never inside one, so a test that is *about* the breaker still
    sees the real accumulating behaviour.
    """

    import asyncio

    from ai_market_monitor.services.provider_runtime import shutdown_provider_runtime

    yield
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(shutdown_provider_runtime())


@pytest.fixture(autouse=True)
def no_test_may_reach_a_payment_company(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """A test run must never send a request to Creem, NOWPayments or Stripe.

    Same shape of problem as the Chromium processes this file already closes: the offline
    suite was quietly doing real outside work. Any test that switches billing on is
    holding obviously fake keys, and the checkout code then sent those keys to the real
    payment companies. They answered "wrong key" — 401 from Creem, 403 from NOWPayments —
    and the test read that as *our* server refusing the customer. Four cases of
    ``test_invariant_billing_offers`` failed that way while both the page and the server
    rule were correct.

    Autouse and repo-wide, because this is a fact about the whole product, not about the
    four tests that happened to show it. A test that means to exercise the payment path
    calls ``stub_payment_companies`` and gets in-process answers instead.
    """

    from tests.support.billing_config import refuse_payment_network

    refuse_payment_network(monkeypatch)
    yield


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


async def _build_context(**overrides: object) -> AsyncIterator[dict]:
    """One app, built the way the running product builds it.

    ``overrides`` are settings this particular test needs to differ. Everything else
    stays at the shipped default, so a test can never pass by accident on a posture the
    product has left behind.
    """

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
        setup_chat_legacy_test_compat_enabled=True,
        capability_extension_enabled=False,
        public_chat_ai_enabled=False,
        email_adapter="memory",
        tracedge_market_data_mode="fixture",
        tracedge_fixture_market_data_enabled=True,
        allow_mock_providers=True,
        # The offline suite never drives a real browser. Reading a page with Chromium is
        # on by default in the product, and every test that builds a source-resolution
        # service with a fake fetcher was quietly starting one: 61 Chromium processes and
        # 20 driver processes were left behind by four test files alone, and the suite
        # slowed to a crawl long before it finished. Nothing here is testing the browser —
        # the tests that are switch it on themselves, and the test that proves the shipped
        # default is "on" builds its own Settings and still sees True.
        sharia_source_browser_render_enabled=False,
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
            # The product's default assistant model. The fixture mirrors the
            # shipped priced set; without this entry every test that leaves
            # the default model in place fails the cost gate it is not
            # testing.
            "muse-spark-1.3-contributor": {
                "input": 0.1,
                "cached_input": 0.002,
                "output": 0.2,
            },
        },
        disclaimer_version="test-2026-06",
        trial_days=7,
        **overrides,  # type: ignore[arg-type]
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_market_data_provider] = (
        lambda: FixtureMarketDataProvider()
    )
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


@pytest_asyncio.fixture
async def test_context() -> AsyncIterator[dict]:
    """The product as it is shipped today: launched, public, pricing visible."""

    async for context in _build_context():
        yield context


@pytest_asyncio.fixture
async def waitlist_context() -> AsyncIterator[dict]:
    """The product pulled back to the waitlist.

    Still a supported posture after launch — it is the one switch that can close the
    public site without a deploy — so what it does must stay covered. It is asked for
    here explicitly, because tests that merely *assumed* it kept asserting a pre-launch
    site for as long as pre-launch happened to be the default, and then failed on the
    day the product went live rather than the day the behaviour broke.
    """

    async for context in _build_context(public_waitlist_mode=True):
        yield context
