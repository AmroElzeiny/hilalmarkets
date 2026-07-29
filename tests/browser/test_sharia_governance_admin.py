from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.browser.conftest import (
    _run_async_in_thread,
    assert_hilal_brand_palette,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    signup,
    unique_email,
)


def _promote_admin_and_seed_case(database_url: str | None, email: str) -> str:
    if not database_url:
        raise AssertionError("Governance visual QA requires the auto-started browser database.")

    async def _seed() -> str:
        from ai_market_monitor.db.models import (
            User,
            UserIdentity,
        )
        from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
        from tests.services.test_sc_malaysia_governance import _ready_case

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError("Signed-up browser user was not persisted.")
            user = await session.get(User, identity.user_id)
            if user is None:
                raise AssertionError("Signed-up browser user record was not found.")
            user.role = UserRole.ADMIN
            case, _ = await _ready_case(session)
            await session.commit()
            case_id = str(case.id)
        await engine.dispose()
        return case_id

    return _run_async_in_thread(_seed)


def _promote_admin_and_seed_customer(
    database_url: str | None,
    admin_email: str,
    customer_email: str,
) -> str:
    if not database_url:
        raise AssertionError("User-control testing requires the auto-started browser database.")

    async def _seed() -> str:
        from datetime import UTC, datetime

        from ai_market_monitor.db.models import User, UserIdentity
        from ai_market_monitor.db.models.enums import IdentityProvider, UserRole

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            admin_identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == admin_email.lower(),
                )
            )
            if admin_identity is None:
                raise AssertionError("Signed-up browser administrator was not persisted.")
            admin = await session.get(User, admin_identity.user_id)
            if admin is None:
                raise AssertionError("Signed-up browser administrator was not found.")
            admin.role = UserRole.ADMIN
            customer = User(display_name="Beta Customer")
            session.add(customer)
            await session.flush()
            session.add(
                UserIdentity(
                    user_id=customer.id,
                    provider=IdentityProvider.EMAIL,
                    provider_subject=customer_email,
                    normalized_identifier=customer_email,
                    display_identifier=customer_email,
                    is_verified=True,
                    is_primary=True,
                    verified_at=datetime.now(UTC),
                    profile_data={},
                )
            )
            await session.commit()
            customer_id = str(customer.id)
        await engine.dispose()
        return customer_id

    return _run_async_in_thread(_seed)


