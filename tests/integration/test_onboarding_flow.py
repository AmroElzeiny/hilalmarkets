from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AttributionTouch,
    DisclaimerAcceptance,
    OnboardingSession,
    Strategy,
    TelegramConnection,
    Trial,
    User,
)
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    OnboardingStatus,
    OnboardingStep,
    StrategyStatus,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition


def start_payload(subject: str = "telegram-123") -> dict:
    return {
        "identity": {
            "provider": "telegram",
            "provider_subject": subject,
            "display_identifier": "market_trader",
            "display_name": "Market Trader",
            "profile_data": {"language_code": "en"},
        },
        "entry_channel": "telegram",
        "attribution": {
            "source": "youtube",
            "medium": "video",
            "campaign": "launch",
            "referral_code": "CREATOR10",
            "consented": True,
        },
    }


def guided_payload(setup_text: str | None = None) -> dict:
    return {
        "exchange": "binance",
        "quote_currency": "USDT",
        "timeframe": "15m",
        "symbols": ["SOL/USDT", "LINK/USDT"],
        "setup_mode": "free_text",
        "setup_text": setup_text
        or (
            "Find bullish liquidity sweeps. Price should be above the four-hour 200 EMA, "
            "volume should be at least 1.5 times average."
        ),
        "trigger_mode": "candle_close",
        "maximum_stop_percent": 2,
        "minimum_reward_to_risk": 2.5,
        "minimum_quote_volume_24h": 1000000,
        "forming_alerts": True,
        "near_miss_threshold": 70,
        "delivery_channels": ["telegram", "web"],
        "maximum_alerts_per_hour": 8,
    }


async def test_ad_to_activation_full_path(test_context):
    client = test_context["client"]
    first = await client.post("/api/v1/onboarding/start", json=start_payload())
    assert first.status_code == 201, first.text
    started = first.json()
    session_id = started["session_id"]
    user_id = started["user_id"]
    identity_id = started["state_data"]["identity_id"]
    headers = {"Authorization": f"Bearer {started['session_token']}"}
    assert started["current_step"] == "disclaimer"

    duplicate = await client.post("/api/v1/onboarding/start", json=start_payload())
    assert duplicate.status_code == 201
    assert duplicate.json()["user_id"] == user_id
    assert duplicate.json()["session_id"] == session_id

    disclaimer = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/disclaimer",
        headers=headers,
        json={
            "identity_id": identity_id,
            "accepted": True,
            "acceptance_source": "telegram",
            "disclaimer_version": "test-2026-06",
        },
    )
    assert disclaimer.status_code == 200, disclaimer.text
    assert disclaimer.json()["current_step"] == "guided_setup"

    guided = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/guided-setup",
        headers=headers,
        json=guided_payload(),
    )
    assert guided.status_code == 200, guided.text
    assert guided.json()["current_step"] == "interpretation"

    interpreted = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/interpret", headers=headers
    )
    assert interpreted.status_code == 200, interpreted.text
    interpretation = interpreted.json()
    assert interpretation["preview"]["unsupported_conditions"] == []
    definition = StrategyDefinition.model_validate(interpretation["preview"]["strategy"])
    schema_hash = definition.canonical_hash()

    before_approval = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/activate",
        headers=headers,
        json={"strategy_name": "Sweep monitor", "confirm_usage_impact": True},
    )
    assert before_approval.status_code == 409
    assert before_approval.json()["detail"]["code"] == "approval_required"

    approved = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/approve",
        headers=headers,
        json={"approved": True, "expected_schema_hash": schema_hash},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["current_step"] == "validation"

    before_preview = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/activate",
        headers=headers,
        json={"strategy_name": "Sweep monitor", "confirm_usage_impact": True},
    )
    assert before_preview.status_code == 409
    assert before_preview.json()["detail"]["code"] == "preview_required"

    preview = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/preview", headers=headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "succeeded"

    # No Telegram connection, and it starts anyway — because this monitor also asks to
    # be told **in the dashboard**, which needs no connection at all.
    #
    # The gate used to ask "is Telegram or WhatsApp connected?" and nothing else counted,
    # so a person whose monitor said "tell me in the dashboard" was refused and sent to
    # connect a channel they had not chosen and did not need. It asks the real question
    # now: can any of the ways *this monitor* names actually reach this person?
    async with test_context["session_factory"]() as session:
        session.add(
            TelegramConnection(
                user_id=UUID(user_id),
                telegram_user_id="telegram-123",
                chat_id="telegram-123",
                status=ConnectionStatus.ACTIVE,
                alerts_enabled=True,
            )
        )
        await session.commit()

    activated = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/activate",
        headers=headers,
        json={"strategy_name": "Sweep monitor", "confirm_usage_impact": True},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(User.id))) == 1
        assert await session.scalar(select(func.count(AttributionTouch.id))) == 2
        assert await session.scalar(select(func.count(DisclaimerAcceptance.id))) == 1
        assert await session.scalar(select(func.count(Trial.id))) == 1
        strategy = await session.scalar(select(Strategy).where(Strategy.user_id == UUID(user_id)))
        assert strategy is not None
        assert strategy.status == StrategyStatus.ACTIVE
        assert strategy.active_version_id == UUID(activated.json()["strategy_version_id"])
        onboarding = await session.get(OnboardingSession, UUID(session_id))
        assert onboarding.status == OnboardingStatus.COMPLETED
        assert onboarding.current_step == OnboardingStep.COMPLETE


async def test_telegram_continuation_is_same_session_and_single_use(test_context):
    client = test_context["client"]
    started = (await client.post("/api/v1/onboarding/start", json=start_payload("tg-456"))).json()

    resumed = await client.post(
        "/api/v1/onboarding/resume", json={"token": started["continuation_token"]}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["user_id"] == started["user_id"]
    assert resumed.json()["session_id"] == started["session_id"]
    assert resumed.json()["session_token"]

    replay = await client.post(
        "/api/v1/onboarding/resume", json={"token": started["continuation_token"]}
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "invalid_link"


async def test_unsupported_interpretation_blocks_approval(test_context):
    client = test_context["client"]
    started = (await client.post("/api/v1/onboarding/start", json=start_payload("tg-789"))).json()
    session_id = started["session_id"]
    headers = {"Authorization": f"Bearer {started['session_token']}"}
    await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/disclaimer",
        headers=headers,
        json={
            "identity_id": started["state_data"]["identity_id"],
            "accepted": True,
            "acceptance_source": "telegram",
            "disclaimer_version": "test-2026-06",
        },
    )
    await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/guided-setup",
        headers=headers,
        json=guided_payload("Find beautiful setups with strong vibes"),
    )
    interpreted = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/interpret", headers=headers
    )
    body = interpreted.json()
    assert body["preview"]["unsupported_conditions"]
    definition = StrategyDefinition.model_validate(body["preview"]["strategy"])
    approval = await client.post(
        f"/api/v1/onboarding/sessions/{session_id}/approve",
        headers=headers,
        json={"approved": True, "expected_schema_hash": definition.canonical_hash()},
    )
    assert approval.status_code == 409
    assert approval.json()["detail"]["code"] == "unsupported_conditions"


async def test_access_token_cannot_cross_onboarding_sessions(test_context):
    client = test_context["client"]
    first = (await client.post("/api/v1/onboarding/start", json=start_payload("tg-a"))).json()
    second = (await client.post("/api/v1/onboarding/start", json=start_payload("tg-b"))).json()
    response = await client.get(
        f"/api/v1/onboarding/sessions/{second['session_id']}",
        headers={"Authorization": f"Bearer {first['session_token']}"},
    )
    assert response.status_code == 404
