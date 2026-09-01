"""Read a page the way a browser reads it, when a plain HTTP request cannot.

``OfficialEvidenceFetcher`` asks a server for a document and gets back what the server
sends. For a growing share of project sites that is a nearly empty HTML shell: the blog
posts, the release notes and the forum threads only exist after JavaScript has run. The
fetcher sees a page with thirty characters of text, calls it unreadable, scores it zero,
and the coin ends up in a review queue with "no working news page" — while the page is
sitting there, perfectly alive, in anybody's browser.

This module is the **one owner** of "render it properly and give me the HTML". It is used
as a *second attempt*, never as the first one:

1. the ordinary HTTP fetch runs, with its robots check and its delay;
2. only if that came back unreadable, unreachable, or blocked in a way a browser could
   get past, is this asked for the same address;
3. whatever comes back is proved exactly like any other page — readable, recent, on the
   project's own domain. Rendering never makes a page more trusted, it only makes it
   *legible*.

**Robots still decides.** A site that says "no automated retrieval" means it whether the
request comes from a script or from a headless browser, so the caller checks robots first
and never sends a disallowed address here. Nothing in this file bypasses that.

**It says so when it cannot look.** A deployment may have no browser engine installed at
all, and :meth:`BrowserPageRenderer.render` answers with a plain reason rather than
raising: an unavailable browser is one fewer way of reading a page, not a failure of the
sweep. The reason is what the review case then prints, so a person is told "switch this
on" instead of "go and find an address".

**It is on by default since 1 September 2026, and the image ships Chromium.** It used to
be off, and the cost of that was not theoretical: a coin whose blog is an ordinary
JavaScript site — which is most of them now — was reported to a reviewer as having no
news page, over and over, with the true reason buried in the case text.

**The memory it may use is bounded on purpose**, because the server it runs on has 3.9 GB
and no swap and Chromium is the largest thing that would ever start there. Three rules do
that, and all three are in this file:

1. one browser for a whole sweep, started only when a page actually needs it, and shut
   down in a ``finally`` whatever happened;
2. every page is opened in its own context and that context is closed straight after, so
   renderer processes cannot pile up across a sweep;
3. a **page budget** — after ``SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES`` pages the browser
   stops answering for the rest of the sweep and says why. A sweep is 25 coins with a
   handful of addresses each; a run that wanted hundreds of renders is a run that has
   gone wrong, and it must not be allowed to discover that by filling the server.

The launch flags below are part of the same budget. Images, GPU, extensions and
background networking are all off: this reads text and dates off a page, and every one of
those costs memory for something the reader never looks at.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any

from ai_market_monitor.core.config import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "LAUNCH_ARGUMENTS",
    "RETRYABLE_WITH_BROWSER",
    "BrowserPageRenderer",
    "RenderedPage",
    "render_engine_available",
]

#: The browser identity a rendered request carries. The same name the plain fetcher
#: sends, so a site's own logs show one visitor rather than two.
USER_AGENT = "HilalMarketsEvidenceBot/1.0 (+compliance research)"

#: Reasons the plain HTTP fetch failed that a real browser might get past. Anything else
#: — a settled 404, a robots refusal — is an answer, and asking again with a browser
#: would only be a second way of hearing the same "no".
RETRYABLE_WITH_BROWSER: frozenset[str] = frozenset(
    {
        "unreadable_document",
        "too_short",
        "official_source_unavailable",
        "official_source_fetch_failed",
    }
)

#: What Chromium is told not to do. Every flag here removes something this reader never
#: uses, and memory is the reason: the server has 3.9 GB and no swap, and a browser that
#: decodes every image on a project's blog is spending the worker container's ceiling on
#: pixels nobody reads. Text and dates are all that is taken from a rendered page.
LAUNCH_ARGUMENTS: tuple[str, ...] = (
    # Required in a container: there is no user namespace to sandbox into, and /dev/shm
    # is 64 MB by default, which Chromium overruns and then crashes on.
    "--no-sandbox",
    "--disable-dev-shm-usage",
    # Nothing here draws.
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--blink-settings=imagesEnabled=false",
    "--mute-audio",
    # Nothing here is a real user session.
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    # One page is open at a time, so one renderer is all that is ever needed. Without
    # this Chromium keeps spare processes for pages that have already been closed.
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=192",
)


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """What a browser saw, or why it could not look."""

    html: str = ""
    engine: str = ""
    #: Empty when the page was rendered. Otherwise the plain reason it was not.
    unavailable_reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.html)


def render_engine_available() -> str:
    """Which browser engine this installation can drive, or an empty string.

    Checked by name rather than by importing, because importing Scrapling's browser
    engines pulls a chain of optional dependencies and raises ``ModuleNotFoundError`` deep
    inside it when one is absent — which is a crash, not an answer.
    """

    if find_spec("playwright") is not None:
        return "playwright"
    return ""


class BrowserPageRenderer:
    """Drives one browser for a whole sweep, and closes it afterwards.

    One browser, not one per page: starting Chromium costs a second and a hundred
    megabytes, and a sweep reads dozens of addresses. The browser is started on the first
    page that actually needs it, so a run where every page reads fine never starts one.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Any = None
        self._browser: Any = None
        self._start_failed = ""
        self._lock = asyncio.Lock()
        #: How many pages this renderer has already drawn. See the page budget in the
        #: module note: the count is per renderer, and the resolver builds one per sweep.
        self._rendered = 0

    @property
    def enabled(self) -> bool:
        return bool(self.settings.sharia_source_browser_render_enabled)

    @property
    def pages_rendered(self) -> int:
        """How many pages this renderer has drawn so far. For reports and for tests."""

        return self._rendered

    @property
    def budget_spent(self) -> bool:
        return self._rendered >= self.settings.sharia_source_browser_render_max_pages

    def why_unavailable(self) -> str:
        """Why a browser cannot be used here, in words for a report. Empty when it can."""

        if not self.enabled:
            return (
                "Reading pages with a browser is switched off "
                "(SHARIA_SOURCE_BROWSER_RENDER_ENABLED=false)."
            )
        if not render_engine_available():
            return (
                "No browser engine is installed. Install Playwright and its Chromium "
                "build (`python -m playwright install chromium`)."
            )
        if self.budget_spent:
            return (
                "This run has already read "
                f"{self._rendered} page(s) with a browser, which is the limit "
                "(SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES). The next run carries on."
            )
        return self._start_failed

    async def render(self, url: str) -> RenderedPage:
        """The HTML of one address after its scripts have run.

        Never raises. Every failure comes back as a reason, because this is the *second*
        way of reading a page and the first one has already failed — turning that into an
        exception would replace a missing source with a broken sweep.
        """

        unavailable = self.why_unavailable()
        if unavailable:
            return RenderedPage(unavailable_reason=unavailable)
        browser = await self._ensure_browser()
        if browser is None:
            return RenderedPage(unavailable_reason=self._start_failed)
        context = None
        # Counted before the attempt, not after a success. A page that times out has
        # already cost the browser its time and its memory, and a run where every page
        # fails must still stop at the budget rather than retrying for ever.
        self._rendered += 1
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                java_script_enabled=True,
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            timeout_ms = int(self.settings.sharia_source_browser_render_timeout_seconds * 1000)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # A blog list renders its posts after the first paint far more often than it
            # renders them in the document. Waiting for the network to go quiet is what
            # separates "the shell arrived" from "the posts arrived"; a page that never
            # goes quiet still gives up and is read with whatever it has.
            #
            # This second wait gets **half** the timeout, not another whole one. Given
            # the same figure, one address that both loads slowly and never goes quiet
            # costs two full timeouts, and a page budget of forty then bounds the run at
            # nearly half an hour of a worker doing nothing else.
            with contextlib.suppress(Exception):  # a busy page is still worth reading
                await page.wait_for_load_state("networkidle", timeout=timeout_ms // 2)
            html = await page.content()
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.info("Browser render of %s failed: %s", url, type(exc).__name__)
            return RenderedPage(
                unavailable_reason=f"The browser could not read the page ({type(exc).__name__})."
            )
        finally:
            if context is not None:
                with contextlib.suppress(Exception):  # closing is best effort
                    await context.close()
        cap = self.settings.sharia_source_browser_render_max_characters
        return RenderedPage(html=html[:cap], engine="playwright")

    async def _ensure_browser(self) -> Any:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            if self._start_failed:
                return None
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=list(LAUNCH_ARGUMENTS),
                )
            except Exception as exc:  # noqa: BLE001 - an absent browser is an answer
                logger.info("Could not start a browser: %s", type(exc).__name__)
                self._start_failed = (
                    "A browser engine is installed but would not start "
                    f"({type(exc).__name__}). Run `python -m playwright install chromium`."
                )
                self._browser = None
                return None
            return self._browser

    async def aclose(self) -> None:
        """Shut the browser down. Safe to call when one was never started."""

        browser, self._browser = self._browser, None
        driver, self._playwright = self._playwright, None
        for closer in (
            getattr(browser, "close", None),
            getattr(driver, "stop", None),
        ):
            if closer is None:
                continue
            with contextlib.suppress(Exception):  # shutting down is best effort
                await closer()
