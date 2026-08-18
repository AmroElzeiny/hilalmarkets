import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AuditEvent,
    CustomerConversationEvent,
    PublicChatConversation,
    PublicChatMessage,
    ReviewCase,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.schemas.system_brain import SystemBrainAgentTurnResponse


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
    dashboard = await test_context["client"].get("/dashboard/system-brain", headers=headers)

    assert dashboard.status_code == 200
    assert dashboard.headers["cache-control"] == "no-store, max-age=0"
    assert "Needs attention" in dashboard.text
    assert "Ask System Brain" in dashboard.text
    assert 'data-testid="system-brain-assistant"' in dashboard.text
    assert 'href="/dashboard/system-brain/cases"' in dashboard.text
    assert 'href="/dashboard/system-brain/operations"' in dashboard.text
    assert 'href="/dashboard/system-brain/governance"' in dashboard.text
    assert 'href="/dashboard/system-brain/audit-settings"' in dashboard.text
    assert "AI token usage" not in dashboard.text
    assert "Customer growth" not in dashboard.text
    assert "governance@example.com" in dashboard.text
    assert "<pre" not in dashboard.text
    assert "tojson" not in dashboard.text

    assert "hilalmarkets-brand.css" in dashboard.text
    stylesheet = (await test_context["client"].get("/static/system-brain.css")).text
    brand_stylesheet = (await test_context["client"].get("/static/hilalmarkets-brand.css")).text
    assert "--hm-apple: #cbfa4d" in brand_stylesheet
    # The page asks for the brand display face by token, and loads the one file that
    # both declares the face and defines the token. Naming the family here again would
    # be a second copy of the answer, free to drift from the first.
    assert "font-family: var(--hm-font-display)" in stylesheet
    assert "hilalmarkets-fonts.css" in dashboard.text
    fonts_stylesheet = (await test_context["client"].get("/static/hilalmarkets-fonts.css")).text
    assert "--hm-font-display:" in fonts_stylesheet
    assert "@font-face" in fonts_stylesheet
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
        "/dashboard/system-brain/authority-sources/import",
        data={"csrf_token": csrf_match.group(1)},
        headers=headers,
        follow_redirects=False,
    )
    assert imported.status_code == 303
    assert queued == ["ai_market_monitor.process_sharia_authority_imports"]

    for path in (
        "/dashboard/system-brain/cases",
        "/dashboard/system-brain/operations",
        "/dashboard/system-brain/governance",
        "/dashboard/system-brain/audit-settings",
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


async def test_system_brain_assistant_is_admin_scoped_and_read_only(
    test_context,
    monkeypatch,
):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="brain-assistant@example.com",
    )
    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="brain-assistant-customer@example.com",
    )
    headers = {"X-User-ID": str(admin.id)}
    dashboard = await test_context["client"].get(
        "/dashboard/system-brain",
        headers=headers,
    )
    csrf = re.search(
        r'data-system-brain-csrf="([a-f0-9]+)"',
        dashboard.text,
    )
    assert csrf is not None

    async def fake_answer(_service, _session, _conversation_id, *, admin_user_id, request):
        assert admin_user_id == admin.id
        assert request.message == "Why is this queue waiting?"
        return SystemBrainAgentTurnResponse(
            conversation_id=uuid4(),
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            run_id=uuid4(),
            status="completed",
            answer="One retained case is waiting for independent evidence review.",
            findings=[
                {
                    "title": "Review required",
                    "detail": "The case remains ready for review.",
                    "severity": "attention",
                    "evidence_ref": "case:bounded-test",
                }
            ],
            suggested_actions=[
                {
                    "label": "Open the case",
                    "rationale": "Review its retained source snapshots.",
                }
            ],
            evidence_refs=["case:bounded-test"],
            limitations=["No terminal action was taken."],
            opportunities=[],
            tool_calls=[{"tool_name": "review_queue_summary", "status": "success"}],
            model="gpt-5.4-nano",
            reasoning_effort="low",
            usage={"input_tokens": 10, "output_tokens": 10},
            latency_ms=12,
        )

    monkeypatch.setattr(
        "ai_market_monitor.api.routers.system_brain.SystemBrainAgentService.run_turn",
        fake_answer,
    )
    denied = await test_context["client"].post(
        "/dashboard/system-brain/assistant",
        json={"message": "Why is this queue waiting?", "history": []},
        headers={
            "X-User-ID": str(ordinary.id),
            "X-CSRF-Token": csrf.group(1),
        },
    )
    answered = await test_context["client"].post(
        "/dashboard/system-brain/assistant",
        json={"message": "Why is this queue waiting?", "history": []},
        headers={**headers, "X-CSRF-Token": csrf.group(1)},
    )

    assert denied.status_code == 403
    assert answered.status_code == 200
    payload = answered.json()
    assert payload["model"] == "gpt-5.4-nano"
    assert payload["reasoning_effort"] == "low"
    assert payload["evidence_refs"] == ["case:bounded-test"]
    assert "approve" not in payload["answer"].casefold()


