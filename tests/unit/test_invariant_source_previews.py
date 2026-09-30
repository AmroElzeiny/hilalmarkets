"""Source preview cards: one catalog, real pages, real pictures, only real sources.

Rules asserted across the whole family, never on one example:

* every page a chat can cite has a name, a description and a screenshot on disk;
* every route id the public assistant knows is a catalog page at the same address, so
  the two cannot drift apart;
* the unlinked How We Screen page is never a card, because a card is a link;
* each kind of evidence Hilal can rest an answer on leads to the page that shows it,
  and an id that was not really handed over leads nowhere;
* a page that needs an account is linked on the product's own hostname.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.launch_stage import LaunchStage
from ai_market_monitor.services.hilal_chat import hilal_source_cards
from ai_market_monitor.services.public_chat import PUBLIC_ROUTE_PATHS, offerable_route_ids
from ai_market_monitor.services.source_previews import (
    SOURCE_PAGES,
    screenshot_path,
    source_preview,
    source_previews,
)

ROOT = Path(__file__).resolve().parents[2]


def _settings(**overrides: object) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_default_methodology_code=None,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("key", sorted(SOURCE_PAGES))
def test_every_citable_page_has_a_name_a_description_and_a_screenshot(key):
    page = SOURCE_PAGES[key]
    assert page.title.strip() and page.description.strip()
    assert screenshot_path(key).is_file(), (
        f"No screenshot for {key}. Run scripts/capture_source_previews.py."
    )
    # A real, small JPEG — a card is a thumbnail, not a download.
    data = screenshot_path(key).read_bytes()
    assert data[:3] == b"\xff\xd8\xff"
    assert 2_000 < len(data) < 250_000


@pytest.mark.parametrize("route_id", sorted(PUBLIC_ROUTE_PATHS))
def test_every_public_assistant_route_is_a_catalog_page_at_the_same_address(route_id):
    assert route_id in SOURCE_PAGES
    assert SOURCE_PAGES[route_id].path == PUBLIC_ROUTE_PATHS[route_id][1]


def test_the_unlinked_page_is_never_a_card():
    assert "how_we_screen" not in SOURCE_PAGES
    assert all(page.path != "/how-we-screen" for page in SOURCE_PAGES.values())


@pytest.mark.parametrize("key", sorted(SOURCE_PAGES))
def test_every_card_opens_its_own_page(key):
    card = source_preview(key, _settings(), asset="LINK")
    assert card is not None
    expected = SOURCE_PAGES[key].path.format(asset="link")
    assert card.url == expected
    assert card.image_url and card.image_url.startswith(f"/static/source-previews/{key}.jpg?v=")
    assert "{" not in card.title and "{" not in card.description


def test_a_coin_page_needs_its_coin():
    assert source_preview("passport", _settings()) is None
    card = source_preview("passport", _settings(), asset="link")
    assert card is not None
    assert card.title == "LINK Evidence Passport"
    assert card.url == "/dashboard/market/link"


def test_an_unknown_key_is_no_card():
    assert source_preview("somewhere_the_model_made_up", _settings()) is None


@pytest.mark.parametrize("key", sorted(k for k, page in SOURCE_PAGES.items() if page.account_only))
def test_account_pages_open_on_the_product_hostname(key):
    settings = _settings(
        public_base_url="https://hilalmarkets.com",
        app_base_url="https://app.hilalmarkets.com",
    )
    card = source_preview(key, settings, asset="btc")
    assert card is not None
    assert card.url.startswith("https://app.hilalmarkets.com/")
    assert card.address.startswith("app.hilalmarkets.com/")


def test_public_pages_stay_on_the_public_hostname():
    settings = _settings(
        public_base_url="https://hilalmarkets.com",
        app_base_url="https://app.hilalmarkets.com",
    )
    card = source_preview("market", settings)
    assert card is not None
    assert card.url == "/market"
    assert card.address == "hilalmarkets.com/market"


def test_cards_are_capped_and_never_repeated():
    wanted: list[tuple[str, str | None]] = [
        ("market", None),
        ("market", None),
        ("help", None),
        ("pricing", None),
        ("terms", None),
    ]
    cards = source_previews(wanted, _settings())
    assert [card.key for card in cards] == ["market", "help", "pricing"]


#: Each kind of evidence row Hilal can say it rests on, and the page that shows it.
HILAL_EVIDENCE = [
    ("asset:LINK", "passport", "/dashboard/market/link"),
    ("passport:LINK:AAOIFI", "passport", "/dashboard/market/link"),
    ("methodology:HILAL_MARKETS_AUTOMATED_SCREEN", "hilal_methodology", "/hilal-methodology"),
    ("plan:pro", "subscription", "/dashboard/subscription"),
    ("account:plan", "subscription", "/dashboard/subscription"),
    ("account:monitors", "monitors", "/dashboard/monitors"),
    ("market:shape", "halal_assets", "/dashboard/market"),
    ("market:exchanges", "halal_assets", "/dashboard/market"),
    ("market:categories", "halal_assets", "/dashboard/market"),
]


@pytest.mark.parametrize(("row", "key", "url"), HILAL_EVIDENCE)
def test_each_kind_of_hilal_evidence_leads_to_the_page_that_shows_it(row, key, url, monkeypatch):
    monkeypatch.setattr(
        "ai_market_monitor.services.hilal_chat.is_automated",
        lambda code: code == "HILAL_MARKETS_AUTOMATED_SCREEN",
    )
    cards = hilal_source_cards([row], known={row}, settings=_settings())
    assert [(card["key"], card["url"]) for card in cards] == [(key, url)]


@pytest.mark.parametrize("row", [item[0] for item in HILAL_EVIDENCE])
def test_a_row_that_was_not_handed_over_leads_nowhere(row):
    assert hilal_source_cards([row], known=set(), settings=_settings()) == []


def test_a_product_word_and_a_reviewed_standard_have_no_page_of_their_own(monkeypatch):
    monkeypatch.setattr("ai_market_monitor.services.hilal_chat.is_automated", lambda code: False)
    rows = ["word:evidence_passport", "methodology:AAOIFI"]
    assert hilal_source_cards(rows, known=set(rows), settings=_settings()) == []


def test_the_market_page_is_not_offered_before_launch():
    waitlist = _settings(launch_stage=LaunchStage.PUBLIC_WAITLIST, public_waitlist_mode=True)
    assert "market" not in offerable_route_ids(waitlist)
    assert "market" in offerable_route_ids(_settings(launch_stage=LaunchStage.PUBLIC_LAUNCH))


def test_both_chats_draw_cards_with_the_one_shared_renderer():
    static = ROOT / "src" / "ai_market_monitor" / "static"
    for script in ("hilalmarkets-public-chat.js", "hm-hilal-chat.js"):
        text = (static / script).read_text(encoding="utf-8")
        assert "HilalSourceCards?.render(" in text, script
    renderer = (static / "hm-source-cards.js").read_text(encoding="utf-8")
    # Opens in a new tab, and sets every server value as text, never as markup.
    assert 'link.target = "_blank"' in renderer
    assert 'link.rel = "noopener"' in renderer
    assert "textContent = source.title" in renderer
    assert "innerHTML = source" not in renderer
