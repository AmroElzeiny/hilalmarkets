from __future__ import annotations

from pathlib import Path

from conftest import _run_async_in_thread, assert_no_raw_traceback, signup, unique_email
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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


def test_sharia_governance_workspace_visual_qa(
    page: Page,
    base_url: str,
    browser_app,
    repo_root: Path,
) -> None:
    email = signup(page, base_url, unique_email("governance-admin"))
    expect(page.locator('a[href^="/system-brain"]')).to_have_count(0)
    case_id = _promote_admin_and_seed_case(browser_app.database_url, email)
    output = repo_root / "reports" / "playwright" / "sharia-governance"
    output.mkdir(parents=True, exist_ok=True)

    page.goto(f"{base_url}/system-brain", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Evidence before publication")).to_be_visible()
    expect(page.get_by_text("Pending initial reviews")).to_be_visible()
    expect(page.get_by_text("SC-BTC-TEST")).to_be_visible()
    expect(page.locator("pre")).to_have_count(0)
    assert_no_raw_traceback(page)
    page.screenshot(path=str(output / "admin-overview-desktop-1440.png"), full_page=True)

    page.set_viewport_size({"width": 900, "height": 1000})
    page.screenshot(path=str(output / "admin-overview-tablet-900.png"), full_page=True)

    page.goto(f"{base_url}/system-brain/reviews/{case_id}", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Asset identity")).to_be_visible()
    expect(page.get_by_role("heading", name="Review each criterion")).to_be_visible()
    expect(page.get_by_role("button", name="Approve", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve with qualification")).to_be_visible()
    expect(page.get_by_label("Final written reasoning")).to_be_visible()
    page.screenshot(path=str(output / "review-case-tablet-900.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("heading", name="Review each criterion")).to_be_visible()
    page.screenshot(path=str(output / "review-case-mobile-390.png"), full_page=True)

    page.emulate_media(reduced_motion="reduce")
    animation_duration = page.locator(".brain-reveal").first.evaluate(
        "node => getComputedStyle(node).animationDuration"
    )
    assert animation_duration in {"0s", "1e-05s"}
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 900, "height": 1000})
    page.get_by_label("Final written reasoning").fill(
        "The retained source, identity mapping, dossier, and criteria were reviewed."
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve", exact=True).click()
    page.wait_for_url("**/system-brain/reviews/**?success=**")
    expect(page.get_by_role("button", name="Review publication summary")).to_be_visible()
    page.get_by_role("button", name="Review publication summary").click()
    publication = page.locator("[data-publication-dialog]")
    expect(publication).to_be_visible()
    publication.get_by_label("Publication reason").fill(
        "Publish the separately approved immutable Passport for customer evidence."
    )
    publication.get_by_role("button", name="Publish approved version").click()
    page.wait_for_url("**/system-brain/reviews/**?success=**")
    expect(page.locator(".brain-terminal strong")).to_have_text("Published")
    page.screenshot(path=str(output / "review-case-published-tablet-900.png"), full_page=True)
    assert_no_raw_traceback(page)
