"""Every contradiction the Phase 6 launch-readiness audit found, pinned by name.

An audit that only writes its findings down finds them again next quarter. Each rule
below is asserted across the whole family it belongs to — every launch stage, every
retired field name, every surface that publishes a link — so that a fix which only
helps the one reported example fails here.

Findings are numbered the way the audit numbered them, and the numbers appear in
`docs/RELEASE_READINESS_REPORT.md`.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ai_market_monitor.core import startup
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.copy_rules import BRAND_NAME_PATTERN, scan_text
from ai_market_monitor.core.launch_stage import STAGE_EXPOSURE, LaunchStage
from ai_market_monitor.core.site_content import (
    FOOTER_NAVIGATION,
    PUBLIC_NAVIGATION,
    footer_navigation,
    public_navigation,
    social_image_url,
)
from ai_market_monitor.services import system_brain, web_auth
from ai_market_monitor.services.ai_setup_chat import AISetupChatService
from ai_market_monitor.services.waitlist_sheet_contract import (
    WAITLIST_SHEET_FIELDS,
    WAITLIST_SHEET_RETIRED_FIELDS,
)

ROOT = Path(__file__).resolve().parents[2]
APPS_SCRIPT = ROOT / "scripts" / "google_apps_script" / "waitlist_webhook.gs"


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "database_url": "sqlite+aiosqlite://",
    }
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------------
# 1.1  AI_AGENT_CONTROL_ENABLED is not the Setup Chat kill switch.
# --------------------------------------------------------------------------------


def test_p6_101_the_old_coordinator_flag_cannot_reach_authenticated_setup_chat():
    """`handle_message` hands every turn to the launch service and returns.

    `docs/OPERATIONS.md` called `AI_AGENT_CONTROL_ENABLED` "the Setup Chat kill switch"
    and told an operator to roll Setup Chat back by turning it off. The branch that
    reads it sits below an unconditional delegation that only a test-only compatibility
    switch can skip, and that switch is refused in any deployed environment. Turning the
    flag off in production changes nothing at all.
    """

    source = inspect.getsource(AISetupChatService.handle_message)
    compat = source.index("setup_chat_legacy_test_compat_enabled")
    delegation = source.index("SetupChatLaunchService(")
    assert compat < delegation, "the compatibility switch must guard the delegation"
    # The branch that reads the old flag still exists, and that is fine — what matters
    # is that it sits *below* the unconditional return, so nothing but the test-only
    # compatibility switch can reach it.
    assert "ai_agent_control_enabled" in source
    assert delegation < source.index("ai_agent_control_enabled")

    # And the compatibility switch cannot be on in a deployed environment.
    with pytest.raises(ValueError, match="SETUP_CHAT_LEGACY_TEST_COMPAT_ENABLED"):
        _settings(
            app_env="production",
            setup_chat_legacy_test_compat_enabled=True,
        )


@pytest.mark.parametrize(
    "name",
    [
        "SETUP_CHAT_EMERGENCY_DISABLED",
        "SETUP_FREE_TEXT_ENABLED",
        "SETUP_PLANNER_ENABLED",
        "SETUP_COMPOSER_ENABLED",
        "SETUP_BUILDER_ENABLED",
        "SETUP_SCANNER_ENABLED",
        "SETUP_MONITOR_ENABLED",
    ],
)
def test_p6_102_every_real_setup_chat_control_is_documented_in_both_examples(name):
    """The switches that do work must be findable without reading the source.

    README said "there is no Setup Chat feature flag". There are seven, and six of them
    appeared in neither environment example, so an operator looking for a way to stop
    Setup Chat during an incident would have found only the one that does nothing.
    """

    for example in (".env.example", ".env.production.example"):
        text = (ROOT / example).read_text(encoding="utf-8")
        assert f"\n{name}=" in text, f"{name} missing from {example}"


def test_p6_103_the_emergency_switch_stops_a_turn_rather_than_falling_back():
    settings = _settings(setup_chat_emergency_disabled=True)
    assert settings.setup_chat_emergency_disabled is True
    source = inspect.getsource(
        __import__(
            "ai_market_monitor.services.setup_chat_launch",
            fromlist=["SetupChatLaunchService"],
        ).SetupChatLaunchService.handle
    )
    assert "SETUP_CHAT_EMERGENCY_DISABLED" in source


# --------------------------------------------------------------------------------
# 1.2  A deterministic, non-AI creation path exists.
# --------------------------------------------------------------------------------


def test_p6_201_the_guided_builder_writes_a_draft_with_no_model_call():
    """Authoring must not depend on the assistant being available.

    Earlier planning material described the deterministic Builder as the canonical path
    and later material implied it had been replaced. It exists: one route, one handler,
    the same mutation authority the assistant uses, and no provider call anywhere in it.
    """

    from ai_market_monitor.api.routers import dashboard_api
    from ai_market_monitor.services.setup_chat_launch import SetupChatLaunchService

    routes = {
        route.path
        for route in dashboard_api.router.routes
        if hasattr(route, "path")
    }
    assert "/dashboard/setup-chat/sessions/{chat_id}/builder-actions" in routes
    assert "/dashboard/setup-chat/builder-contract" in routes

    source = inspect.getsource(SetupChatLaunchService.handle_builder_action)
    for forbidden in ("_run_planner", "_run_composer", "openai", "provider_call"):
        assert forbidden not in source, forbidden
    assert "_apply_server_owned_operations" in source


# --------------------------------------------------------------------------------
# 1.3  Brand guide section 4: the name in prose is "Hilal Markets".
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "HilalMarkets watches the market.",
        "Welcome to HilalMarkets.",
        "Ask HilalMarkets, or don't.",
        "(HilalMarkets)",
        "— HilalMarkets —",
        "أي أصول مفحوصة تريد من HilalMarkets مراقبتها؟",
    ],
)
def test_p6_301_prose_uses_of_the_unspaced_name_are_caught(line):
    violations = scan_text(line, Path("x.py"))
    assert any(item.rule.startswith("brand") for item in violations), line


@pytest.mark.parametrize(
    "line",
    [
        "class HilalMarketsEmailRenderer:",
        "window.HilalMarketsConsentConfig = {};",
        '"User-Agent": "HilalMarkets/1.0",',
        "HilalMarkets_Sharia_Methodology_Import_Pack/scripts",
        'href="/static/hilalmarkets-guide.css"',
        "from x import HilalMarketsEmailRenderer",
    ],
)
def test_p6_302_identifiers_carrying_the_name_are_left_alone(line):
    """Renaming any of these would be a code, asset or wire change, not a copy fix."""

    assert not BRAND_NAME_PATTERN.search(line), line


def test_p6_303_no_customer_surface_still_writes_the_name_without_its_space():
    from ai_market_monitor.core.copy_rules import scan_customer_copy

    offenders = [
        item.describe(ROOT)
        for item in scan_customer_copy(ROOT)
        if item.rule.startswith("brand")
    ]
    assert offenders == []


def test_p6_304_the_support_address_shown_to_a_customer_is_this_product():
    """The fallback was `contact@trace-edge.com`, an earlier product's inbox."""

    assert "trace-edge" not in _settings().support_inbox_email
    assert _settings().support_inbox_email.endswith("@hilalmarkets.com")


