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
        "service": "traceedge",
        "environment": "test",
    }


@pytest.mark.asyncio
async def test_public_deep_health_checks_database_and_redis(test_context, monkeypatch):
    monkeypatch.setattr(public_router.Redis, "from_url", lambda *args, **kwargs: FakeRedis())

    response = await test_context["client"].get("/health/deep")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "traceedge",
        "environment": "test",
        "checks": {"database": "ok", "redis": "ok"},
    }
