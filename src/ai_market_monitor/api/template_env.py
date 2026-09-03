"""Every filter and global a Jinja template may use, installed the same way everywhere.

Each router builds its own ``Jinja2Templates``, and each one used to register whatever
filters its own pages happened to need. That is fine right up to the moment a template
is loaded through a different router's environment — and several are, because the
templates share macros and because ``scripts/check_jinja_templates.py`` loads *every*
template through one environment to prove they all still compile.

The failure is silent in the worst way: the template does not render wrong, it refuses
to load at all, with ``No filter named …`` — and only on the pages served by the router
that did not know about it. ``plan_limit`` and ``asset_logo`` were already being
registered twice by hand for exactly this reason, with a comment in one of them
explaining why.

So there is one function, and every environment is passed through it. Adding a filter
here gives it to every page at once; adding it to a router gives it to some pages and
breaks the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates

from ai_market_monitor.core.asset_logos import asset_logo
from ai_market_monitor.core.dashboard_paths import MONITOR_PATH, monitor_edit_path
from ai_market_monitor.services.billing import method_word, provider_method, provider_word
from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_PUBLIC_PATH as AUTOMATED_METHODOLOGY_PATH,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
)
from ai_market_monitor.services.sharia_automated_screen import (
    METHODOLOGY_DISPLAY_NAME as AUTOMATED_METHODOLOGY_NAME,
)
from ai_market_monitor.services.sharia_automated_screen import (
    METHODOLOGY_SYSTEM_CODE as AUTOMATED_METHODOLOGY_CODE,
)
from ai_market_monitor.services.sharia_source_catalog import category_label, state_label


def short_datetime(value: datetime | None, timezone_name: str = "UTC") -> str:
    if value is None:
        return "-"
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def reward_amount(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01"))
    if value == Decimal("0.00"):
        return "$ 0.00"
    return f"${value:.2f}"


def plan_limit(value: object) -> str:
    if isinstance(value, int) and value >= 100_000:
        return "Unlimited"
    return str(value)


def payment_company(value: object) -> str:
    """What one payment company is called in front of a person.

    Four pages each held their own list turning ``creem`` into "Creem" and ``nowpayments``
    into "NOWPayments", and one of them simply wrote both names into a sentence whatever
    the server was set to. The billing service owns that list; this is how a page reaches
    it. Anything the service does not know is tidied rather than printed raw, so a stored
    word like ``bank_transfer`` never reaches a reader as it is written in the database.
    """

    if not value:
        return ""
    known = provider_word(str(value))
    if known is not None:
        return known
    return str(value).replace("_", " ").title()


def payment_route(value: object) -> str:
    """How a payment was taken: the way of paying, and the company that took it.

    A finished payment keeps only the company's name, so which way of paying it was is
    read back from the company. Both halves come from the billing service; the payment
    history page used to hold its own two-line version of this.
    """

    company = payment_company(value)
    if not company:
        return ""
    way = method_word(provider_method(str(value)))
    return f"{way.title()} via {company}" if way else company


def register(templates: Jinja2Templates) -> Jinja2Templates:
    """Give one template environment everything the product's templates expect."""

    templates.env.filters["short_dt"] = short_datetime
    templates.env.filters["reward_amount"] = reward_amount
    templates.env.filters["plan_limit"] = plan_limit
    # The payment company's name, from the one place that owns it. Four pages used to
    # translate `creem` into "Creem" themselves, and the plan popup told everybody their
    # details go to "Creem or NOWPayments" whatever the server was really set to.
    templates.env.filters["payment_company"] = payment_company
    templates.env.filters["payment_route"] = payment_route
    # Plain words for a source's state and kind. Without these the System Brain printed
    # the stored value at a reviewer: "candidate", "unreachable", "not_permitted".
    templates.env.filters["source_state"] = state_label
    templates.env.filters["source_category"] = category_label
    # The one owner of "which pictures exist for this coin", reachable from a template.
    # Six templates used to answer it themselves, each knowing a different subset; the
    # catalogue address was typed into two of them by hand.
    templates.env.globals["asset_logo"] = asset_logo
    # Where a monitor is made, and where one is changed. Reachable from every template
    # so no page writes the address itself. Seven templates used to type the older
    # assistant page's address by hand, and each had to be found again when it moved.
    templates.env.globals["monitor_path"] = MONITOR_PATH
    templates.env.globals["monitor_edit_path"] = monitor_edit_path
    # Which standard is the machine-made one, and the one sentence that has to travel
    # with its results. A template that showed an automated verdict without the warning
    # would be presenting a rule's output as a reviewed religious decision, so neither
    # value is retyped in a page: both come from the screen module that owns them.
    templates.env.globals["automated_methodology_code"] = AUTOMATED_METHODOLOGY_CODE
    templates.env.globals["automated_methodology_disclosure"] = AUTOMATED_DISCLOSURE
    templates.env.globals["automated_methodology_name"] = AUTOMATED_METHODOLOGY_NAME
    # Where the whole standard is explained, in public. Every notice links here, so the
    # address is a global rather than a string typed into each of the five templates
    # that draw the notice — the failure this product has repeated most often is a page
    # holding its own copy of an address that later moved.
    templates.env.globals["automated_methodology_path"] = AUTOMATED_METHODOLOGY_PATH
    return templates
