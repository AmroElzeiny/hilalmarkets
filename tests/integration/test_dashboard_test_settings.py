"""`/dashboard/settings` must render, and every control on it must really save.

The hardest rule this page is held to is H1: *a setting with no backend reader does not
ship*. So most of these tests do not look at the page at all — they change a setting the
way the page changes it, then ask the product's own reader what it now believes.

The rules are in `docs/dashboard-test-account-rules.md`. The findings these fix are in
`docs/ACCOUNT_PAGES_REPORT.md`.
"""

from __future__ import annotations

import lxml.html
import pytest
from sqlalchemy import select

from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import DashboardPreference, User, UserIdentity
from ai_market_monitor.db.models.enums import DeliveryChannel, IdentityProvider
from ai_market_monitor.services.notification_preferences import (
    NotificationPreferenceService,
    symbol_is_muted,
)
from tests.integration.test_dashboard_web import _signup_and_verify

SETTINGS = "/dashboard/settings"
SAVE = "/api/v1/dashboard/preferences/settings"

#: A complete, valid submission. Tests change one thing in it and send the rest, exactly
#: as the page does — the whole set, never one field.
WHOLE_SET = {
    "timezone": "Europe/London",
    "near_miss_enabled": True,
    "near_miss_threshold": 70,
    "maximum_alerts_per_hour": 50,
    "maximum_alerts_per_day": 500,
    "alert_channels": ["telegram"],
    "providers": ["binance", "bybit"],
    "alert_days": ["Every Day"],
    "alert_hours": [],
    "finished_opportunity_alerts": True,
    "muted_symbols": [],
    "compliance_alert_channels": ["telegram"],
    "compliance_alert_digest": "immediate",
    "dashboard_notifications_enabled": True,
    "dashboard_notification_sound": "chime",
    "forming_dashboard_notifications": False,
    "forming_notification_sound": "pulse",
    "qualification_change_alerts": True,
}


async def _account(test_context, email: str):
    """Sign somebody up and hand back their id and their form token."""

    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == email,
            )
        )
    assert user_id is not None
    return user_id, csrf_token(test_context["settings"], user_id)


async def _save(test_context, token: str, **changes):
    """Save the whole set with some of it changed, the way the page does."""

    return await test_context["client"].put(
        SAVE, headers={"X-CSRF-Token": token}, json={**WHOLE_SET, **changes}
    )


async def _stored(test_context, user_id) -> dict:
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(
            select(DashboardPreference).where(DashboardPreference.user_id == user_id)
        )
        return dict(preference.notification_preferences or {}) if preference else {}


async def _page(test_context, email: str) -> str:
    await _signup_and_verify(test_context, email=email)
    response = await test_context["client"].get(SETTINGS)
    assert response.status_code == 200, response.text[:800]
    return response.text


def _words(markup: str) -> str:
    document = lxml.html.fromstring(markup)
    for node in document.xpath("//script | //style | //template"):
        node.getparent().remove(node)
    return " ".join(document.text_content().split())


# ── The page ────────────────────────────────────────────────────────────────


async def test_the_page_renders(test_context):
    page = await _page(test_context, "set-render@example.com")

    assert "hm-settings-test.js" in page
    assert "hm-account-test.css" in page
    assert "Settings" in page


async def test_every_setting_says_what_it_does_for_the_person(test_context):
    """Rule H3. The live page put a bare label beside a box — "Near-miss alerts",
    "Maximum alerts per hour" — and left a beginner to work out the rest."""

    page = await _page(test_context, "set-says@example.com")
    document = lxml.html.fromstring(page)

    rows = document.xpath('//div[contains(@class,"g-row-copy")]')
    assert rows
    for row in rows:
        assert row.xpath("./strong"), "a setting with no name"
        assert row.xpath("./span"), "a setting with no explanation"


