import re
from pathlib import Path

ROOT = Path("src/ai_market_monitor")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_customer_navigation_uses_consolidated_information_architecture():
    """Read from the menu itself, not from the text of the file that holds it.

    This used to search the source of `core/site_content.py` for quoted words, which
    cannot tell a decision from a sentence about one: renaming the front page to "Home"
    and *explaining why in a comment above it* made the same test both pass and fail on
    words that were never menu entries. The menu is data; it is imported and read.
    """

    from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION

    labels = {item.label for group in DASHBOARD_NAVIGATION for item in group.items}
    endpoints = {item.endpoint for group in DASHBOARD_NAVIGATION for item in group.items}

    for gone in (
        "Saved Assets",
        "Compliance Changes",
        "How We Screen",
        "Market Scanner",
        "Halal Market",
    ):
        assert gone not in labels, gone
    assert "Notifications" in labels
    assert "Halal Assets" in labels

    # Two entries were removed from the menu and both of their pages with them: the old
    # counting front page, and Trading Assistant, whose page answers 404 now. A name left
    # in the menu after its page is gone is a link to nothing, so the two are checked
    # together — by the **endpoint**, because an endpoint is the page and a label is only
    # what it is called this week.
    assert "dashboard_home_page" not in endpoints
    assert "check_market_page" not in endpoints
    assert "Trading Assistant" not in labels

    # The front page is called Home. "Main" was the name of the router that serves it,
    # which is not a word a customer has any use for.
    assert "Home" in labels
    assert "Main" not in labels

    # The thing a person builds is called a monitor, in the menu and on the page it
    # opens. A button called one thing that opens a page called another is worse than
    # either name on its own.
    assert "Monitors" in labels
    assert "Create a monitor" in labels
    assert "Watchlists" not in labels


def test_one_market_page_owns_the_list_of_coins_a_person_keeps():
    """There were two market pages, and only one of them was in the menu.

    The older one answered at `/dashboard/market` with its own staged "saved assets"
    dialog; the redesigned one answered at a second address with Favorites, and the menu
    opened that. So the address written into every alert email and every Telegram button
    showed a different screen from the one the menu showed. The older page and its
    template are gone, and the redesigned one answers at `/dashboard/market`.
    """

    market = _read("templates/hilal/dashboard_test/market.html")

    # Favorites lives in one included partial, so the dialog exists once per document.
    assert "data-open-favorites" in market
    assert 'include "hilal/dashboard_test/partials/favorites_dialog.html"' in market
    assert not (ROOT / "templates/hilal/dashboard/market.html").exists()
    assert not (ROOT / "templates/hilal/dashboard/partials/live_market.html").exists()


def test_activity_is_a_minimal_evidence_workspace():
    activity = _read("templates/hilal/dashboard/activity.html")
    dashboard_runtime = _read("static/dashboard.js")

    assert "Notification center" not in activity
    assert "Show evidence difference" in activity
    assert "data-evidence-dialog" in activity
    assert "Condition bottlenecks" in activity
    assert "Monitor and strategy health" in activity
    assert ">Ended<" not in activity
    assert 'class="stat-grid"' not in activity
    assert 'class="activity-toolbar"' not in activity
    assert "readiness-filters" in activity
    assert "data-radar-state" in activity
    assert "data-radar-sort" in activity
    assert "activity-page-tabs" not in activity
    assert activity.count("data-hm-select") == 2
    assert "opportunity-journey-actions" in activity
    assert "dashboard.css" in activity
    assert 'xmlns="http://www.w3.org/2000/svg"' in dashboard_runtime


def test_settings_offer_channels_a_schedule_and_a_threshold_and_decide_none_of_them():
    """The one Settings page, and it asks the server what may be chosen.

    The older page at this address is gone. It was one long form with a Save button, and
    it decided things inside its own template: which channels existed, which sounds there
    were, which defaults applied. Two of its controls saved nothing at the other end and
    two settings the product really reads had no control at all.

    Every list here now comes from the router, so a channel the product cannot deliver
    cannot be offered — which is what this checks: the controls exist, and not one of
    them names a value of its own.
    """

    settings = _read("templates/hilal/dashboard_test/settings.html")

    for control in (
        'data-g-set="alert_channels"',
        'data-g-set="alert_days"',
        'data-g-set="alert_hours"',
        'data-g-number="near_miss_threshold"',
        'data-g-switch="dashboard_notifications_enabled"',
        'data-g-pick="dashboard_notification_sound"',
        'data-g-set="compliance_alert_channels"',
    ):
        assert control in settings, control

    # Every choice is drawn from a loop over server data, never typed in here.
    for loop in ("for channel in alert_channels", "for provider in providers"):
        assert loop in settings, loop
    for typed in ('value="binance"', 'value="bybit"', 'value="whatsapp"', 'value="telegram"'):
        assert typed not in settings, f"{typed} is decided in the router, not the page"

    # It saves as you go, so there is no Save button to leave un-pressed.
    assert "data-g-saved" in settings
    assert "data-settings-save" not in settings
    assert not (ROOT / "templates/hilal/dashboard/settings.html").exists()


