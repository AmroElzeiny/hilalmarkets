from datetime import UTC, datetime

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AuditEvent,
    BillingEvent,
    Strategy,
    SupportRequest,
    User,
)
from ai_market_monitor.db.models.enums import StrategyStatus, SupportRequestStatus, UserRole
from ai_market_monitor.services.reliability import ReliabilityService


async def create_user(session, *, role=UserRole.USER, name="User") -> User:
    user = User(display_name=name, role=role)
    session.add(user)
    await session.flush()
    return user


async def test_status_and_admin_dashboard_require_roles_and_audit_actions(test_context):
    client = test_context["client"]
    async with test_context["session_factory"]() as session:
        admin = await create_user(session, role=UserRole.ADMIN, name="Admin")
        normal = await create_user(session, role=UserRole.USER, name="Trader")
        strategy = Strategy(user_id=normal.id, name="Monitor", status=StrategyStatus.ACTIVE)
        ticket = SupportRequest(
            user_id=normal.id,
            category="missing_alert",
            priority="high",
            status=SupportRequestStatus.OPEN,
            subject="Missing alert",
            description="Why no alert?",
            context={"strategy_id": str(strategy.id)},
        )
        session.add_all([strategy, ticket])
        await ReliabilityService(session).record_market_data_health(
            provider="ccxt",
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            latest_candle_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            candle_count=30,
            expected_candle_count=30,
        )
        await session.commit()
        admin_headers = {"X-User-ID": str(admin.id)}
        user_headers = {"X-User-ID": str(normal.id)}

    public_status = await client.get("/api/v1/status/summary")
    assert public_status.status_code == 200
    assert public_status.json()["overall_status"] == "healthy"
    assert float(public_status.headers["X-Process-Time-Ms"]) >= 0

    forbidden = await client.get("/api/v1/admin/overview", headers=user_headers)
    assert forbidden.status_code == 403

    overview = await client.get("/api/v1/admin/overview", headers=admin_headers)
    assert overview.status_code == 200, overview.text
    assert overview.json()["users"] == 2
    support_queue = await client.get("/api/v1/admin/support", headers=admin_headers)
    assert support_queue.status_code == 200
    assert support_queue.json()["support_requests"][0]["category"] == "missing_alert"

    pause = await client.post(
        f"/api/v1/admin/strategies/{strategy.id}/pause",
        headers=admin_headers,
        json={"reason": "Investigating disputed setup"},
    )
    assert pause.status_code == 200, pause.text
    assert pause.json()["status"] == "paused"

    resume = await client.post(
        f"/api/v1/admin/strategies/{strategy.id}/resume",
        headers=admin_headers,
        json={"reason": "Investigation complete"},
    )
    assert resume.status_code == 409, resume.text
    assert resume.json()["detail"]["code"] == "active_version_missing"

    incident = await client.post(
        "/api/v1/admin/incidents",
        headers=admin_headers,
        json={
            "title": "Telegram delivery degraded",
            "description": "Telegram API errors exceeded retry threshold.",
            "incident_type": "telegram_delivery",
            "severity": "major",
            "affected_users": [str(normal.id)],
        },
    )
    assert incident.status_code == 200, incident.text
    incident_id = incident.json()["incident_id"]
    incident_queue = await client.get("/api/v1/admin/incidents", headers=admin_headers)
    assert incident_queue.status_code == 200
    assert incident_queue.json()["incidents"][0]["incident_type"] == "telegram_delivery"

    resolved = await client.post(
        f"/api/v1/admin/incidents/{incident_id}/resolve",
        headers=admin_headers,
        json={"resolution": "Retries recovered and delivery backlog cleared."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    support = await client.post(
        f"/api/v1/admin/support/{ticket.id}/resolve",
        headers=admin_headers,
        json={"resolution": "Explained condition failure and attached logs."},
    )
    assert support.status_code == 200
    assert support.json()["status"] == "resolved"

    async with test_context["session_factory"]() as session:
        strategy_row = await session.get(Strategy, strategy.id)
        ticket_row = await session.get(SupportRequest, ticket.id)
        assert strategy_row.status == StrategyStatus.PAUSED
        assert ticket_row.status == SupportRequestStatus.RESOLVED
        assert await session.scalar(select(func.count(AuditEvent.id))) >= 4


async def test_admin_can_extend_trial_and_reprocess_non_failed_webhook(test_context):
    client = test_context["client"]
    async with test_context["session_factory"]() as session:
        admin = await create_user(session, role=UserRole.ADMIN, name="Admin")
        user = await create_user(session, role=UserRole.USER, name="Trial User")
        event = BillingEvent(
            user_id=user.id,
            provider="stripe",
            provider_event_id="evt_existing",
            event_type="customer.subscription.updated",
            processing_status="processed",
            payload_redacted={"id": "evt_existing", "type": "customer.subscription.updated"},
            created_at=datetime.now(UTC),
            processed_at=datetime.now(UTC),
        )
        session.add(event)
        await session.commit()
        headers = {"X-User-ID": str(admin.id)}

    extension = await client.post(
        f"/api/v1/admin/users/{user.id}/trial-extension",
        headers=headers,
        json={"days": 7, "reason": "Billing support goodwill extension"},
    )
    assert extension.status_code == 200, extension.text
    assert extension.json()["status"] == "manually_extended"

    reprocess = await client.post(
        "/api/v1/admin/billing-events/evt_existing/reprocess",
        headers=headers,
    )
    assert reprocess.status_code == 200, reprocess.text
    assert reprocess.json()["replayed"] is True


async def test_production_admin_rejects_header_only_principal(test_context):
    test_context["settings"].app_env = "production"
    async with test_context["session_factory"]() as session:
        admin = await create_user(session, role=UserRole.ADMIN, name="Production Admin")
        await session.commit()

    response = await test_context["client"].get(
        "/api/v1/admin/overview",
        headers={"X-User-ID": str(admin.id)},
    )

    assert response.status_code == 401
    assert "Header principals are disabled" in response.text
