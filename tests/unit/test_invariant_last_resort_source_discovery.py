"""The two last resorts: read it with a browser, then ask a model. In that order.

A coin whose blog only exists after JavaScript has run, and a coin whose blog nobody
guessed, both ended up in the same place: a review case telling a person to go and type an
address. Two things now happen before that, and the rules they must not break are the
interesting part.

**The browser is a second attempt at reading, never a second opinion.** It runs only when a
plain request could not read the page *and* a browser could plausibly help. A settled "no"
— robots said no, the address is gone, the site forbids us — is an answer, and asking again
with a browser is only a second way of hearing it. Whatever a browser returns is judged by
exactly the same rules as anything else: rendering makes a page legible, never trusted.

**The model is the last layer and the only paid one.** It runs for a coin only when every
free layer has had its turn and a required category still holds nothing at all. What it
returns is filtered by the same function a search engine's results are filtered by — kept
only when provably the project's own domain or its own handle — and then fetched and
proved. A model that invents an address produces one that fails its proof and disappears,
exactly like a wrong guess from the convention layer.

That is the whole safety argument, and it is deliberately **not** a confidence number:
confidence is the model's opinion of itself and cannot tell a real address from an invented
one. Only fetching it can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import CanonicalAsset, OfficialSource, ReviewCase
from ai_market_monitor.services.sharia_page_render import (
    RETRYABLE_WITH_BROWSER,
    BrowserPageRenderer,
    RenderedPage,
)
from ai_market_monitor.services.sharia_research import ShariaResearchError
from ai_market_monitor.services.sharia_source_ai_discovery import (
    SUGGESTION_SCHEMA,
    AISourceDiscovery,
    _rows_to_results,
)
from ai_market_monitor.services.sharia_source_catalog import (
    LAYER_ORDER,
    PAID_LAYERS,
    VERIFIED,
    DiscoveryLayer,
    SearchResult,
    candidates_for,
)
from ai_market_monitor.services.sharia_source_resolution import (
    SourceProof,
    SourceResolutionService,
)
from tests.unit.test_invariant_official_sources_from_the_open_web import (  # noqa: E402
    _asset,
    _Discovery,
    _Internet,
    _live_page,
)

_GONE = ShariaResearchError("official_source_fetch_failed", "Official source returned HTTP 404.")
_ROBOTS = ShariaResearchError(
    "robots_disallowed", "The official source does not permit automated retrieval."
)

#: A page a plain fetch reads as almost nothing — the exact shape of a site whose content
#: only exists after JavaScript has run.
_SHELL = ("<html><body><div id='root'></div></body></html>", {"content-type": "text/html"}, 200)


class _Model:
    """A stand-in for the paid layer. Counts how often it was actually asked."""

    def __init__(self, results: tuple[SearchResult, ...] = (), *, configured: bool = True) -> None:
        self._results = results
        self._configured = configured
        self.asked = 0
        self.last_already_tried: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return self._configured

    def requirement(self) -> str:
        return "" if self._configured else "Asking a model is switched off in this test."

    async def suggest(self, *, asset_name, symbol, official_website, already_tried=()):
        self.asked += 1
        self.last_already_tried = tuple(already_tried)
        return self._results


class _Browser:
    """A stand-in for a real browser. Says what it was asked to render."""

    def __init__(self, pages: dict[str, str] | None = None, *, unavailable: str = "") -> None:
        self.pages = pages or {}
        self.unavailable = unavailable
        self.rendered: list[str] = []

    def why_unavailable(self) -> str:
        return self.unavailable

    async def render(self, url: str) -> RenderedPage:
        self.rendered.append(url)
        if self.unavailable:
            return RenderedPage(unavailable_reason=self.unavailable)
        html = self.pages.get(url, "")
        return RenderedPage(html=html, engine="test") if html else RenderedPage(
            unavailable_reason="The browser could not read the page (test)."
        )

    async def aclose(self) -> None:
        return None


def _rendered_news(days_old: int = 3) -> str:
    """The same live newsroom, as HTML a browser produced."""

    return _live_page(days_old=days_old)[0]


# --------------------------------------------------------------------------------
# The order of the layers, and what earns the paid one.
# --------------------------------------------------------------------------------


def test_the_paid_layer_is_the_last_one_tried():
    """Every free way of finding a page runs first. This is the whole cost control."""

    assert LAYER_ORDER[-1] is DiscoveryLayer.ASSISTED
    assert set(PAID_LAYERS) == {DiscoveryLayer.ASSISTED}
    for layer in LAYER_ORDER[:-1]:
        assert layer not in PAID_LAYERS


async def test_a_model_is_never_asked_when_a_free_layer_already_found_the_pages(
    test_context,
) -> None:
    """It costs money per coin per sweep. A coin that is fine must never spend it."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        model = _Model(results=(SearchResult(url="https://aptosfoundation.org/news"),))
        pages = {
            "https://t.me/s/aptos_network": _live_page(),
            "https://www.reddit.com/r/Aptos/": _live_page(),
            "https://aptosfoundation.org/currents": _live_page(),
            "https://forum.aptosfoundation.org/": _live_page(),
            "https://aptosfoundation.org/blog": _live_page(),
            "https://aptosfoundation.org/community": _live_page(),
        }
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet(pages),
            discovery=_Discovery(
                links=("https://t.me/aptos_network", "https://www.reddit.com/r/Aptos/")
            ),
            ai_discovery=model,
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()

    assert outcome.missing == ()
    assert model.asked == 0, "the paid layer ran for a coin that was already covered"


