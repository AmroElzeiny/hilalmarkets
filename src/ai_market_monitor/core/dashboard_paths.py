"""Every signed-in address that more than one module has to know, written once.

Two routers serve the dashboard and neither may guess the other's addresses.
`dashboard_test.py` imports from `dashboard.py`, so `dashboard.py` cannot import back
from it; a name written in both files is a name that drifts. Both import this instead.

It sits in `core/` rather than beside the routers because the routers are not the only
readers. Alert email, Compliance Watch, the WhatsApp replies and the Telegram buttons all
write dashboard addresses into messages that leave the product, and an address in a sent
message cannot be corrected later. They import from here too.

That had already happened. The Opportunities page was registered from a constant called
``LIFECYCLES_PATH`` in one file and ``OPPORTUNITIES_PATH`` in the other — one address,
two names, two owners — and the redirects in the first file pointed at whichever of them
the reader happened to have in mind.

**The redesigned page is the page now.** These eight addresses used to serve an older
copy of each screen while `/dashboard-test/...` served the redesigned one. The side menu
opened the redesigned copies; every Telegram button, every WhatsApp reply and every alert
email opened the older ones. A person following a message from us and a person using the
menu were on two different screens with the same name. There is one of each now, at the
address the messages already used.
"""

from __future__ import annotations

from typing import Final

#: The front page of the dashboard, and the place sign-in lands on.
#:
#: It was `/main`, named after the router that serves it, and the side menu called it
#: "Main". Both are internal words for the page a person is meant to start on, so the
#: page is **Home** and its address is `/home`.
#:
#: The address was written in three separate files — `routers/main_dashboard.py`,
#: `routers/dashboard.py` and `routers/public.py` — each with its own constant for the
#: same string. Three owners for one address is how the redirect in one file ends up
#: pointing somewhere the other two do not serve. There is one now, and all three import
#: it.
HOME_PATH: Final[str] = "/home"

#: The address Home used to answer at, kept as a permanent redirect rather than deleted.
#:
#: It is written into email that has already been sent, into Telegram buttons, into the
#: `target_path` column of two tables and into saved bookmarks. None of those can be
#: corrected after the fact, so the address stays and moves — exactly as `/dashboard`
#: already does.
LEGACY_HOME_PATH: Final[str] = "/main"

#: Coins that passed screening, and one coin's Evidence Passport beneath it.
MARKET_PATH: Final[str] = "/dashboard/market"

#: The visual canvas where a monitor is drawn.
#:
#: The address says what a person came here to do. "Monitor" is a noun and the name of a
#: thing they may not have yet; the page is where one is made. Every button that opens it
#: already says "Create a monitor", and the guide's own step is called
#: ``nav-create-monitor``.
#:
#: This address was already taken — by the older assistant page, registered in
#: ``routers/dashboard.py`` as a second front door onto ``/dashboard/strategies/new``.
#: One address, two pages, and which one answered depended on the order the routers
#: happened to be registered in. The alias is gone, that page is gone, and this is the
#: one place a monitor is authored: made here, and changed here.
MONITOR_PATH: Final[str] = "/dashboard/create-monitor"

#: The address the canvas used to answer at, kept as a permanent redirect rather than
#: deleted — the same rule ``LEGACY_HOME_PATH`` above follows. It is written into saved
#: bookmarks and into the "put a monitor away" redirect that has already been sent.
LEGACY_MONITOR_PATH: Final[str] = "/dashboard/monitor"

#: The older assistant page: a chat box that asked somebody to describe a monitor in
#: words. It is gone, and this address forwards to the canvas.
#:
#: Kept rather than deleted because it is written into payment email that has already
#: been sent, into Telegram buttons and into saved bookmarks — the same rule
#: ``LEGACY_HOME_PATH`` above follows. It was also the platform's *only* front door onto
#: authoring for a long time, so it is the address most likely to be sitting in somebody's
#: browser history.
LEGACY_ASSISTANT_PATH: Final[str] = "/dashboard/strategies/new"


def monitor_edit_path(monitor_id: object) -> str:
    """The canvas, opened on a monitor somebody already has.

    One page and one address for authoring, whether a monitor is being made or changed.
    A second address for "change" is how the two drift into two different pages with the
    same job — which is exactly what happened last time, and why the assistant page and
    the canvas both claimed to be where a monitor is built.
    """

    return f"{MONITOR_PATH}?monitor={monitor_id}"


#: Where the canvas asks its own questions. The router mounts itself here and the page is
#: handed the address rather than assembling one, so there is one owner for it.
#:
#: ``/api/v1`` is added by ``main.py`` when the router is included, which is why the two
#: are written separately.
MONITOR_CANVAS_PREFIX: Final[str] = "/dashboard/monitor-canvas"
MONITOR_CANVAS_API: Final[str] = f"/api/v1{MONITOR_CANVAS_PREFIX}"

#: Where one monitor's board is read from. The canvas adds ``/{monitor_id}``.
MONITOR_BOARD_URL: Final[str] = f"{MONITOR_CANVAS_API}/monitors"

#: The monitors somebody has.
#:
#: The page is called **Monitors** — in its own heading, in the side menu, in the topbar
#: action and in every sentence the product writes about it. Its address said
#: ``watchlists``, which is a different product word: a Favorites list of coins is a
#: watchlist here, and a monitor is a rule that watches them. One page, two names, and
#: the address was the one nobody could see. The page answers at its own name now.
MONITORS_PATH: Final[str] = "/dashboard/monitors"

#: The address Monitors used to answer at, kept as a permanent redirect rather than
#: deleted. It is written into email that has already been sent, into Telegram buttons
#: and into saved bookmarks, none of which can be corrected after the fact — the same
#: rule ``LEGACY_HOME_PATH`` above follows.
LEGACY_WATCHLISTS_PATH: Final[str] = "/dashboard/watchlists"

#: What the monitors found.
OPPORTUNITIES_PATH: Final[str] = "/dashboard/opportunities"

#: Where a person can be told: Telegram, WhatsApp, email, the dashboard itself.
CONNECTIONS_PATH: Final[str] = "/dashboard/connections"

#: The older name for the same page. Kept as a 308 rather than a second page, because
#: outgoing email, the WhatsApp replies and the account-link flow all still write it.
INTEGRATIONS_PATH: Final[str] = "/dashboard/integrations"

#: The plan somebody is on.
SUBSCRIPTION_PATH: Final[str] = "/dashboard/subscription"

#: Preferences.
SETTINGS_PATH: Final[str] = "/dashboard/settings"

#: Help, and the tickets somebody has opened.
SUPPORT_PATH: Final[str] = "/dashboard/support"

#: Evidence and Activity: every lifecycle, every alert proof, every screening change and
#: every missed-alert investigation, in one list with its own tabs.
#:
#: This is a *different page* from Opportunities, and it is here rather than deleted
#: because several things still ask it a question that Opportunities cannot answer.
#: Opportunities says what is close right now; this says what happened and why, including
#: `?tab=compliance_changes`, which the front page, the alert emails, the WhatsApp
#: replies and Compliance Watch all link to by name.
#:
#: It used to answer at ``/dashboard/opportunities`` while its own title said "Evidence
#: and Activity" and the menu sent everybody to the redesigned Opportunities page
#: instead. One address, one page: this one keeps its own name.
LIFECYCLES_PATH: Final[str] = "/dashboard/lifecycles"

#: Where a screening change is explained, for everything that links straight to one.
COMPLIANCE_CHANGES_PATH: Final[str] = f"{LIFECYCLES_PATH}?tab=compliance_changes"
