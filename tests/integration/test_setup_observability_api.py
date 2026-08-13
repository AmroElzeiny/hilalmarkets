from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    Strategy,
    User,
)
from ai_market_monitor.db.models.enums import AlertType, DeliveryChannel, DeliveryStatus
from tests.support.entitlements import grant_monitor_plan
from tests.unit.test_setup_observability import _seed_lifecycle, _seed_monitor


async def _signup(test_context, email: str) -> None:
    started = await test_context["client"].post(
        "/signup",
        data={
            "email": email,
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert started.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_observability_api_filter_investigation_and_user_isolation(test_context):
    await _signup(test_context, "observability-api@example.com")
    # The investigation call further down is a Monitor-plan feature.
    await grant_monitor_plan(test_context["session_factory"])
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        _same_user, strategy, version = await _seed_monitor(
            session, user=user, name="SOL Volume Watch"
        )
        _, _, scan, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        session.add(
            CandidateReadinessSnapshot(
                user_id=user.id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                setup_instance_id=setup.id,
                scan_result_id=scan.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                lifecycle_state="confirmation_pending",
                stage_rank=3,
                required_total=1,
                required_passed=0,
                optional_total=0,
                optional_passed=0,
                blocker_key="volume_ratio",
                blocker_label="Volume confirmation",
                blocker_outcome="failed",
                blocker_actual={"value": 1.27},
                blocker_required={"value": 1.5},
                blocker_distance=Decimal("0.23"),
                blocker_unit="absolute",
                most_recent_change="Volume became the final blocker.",
                last_changed_at=datetime.now(UTC),
                last_evaluated_at=datetime.now(UTC),
                data_freshness_ms=250,
                data_health="healthy",
                notification_status="not_attempted",
                condition_tree={},
                latest_values=[],
            )
        )
        empty_strategy = Strategy(user_id=user.id, name="Empty Monitor")
        session.add(empty_strategy)
        await session.commit()
        setup_id = setup.id
        strategy_id = strategy.id
        empty_id = empty_strategy.id

    radar = await test_context["client"].get(
        f"/api/v1/dashboard/observability/radar?monitor_id={strategy_id}"
    )
    assert radar.status_code == 200
    assert radar.json()["total"] == 1
    assert radar.json()["items"][0]["monitor_name"] == "SOL Volume Watch"

    investigation = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/investigation"
    )
    assert investigation.status_code == 200
    assert investigation.json()["primary_category"] == "strategy_condition_failure"
    assert investigation.json()["conditions"][0]["actual"] == 1.27

    missing = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{uuid4()}/investigation"
    )
    assert missing.status_code == 404

    filtered_page = await test_context["client"].get(
        f"/dashboard/opportunities?monitor={strategy_id}"
    )
    assert filtered_page.status_code == 200
    assert "SOL Volume Watch" in filtered_page.text
    assert "data-lifecycle-investigate" in filtered_page.text
    assert "Strategy version" not in filtered_page.text

    empty_page = await test_context["client"].get(f"/dashboard/opportunities?monitor={empty_id}")
    assert "No lifecycle records found for this Watchlist." in empty_page.text


async def test_successful_delivery_hides_why_no_alert_action(test_context):
    await _signup(test_context, "observability-delivered@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        _, strategy, version = await _seed_monitor(session, user=user)
        _, _, _, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"delivered-{setup.id}",
            title="Research match",
            body="Delivered",
            proof_receipt={},
        )
        session.add(alert)
        await session.flush()
        session.add(
            AlertDelivery(
                alert_id=alert.id,
                channel=DeliveryChannel.TELEGRAM,
                destination_key="chat:123",
                status=DeliveryStatus.SENT,
                attempt_count=1,
                delivered_at=datetime.now(UTC),
            )
        )
        await session.commit()
    page = await test_context["client"].get("/dashboard/opportunities")
    assert page.status_code == 200
    assert "data-lifecycle-investigate" not in page.text