async def test_a_model_is_asked_only_after_every_free_layer_has_failed(test_context) -> None:
    """And what it offers is proved like anything else before it counts.

    The only page that exists here is one no free layer ever proposes: ``/insights`` is a
    word the classifier knows but not one the guessing layer tries, so every free layer
    genuinely comes back with nothing for the required category. That is the exact
    condition the paid layer is for, and it is the only condition it may run in.
    """

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="ZZZ", name="Zeta", website="https://zeta.example/")
        model = _Model(
            results=(
                SearchResult(url="https://zeta.example/insights", title="Zeta newsroom"),
                SearchResult(url="https://www.reddit.com/r/zeta/", title="Zeta community"),
            )
        )
        pages = {
            "https://zeta.example/": _live_page(),
            "https://zeta.example/insights": _live_page(),
            "https://www.reddit.com/r/zeta/": _live_page(),
        }
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet(pages),
            discovery=_Discovery(),
            ai_discovery=model,
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()
        rows = list(
            (
                await session.scalars(
                    select_official_sources(asset.id)
                )
            ).all()
        )

    assert model.asked == 1
    # The addresses the free layers had already disproved are handed over, so the one
    # answer is not spent offering them back.
    assert model.last_already_tried
    proved = {row.source_url for row in rows if row.verification_state == VERIFIED}
    assert "https://zeta.example/insights" in proved
    # The community page it offered is kept too. Optional means "never demanded", not
    # "thrown away when a layer finds one".
    assert "https://www.reddit.com/r/zeta/" in proved
    assert outcome.missing == ()


async def test_a_model_that_is_switched_off_is_never_called(test_context) -> None:
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="OFF", name="Offed", website="https://off.example/")
        model = _Model(
            results=(SearchResult(url="https://off.example/blog"),), configured=False
        )
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet({}),
            discovery=_Discovery(),
            ai_discovery=model,
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()

    assert model.asked == 0


@pytest.mark.parametrize(
    "address",
    [
        # Somebody else's coverage of the project.
        "https://cryptonews.example/zeta-launches-staking",
        # An exchange listing page.
        "https://exchange.example/markets/zeta",
        # A single post rather than the feed that keeps producing them.
        "https://x.com/zeta/status/1234567890",
        # A handle that is not the project's.
        "https://x.com/zetanews_daily",
        # Not https at all.
        "http://zeta.example/blog",
    ],
)
def test_an_address_a_model_offers_is_dropped_unless_it_is_provably_the_projects_own(
    address,
) -> None:
    """The same filter a search engine's answers go through. Giving the model layer its
    own judgement is the duplicate-vocabulary failure this codebase keeps meeting."""

    produced = candidates_for(
        DiscoveryLayer.ASSISTED,
        symbol="ZETA",
        asset_name="Zeta",
        official_website="https://zeta.example/",
        official_documentation=None,
        search_results=(SearchResult(url=address),),
    )

    assert produced == ()


def test_an_address_a_model_offers_is_scored_below_a_searched_one() -> None:
    """A model is recalling; a search engine is looking. Neither is believed unproved."""

    assisted = candidates_for(
        DiscoveryLayer.ASSISTED,
        symbol="ZETA",
        asset_name="Zeta",
        official_website="https://zeta.example/",
        official_documentation=None,
        search_results=(SearchResult(url="https://zeta.example/blog"),),
    )
    searched = candidates_for(
        DiscoveryLayer.SEARCH,
        symbol="ZETA",
        asset_name="Zeta",
        official_website="https://zeta.example/",
        official_documentation=None,
        search_results=(SearchResult(url="https://zeta.example/blog"),),
    )

    assert assisted and searched
    assert assisted[0].layer is DiscoveryLayer.ASSISTED
    assert assisted[0].confidence < searched[0].confidence
    # And unproved it is below the floor, so it can never stand as evidence on its own.
    assert assisted[0].confidence < 0.70


