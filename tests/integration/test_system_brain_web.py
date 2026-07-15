from ai_market_monitor.db.models import User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole


async def _user(test_context, *, role: UserRole, email: str) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=email.split("@", 1)[0], role=role)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=email,
                normalized_identifier=email,
                display_identifier=email,
                is_verified=True,
                is_primary=True,
            )
        )
        await session.commit()
        return user


async def test_system_brain_requires_real_application_admin_role(test_context):
    missing = await test_context["client"].get("/system-brain")
    assert missing.status_code == 401

    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="customer@example.com",
    )
    support = await _user(
        test_context,
        role=UserRole.SUPPORT,
        email="support@example.com",
    )
    for user in (ordinary, support):
        response = await test_context["client"].get(
            "/system-brain",
            headers={"X-User-ID": str(user.id)},
        )
        assert response.status_code == 403

    customer_dashboard = await test_context["client"].get(
        "/dashboard",
        headers={"X-User-ID": str(ordinary.id)},
    )
    if customer_dashboard.status_code == 303:
        customer_dashboard = await test_context["client"].get(
            customer_dashboard.headers["location"],
            headers={"X-User-ID": str(ordinary.id)},
        )
    assert customer_dashboard.status_code == 200
    assert 'href="/system-brain"' not in customer_dashboard.text


async def test_system_brain_renders_live_sharia_governance_workspace(test_context):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="governance@example.com",
    )
    headers = {"X-User-ID": str(admin.id)}
    dashboard = await test_context["client"].get("/system-brain", headers=headers)

    assert dashboard.status_code == 200
    assert dashboard.headers["cache-control"] == "no-store, max-age=0"
    assert "Evidence before publication" in dashboard.text
    assert "Initial Coin Reviews" in dashboard.text
    assert "Published Assets" in dashboard.text
    assert "Telegram / Delivery" in dashboard.text
    assert "governance@example.com" in dashboard.text
    assert "<pre" not in dashboard.text
    assert "tojson" not in dashboard.text

    stylesheet = (await test_context["client"].get("/static/system-brain.css")).text
    assert "--emerald-950" in stylesheet
    assert "prefers-reduced-motion" in stylesheet

    for path in (
        "/system-brain/reviews?kind=initial_asset_review",
        "/system-brain/reviews?kind=material_source_change",
        "/system-brain/published-assets",
        "/system-brain/rejected-assets",
        "/system-brain/methodologies",
        "/system-brain/source-registry",
        "/system-brain/scraper-runs",
        "/system-brain/ai-assessments",
        "/system-brain/delivery-health",
        "/system-brain/audit-history",
    ):
        response = await test_context["client"].get(path, headers=headers)
        assert response.status_code == 200, path
        assert "<pre" not in response.text


async def test_system_brain_all_sections_enforce_admin_role(test_context):
    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="no-admin@example.com",
    )
    for path in (
        "/system-brain/reviews?kind=initial_asset_review",
        "/system-brain/published-assets",
        "/system-brain/source-registry",
        "/system-brain/scraper-runs",
        "/system-brain/ai-assessments",
        "/system-brain/delivery-health",
        "/system-brain/audit-history",
    ):
        response = await test_context["client"].get(
            path,
            headers={"X-User-ID": str(ordinary.id)},
        )
        assert response.status_code == 403


async def test_system_brain_can_require_cloudflare_access_before_admin(test_context):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="contact@trace-edge.com",
    )
    settings = test_context["settings"]
    settings.system_brain_cloudflare_access_required = True
    settings.system_brain_admin_username = "contact@trace-edge.com"
    headers = {"X-User-ID": str(admin.id)}

    missing = await test_context["client"].get("/system-brain", headers=headers)
    assert missing.status_code == 403

    wrong = await test_context["client"].get(
        "/system-brain",
        headers={
            **headers,
            "cf-access-authenticated-user-email": "other@example.com",
            "cf-access-jwt-assertion": "test-assertion",
        },
    )
    assert wrong.status_code == 403

    passed = await test_context["client"].get(
        "/system-brain",
        headers={
            **headers,
            "cf-access-authenticated-user-email": "contact@trace-edge.com",
            "cf-access-jwt-assertion": "test-assertion",
        },
    )
    assert passed.status_code == 200