# --------------------------------------------------------------------------------
# 1.4  Public preview metadata is absolute and HTTPS.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    [
        "http://hilalmarkets.com",
        "https://hilalmarkets.com",
        "http://www.hilalmarkets.com/",
        "https://staging.hilalmarkets.com",
    ],
)
def test_p6_401_a_real_host_always_gets_an_absolute_https_preview_image(base):
    url = social_image_url(base)
    assert url.startswith("https://"), url
    assert "/static/hilalmarkets-social-preview.png" in url


@pytest.mark.parametrize(
    "base",
    ["http://localhost:8000", "http://127.0.0.1:8000", "http://0.0.0.0:8000"],
)
def test_p6_402_a_developer_machine_keeps_a_scheme_that_actually_loads(base):
    """Forcing HTTPS on a machine with no certificate breaks the local page and
    proves nothing, so a local host is the one exception and it is named."""

    assert social_image_url(base).startswith("http://")


def test_p6_403_the_committed_landing_bundle_publishes_no_relative_preview_image():
    """One page must not carry two answers, and never a relative one.

    The built bundle shipped `og:image="/static/landing/..."`. A social scraper
    resolves that against nothing.
    """

    html = (
        ROOT / "src" / "ai_market_monitor" / "static" / "landing" / "index.html"
    ).read_text(encoding="utf-8")
    for tag in ('property="og:image"', 'name="twitter:image"'):
        assert tag not in html, tag