async def test_an_address_a_model_invented_never_becomes_evidence(test_context) -> None:
    """The one that matters. A confident wrong answer must die at the fetch."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="ZZZ", name="Zeta", website="https://zeta.example/")
        # An address that survives the filter — it is on the project's own domain and its
        # last path word is one a newsroom uses — and is not actually there.
        model = _Model(results=(SearchResult(url="https://zeta.example/newsroom"),))
        service = SourceResolutionService(
            session,
            settings,
            # Every address answers 404, including the invented one.
            fetcher=_Internet({}),
            discovery=_Discovery(),
            ai_discovery=model,
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()
        rows = list((await session.scalars(select_official_sources(asset.id))).all())

    assert model.asked == 1
    invented = [row for row in rows if row.source_url == "https://zeta.example/newsroom"]
    assert invented, "the proposal should be recorded so it is not tried again blindly"
    assert invented[0].verification_state != VERIFIED
    assert invented[0].confidence == 0.0
    # And the coin is still reported as missing its pages, rather than quietly "covered"
    # by an address nobody could fetch.
    assert outcome.missing
    assert outcome.escalated is True


def test_the_model_is_only_ever_allowed_to_answer_with_addresses() -> None:
    """A closed schema is what stops it volunteering an opinion the product must not hold."""

    assert SUGGESTION_SCHEMA["additionalProperties"] is False
    item = SUGGESTION_SCHEMA["properties"]["addresses"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {"url", "what_it_is"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"output_text": "not json at all"},
        {"output_text": "{}"},
        {"output_text": '{"addresses": "not a list"}'},
        {"output_text": '{"addresses": [{"url": ""}]}'},
        {"output": [{"type": "message", "content": [{"text": "[]"}]}]},
    ],
)
def test_an_unreadable_model_answer_is_nothing_rather_than_a_crash(payload) -> None:
    """A provider having a bad day must not stop the sweep proving what it already has.

    Total by construction. A reader that raises would turn "the model replied oddly" into
    "the whole source sweep failed", which is a diagnostic becoming the failure.
    """

    assert _rows_to_results(payload) == ()


def test_a_model_answer_is_read_through_one_place() -> None:
    payload = {
        "output_text": '{"addresses": [{"url": "https://zeta.example/blog", "what_it_is": "blog"}]}'
    }
    assert _rows_to_results(payload) == (
        SearchResult(url="https://zeta.example/blog", title="blog"),
    )


def test_asking_a_model_is_off_until_it_is_switched_on() -> None:
    settings = Settings(_env_file=None)
    discovery = AISourceDiscovery(settings)
    assert discovery.configured is False
    assert "switched off" in discovery.requirement()


# --------------------------------------------------------------------------------
# The browser: a second attempt at reading, never a second opinion.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("proof", "expected"),
    [
        # A page that answered with almost no text — the JavaScript-only shape.
        (SourceProof(True, True, False, False, status=200, error_code="too_short"), True),
        # A document that could not be parsed at all.
        (
            SourceProof(True, True, False, False, status=200, error_code="unreadable_document"),
            True,
        ),
        # A timeout. Worth one more try.
        (
            SourceProof(
                False, True, False, False, error_code="official_source_unavailable"
            ),
            True,
        ),
        # Robots said no. That is a settled answer, in any client.
        (
            SourceProof(False, False, False, False, error_code="robots_disallowed"),
            False,
        ),
        # The address is gone.
        (SourceProof(True, True, False, False, status=404), False),
        # The site forbids us.
        (SourceProof(True, True, False, False, status=403), False),
        # Read fine, only stale. A browser cannot make a page publish.
        (SourceProof(True, True, True, False, status=200), False),
    ],
)
def test_a_browser_is_only_asked_where_it_could_actually_help(proof, expected) -> None:
    assert SourceResolutionService._browser_could_help(proof) is expected


def test_the_retryable_list_and_the_guard_agree() -> None:
    """One vocabulary. A code in the list that the guard refuses would be a dead entry."""

    for code in RETRYABLE_WITH_BROWSER:
        proof = SourceProof(True, True, False, False, status=200, error_code=code)
        assert SourceResolutionService._browser_could_help(proof) is True


async def test_a_page_only_a_browser_can_read_becomes_a_working_source(test_context) -> None:
    """The whole point: the page was alive all along, and the plain fetch saw a shell.

    The blog address comes from the **project's own homepage**, which is where every
    on-site address comes from now. Until 4 September 2026 this test leaned on the
    guessing layer to invent ``/blog``; that layer is gone, and leaning on it was hiding
    the fact that the browser retry had never been proved on a link anybody published.
    """

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="JSX", name="Jsx", website="https://jsx.example/")
        browser = _Browser({"https://jsx.example/blog": _rendered_news()})
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet(
                {
                    "https://jsx.example/": _live_page(),
                    "https://jsx.example/blog": _SHELL,
                    "https://www.reddit.com/r/jsx/": _live_page(),
                }
            ),
            discovery=_Discovery(
                links=(
                    "https://jsx.example/blog",
                    "https://www.reddit.com/r/jsx/",
                )
            ),
            renderer=browser,
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()
        rows = list((await session.scalars(select_official_sources(asset.id))).all())

    blog = [row for row in rows if row.source_url == "https://jsx.example/blog"]
    assert blog, "the address should have been registered"
    assert blog[0].verification_state == VERIFIED
    assert "https://jsx.example/blog" in browser.rendered
    # And the row says how it was read, so nobody has to guess later.
    assert blog[0].check_detail.get("read_with") == "test"


async def test_a_robots_refusal_is_never_retried_with_a_browser(test_context) -> None:
    """A site that says no means it whether the request comes from a script or a browser."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOB", name="Nob", website="https://nob.example/")
        browser = _Browser({"https://nob.example/blog": _rendered_news()})
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet({"https://nob.example/": _live_page()}, default=_ROBOTS),
            discovery=_Discovery(),
            renderer=browser,
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()

    assert browser.rendered == [], "a browser was used to get past a robots refusal"