async def test_a_setting_with_a_consequence_says_it_before_it_happens(test_context):
    """Rule H4. Unticking an exchange stops every Watchlist that uses it — `scanner.py`
    and `strategy.py` both refuse to scan one. The live page said "Highlighted providers
    are available to Scanner and Watchlist universe checks", which describes a mechanism
    rather than a consequence."""

    page = await _page(test_context, "set-consequence@example.com")
    words = _words(page)

    assert "stops every Watchlist that uses it" in words
    assert "universe checks" not in words


async def test_no_hour_chosen_is_explained_as_any_time_not_as_never(test_context):
    """The product treats an empty hour list as "any hour". A page that let somebody
    read an empty grid as "never" would be wrong in the most expensive direction."""

    page = await _page(test_context, "set-hours@example.com")

    assert "data-g-hours-words" in page


async def test_the_one_setting_with_a_lasting_effect_asks_before_it_acts(test_context):
    """Rule H4 and D5. Every other control here is undone by pressing it again; this one
    stops Watchlists looking at the market. It asks in a real dialog, not a browser
    confirm box — which is unstyled, unbranded, and not announced as a choice."""

    page = await _page(test_context, "set-ask@example.com")

    assert "data-g-ask-dialog" in page
    assert "Keep watching it" in page
    assert "Stop watching it" in page


async def test_the_switch_is_a_real_switch(test_context):
    """Rule D2 and D3: state in the accessibility tree, not only in the pixels."""

    page = await _page(test_context, "set-switch@example.com")

    assert 'role="switch"' in page
    assert "aria-checked=" in page


async def test_a_channel_that_cannot_be_chosen_says_why(test_context):
    """Rule H8. A greyed-out box with no reason is a dead end."""

    page = await _page(test_context, "set-locked@example.com")
    words = _words(page)

    assert "not switched on for this platform yet" in words or "not part of your plan" in words


@pytest.mark.parametrize(
    "jargon",
    [
        "Near-miss threshold",
        "compliance",
        "Evidence-change delivery",
        "Spot market providers",
        "universe checks",
        "Alert behavior",
        "digest",
    ],
)
async def test_no_word_from_inside_the_machine_reaches_the_page(test_context, jargon):
    """Rule E2. Every one of these was on the live page."""

    page = await _page(test_context, f"set-plain-{abs(hash(jargon))}@example.com")
    assert jargon.lower() not in _words(page).lower(), jargon


@pytest.mark.parametrize(
    "claim",
    ["100% halal", "guaranteed", "risk-free", "buy now", "AI trades for you"],
)
async def test_no_forbidden_claim_reaches_the_page(test_context, claim):
    page = await _page(test_context, f"set-claim-{abs(hash(claim))}@example.com")
    assert claim.lower() not in _words(page).lower(), claim


# ── Every control really saves ──────────────────────────────────────────────


async def test_saving_needs_a_form_token(test_context):
    await _signup_and_verify(test_context, email="set-csrf@example.com")

    response = await test_context["client"].put(SAVE, json=WHOLE_SET)
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("field", "sent", "key", "expected"),
    [
        ("timezone", "Asia/Dubai", "timezone", "Asia/Dubai"),
        ("near_miss_enabled", False, "near_miss_enabled", False),
        ("near_miss_threshold", 35, "near_miss_threshold", 35),
        ("maximum_alerts_per_hour", 7, "maximum_alerts_per_hour", 7),
        ("maximum_alerts_per_day", 9, "maximum_alerts_per_day", 9),
        ("finished_opportunity_alerts", False, "lifecycle_enabled", False),
        ("compliance_alert_digest", "daily", "compliance_alert_digest", "daily"),
        ("dashboard_notifications_enabled", False, "dashboard_notifications_enabled", False),
        ("dashboard_notification_sound", "bell", "dashboard_notification_sound", "bell"),
        ("forming_dashboard_notifications", True, "forming_dashboard_notifications", True),
        ("forming_notification_sound", "soft", "forming_notification_sound", "soft"),
        ("qualification_change_alerts", False, "qualification_change_alerts", False),
        ("providers", ["bybit"], "providers", ["bybit"]),
        ("alert_days", ["Monday", "Friday"], "alert_days", ["Monday", "Friday"]),
        ("alert_hours", ["09:00", "10:00"], "alert_hours", ["09:00", "10:00"]),
        ("muted_symbols", ["btc"], "muted_symbols", ["BTC"]),
    ],
)
async def test_every_control_reaches_the_stored_record(
    test_context, field, sent, key, expected
):
    """Rule H1, across the whole family rather than one example.

    A control that the page offers and the record never receives is a control that
    lies. Parametrised so a fix that only makes one field work fails this.
    """

    user_id, token = await _account(test_context, f"set-{field}@example.com")

    response = await _save(test_context, token, **{field: sent})
    assert response.status_code == 200, response.text[:400]

    stored = await _stored(test_context, user_id)
    assert stored[key] == expected


