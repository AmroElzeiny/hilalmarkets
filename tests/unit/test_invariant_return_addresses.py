"""Every trip out of the product must come back to the name it started on.

This deployment answers on two names. `deploy/Caddyfile` sends both `hilalmarkets.com`
and `app.hilalmarkets.com` to the same application, and neither the sign-in pages nor the
dashboard refuse either one. So whenever the product sends somebody out to an outside
service — Google to choose an account, the payment company to pay — the address it hands
over has to point back at the name that person is actually using.

Getting it wrong is silent, which is what makes it worth a rule of its own:

* the session cookie belongs to whichever host set it, so coming back on the other name
  arrives with no session at all. After paying, that is a sign-in page;
* the Google popup talks to the page underneath with `postMessage`, and a message aimed
  at a different origin is never delivered. The window just closes.

There used to be four separate copies of "work out the base address", each written as
`str(settings.app_base_url or settings.public_base_url)`, and every one of them named the
application host no matter who was asking. `Settings.base_url_for` is the single owner
now, and the last test here stops the copies coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"

PUBLIC = "https://hilalmarkets.com"
APP = "https://app.hilalmarkets.com"

#: Both names, because a rule proved on one of them is not a rule.
EVERY_HOST = ["hilalmarkets.com", "app.hilalmarkets.com"]


def _settings(**overrides):
    from ai_market_monitor.core.config import Settings

    base = {
        "app_secret_key": "x" * 48,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "public_base_url": PUBLIC,
        "app_base_url": APP,
    }
    base.update(overrides)
    return Settings(**base)


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").lower()


@pytest.mark.parametrize("host", EVERY_HOST)
def test_a_person_is_handed_back_to_the_name_they_are_using(host: str) -> None:
    settings = _settings()
    assert _host(settings.base_url_for(host)) == host


def test_every_name_the_product_answers_on_has_an_address() -> None:
    settings = _settings()
    assert {_host(base) for base in settings.site_base_urls} == set(EVERY_HOST)
    # The application's own name comes first, because that is the answer when nobody
    # said which name they were using.
    assert _host(settings.site_base_urls[0]) == "app.hilalmarkets.com"


@pytest.mark.parametrize(
    "forged",
    ["evil.example.com", "hilalmarkets.com.evil.example.com", "", None, "  "],
)
def test_a_forged_host_can_never_introduce_an_address(forged: str | None) -> None:
    """The host only ever *chooses* from a closed set built out of settings.

    A `Host` header comes from whoever is calling. Letting it contribute the string would
    let somebody point a return address at their own site; letting it choose between two
    names we registered ourselves can do nothing worse than pick the other one of ours.
    """

    settings = _settings()
    assert settings.base_url_for(forged) in settings.site_base_urls


def test_a_name_is_matched_whatever_case_it_arrives_in() -> None:
    settings = _settings()
    assert settings.base_url_for("APP.HILALMARKETS.COM") == APP
    assert settings.base_url_for("HilalMarkets.com") == PUBLIC


def test_one_name_deployments_collapse_to_one_address() -> None:
    """Locally the two settings are the same host, and nothing should change there."""

    same = _settings(
        public_base_url="http://localhost:8000", app_base_url="http://localhost:8000"
    )
    assert same.site_base_urls == ("http://localhost:8000",)
    assert same.base_url_for("localhost") == "http://localhost:8000"
    assert same.base_url_for("anything-else") == "http://localhost:8000"


def test_google_return_addresses_are_built_from_the_same_owner() -> None:
    """One list, so the Google door cannot drift away from the rest of the product."""

    settings = _settings()
    assert settings.google_oauth_redirect_uris == tuple(
        f"{base}/auth/google/callback" for base in settings.site_base_urls
    )
    for host in EVERY_HOST:
        assert settings.google_oauth_redirect_uri_for(host) == (
            f"{settings.base_url_for(host)}/auth/google/callback"
        )


#: The shape the four deleted copies all had. Written as a pattern rather than a list of
#: files, so a fifth copy in a file that does not exist yet is caught too.
_HAND_BUILT_BASE = re.compile(
    r"(app_base_url\s+or\s+(settings|self)\.public_base_url)"
)


def test_nobody_works_out_a_base_address_for_themselves() -> None:
    """`Settings.base_url_for` is the only place that decides this.

    Every one of the four copies this replaced looked reasonable on its own, and every
    one of them silently named the application host for a person who was not on it. A
    fifth copy would do the same, so the pattern itself is refused.
    """

    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "config.py" and path.parent.name == "core":
            continue  # the owner itself
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if _HAND_BUILT_BASE.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, (
        "these build a base address by hand instead of asking Settings.base_url_for: "
        + ", ".join(offenders)
    )


def test_no_route_glues_a_return_path_onto_a_base_by_hand() -> None:
    """The callback path belongs to `Settings` too.

    A route that pasted a base and `/auth/google/callback` together would make a second
    return address that could disagree with the registered one, and Google compares them
    character for character.
    """

    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "config.py" and path.parent.name == "core":
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "/auth/google/callback" in line and "://" in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, "these build a Google return address by hand: " + ", ".join(
        offenders
    )
