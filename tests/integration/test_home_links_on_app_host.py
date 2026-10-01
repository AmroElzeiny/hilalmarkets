"""Every way back to the website leaves the product's own hostname.

On `app.hilalmarkets.com` the root `/` is the dashboard, and the dashboard sends a
visitor who is not signed in to sign-in. Sign-in, sign-up and every public page are
served on that hostname too, so a link written as a plain `/` — "Back to the website",
the logo, `/#pricing` — reloaded the sign-in page instead of opening the website.

The rule tested here is the class, not the one button: no page served on the app
hostname may point at the home page, or an anchor on it, with a plain path; and every
server redirect to the home page goes to the marketing hostname.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from ai_market_monitor.core.app_links import site_link
from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.site_content import PUBLIC_PAGES, WAITLIST_ANCHOR

SITE = "http://testserver"
APP = "http://app.testserver"

#: An `href` that means "the home page" written as a plain path: `/`, `/#…` or `/?…`.
HOME_PATH_HREF = re.compile(r"""href\s*=\s*["'](/(?:[#?][^"']*)?)["']""")

ROOT = Path(__file__).resolve().parents[2]


def _with_hosts(test_context, *, site: str = SITE, app: str = APP):
    settings = test_context["settings"]
    changed = settings.model_copy(
        update={"public_base_url": AnyHttpUrl(site), "app_base_url": AnyHttpUrl(app)}
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: changed
    return changed


def test_site_link_is_a_plain_path_when_there_is_one_hostname(test_context):
    settings = test_context["settings"].model_copy(
        update={"public_base_url": AnyHttpUrl(SITE), "app_base_url": AnyHttpUrl(SITE)}
    )
    for path in ("/", "/#pricing", WAITLIST_ANCHOR):
        assert site_link(settings, path) == path


def test_site_link_is_the_marketing_hostname_when_the_product_has_its_own(test_context):
    settings = test_context["settings"].model_copy(
        update={"public_base_url": AnyHttpUrl(SITE), "app_base_url": AnyHttpUrl(APP)}
    )
    for path in ("/", "/#pricing", WAITLIST_ANCHOR):
        assert site_link(settings, path) == f"{SITE}{path}"


AUTH_PAGES = ("/signin", "/signup", "/reset-password")


@pytest.mark.parametrize(
    "path", [*AUTH_PAGES, *(page.path for page in PUBLIC_PAGES if page.path != "/")]
)
async def test_no_page_on_the_app_hostname_links_home_with_a_plain_path(test_context, path):
    _with_hosts(test_context)
    response = await test_context["client"].get(f"{APP}{path}", follow_redirects=False)
    if response.status_code in {301, 302, 303, 307, 308}:
        # A page hidden by the launch stage redirects; where it goes is tested below.
        location = response.headers["location"]
        assert not HOME_PATH_HREF.fullmatch(f'href="{location}"'), location
        return
    assert response.status_code == 200, (path, response.status_code)
    plain = HOME_PATH_HREF.findall(response.text)
    assert plain == [], f"{path} links home with a plain path: {plain}"
    # The React header and footer build their home links from this one value.
    if '"homeHref"' in response.text:
        found = re.search(r'"homeHref":\s*("[^"]*")', response.text)
        assert found is not None
        assert json.loads(found.group(1)) == f"{SITE}/"


async def test_back_to_the_website_on_sign_in_opens_the_website(test_context):
    """The reported case, followed all the way: the link must not lead back to sign-in."""

    _with_hosts(test_context)
    client = test_context["client"]
    page = await client.get(f"{APP}/signin")
    back = re.search(r'class="auth-back" href="([^"]+)"', page.text)
    assert back is not None
    assert back.group(1) == f"{SITE}/"
    landing = await client.get(back.group(1), follow_redirects=False)
    assert landing.status_code == 200


async def test_the_waitlist_redirect_goes_to_the_marketing_hostname(test_context):
    settings = _with_hosts(test_context)
    settings.public_waitlist_mode = True
    assert settings.waitlist_mode
    response = await test_context["client"].get(f"{APP}/pricing", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}{WAITLIST_ANCHOR}"


async def test_a_bad_plan_link_goes_to_pricing_on_the_marketing_hostname(test_context):
    _with_hosts(test_context)
    response = await test_context["client"].get(
        f"{APP}/subscribe?plan_code=nope", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/#pricing"


#: The React pages draw their own links. A home link there must come from `homeHref`.
REACT_HOME_PATH = re.compile(r"""href=["']/(?:#[^"']*)?["']|['"]/#[a-z]""")


@pytest.mark.parametrize(
    "source",
    sorted((ROOT / "Hilal-Markets-Website" / "src").rglob("*.tsx")),
    ids=lambda p: p.name,
)
def test_react_pages_build_home_links_from_the_server(source: Path):
    text = source.read_text(encoding="utf-8")
    assert REACT_HOME_PATH.findall(text) == [], source.name
