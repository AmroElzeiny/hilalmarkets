from __future__ import annotations

import pytest

from ai_market_monitor.api.routers import public as public_router


class FakeRedis:
    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_public_health_returns_service_metadata(test_context):
    response = await test_context["client"].get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "hilalmarkets",
        "environment": "test",
        "evaluator_fault_control_available": False,
    }


@pytest.mark.asyncio
async def test_public_deep_health_checks_database_and_redis(test_context, monkeypatch):
    monkeypatch.setattr(public_router.Redis, "from_url", lambda *args, **kwargs: FakeRedis())

    response = await test_context["client"].get("/health/deep")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "hilalmarkets"
    assert payload["environment"] == "test"
    assert payload["checks"]["database"] == "ok"
    assert payload["checks"]["redis"] == "ok"
    assert payload["checks"]["sharia_admin_notifications"] in {"ok", "degraded"}
    assert payload["checks"]["sharia_ai_research"] in {"ok", "degraded"}
    assert payload["checks"]["sharia_source_policy"] == "ok"
    # Whether the product is checking the market at all is answerable from outside the
    # machine. Without it every other check reads "ok" while nothing is being watched,
    # and the only way to find out was to read an environment file on the server.
    assert payload["checks"]["market_scanning"] in {"ok", "off"}
    expected_status = (
        "ok"
        if all(value == "ok" for value in payload["checks"].values())
        else "degraded"
    )
    assert payload["status"] == expected_status
