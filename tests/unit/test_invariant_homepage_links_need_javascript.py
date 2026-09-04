"""A homepage that answers with no links at all has not been read.

The strongest claim a coin can make about where it publishes is a link on its own site:
the project itself saying "this is ours". The harvest that reads those links ran on the
raw HTML only, and a modern project homepage draws its navigation with JavaScript — so
for those projects the layer returned **nothing at all**, silently, and the coin fell
through to the layer that guesses ``/blog`` and ``/news``. Those guesses 404, and the
coin was then reported to a person as having no news page.

Measured on 4 September 2026 against five real homepages:

==================  ======================
Homepage            Links in the raw HTML
==================  ======================
bitcoin.org         63
ethereum.org        96
solana.com          56
htxdao.com          **0**
tron.network        **0**
==================  ======================

There is no middle ground, which is why the rule is "zero links" and not a threshold
somebody picked. HTX DAO was the reported coin; TRON is a top-twenty coin with the same
fault, which is what makes this a class rather than an instance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.sharia_page_render import (
    RETRYABLE_WITH_BROWSER,
    RenderedPage,
)
from ai_market_monitor.services.sharia_research import FetchTarget, ShariaResearchError
from ai_market_monitor.services.sharia_source_discovery import WebSourceDiscovery
from ai_market_monitor.services.sharia_source_resolution import SourceResolutionService

pytestmark = pytest.mark.anyio

SITE = "https://www.htxdao.com/"

#: What a plain fetch of a script-drawn homepage really returns, copied in shape from
#: ``htxdao.com``: stylesheets, and language alternates declared as ``<link>`` tags in the
#: head. Not one ``<a>``, because every anchor on that site is written by JavaScript —
#: which is why ``extract_links`` honestly finds zero, and why zero is the signal.
SCRIPT_DRAWN_HTML = """
<!doctype html><html lang="en-us"><head>
<link href="/_next/static/css/a.css" rel="stylesheet">
<link rel="alternate" hreflang="en-us" href="https://www.htxdao.com/en-us/">
<link rel="alternate" hreflang="zh-cn" href="https://www.htxdao.com/zh-cn/">
</head><body><div id="__next"></div></body></html>
"""

#: The same homepage once its scripts have run: the real navigation.
RENDERED_HTML = """
<!doctype html><html><body><nav>
<a href="https://www.htxdao.com/en-us/proposals">Proposals</a>
<a href="https://t.me/htxdao">Telegram</a>
<a href="https://x.com/HTX_DAO">X</a>
</nav></body></html>
"""

PLAIN_HTML = """
<!doctype html><html><body>
<a href="https://bitcoin.org/en/blog">Blog</a>
<a href="https://bitcoin.org/en/events">Events</a>
</body></html>
"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings() -> Settings:
    return Settings(database_url="sqlite+aiosqlite://")


class _Fetcher:
    """Answers one homepage with fixed HTML, or raises the code the real one would."""

    def __init__(self, html: str | None = None, *, error: str | None = None) -> None:
        self._html = html
        self._error = error
        self.calls = 0

    async def fetch(self, target: FetchTarget):
        self.calls += 1
        if self._error is not None:
            raise ShariaResearchError(self._error, "no", retryable=True)
        return self._html, {"content-type": "text/html"}, 200


class _Renderer:
    """A browser that either draws the page or says plainly why it cannot."""

    def __init__(self, html: str = "", *, unavailable: str = "") -> None:
        self._html = html
        self._unavailable = unavailable
        self.rendered: list[str] = []

    async def render(self, url: str) -> RenderedPage:
        self.rendered.append(url)
        if self._unavailable:
            return RenderedPage(unavailable_reason=self._unavailable)
        return RenderedPage(html=self._html, engine="playwright")

    def why_unavailable(self) -> str:
        return self._unavailable

    async def aclose(self) -> None:
        return None


def _discovery(fetcher: _Fetcher, renderer: _Renderer) -> WebSourceDiscovery:
    return WebSourceDiscovery(_settings(), fetcher=fetcher, renderer=renderer)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


async def test_a_homepage_with_no_links_is_drawn_again_in_a_browser() -> None:
    """The reported failure, asserted directly."""

    renderer = _Renderer(RENDERED_HTML)
    found = await _discovery(_Fetcher(SCRIPT_DRAWN_HTML), renderer).channel_links(SITE)

    assert renderer.rendered == [SITE]
    assert "https://t.me/htxdao" in found
    assert "https://x.com/HTX_DAO" in found
    assert "https://www.htxdao.com/en-us/proposals" in found


