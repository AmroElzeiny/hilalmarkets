from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.browser.conftest import (
    _run_async_in_thread,
    assert_hilal_brand_palette,
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    seed_system_brain_reviewer,
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


def test_research_is_nowhere_near_the_decision_bar(
    page: Page,
    base_url: str,
    browser_app,
) -> None:
    """Measured in a real browser, because the claims are about what a person sees.

    Three of them, and none can be proved by reading the files:

    * with nothing ticked, **neither** bar is on screen. The Cases page loads only the
      brand tokens and its own stylesheet, so there is no reset behind them: the browser's
      own ``[hidden] { display: none }`` is the weakest rule there is, and the decision
      bar's ``display: grid`` beat it. Approve and Reject sat in front of the reviewer all
      the time, with nothing selected;
    * once a case is ticked, both appear — and the research button is a long way below the
      decision bar, so a hand aiming at one is nowhere near the other;
    * the research button is one line tall. Every ``svg`` on this page is a block, and
      ``.brain-secondary-button`` never laid its icon inline, so the icon dropped above
      the words.
    """

    email = signup(page, base_url, unique_email("research-admin"))
    # `seed_system_brain_reviewer`, not `_promote_admin_and_seed_case`. The second one
    # builds the full SC-Malaysia fixture, whose monitoring run has a **fixed**
    # idempotency key — so two tests in this file sharing one browser database would make
    # the later one fail on a unique constraint. This test only needs a reviewer and one
    # row in the list, which is exactly what this seeder makes, with a fresh key.
    seed_system_brain_reviewer(browser_app.database_url, email)

    page.goto(f"{base_url}/dashboard/system-brain/cases", wait_until="networkidle")
    decision = page.locator("#bulk-decision-form")
    research = page.locator("#case-research-form")

    # Nothing ticked: nothing offered.
    expect(decision).to_be_hidden()
    expect(research).to_be_hidden()

    page.locator("[data-case-select]").first.check()
    expect(decision).to_be_visible()
    expect(research).to_be_visible()

    approve = decision.get_by_role("button", name=re.compile("Approve", re.I))
    run = research.get_by_role("button", name=re.compile("Run research", re.I))
    approve_box = approve.bounding_box()
    run_box = run.bounding_box()
    assert approve_box and run_box
    gap = run_box["y"] - (approve_box["y"] + approve_box["height"])
    assert gap > 200, (
        f"Run research sits {gap:.0f}px below Approve; it must be far enough away that "
        "a mis-click cannot reach the decision"
    )

    # One line tall, so the icon is beside the label and not stacked over it.
    assert run_box["height"] < 60, f"the research button is {run_box['height']:.0f}px tall"

    # And the two forms really do go to two different places.
    assert research.get_attribute("action").split("?")[0].endswith("/cases/research")
    assert decision.get_attribute("action").split("?")[0].endswith("/cases/bulk-decision")
    # Both bars are only on screen once something is ticked, so the section walk in the
    # visual QA test never sees them. This is the only place their colours are measured.
    assert_hilal_brand_palette(page)
    assert_no_horizontal_overflow(page)
    assert_no_raw_traceback(page)


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
    expect(page.get_by_role("heading", name="Needs attention")).to_be_visible()
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
    # The review page was rewritten to carry the facts and the controls, and none of the
    # sentences that explained what a panel was for. These are what a reviewer now sees.
    #
    # The button is "Approve & publish", not "Approve". It was renamed when approving and
    # publishing became one governed step, and this file still asked for the old name —
    # nothing caught it, because the palette check a few lines above failed first and this
    # part of the test had not run since.
    expect(page.get_by_text("AI suggestions by field")).to_be_visible()
    expect(page.get_by_role("button", name="Approve & publish", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve with note")).to_be_visible()
    expect(page.get_by_role("button", name="Mark all as passed")).to_be_visible()
    expect(page.get_by_label("Your reason")).to_be_visible()
    page.screenshot(path=str(output / "review-case-tablet-900.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".brain-mobile-decision-note")).to_be_visible()
    expect(page.get_by_role("button", name="Approve & publish", exact=True)).to_be_hidden()
    page.screenshot(path=str(output / "review-case-mobile-390.png"), full_page=True)

    page.emulate_media(reduced_motion="reduce")
    animation_duration = page.locator(".brain-reveal").first.evaluate(
        "node => getComputedStyle(node).animationDuration"
    )
    assert animation_duration in {"0s", "1e-05s"}
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 900, "height": 1000})

    # "Mark all as passed" is the one-click path a reviewer takes when every condition is
    # met. It must select `pass` on every condition — and only where the methodology
    # allows it — and it must never submit anything by itself.
    page.get_by_role("button", name="Mark all as passed").click()
    outcomes = page.locator('select[name="criterion_outcome"]')
    assert outcomes.count() > 0
    for index in range(outcomes.count()):
        assert outcomes.nth(index).input_value() == "pass"
    expect(page).to_have_url(re.compile(r"/dashboard/system-brain/cases/[0-9a-f-]+$"))

    page.get_by_text("Use rules", exact=True).click()
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
    page.get_by_label("Your reason").fill(
        "The retained source, identity mapping, dossier, and criteria were reviewed."
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Approve & publish", exact=True).click()
    page.wait_for_url("**/dashboard/system-brain/cases/**?success=**")
    # Approving **is** publishing now: a reviewer approving an asset is asking for it to
    # be in front of customers, so the two run together as two recorded governed steps
    # rather than two clicks. This file still walked the old second click and its
    # "Publish…" dialog, which no longer exists on this path — and nothing caught that,
    # because the palette check earlier in the test failed first and none of this had run
    # since. The separate dialog still exists for the cases that legitimately wait (a
    # second reviewer, or written permission not yet recorded); this one does not.
    expect(page.get_by_role("button", name="Publish…")).to_have_count(0)
    expect(page.locator(".brain-terminal strong")).to_have_text("Published")
    page.screenshot(path=str(output / "review-case-published-tablet-900.png"), full_page=True)
    assert_no_raw_traceback(page)