async def test_a_browser_that_cannot_run_is_reported_and_never_raises(test_context) -> None:
    """An absent browser is one fewer way of reading a page, not a broken sweep."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOB2", name="Nobtwo", website="https://n2.example/")
        browser = _Browser(unavailable="No browser engine is installed.")
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet(
                {"https://n2.example/": _live_page(), "https://n2.example/blog": _SHELL}
            ),
            discovery=_Discovery(),
            renderer=browser,
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()
        case = await session.scalar(
            select_gap_case(asset.id)
        )

    assert outcome.escalated is True
    assert case is not None
    # The case names what is stopping the machine, instead of asking a person to type an
    # address the machine is supposed to find.
    assert "No browser engine is installed." in case.human_review_reason
    assert "add the correct address" not in case.human_review_reason


def test_the_browser_is_on_by_default() -> None:
    """It was off until 1 September 2026, and that was the wrong default.

    Most project blogs only exist after their JavaScript has run. With rendering off, a
    plain fetch sees an empty shell, the page is scored unreadable, and the coin is
    reported to a reviewer as having no news page — a fault that is ours, described as a
    fault of the project's. Measured on 20 coins: 9 readable without it, 17 with it.
    """

    assert BrowserPageRenderer(Settings(_env_file=None)).enabled is True


def test_a_browser_that_is_switched_off_says_so_in_words() -> None:
    """Switching it off stays possible, and must never look like "the page is broken"."""

    settings = Settings(_env_file=None, sharia_source_browser_render_enabled=False)
    renderer = BrowserPageRenderer(settings)
    assert renderer.enabled is False
    assert "switched off" in renderer.why_unavailable()


async def test_a_disabled_browser_answers_with_a_reason_rather_than_rendering() -> None:
    settings = Settings(_env_file=None, sharia_source_browser_render_enabled=False)
    renderer = BrowserPageRenderer(settings)
    page = await renderer.render("https://example.com/blog")
    assert page.ok is False
    assert page.unavailable_reason
    # Closing one that never started is safe.
    await renderer.aclose()


async def test_a_browser_stops_at_its_page_budget_and_says_why() -> None:
    """One browser per sweep is only safe while the pages it draws are finite.

    The server has 3.9 GB and no swap. A run that met a whole batch of unreadable
    addresses would otherwise keep starting renderers until Docker killed the container,
    and the run would then be retried into the same wall.
    """

    settings = Settings(
        _env_file=None,
        sharia_source_browser_render_enabled=True,
        sharia_source_browser_render_max_pages=2,
    )
    renderer = BrowserPageRenderer(settings)
    assert renderer.budget_spent is False

    # Spend the budget without starting a real browser: the counter is what the guard
    # reads, and a real Chromium is not what this rule is about.
    renderer._rendered = 2

    assert renderer.budget_spent is True
    page = await renderer.render("https://example.com/blog")
    assert page.ok is False
    assert "limit" in page.unavailable_reason
    assert "SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES" in page.unavailable_reason
    await renderer.aclose()


# --------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------


def select_official_sources(asset_id):
    from sqlalchemy import select

    return select(OfficialSource).where(OfficialSource.canonical_asset_id == asset_id)


def select_gap_case(asset_id):
    from sqlalchemy import select

    return select(ReviewCase).where(
        ReviewCase.idempotency_key == f"official-source-gap:{asset_id}"
    )


__all__ = ["CanonicalAsset", "datetime", "timedelta", "UTC"]