def test_system_brain_user_controls_use_branded_confirmation_dialog(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    admin_email = signup(page, base_url, unique_email("user-control-admin"))
    customer_email = unique_email("user-control-customer")
    customer_id = _promote_admin_and_seed_customer(
        browser_app.database_url,
        admin_email,
        customer_email,
    )
    native_dialogs: list[str] = []
    page.on(
        "dialog",
        lambda dialog: (native_dialogs.append(dialog.message), dialog.dismiss()),
    )

    page.goto(f"{base_url}/dashboard/system-brain/users", wait_until="networkidle")
    card = page.locator(f'[data-user-id="{customer_id}"]')
    expect(card).to_be_visible()
    expect(card).to_contain_text(customer_email)

    card.get_by_text("Upgrade", exact=True).click()
    card.locator(".brain-plan-option").filter(has_text="Full access").click()
    expect(card.locator('input[name="tier"][value="full_access"]')).to_be_checked()
    card.locator('input[name="months"]').fill("2")
    expect(card.get_by_role("button", name="Apply", exact=True)).to_be_visible()
    card.get_by_role("button", name="Apply", exact=True).click()

    confirmation = page.locator("[data-user-action-dialog]")
    expect(confirmation).to_be_visible()
    expect(confirmation).to_contain_text("Selected access: Full access for 2 months")
    confirmation.get_by_role("button", name="Cancel", exact=True).click()
    expect(confirmation).to_be_hidden()

    card.get_by_role("button", name="Ban", exact=True).click()
    expect(confirmation).to_be_visible()
    expect(confirmation.get_by_role("heading")).to_have_text("Ban this profile?")
    confirmation.get_by_role("button", name="Cancel", exact=True).click()
    card.get_by_role("button", name="Delete", exact=True).click()
    expect(confirmation).to_be_visible()
    expect(confirmation.get_by_role("heading")).to_have_text("Delete this profile?")
    confirmation.get_by_role("button", name="Cancel", exact=True).click()
    assert native_dialogs == []
    assert_no_horizontal_overflow(page)


def test_sharia_governance_workspace_visual_qa(
    page: Page,
    base_url: str,
    browser_app,
    repo_root: Path,
) -> None:
    email = signup(page, base_url, unique_email("governance-admin"))
    expect(page.locator('a[href^="/system-brain"]')).to_have_count(0)
    expect(page.locator('a[href^="/dashboard/system-brain"]')).to_have_count(0)
    case_id = _promote_admin_and_seed_case(browser_app.database_url, email)
    output = repo_root / "reports" / "playwright" / "sharia-governance"
    output.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/dashboard/system-brain", wait_until="networkidle")
    expect(page.get_by_role("heading", name="System Brain", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Needs Attention")).to_be_visible()
    expect(page.get_by_text("SC-BTC-TEST")).to_be_visible()
    expect(page.locator("pre")).to_have_count(0)
    assert "Onest" in page.locator("body").evaluate(
        "node => getComputedStyle(node).fontFamily"
    )
    assert "Geometria" in page.locator("h1").evaluate(
        "node => getComputedStyle(node).fontFamily"
    )
    assert_hilal_brand_palette(page)
    assert_no_raw_traceback(page)
    page.screenshot(path=str(output / "admin-overview-desktop-1440.png"), full_page=True)

    admin_routes = page.locator(".brain-sidebar nav a").evaluate_all(
        "links => [...new Set(links.map(link => link.getAttribute('href')))]"
    )
    for index, route in enumerate(admin_routes):
        target = route if route.startswith("http") else f"{base_url}{route}"
        page.goto(target, wait_until="domcontentloaded")
        expect(page.locator(".brain-main")).to_be_visible()
        assert_no_horizontal_overflow(page)
        assert_hilal_brand_palette(page)
        page.screenshot(
            path=str(output / f"admin-section-{index + 1:02d}.png"),
            full_page=True,
        )

    page.goto(f"{base_url}/dashboard/system-brain", wait_until="networkidle")

    page.set_viewport_size({"width": 900, "height": 1000})
    page.screenshot(path=str(output / "admin-overview-tablet-900.png"), full_page=True)

    page.goto(
        f"{base_url}/dashboard/system-brain/cases/{case_id}",
        wait_until="networkidle",
    )
    expect(page.get_by_text("Why this case exists")).to_be_visible()
    expect(page.get_by_role("heading", name="Verify each factual suggestion")).to_be_visible()
    expect(page.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve with qualification")).to_be_visible()
    expect(page.get_by_label("Decision rationale")).to_be_visible()
    page.screenshot(path=str(output / "review-case-tablet-900.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".brain-mobile-decision-note")).to_be_visible()
    expect(page.get_by_role("button", name="Approve", exact=True)).to_be_hidden()
    page.screenshot(path=str(output / "review-case-mobile-390.png"), full_page=True)

    page.emulate_media(reduced_motion="reduce")
    animation_duration = page.locator(".brain-reveal").first.evaluate(
        "node => getComputedStyle(node).animationDuration"
    )
    assert animation_duration in {"0s", "1e-05s"}
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 900, "height": 1000})
    page.get_by_text("Use-specific scope decisions", exact=True).click()
    for select_box in page.locator('select[name="criterion_outcome"]').all():
        select_box.select_option("pass")
    for explanation in page.locator('textarea[name="criterion_reason"]').all():
        explanation.fill("The retained evidence was reviewed for this required criterion.")
    use_statuses = {
        "asset_level_sc_reference": "covered",
        "spot_ownership_and_monitoring": "qualified",
        "native_staking": "not_applicable",
    }
    for fieldset in page.locator('select[name="use_decision"]').all():
        key = fieldset.locator("xpath=preceding-sibling::input[@name='use_key']").input_value()
        fieldset.select_option(use_statuses.get(key, "not_covered"))
    for explanation in page.locator('textarea[name="use_reason"]').all():
        explanation.fill("This use was explicitly reviewed against the retained evidence.")
    page.get_by_label("Decision rationale").fill(
        "The retained source, identity mapping, dossier, and criteria were reviewed."
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve", exact=True).click()
    page.wait_for_url("**/dashboard/system-brain/cases/**?success=**")
    expect(page.get_by_role("button", name="Review publication impact")).to_be_visible()
    page.get_by_role("button", name="Review publication impact").click()
    publication = page.locator("[data-publication-dialog]")
    expect(publication).to_be_visible()
    publication.get_by_label("Publication reason").fill(
        "Publish the separately approved immutable Passport for customer evidence."
    )
    publication.get_by_role("button", name="Publish approved version").click()
    page.wait_for_url("**/dashboard/system-brain/cases/**?success=**")
    expect(page.locator(".brain-terminal strong")).to_have_text("Published")
    page.screenshot(path=str(output / "review-case-published-tablet-900.png"), full_page=True)
    assert_no_raw_traceback(page)