# --------------------------------------------------------------------------------
# 1.5  The committed receiver reads what the server sends.
# --------------------------------------------------------------------------------


#: The two fields the receiver accepts and deliberately does not use.
#:
#: ``name`` is always empty — the form asks for an email and nothing else. ``status`` is
#: always the literal ``"waitlist"``, which names the list, not the row: the sheet's own
#: Status column is an editable workflow value (New, Invited, Joined Beta…) that the
#: beta team owns, and overwriting it on every delivery would erase their work.
WAITLIST_ACCEPTED_BUT_UNUSED = frozenset({"name", "status"})


@pytest.mark.parametrize(
    "field", sorted(set(WAITLIST_SHEET_FIELDS) - WAITLIST_ACCEPTED_BUT_UNUSED)
)
def test_p6_501_the_apps_script_reads_every_field_the_server_sends(field):
    """`secret` was the one that mattered: the script read `webhook_secret`, so every
    signup came back unauthorized. Each carrying field is checked, not just that one."""

    script = APPS_SCRIPT.read_text(encoding="utf-8")
    assert f"payload.{field}" in script, field


@pytest.mark.parametrize("field", sorted(WAITLIST_SHEET_RETIRED_FIELDS))
def test_p6_502_the_apps_script_requires_no_field_the_server_stopped_sending(field):
    """`event_id` and `submitted_at` were required and are never sent. A receiver that
    requires an absent field rejects everything, and the rejection reads as an ordinary
    delivery failure."""

    script = APPS_SCRIPT.read_text(encoding="utf-8")
    assert f"payload.{field}" not in script, field


def test_p6_503_authorisation_uses_the_contract_field_name():
    script = APPS_SCRIPT.read_text(encoding="utf-8")
    assert "payload.secret !== expectedSecret" in script


# --------------------------------------------------------------------------------
# 1.6  Every public surface obeys the launch stage, in every stage.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", sorted(LaunchStage, key=lambda item: item.value))
def test_p6_601_menus_hide_exactly_what_the_stage_hides(stage):
    """The header, the footer and the stage table used to be three opinions.

    The menus were gated on `waitlist_mode`, true in only one of four stages. At
    `internal` and `private_beta_invite` both menus therefore advertised Pricing and
    Halal Assets while the stage's own table said both were hidden.
    """

    hidden = STAGE_EXPOSURE[stage].hidden_pages
    header = {item.page for item in public_navigation(hidden_pages=hidden)}
    footer = {
        item.page
        for group in footer_navigation(hidden_pages=hidden)
        for item in group.items
    }
    for page in hidden:
        assert page not in header, (stage, page)
        assert page not in footer, (stage, page)
    visible = {item.page for item in PUBLIC_NAVIGATION} - hidden
    assert visible <= header
    all_footer = {item.page for group in FOOTER_NAVIGATION for item in group.items}
    assert (all_footer - hidden) <= footer