async def test_the_reader_agrees_with_what_was_stored(test_context):
    """The one owner writes it; the one reader reads it. If those two disagree, a
    setting that looks saved changes nothing."""

    user_id, token = await _account(test_context, "set-reader@example.com")
    await _save(
        test_context,
        token,
        near_miss_enabled=False,
        near_miss_threshold=42,
        maximum_alerts_per_hour=3,
        finished_opportunity_alerts=False,
        alert_days=["Monday"],
        alert_hours=["08:00"],
        muted_symbols=["sol"],
        timezone="Asia/Tokyo",
    )

    async with test_context["session_factory"]() as session:
        preference = await NotificationPreferenceService(
            session, test_context["settings"]
        ).current(user_id)

    assert preference.near_miss_enabled is False
    assert preference.near_miss_threshold == 42
    assert preference.maximum_alerts_per_hour == 3
    assert preference.lifecycle_enabled is False
    assert preference.alert_days == {"Monday"}
    assert preference.alert_hours == {"08:00"}
    assert preference.timezone == "Asia/Tokyo"
    assert symbol_is_muted("SOL/USDT", preference.muted_symbols) is True


async def test_the_in_app_notice_can_never_be_switched_off(test_context):
    """A person must always be able to come back and read what happened, whatever else
    they turned off."""

    user_id, token = await _account(test_context, "set-web@example.com")

    await _save(test_context, token, alert_channels=[], compliance_alert_channels=[])

    stored = await _stored(test_context, user_id)
    assert stored["alert_channels"] == ["web"]
    assert stored["compliance_alert_channels"] == ["web"]


async def test_the_time_zone_is_written_to_the_account_as_well(test_context):
    """The schedule is read against the account's own zone, so saving one without the
    other would run somebody's quiet hours in the wrong time."""

    user_id, token = await _account(test_context, "set-zone@example.com")

    await _save(test_context, token, timezone="America/New_York")

    async with test_context["session_factory"]() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.timezone == "America/New_York"


async def test_saving_one_thing_does_not_quietly_change_another(test_context):
    """Rule H6. The live page posted the whole form, so a channel it had no box for was
    switched off the next time anybody saved anything at all."""

    user_id, token = await _account(test_context, "set-keep@example.com")
    await _save(test_context, token, alert_channels=["telegram"], muted_symbols=["ada"])

    await _save(
        test_context,
        token,
        alert_channels=["telegram"],
        muted_symbols=["ada"],
        near_miss_threshold=20,
    )

    stored = await _stored(test_context, user_id)
    assert DeliveryChannel.TELEGRAM.value in stored["alert_channels"]
    assert stored["muted_symbols"] == ["ADA"]
    assert stored["near_miss_threshold"] == 20


