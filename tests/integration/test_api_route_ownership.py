from ai_market_monitor.db.models import User


async def _users(test_context) -> tuple[User, User]:
    async with test_context["session_factory"]() as session:
        owner = User(display_name="API owner")
        other = User(display_name="Different API user")
        session.add_all([owner, other])
        await session.commit()
        return owner, other


async def test_public_billing_catalog_exposes_only_customer_plans(test_context) -> None:
    response = await test_context["client"].get("/api/v1/billing/plans")
    assert response.status_code == 200
    assert response.json()["billing_enabled"] is False
    assert response.json()["billing_mode"] == "disabled"
    codes = {plan["code"] for plan in response.json()["plans"]}
    assert codes == {"demo", "trader", "pro"}
    assert not codes & {
        "lifetime",
        "creator",
        "community",
        "trial",
        "admin",
        "pro_trial",
    }


async def test_billing_routes_derive_and_enforce_authenticated_owner(test_context) -> None:
    owner, other = await _users(test_context)
    unauthenticated = await test_context["client"].post(
        "/api/v1/billing/checkout",
        json={"plan_code": "trader"},
    )
    assert unauthenticated.status_code == 401

    own = await test_context["client"].get(
        f"/api/v1/billing/users/{owner.id}/entitlement",
        headers={"X-User-ID": str(owner.id)},
    )
    assert own.status_code == 200

    forbidden = await test_context["client"].get(
        f"/api/v1/billing/users/{other.id}/entitlement",
        headers={"X-User-ID": str(owner.id)},
    )
    assert forbidden.status_code == 403

    injected = await test_context["client"].post(
        "/api/v1/billing/checkout",
        headers={"X-User-ID": str(owner.id)},
        json={"plan_code": "trader", "user_id": str(other.id)},
    )
    assert injected.status_code == 422

    disabled = await test_context["client"].post(
        "/api/v1/billing/checkout",
        headers={"X-User-ID": str(owner.id)},
        json={"plan_code": "trader"},
    )
    assert disabled.status_code == 409
    assert disabled.json()["detail"]["code"] == "billing_disabled"


async def test_retired_discord_routes_are_not_registered(test_context) -> None:
    response = await test_context["client"].post(
        "/api/v1/discord/oauth/state",
        json={},
    )
    assert response.status_code == 404


async def test_operational_status_detail_requires_admin(test_context) -> None:
    for path in (
        "/api/v1/status/market-data",
        "/api/v1/status/integrations",
        "/api/v1/status/incidents",
    ):
        response = await test_context["client"].get(path)
        assert response.status_code == 401, path