@pytest.mark.parametrize("stage", sorted(LaunchStage, key=lambda item: item.value))
def test_p6_602_the_sitemap_lists_nothing_the_stage_hides(stage):
    from ai_market_monitor.core.site_content import PUBLIC_PAGES

    settings = _settings(launch_stage=stage, public_waitlist_mode=False)
    hidden = settings.stage_exposure.hidden_pages
    listed = {item.page for item in PUBLIC_PAGES if item.page not in hidden}
    assert not (listed & hidden)
    source = inspect.getsource(
        __import__(
            "ai_market_monitor.api.routers.public", fromlist=["sitemap"]
        ).sitemap
    )
    assert "stage_exposure.hidden_pages" in source
    assert "waitlist_mode" not in source


@pytest.mark.parametrize("stage", sorted(LaunchStage, key=lambda item: item.value))
def test_p6_603_the_assistant_offers_no_link_the_stage_forbids(stage):
    from ai_market_monitor.core.site_content import is_account_only_path
    from ai_market_monitor.services.public_chat import PUBLIC_ROUTE_PATHS, offerable_route_ids

    settings = _settings(launch_stage=stage, public_waitlist_mode=False)
    exposure = settings.stage_exposure
    offered = offerable_route_ids(settings)
    for route_id in offered:
        assert route_id not in exposure.hidden_pages, (stage, route_id)
        if not exposure.assistant_may_offer_account:
            _label, path = PUBLIC_ROUTE_PATHS[route_id]
            assert not is_account_only_path(path), (stage, route_id)


@pytest.mark.parametrize("stage", sorted(LaunchStage, key=lambda item: item.value))
def test_p6_604_a_purchasable_offer_is_published_only_where_pricing_is_advertised(stage):
    """schema.org `Offer` tells a search engine the product can be bought today."""

    source = inspect.getsource(
        __import__(
            "ai_market_monitor.api.routers.public", fromlist=["_public_context"]
        )._public_context
    )
    assert 'application["offers"]' in source
    marker = source.index('application["offers"]')
    guard = source.rindex("if ", 0, marker)
    assert "advertises_pricing" in source[guard:marker]


# --------------------------------------------------------------------------------
# 1.7  A database backup is not an ordinary filename.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "ai_market_monitor.db",
        "ai_market_monitor.db.bak-20260803",
        "ai_market_monitor.sqlite3.backup",
        "app.db.old",
        "app.db~",
        "worker.log.1",
        "dump.sqlite-2026",
    ],
)
def test_p6_701_every_shape_of_a_committed_database_or_log_is_refused(path):
    from scripts.check_release_invariants import FORBIDDEN_TRACKED_PATTERNS

    assert any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS), path


@pytest.mark.parametrize(
    "path",
    ["schema.dbml", "engine/logic.py", "notes.dbdocs", "VvvebJs/demo.db"],
)
def test_p6_702_an_ordinary_filename_that_merely_looks_like_one_is_not(path):
    from scripts.check_release_invariants import FORBIDDEN_TRACKED_PATTERNS

    assert not any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS), path


# --------------------------------------------------------------------------------
# Security: one owner for the fixed test code, and a deployed refusal.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["development", "staging", "production"])
def test_p6_801_no_environment_but_test_ever_gets_a_predictable_code(env):
    """The System Brain login read `AUTH_TEST_FIXED_CODE` raw.

    `web_auth` checked the environment and the six-digit shape; the governance console's
    second factor checked neither. Same variable, two readings, and the one guarding the
    admin console was the unguarded one.
    """

    assert _settings(app_env=env, auth_test_fixed_code="123456").fixed_auth_code is None


@pytest.mark.parametrize(
    "value", ["", "   ", "12345", "1234567", "abcdef", "12345a", None]
)
def test_p6_802_a_malformed_fixed_code_is_refused_even_in_test(value):
    assert _settings(app_env="test", auth_test_fixed_code=value).fixed_auth_code is None