def test_builder_uses_ai_sheet_and_minimizable_canvas_assistant():
    builder = _read("templates/hilal/dashboard/builder.html")
    script = _read("static/ai-setup-chat.js")

    assert "AI sheet" in builder
    assert "Your Watchlist sheet" not in builder
    assert "Live translation" not in builder
    assert "Advanced Controls" not in builder
    assert "data-ai-minimize-chat" in builder
    assert 'classList.toggle("assistant-minimized")' in script
    heading = builder.split(
        '<section class="guided-builder-heading"',
        1,
    )[1].split("</section>", 1)[0]
    assert "guided-builder-heading-line" in heading
    assert "guided-builder-heading-actions" in heading
    assert "Market assistant" in heading
    assert "guided-starts" not in heading
    assert "ai-chat-guided-starts" in builder
    assert 'class="guided-starts ai-chat-guided-starts"' in builder


def test_dashboard_shell_has_notification_center_and_cache_busted_brand_assets():
    base = _read("templates/hilal/base_dashboard.html")
    topbar = _read("templates/hilal/partials/dashboard_topbar.html")
    sidebar = _read("templates/hilal/partials/dashboard_sidebar.html")

    # Every asset carries a cache-busting key. Which key it is belongs to the release,
    # and `test_dashboard_static_assets` proves they all share one; naming the string
    # here as well taught the habit of editing the test instead of the templates.
    assert re.search(r"\?v=[a-zA-Z0-9-]+", base)
    assert "data-notification-center" in topbar
    assert 'data-icon="bell"' in topbar
    assert "data-sidebar-collapse" in sidebar
    assert 'data-icon="panel"' in sidebar
    assert 'data-icon="panel_expand"' in sidebar
    assert "hilal-markets-logo.svg" in sidebar
    # The menu draws from the product's one icon table, like every other icon on every
    # other page. It used to have a second system of its own — see the test below.
    assert 'data-icon="{{ item.icon }}"' in sidebar
    assert 'class="nav-icon' not in sidebar


def test_the_side_menu_has_no_icon_system_of_its_own():
    """One icon table for the whole product, and the menu reads it like everything else.

    The menu used to be masked from eleven `.nav-icon-*` rules over eleven SVG files that
    nothing else on the site referenced. So a new entry needed an icon in two unrelated
    places, and a mismatch drew an empty square with nothing reporting it — which is
    exactly what happened when the canvas entry was added. Both the rules and the files
    are gone, and this is what keeps them gone.
    """

    styles = _read("static/hilalmarkets-dashboard-v2.css")
    shell = _read("static/hm-shell.css")
    icons = _read("static/hilalmarkets-icons.js")

    assert "--nav-icon-url" not in styles
    assert "--nav-icon-url" not in shell
    for retired in (
        "nav-home.svg",
        "nav-market.svg",
        "nav-radar.svg",
        "nav-scan.svg",
        "nav-activity.svg",
        "nav-bell.svg",
        "nav-billing.svg",
        "nav-support.svg",
        "nav-gauge.svg",
        "nav-workflow.svg",
    ):
        assert not (ROOT / "static" / retired).exists(), f"{retired} came back"
        assert retired not in styles + shell

    # The current page is still marked in apple green, and still not by colour alone:
    # the row is filled near-black and carries `aria-current="page"` as well.
    assert ".hm-nav-link.is-active .hm-nav-icon" in shell
    assert "color: var(--hm-apple);" in shell
    assert 'aria-current="page"' in _read("templates/hilal/partials/dashboard_sidebar.html")
    assert "circle_plus:" in icons


