import re

from sqlalchemy import func, select

from ai_market_monitor.db.models import AuditEvent, ReviewCase, User, UserIdentity
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


async def test_system_brain_renders_live_sharia_governance_workspace(test_context, monkeypatch):
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
    assert "Import SC Malaysia now" in dashboard.text
    assert 'data-testid="ai-operations-overview"' in dashboard.text
    assert "Live coordinator and public support" in dashboard.text
    assert "Forbidden executed" in dashboard.text
    assert "governance@example.com" in dashboard.text
    assert "<pre" not in dashboard.text
    assert "tojson" not in dashboard.text

    assert "hilalmarkets-brand.css" in dashboard.text
    stylesheet = (await test_context["client"].get("/static/system-brain.css")).text
    brand_stylesheet = (
        await test_context["client"].get("/static/hilalmarkets-brand.css")
    ).text
    assert "--hm-apple: #cbfa4d" in brand_stylesheet
    assert 'font-family: "Geometria"' in stylesheet
    assert "#0b3b31" not in stylesheet.lower()
    assert "prefers-reduced-motion" in stylesheet

    csrf_match = re.search(r'name="csrf_token" value="([a-f0-9]+)"', dashboard.text)
    assert csrf_match is not None
    queued: list[str] = []
    monkeypatch.setattr(
        "ai_market_monitor.worker.app.send_task",
        lambda task_name: queued.append(task_name),
    )
    imported = await test_context["client"].post(
        "/system-brain/sc-malaysia/import",
        data={"csrf_token": csrf_match.group(1)},
        headers=headers,
        follow_redirects=False,
    )
    assert imported.status_code == 303
    assert queued == ["ai_market_monitor.process_sc_malaysia_imports"]

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


async def test_review_action_enforces_admin_csrf_state_and_audit(test_context):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="review-actions@example.com",
    )
    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="review-actions-customer@example.com",
    )
    async with test_context["session_factory"]() as session:
        case = ReviewCase(
            case_reference="CHG-WEB-ACTION",
            case_type="material_source_change",
            state="ready_for_review",
            publication_state="change_under_review",
            title="Review reported source change",
            priority="normal",
            risk_severity="low",
            human_review_reason="A source report requires a recorded human decision.",
            requested_evidence=[],
            admin_notes=[],
            idempotency_key="web-review-action-test",
        )
        session.add(case)
        await session.commit()
        case_id = case.id

    headers = {"X-User-ID": str(admin.id)}
    detail = await test_context["client"].get(
        f"/system-brain/reviews/{case_id}", headers=headers
    )
    assert detail.status_code == 200
    assert "Dismiss false positive" in detail.text
    csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"', detail.text)
    assert csrf is not None
    form = {
        "action": "dismiss_false_positive",
        "reason": "The reviewed source content is unchanged and the report is not material.",
        "csrf_token": csrf.group(1),
    }

    denied = await test_context["client"].post(
        f"/system-brain/reviews/{case_id}/decision",
        data=form,
        headers={"X-User-ID": str(ordinary.id)},
        follow_redirects=False,
    )
    bad_csrf = await test_context["client"].post(
        f"/system-brain/reviews/{case_id}/decision",
        data={**form, "csrf_token": "invalid"},
        headers=headers,
        follow_redirects=False,
    )
    recorded = await test_context["client"].post(
        f"/system-brain/reviews/{case_id}/decision",
        data=form,
        headers=headers,
        follow_redirects=False,
    )
    replay = await test_context["client"].post(
        f"/system-brain/reviews/{case_id}/decision",
        data=form,
        headers=headers,
        follow_redirects=False,
    )

    assert denied.status_code == 403
    assert bad_csrf.status_code == 403
    assert recorded.status_code == 303
    assert "success=" in recorded.headers["location"]
    assert replay.status_code == 303
    assert "error=" in replay.headers["location"]

    async with test_context["session_factory"]() as session:
        stored = await session.get(ReviewCase, case_id)
        event_count = await session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "sharia.review_false_positive_dismissed",
                AuditEvent.target_id == str(case_id),
            )
        )

    assert stored is not None and stored.state == "superseded"
    assert event_count == 1