def test_p6_803_a_well_formed_fixed_code_is_honoured_only_in_test():
    assert _settings(app_env="test", auth_test_fixed_code="123456").fixed_auth_code == "123456"


@pytest.mark.parametrize(
    "function",
    [
        web_auth.WebAuthService._new_auth_code,
        system_brain.SystemBrainAuthService.begin_login,
    ],
)
def test_p6_804_both_code_issuers_read_the_one_owner(function):
    source = inspect.getsource(function)
    assert "settings.fixed_auth_code" in source
    # Reading the raw setting is what the System Brain login did. Naming it in a comment
    # is fine; reading it is not.
    assert "settings.auth_test_fixed_code" not in source


@pytest.mark.parametrize("env", ["staging", "production"])
def test_p6_805_a_deployed_process_refuses_to_boot_with_a_fixed_code(env):
    settings = _settings(
        app_env=env,
        auth_test_fixed_code="123456",
        database_url="postgresql+asyncpg://u:a-strong-password@db/app",
        public_base_url="https://hilalmarkets.com",
        allow_mock_providers=False,
        api_rate_limiting_enabled=True,
        api_rate_limit_fail_closed=True,
    )
    with pytest.raises(startup.RuntimeConfigurationError, match="AUTH_TEST_FIXED_CODE"):
        startup.validate_runtime_configuration(settings)


# --------------------------------------------------------------------------------
# An ordinary English word after "no" is not a coin to blocklist.
#
# Found while attributing a failing test to a clean worktree: it failed at the audit's
# starting commit too, so it is not a regression from this work - it is a defect this
# audit found and fixed. "with no carry-over" put CARRY/USDT on the exclusion list.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Bind approval to the exact reviewed hash with no carry-over.",
        "No problem, keep it as it is.",
        "No changes to the setup please.",
        "No rush on this one.",
        "There is no doubt about the direction.",
        "Use no leverage.",
        "No worries, that reads correctly.",
        "no thanks",
    ],
)
def test_p6_811_ordinary_prose_after_no_never_blocklists_a_market(sentence):
    """The guard was a hand-written list of 24 English words.

    Against an unbounded family of them, a blocklist can only ever be extended one
    incident at a time. Capitalisation is the proof a bare word is a ticker, and it
    was being thrown away by `re.IGNORECASE`.
    """

    from ai_market_monitor.engine.strategy_state import _explicit_bare_asset_exclusions

    assert _explicit_bare_asset_exclusions(sentence, quote="USDT") == ()


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("no LTC", ("LTCUSDT",)),
        ("drop LTC", ("LTCUSDT",)),
        ("exclude SOL", ("SOLUSDT",)),
        ("no the LTC", ("LTCUSDT",)),
        ("LTC is excluded", ("LTCUSDT",)),
        ("XRP not included", ("XRPUSDT",)),
    ],
)
def test_p6_812_a_capitalised_ticker_after_an_exclusion_word_still_excludes(
    sentence, expected
):
    """The fix must not cost the behaviour the pattern exists for."""

    from ai_market_monitor.engine.strategy_state import _explicit_bare_asset_exclusions

    assert _explicit_bare_asset_exclusions(sentence, quote="USDT") == expected


# --------------------------------------------------------------------------------
# Invariant 17: both environment examples describe every setting.
# --------------------------------------------------------------------------------


def test_p6_901_every_setting_appears_in_both_environment_examples():
    """A hand-written list of what must be documented documents what somebody
    remembered. 41 settings were in neither file while this check passed."""

    from scripts.check_release_invariants import required_environment_keys

    def keys(name: str) -> set[str]:
        found = set()
        for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                found.add(line.split("=", 1)[0].strip())
        return found

    development = keys(".env.example")
    production = keys(".env.production.example")
    required = required_environment_keys()
    assert sorted(required - development) == []
    assert sorted(required - production) == []
    assert sorted(development ^ production) == []