async def test_a_time_zone_nobody_offered_saves_nothing_at_all(test_context):
    """Rule H5 and the compiler's own habit: fail closed. A refused value leaves the
    record exactly as it was rather than falling back to a default."""

    user_id, token = await _account(test_context, "set-badzone@example.com")
    await _save(test_context, token, near_miss_threshold=25)

    response = await _save(
        test_context, token, timezone="Mars/Olympus", near_miss_threshold=99
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_timezone"
    stored = await _stored(test_context, user_id)
    assert stored["near_miss_threshold"] == 25


@pytest.mark.parametrize(
    ("field", "junk", "key", "kept"),
    [
        ("dashboard_notification_sound", "airhorn", "dashboard_notification_sound", "chime"),
        ("forming_notification_sound", "airhorn", "forming_notification_sound", "pulse"),
        ("compliance_alert_digest", "hourly", "compliance_alert_digest", "immediate"),
        ("providers", ["kraken"], "providers", ["binance"]),
        ("alert_days", ["Someday"], "alert_days", ["Every Day"]),
        ("alert_hours", ["25:00"], "alert_hours", []),
    ],
)
async def test_a_value_nobody_offered_is_never_stored(
    test_context, field, junk, key, kept
):
    """Never the raw value, never the nearest one. The same default the page shows."""

    user_id, token = await _account(test_context, f"set-junk-{field}@example.com")

    response = await _save(test_context, token, **{field: junk})
    assert response.status_code == 200

    stored = await _stored(test_context, user_id)
    assert stored[key] == kept


async def test_every_day_wins_over_any_other_day(test_context):
    """Two answers to one question cannot both be stored: "every day" and "only Monday"
    is not a schedule anybody meant."""

    user_id, token = await _account(test_context, "set-everyday@example.com")

    await _save(test_context, token, alert_days=["Every Day", "Monday"])

    stored = await _stored(test_context, user_id)
    assert stored["alert_days"] == ["Every Day"]


async def test_there_is_no_second_way_to_save_a_setting(test_context):
    """There used to be two Settings pages and two ways to save.

    The older page posted the whole form to `POST /dashboard/settings`, and this checked
    that the form still wrote through `AccountSettingsService` like the redesigned page
    does. Both the page and its handler are gone, so the check is now the stronger one:
    nothing may write a setting except the one endpoint that knows today's rules.

    A form that still posted here would be a control saved by a route that had not been
    told, for example, which sounds exist — which is exactly how the two pages came to
    disagree about what a person had chosen.
    """

    email = "set-liveform@example.com"
    user_id, token = await _account(test_context, email)

    response = await test_context["client"].post(
        "/dashboard/settings",
        data={"timezone": "Asia/Singapore"},
        follow_redirects=False,
    )
    assert response.status_code == 405

    # And the one way in still saves.
    await _save(test_context, token, timezone="Asia/Singapore", providers=["bybit"])
    stored = await _stored(test_context, user_id)
    assert stored["timezone"] == "Asia/Singapore"
    assert stored["providers"] == ["bybit"]


@pytest.mark.parametrize("path", [SETTINGS, "/dashboard/settings"])
async def test_a_fresh_account_is_shown_what_the_product_actually_believes(
    test_context, path
):
    """Both pages must draw the channels the reader reports, not their own defaults.

    The live page decided its own in Jinja and decided differently: an account with
    nothing saved was shown Telegram *and WhatsApp* already ticked, while
    `NotificationPreferenceService` believed the person was on the in-app notice and
    Telegram. The page was making a claim about the account that only became true if
    somebody happened to press Save.
    """

    email = f"set-default-{abs(hash(path))}@example.com"
    user_id, _token = await _account(test_context, email)

    async with test_context["session_factory"]() as session:
        believed = {
            channel.value
            for channel in (
                await NotificationPreferenceService(
                    session, test_context["settings"]
                ).current(user_id)
            ).channels
        }

    document = lxml.html.fromstring((await test_context["client"].get(path)).text)
    drawn = {
        node.get("data-value") or node.get("value")
        for node in document.xpath(
            '//button[@data-g-set="alert_channels"][@aria-pressed="true"]'
            ' | //input[@name="alert_channels"][@checked]'
        )
    }

    # The in-app notice is never a box on either page — it cannot be switched off — so
    # it is the one value the drawing is allowed to leave out.
    assert drawn == believed - {DeliveryChannel.WEB.value}


async def test_the_live_page_still_renders(test_context):
    await _signup_and_verify(test_context, email="set-live@example.com")
    response = await test_context["client"].get("/dashboard/settings")
    assert response.status_code == 200