async def test_a_homepage_that_already_gave_links_is_never_re_rendered() -> None:
    """The browser is the expensive path. It is spent only when nothing else worked.

    A page budget shared with proving candidate pages means a needless render is a page
    some other coin does not get.
    """

    renderer = _Renderer(RENDERED_HTML)
    found = await _discovery(_Fetcher(PLAIN_HTML), renderer).channel_links(
        "https://bitcoin.org/"
    )

    assert renderer.rendered == []
    assert "https://bitcoin.org/en/blog" in found


@pytest.mark.parametrize(
    "reason",
    [
        "Reading pages with a browser is switched off.",
        "No browser engine is installed.",
        "This run has already read 8 page(s) with a browser, which is the limit.",
    ],
)
async def test_no_browser_means_no_opinion_never_a_crash(reason: str) -> None:
    """Every way the browser can be unavailable ends as an empty answer, not an error."""

    renderer = _Renderer(unavailable=reason)
    found = await _discovery(_Fetcher(SCRIPT_DRAWN_HTML), renderer).channel_links(SITE)

    assert found == ()
    assert renderer.rendered == [SITE]


@pytest.mark.parametrize("code", sorted(RETRYABLE_WITH_BROWSER))
async def test_a_homepage_that_refused_the_plain_fetch_is_drawn_instead(code: str) -> None:
    """A bot filter is not an answer about what the project publishes.

    ``hedera.com`` returns **403** to a plain request from this bot and nothing else.
    The harvest used to give up there, so for every coin behind a bot filter the project's
    own website — the one place its news link actually lives — was never read at all.

    Asserted over the whole retryable family, not the one status that was reported: the
    page prover already retried all of these with a browser, and the homepage harvest
    disagreeing with it is exactly the two-readers-one-question failure this codebase
    keeps hitting.
    """

    renderer = _Renderer(RENDERED_HTML)
    found = await _discovery(_Fetcher(error=code), renderer).channel_links(SITE)

    assert renderer.rendered == [SITE], f"{code} should have been given to the browser"
    assert "https://t.me/htxdao" in found


@pytest.mark.parametrize("code", ["robots_disallowed", "robots_unavailable"])
async def test_a_robots_refusal_is_never_worked_around_with_a_browser(code: str) -> None:
    """When a site's own rules say no, opening a browser is going around the answer.

    The distinction lives in ``RETRYABLE_WITH_BROWSER`` — one list, shared with the page
    prover — so neither reader can quietly decide robots is retryable.
    """

    assert code not in RETRYABLE_WITH_BROWSER
    renderer = _Renderer(RENDERED_HTML)
    found = await _discovery(_Fetcher(error=code), renderer).channel_links(SITE)

    assert found == ()
    assert renderer.rendered == []


def test_the_two_readers_share_one_list_of_retryable_failures() -> None:
    """Imported, never re-typed. A second copy would drift from the prover's."""

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ai_market_monitor"
        / "services"
        / "sharia_source_discovery.py"
    ).read_text(encoding="utf-8")
    assert "RETRYABLE_WITH_BROWSER" in source
    assert "official_source_fetch_failed" not in source, (
        "the discovery module names a failure code directly; import the shared list from "
        "sharia_page_render instead"
    )


async def test_the_answer_is_remembered_so_one_sweep_reads_a_homepage_once() -> None:
    """Including the rendered one — otherwise every layer pays for the browser again."""

    fetcher = _Fetcher(SCRIPT_DRAWN_HTML)
    renderer = _Renderer(RENDERED_HTML)
    discovery = _discovery(fetcher, renderer)

    first = await discovery.channel_links(SITE)
    second = await discovery.channel_links(SITE)

    assert first == second
    assert fetcher.calls == 1
    assert renderer.rendered == [SITE]


@pytest.mark.parametrize("site", ["", None, "not-a-url", "ftp://example.test/"])
async def test_an_address_that_is_not_an_official_website_is_never_touched(site) -> None:
    fetcher = _Fetcher(SCRIPT_DRAWN_HTML)
    renderer = _Renderer(RENDERED_HTML)

    assert await _discovery(fetcher, renderer).channel_links(site) == ()
    assert fetcher.calls == 0
    assert renderer.rendered == []


# ---------------------------------------------------------------------------
# One browser for the whole sweep
# ---------------------------------------------------------------------------


def test_the_resolver_lends_its_own_browser_to_the_discovery() -> None:
    """Two renderers would be two Chromiums, two budgets, and one of them never closed.

    On a 3.9 GB server the second browser is the largest thing running, and only the
    resolver's own is shut down at the end of a sweep.
    """

    service = SourceResolutionService.__new__(SourceResolutionService)
    SourceResolutionService.__init__(service, None, _settings())  # type: ignore[arg-type]

    assert service.discovery.renderer is service.renderer


def test_a_discovery_used_on_its_own_still_has_a_browser() -> None:
    """It must not depend on the resolver having built one for it."""

    discovery = WebSourceDiscovery(_settings())

    assert discovery.renderer is not None
    assert hasattr(discovery.renderer, "render")