async def test_operational_workspace_apis_persist_and_audit_exact_transcripts(test_context):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="workspace-admin@example.com",
    )
    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="workspace-customer@example.com",
    )
    admin_headers = {"X-User-ID": str(admin.id)}
    page = await test_context["client"].get("/dashboard/system-brain", headers=admin_headers)
    csrf = re.search(r'data-system-brain-csrf="([a-f0-9]+)"', page.text)
    assert csrf is not None
    write_headers = {**admin_headers, "X-CSRF-Token": csrf.group(1)}

    created = await test_context["client"].post(
        "/api/v1/system-brain/conversations",
        headers=write_headers,
        json={"title": "Revenue evidence"},
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store, max-age=0"
    conversation_id = created.json()["conversation_id"]
    renamed = await test_context["client"].patch(
        f"/api/v1/system-brain/conversations/{conversation_id}",
        headers=write_headers,
        json={"title": "Retained revenue evidence"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Retained revenue evidence"

    now = datetime.now(UTC)
    public_id = uuid4()
    public_message_id = uuid4()
    async with test_context["session_factory"]() as session:
        session.add(
            PublicChatConversation(
                id=public_id,
                session_key_hash="a" * 64,
                state_json={},
                stage="ANSWER",
                message_count=1,
                model="test-model",
                expires_at=now + timedelta(days=7),
            )
        )
        session.add(
            PublicChatMessage(
                id=public_message_id,
                conversation_id=public_id,
                sequence=1,
                role="user",
                content="What does the monitor do?",
                telemetry_redacted={},
                created_at=now,
                retain_until=now + timedelta(days=7),
            )
        )
        session.add(
            CustomerConversationEvent(
                id=1,
                source_type="public_site_chat",
                conversation_id=public_id,
                event_type="message_persisted",
                message_id=public_message_id,
                occurred_at=now,
            )
        )
        await session.commit()

    listed = await test_context["client"].get(
        "/api/v1/system-brain/customer-conversations?source=public_site_chat",
        headers=admin_headers,
    )
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store, max-age=0"
    item = next(row for row in listed.json()["items"] if row["conversation_id"] == str(public_id))
    assert item["display_name"] == "Anonymous visitor"
    assert item["user_email"] is None

    detail = await test_context["client"].get(
        f"/api/v1/system-brain/customer-conversations/public_site_chat/{public_id}",
        params={"access_reason": "support quality review"},
        headers=admin_headers,
    )
    assert detail.status_code == 200
    assert [message["content"] for message in detail.json()["messages"]] == [
        "What does the monitor do?"
    ]
    events = await test_context["client"].get(
        "/api/v1/system-brain/customer-conversation-events?after_id=0",
        headers=admin_headers,
    )
    assert events.status_code == 200
    assert events.json()["items"][0]["message_id"] == str(public_message_id)
    denied = await test_context["client"].get(
        "/api/v1/system-brain/customer-conversation-events?after_id=0",
        headers={"X-User-ID": str(ordinary.id)},
    )
    assert denied.status_code == 403

    async with test_context["session_factory"]() as session:
        viewed = int(
            await session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "system_brain.customer_conversation.view",
                    AuditEvent.target_id == str(public_id),
                )
            )
            or 0
        )
    assert viewed == 1


async def test_system_brain_all_sections_enforce_admin_role(test_context):
    ordinary = await _user(
        test_context,
        role=UserRole.USER,
        email="no-admin@example.com",
    )
    for path in (
        "/dashboard/system-brain",
        "/dashboard/system-brain/cases",
        "/dashboard/system-brain/operations",
        "/dashboard/system-brain/governance",
        "/dashboard/system-brain/audit-settings",
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


async def test_review_queue_hides_published_cases_but_keeps_the_registry_filter(
    test_context,
):
    admin = await _user(
        test_context,
        role=UserRole.ADMIN,
        email="published-queue-admin@example.com",
    )
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                ReviewCase(
                    case_reference="QUEUE-PUBLISHED",
                    case_type="initial_asset_review",
                    state="published",
                    publication_state="published",
                    title="Published asset should stay in the audit registry",
                    priority="normal",
                    risk_severity="none",
                    human_review_reason="Publication completed.",
                    idempotency_key="queue-published-case",
                ),
                ReviewCase(
                    case_reference="QUEUE-OPEN",
                    case_type="initial_asset_review",
                    state="ready_for_review",
                    publication_state="unpublished",
                    title="Open asset needs a review",
                    priority="normal",
                    risk_severity="none",
                    human_review_reason="Human review is still required.",
                    idempotency_key="queue-open-case",
                ),
            ]
        )
        await session.commit()

    headers = {"X-User-ID": str(admin.id)}
    queue = await test_context["client"].get(
        "/dashboard/system-brain/cases",
        headers=headers,
    )
    assert queue.status_code == 200
    assert "Open asset needs a review" in queue.text
    assert "Published asset should stay in the audit registry" not in queue.text

    published = await test_context["client"].get(
        "/dashboard/system-brain/cases?state=published",
        headers=headers,
    )
    assert published.status_code == 200
    assert "Published asset should stay in the audit registry" in published.text


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
    detail = await test_context["client"].get(f"/system-brain/reviews/{case_id}", headers=headers)
    assert detail.status_code == 200
    # The action, not its label. The button is called "False alarm" on the page now, and
    # a test that pinned the words would fail on a wording change while a test that
    # pinned the wrong action would pass on a broken one.
    assert 'value="dismiss_false_positive"' in detail.text
    # The one-click path for "every condition was met" is on the page, and it is a plain
    # button: it fills the form in, it never submits it, so the reviewer still decides.
    assert "data-approve-all" in detail.text
    assert 'type="button"' in detail.text.split("data-approve-all", 1)[0].rsplit("<button", 1)[1]
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