def test_requested_selects_share_one_branded_accessible_component():
    # Two of the three pages that used to be read here are gone, and their replacements
    # do not use this component. Evidence and Activity still does, and the component
    # itself is what this is really about.
    activity = _read("templates/hilal/dashboard/activity.html")
    runtime = _read("static/hilalmarkets.js")
    styles = _read("static/hilalmarkets-dashboard-v2.css")

    assert activity.count("data-hm-select") == 2
    assert 'document.createElement("button")' in runtime
    assert 'setAttribute("role", "listbox")' in runtime
    assert 'setAttribute("aria-haspopup", "listbox")' in runtime
    assert 'new Event("change", { bubbles: true })' in runtime
    assert '"ArrowDown"' in runtime
    assert '"Escape"' in runtime
    assert ".hm-select-trigger" in styles
    assert ".hm-select-option.is-selected" in styles
    assert ".hm-select-menu[hidden]" in styles


def test_notifications_billing_and_support_use_the_compact_layouts():
    """Billing keeps its own address and its own layout; the other two moved.

    Notifications and Support were two pages each — an older one at `/dashboard/...` and
    a redesigned one at `/dashboard-test/...` — and the menu opened the redesigned ones
    while every message we send opened the older ones. The older two are gone, so what is
    checked here is that they are gone and that their replacements are the compact shape.
    """

    billing = _read("templates/hilal/dashboard/billing.html")
    connections = _read("templates/hilal/dashboard_test/connections.html")
    support = _read("templates/hilal/dashboard_test/support.html")
    styles = _read("static/hilalmarkets-dashboard-v2.css")

    assert "billing-current-plan" in billing
    assert "billing-current-meta" in billing
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in styles
    assert "body.hilal-dashboard .billing-plan-panel .dashboard-price-card {" in styles
    assert "width: 100%;" in styles

    # Neither replacement repeats its own name in a heading above itself: the shared
    # topbar already says which page this is, from the navigation data.
    assert "<h1>Notifications</h1>" not in connections
    assert 'class="grid grid-3"' not in support
    for gone in (
        "templates/hilal/dashboard/integrations.html",
        "templates/hilal/dashboard/support.html",
    ):
        assert not (ROOT / gone).exists(), gone


def test_collapsed_sidebar_reduces_the_shell_to_icon_width():
    styles = _read("static/hm-shell.css")
    runtime = _read("static/hm-shell.js")

    assert "body.dashboard-body.hilal-dashboard.sidebar-collapsed" in styles
    assert "--hm-nav-min: 84px" in styles
    assert "grid-template-columns: var(--hm-nav-min) minmax(0, 1fr)" in styles
    assert 'classList.toggle("sidebar-collapsed"' in runtime
    assert "hilalmarkets-sidebar-collapsed" in runtime
    assert 'data-sidebar-collapse-icon="minimize"' in runtime
    assert 'data-sidebar-collapse-icon="expand"' in runtime


def test_only_one_file_decides_whether_the_side_menu_is_minimized():
    """Three files used to write the same class on `<body>` from two different keys.

    `hilalmarkets.js` stored the answer under `hilalmarkets-sidebar-collapsed` and
    `dashboard.js` under `amm-sidebar-collapsed`, and both ran on every dashboard page.
    Whichever executed last decided what a person saw, so pressing the button changed one
    key while the other kept its old answer for the next page load — which is why the
    menu looked like it forgot the state that had just been chosen.
    """

    owner = _read("static/hm-shell.js")
    assert 'STORAGE_KEY = "hilalmarkets-sidebar-collapsed"' in owner

    # The quoted forms only. Both files explain in a comment what they used to do, and a
    # test that could not tell a comment from a call would have to be satisfied by
    # deleting the explanation.
    for other in ("static/hilalmarkets.js", "static/dashboard.js"):
        source = _read(other)
        assert 'classList.toggle("sidebar-collapsed"' not in source, other
        assert '"amm-sidebar-collapsed"' not in source, other
        assert '"[data-sidebar-collapse]"' not in source, other

    # And the stylesheets say it once as well: the rules lived in three sheets, and the
    # copy in `hilalmarkets.css` hid the link labels with `display: none`, which takes
    # them out of the accessibility tree. Comments are stripped first, so the note left
    # in each file explaining what moved is not itself the thing that fails.
    for sheet in ("static/hilalmarkets.css", "static/hilalmarkets-dashboard-v2.css"):
        rules = re.sub(r"/\*.*?\*/", "", _read(sheet), flags=re.DOTALL)
        assert ".sidebar-collapsed" not in rules, sheet


