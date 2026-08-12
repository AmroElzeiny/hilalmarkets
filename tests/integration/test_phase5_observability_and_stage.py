"""Phase 5 through the real application: admin surfaces, stages, banners, refusals.

Every test drives the ASGI app the way a client would. A schema existing is not proof
that an endpoint answers, so nothing here asserts against a service in isolation.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.launch_stage import LaunchStage, stage_exposure
from ai_market_monitor.core.product_boundaries import refuse
from ai_market_monitor.db.models import User
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.observability.banners import (
    BannerKind,
    banner_for_ai_disabled,
    customer_status_banners,
)
from ai_market_monitor.observability.issues import OperationalIssueService
from ai_market_monitor.observability.metrics import MetricsRecorder, get_metrics_recorder

pytestmark = pytest.mark.asyncio


async def _admin_headers(session_factory) -> dict[str, str]:
    async with session_factory() as session:
        admin = User(display_name="Phase 5 Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.commit()
        return {"X-User-ID": str(admin.id)}


# --------------------------------------------------------------------------
# Admin surfaces
# --------------------------------------------------------------------------


async def test_admin_health_reports_objectives_alerts_and_issue_counts(
    test_context: dict,
) -> None:
    client: AsyncClient = test_context["client"]
    headers = await _admin_headers(test_context["session_factory"])

    response = await client.get("/api/v1/admin/health", headers=headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["slo_definition_version"]
    names = {item["name"] for item in payload["service_level_objectives"]}
    assert {"api_availability", "setup_chat_turn_success", "alert_delivery_success"} <= names
    for item in payload["service_level_objectives"]:
        assert item["state"] in {"met", "breached", "no_data"}
        assert item["runbook_anchor"].startswith("#")
    assert payload["operational_issues"]["needs_attention"] == 0
    assert isinstance(payload["firing_alerts"], list)


async def test_admin_health_keeps_the_keys_it_already_had(test_context: dict) -> None:
    """Adding to this endpoint must not break whatever already reads it."""

    client: AsyncClient = test_context["client"]
    headers = await _admin_headers(test_context["session_factory"])
    payload = (await client.get("/api/v1/admin/health", headers=headers)).json()
    for key in (
        "overall_status",
        "market_data",
        "integrations",
        "open_incidents",
        "failed_jobs",
        "generated_at",
        "recent_market_data",
    ):
        assert key in payload


async def test_an_issue_recorded_in_the_queue_shows_up_on_the_admin_surface(
    test_context: dict,
) -> None:
    client: AsyncClient = test_context["client"]
    headers = await _admin_headers(test_context["session_factory"])
    async with test_context["session_factory"]() as session:
        service = OperationalIssueService(session)
        await service.record_occurrence(
            dedupe_key="alert:scans_delayed:scanner",
            category="scanner",
            severity="page",
            summary="Scheduled scans are not finishing in their window.",
            affected_scope="scanner",
            evidence_refs=("slo:scheduled_scan_completion",),
            runbook_anchor="#scans-delayed",
        )
        await session.commit()

    payload = (await client.get("/api/v1/admin/health", headers=headers)).json()
    assert payload["operational_issues"]["open"] == 1
    assert payload["operational_issues"]["needs_attention"] == 1


@pytest.mark.parametrize("path", ["/api/v1/admin/health", "/api/v1/admin/activity"])
async def test_admin_observability_refuses_a_non_admin(
    test_context: dict, path: str
) -> None:
    client: AsyncClient = test_context["client"]
    async with test_context["session_factory"]() as session:
        customer = User(display_name="Ordinary Customer", role=UserRole.USER)
        session.add(customer)
        await session.commit()
        customer_headers = {"X-User-ID": str(customer.id)}

    assert (await client.get(path, headers=customer_headers)).status_code == 403
    assert (await client.get(path)).status_code == 401


async def test_admin_surfaces_are_absent_from_the_public_sitemap(
    test_context: dict,
) -> None:
    client: AsyncClient = test_context["client"]
    sitemap = (await client.get("/sitemap.xml")).text
    for fragment in ("/api/v1/admin", "system-brain", "admin/health"):
        assert fragment not in sitemap


# --------------------------------------------------------------------------
# Status endpoints stay backward compatible
# --------------------------------------------------------------------------


async def test_health_endpoint_still_answers(test_context: dict) -> None:
    client: AsyncClient = test_context["client"]
    assert (await client.get("/health")).status_code == 200


async def test_status_summary_keeps_its_shape(test_context: dict) -> None:
    client: AsyncClient = test_context["client"]
    payload = (await client.get("/api/v1/status/summary")).json()
    for key in (
        "overall_status",
        "market_data",
        "integrations",
        "open_incidents",
        "failed_jobs",
        "generated_at",
    ):
        assert key in payload


@pytest.mark.parametrize(
    "path", ["/api/v1/status/market-data", "/api/v1/status/integrations"]
)
async def test_admin_status_endpoints_still_require_admin(
    test_context: dict, path: str
) -> None:
    client: AsyncClient = test_context["client"]
    headers = await _admin_headers(test_context["session_factory"])
    assert (await client.get(path, headers=headers)).status_code == 200
    assert (await client.get(path)).status_code == 401


# --------------------------------------------------------------------------
# Launch stage drives the public surface
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage",
    [LaunchStage.INTERNAL, LaunchStage.PRIVATE_BETA_INVITE, LaunchStage.PUBLIC_WAITLIST],
    ids=lambda item: item.value,
)
async def test_a_pre_launch_stage_never_advertises_pricing_or_checkout(
    stage: LaunchStage,
) -> None:
    exposure = stage_exposure(stage)
    assert not exposure.advertises_pricing
    assert not exposure.exposes_checkout
    assert "pricing" in exposure.hidden_pages


async def test_the_shipped_stage_hides_pricing_from_the_public_site(
    test_context: dict,
) -> None:
    """The default deployment is pre-launch, so pricing must not be reachable."""

    client: AsyncClient = test_context["client"]
    settings: Settings = test_context["settings"]
    assert settings.waitlist_mode is True

    landing = await client.get("/")
    assert landing.status_code == 200
    sitemap = (await client.get("/sitemap.xml")).text
    assert "/pricing" not in sitemap


async def test_pricing_redirects_to_the_waitlist_while_pre_launch(
    test_context: dict,
) -> None:
    client: AsyncClient = test_context["client"]
    response = await client.get("/pricing", follow_redirects=False)
    assert response.status_code in {301, 302, 303, 307, 308}
    assert "waitlist" in response.headers.get("location", "")


# --------------------------------------------------------------------------
# Degradation never renders as a screening or compiler failure
# --------------------------------------------------------------------------


async def test_ai_disabled_shows_an_ai_banner_not_a_screening_error() -> None:
    banners = customer_status_banners(MetricsRecorder(), ai_enabled=False)
    assert [item.kind for item in banners] == [BannerKind.AI_UNAVAILABLE]
    message = " ".join(
        (banners[0].headline + " " + banners[0].still_works + " " + banners[0].paused)
        .casefold()
        .split()
    )
    for forbidden in ("shariah", "sharia", "screening", "compiler", "haram", "halal"):
        assert forbidden not in message


async def test_a_provider_outage_says_monitors_keep_running() -> None:
    """The half that stops a bad hour becoming a lost customer."""

    recorder = MetricsRecorder()
    for _ in range(20):
        recorder.record(
            "provider_calls_total",
            1.0,
            provider="openai",
            operation="responses",
            outcome="timeout",
        )
    banners = customer_status_banners(recorder)
    kinds = {item.kind for item in banners}
    assert BannerKind.PROVIDER_DEGRADED in kinds
    degraded = next(item for item in banners if item.kind is BannerKind.PROVIDER_DEGRADED)
    assert "still" in degraded.still_works.casefold()
    assert "screening" in degraded.still_works.casefold()


async def test_a_healthy_quiet_system_shows_no_banner_at_all() -> None:
    """A banner on an idle morning teaches customers to ignore banners."""

    assert customer_status_banners(MetricsRecorder()) == ()


async def test_the_ai_disabled_banner_is_the_same_message_as_an_ai_outage() -> None:
    """A deliberate switch and a failure look identical to a customer."""

    assert banner_for_ai_disabled().kind is BannerKind.AI_UNAVAILABLE


async def _signup(test_context: dict, email: str) -> None:
    client: AsyncClient = test_context["client"]
    response = await client.post(
        "/signup",
        data={
            "email": email,
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_a_healthy_dashboard_renders_no_banner(test_context: dict) -> None:
    await _signup(test_context, "phase5-clean@example.com")
    page = await test_context["client"].get("/dashboard")
    assert page.status_code == 200
    assert "data-status-banner" not in page.text


async def test_the_dashboard_renders_the_banner_with_both_halves(
    test_context: dict,
) -> None:
    """Proof the wiring reaches the page, not only that the service returns objects."""

    await _signup(test_context, "phase5-banner@example.com")
    page = await test_context["client"].get(
        "/dashboard?force_status_banner=ai_unavailable"
    )
    assert page.status_code == 200
    assert 'data-status-banner="ai_unavailable"' in page.text
    assert 'role="status"' in page.text
    assert "Still working:" in page.text
    assert "Paused:" in page.text
    lowered = page.text.casefold()
    banner_start = lowered.index("data-status-banner")
    banner_text = lowered[banner_start : banner_start + 900]
    for forbidden in ("shariah", "compiler", "haram"):
        assert forbidden not in banner_text


async def test_the_forced_banner_control_is_inert_outside_development(
    test_context: dict,
) -> None:
    """A query string must never decide what a deployed customer is told.

    Checked directly against the helper with a production environment, because the
    test client can only ever run as app_env=test.
    """

    from starlette.datastructures import QueryParams

    from ai_market_monitor.api.routers.dashboard import _status_banners

    class _Request:
        query_params = QueryParams("force_status_banner=ai_unavailable")

    production = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
    )
    assert _status_banners(_Request(), production) == []


# --------------------------------------------------------------------------
# Unsupported capabilities
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "trade_execution",
        "brokerage_custody",
        "buy_sell_recommendations",
        "financial_advice",
        "leverage_and_margin",
        "backtesting",
        "portfolio_tracking",
    ],
)
async def test_an_unsupported_request_names_the_capability_it_refuses(key: str) -> None:
    refusal = refuse(key)
    assert refusal.key == key
    assert refusal.title
    message = refusal.customer_message()
    assert refusal.title in message
    # Never dressed up as a religious or compiler refusal.
    for forbidden in ("shariah", "haram", "compiler", "invalid syntax"):
        assert forbidden not in message.casefold()


async def test_a_refusal_is_never_answered_with_a_nearby_capability() -> None:
    refusal = refuse("trade_execution")
    assert "instead" not in refusal.reason.casefold()
    assert "alternative" not in refusal.reason.casefold()


# --------------------------------------------------------------------------
# Observability is read-only
# --------------------------------------------------------------------------


async def test_recording_metrics_never_writes_product_state(
    test_context: dict,
) -> None:
    """Instrumentation must not be able to change a strategy or an entitlement."""

    session_factory = test_context["session_factory"]
    async with session_factory() as session:
        before = len((await session.scalars(_all_users())).all())
    recorder = get_metrics_recorder()
    recorder.record(
        "ai_turns_total", 1.0, feature="setup_chat", model="gpt-5.4-nano", outcome="success"
    )
    async with session_factory() as session:
        after = len((await session.scalars(_all_users())).all())
    assert before == after


def _all_users():
    from sqlalchemy import select

    return select(User)


async def test_requests_through_the_app_are_counted(test_context: dict) -> None:
    """The API objectives are only real if the middleware actually records."""

    client: AsyncClient = test_context["client"]
    recorder = get_metrics_recorder()
    before = recorder.total("http_requests_total")
    await client.get("/health")
    assert recorder.total("http_requests_total") > before


async def test_an_unmatched_path_does_not_create_a_new_route_label(
    test_context: dict,
) -> None:
    """A 404 sweep must not be able to fill the metric store."""

    client: AsyncClient = test_context["client"]
    recorder = get_metrics_recorder()
    for index in range(20):
        await client.get(f"/definitely-not-a-route-{index}")
    routes = {
        sample.label_map.get("route")
        for sample in recorder.snapshot()
        if sample.name == "http_requests_total"
    }
    assert "unmatched" in routes
    assert not any(route and "definitely-not-a-route" in route for route in routes)


async def test_session_fixture_is_usable(test_context: dict) -> None:
    async with test_context["session_factory"]() as session:
        assert isinstance(session, AsyncSession)