def test_auth_pages_use_the_official_logo_and_brand_styles():
    auth = _read("templates/auth.html")
    styles = _read("static/hilalmarkets-auth.css")

    # Every mark on the page is the official asset. There are two references now: the
    # near-black panel carries one, and the form card carries a smaller one that only
    # appears on a phone, where the panel sits below the form. What the rule is really
    # about is that neither of them hand-typesets the wordmark (brand guide, section 5).
    assert auth.count("hilal-markets-logo.svg") == 2
    assert "hilal markets" not in auth.replace("Hilal Markets", "")
    assert "auth-form-logo" not in auth
    assert "Create your account, review screened assets" not in auth
    assert "Return to your screened market" not in auth
    assert "hilalmarkets-auth.css" in auth
    assert "auth-trust" in auth
    assert "SOL/USDT" not in auth
    assert "var(--hm-apple)" in styles
    assert "prefers-reduced-motion" in styles


def test_requested_home_market_passport_and_scanner_refinements_are_bound():
    # Two of the pages this used to check are gone. Home was replaced by Main, and the
    # older market page was replaced by the redesigned one at the same address. The
    # Passport below is the *historical* one, which is a different page and still served.
    passport = _read("templates/hilal/dashboard/passport.html")
    quick_passport = _read("templates/hilal/dashboard/partials/passport_quick_view.html")
    brain = _read("templates/system_brain.html")
    market_runtime = _read("static/sharia-market.js")
    dashboard_styles = _read("static/hilalmarkets-dashboard-v2.css")

    assert "methodology.form?.requestSubmit()" in market_runtime
    assert "marketStatusLabel" in market_runtime
    assert "data-favorite-toggle" in market_runtime
    assert "status_change_following" in _read("api/routers/dashboard.py")
    assert "min-height: 80px" in dashboard_styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in dashboard_styles

    assert "passport-page-title" not in passport
    assert "Back to market" in passport.split("</section>", 1)[0]
    assert "Reviewed by" not in passport
    assert "Next source scan" in passport
    assert "passport-select-control" in passport
    assert "Reviewed by" not in quick_passport
    assert "data-passport-quick-next-scan" in quick_passport
    assert "data-passport-quick-methodologies" in quick_passport
    assert "Add to My Screened Watchlist" not in quick_passport
    assert "Create Watchlist" not in quick_passport
    assert "Reviewer record" not in brain

    assert ".opportunity-journey-progress" in dashboard_styles
    assert 'data-dashboard-page="check_market"' in dashboard_styles
    assert ".passport-select-control" in dashboard_styles
    assert "position: relative;" in dashboard_styles
    assert "position: static !important;" in dashboard_styles
    scanner_styles = dashboard_styles.split(
        'data-dashboard-page="check_market"',
        1,
    )[1]
    assert "background: var(--hm-surface);" in scanner_styles
    assert "background: #cbfa4d !important;" in scanner_styles
    assert "background-image: none !important;" in scanner_styles
    assert "color: var(--hm-ink) !important;" in scanner_styles


def test_opportunity_cards_use_the_shared_real_asset_logo_loader():
    """It used to be enough that the card wrote the loader's attributes itself. That is
    now the defect: writing them by hand is how six templates each ended up knowing a
    different subset of a coin's pictures, and two of them typed the catalogue version
    into the template. The card asks the one macro, which asks the one owner."""

    opportunity_card = _read("templates/hilal/macros/opportunity_card.html")
    assert "coin_logo" in opportunity_card
    assert "data-asset-logo-module" not in opportunity_card
    assert "@web3icons/core" not in opportunity_card


def test_system_brain_is_reviewer_first_and_uses_five_sections():
    template = _read("templates/system_brain.html")
    review = _read("templates/system_brain_review_workspace.html")
    styles = _read("static/system-brain.css")
    runtime = _read("static/system-brain.js")
    shared_renderer = _read("static/chat-message-renderer.js")

    for label in ("Inbox", "Cases", "Operations", "Governance", "Audit &amp; Settings"):
        assert f"<span>{label}</span>" in template
    assert template.count("<nav>") == 1
    assert 'data-testid="system-brain-assistant"' in template
    assert "/api/v1/system-brain/conversations/" in runtime
    assert "customer-conversation-stream" in runtime
    assert "X-CSRF-Token" in runtime
    assert "HilalChatMessageRenderer.render" in runtime
    assert "escapeHtml" in shared_renderer
    assert "brain-overview-grid" in styles
    assert "brain-field-list" in styles
    assert "brain-terminal-action" in styles
    assert "Terminal governance actions are available" in review
    assert "AI research assistance. This is not a Sharia ruling" in review
    assert "dismiss_false_positive" in review
