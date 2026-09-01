"""The research page renders through the real app, and says what it is.

The one thing this page must never do is read as a Shariah ruling. So the tests below
are mostly about wording: that the warning is on the page, that a machine is named as
the author, and that "not enough data" is never presented as a refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_market_monitor.core.dashboard_paths import RESEARCH_PATH
from ai_market_monitor.db.models import AutomatedScreenRun, CoinEvidenceDocument
from ai_market_monitor.services.sharia_automated_screen import AUTOMATED_DISCLOSURE
from tests.integration.test_dashboard_web import _signup_and_verify


async def _seed_runs(session) -> None:
    now = datetime.now(UTC)
    session.add(
        AutomatedScreenRun(
            symbol="ZEBRA",
            asset_name="Zebra Network",
            verdict="eligible",
            reasons=[
                {
                    "text": "It runs its own blockchain network.",
                    "quote": "…proof of stake…",
                    "url": "https://zebra.example/",
                }
            ],
            activities=["own_settlement_network"],
            blocking_activities=[],
            evidence=[],
            open_questions=[],
            documents_read=5,
            primary_documents_read=4,
            decided_at=now,
        )
    )
    session.add(
        AutomatedScreenRun(
            symbol="LENDR",
            asset_name="Lendr Finance",
            verdict="not_eligible",
            reasons=[
                {
                    "text": "The project's own business is lending money.",
                    "quote": "…lending protocol…",
                    "url": "https://lendr.example/docs",
                }
            ],
            activities=["lending_borrowing"],
            blocking_activities=["lending_borrowing"],
            evidence=[],
            open_questions=[],
            documents_read=6,
            primary_documents_read=5,
            decided_at=now,
        )
    )
    session.add(
        AutomatedScreenRun(
            symbol="QUIET",
            asset_name="Quiet Token",
            verdict="not_enough_data",
            reasons=[
                {
                    "text": "We could not read any page from this project.",
                    "quote": "",
                    "url": "",
                }
            ],
            activities=[],
            blocking_activities=[],
            evidence=[],
            open_questions=[],
            documents_read=0,
            primary_documents_read=0,
            decided_at=now,
        )
    )
    session.add(
        CoinEvidenceDocument(
            symbol="ZEBRA",
            url="https://zebra.example/",
            category="official_website",
            title="Zebra",
            characters=2400,
            seeded=True,
            is_primary=True,
            fetched_at=now,
        )
    )
    await session.commit()


async def test_the_page_renders_and_names_the_machine(test_context):
    await _signup_and_verify(test_context, email="research-page@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    page = (await test_context["client"].get(RESEARCH_PATH)).text

    assert "Coins we researched" in page
    assert "Nobody has reviewed these answers" in page
    assert AUTOMATED_DISCLOSURE in page
    assert "Hilal Markets Methodology" in page


async def test_every_verdict_reaches_the_page_in_plain_words(test_context):
    await _signup_and_verify(test_context, email="research-words@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    page = (await test_context["client"].get(RESEARCH_PATH)).text

    for symbol, label in (
        ("ZEBRA", "Looks clean"),
        ("LENDR", "Has a problem"),
        ("QUIET", "Not enough data"),
    ):
        assert symbol in page, symbol
        assert label in page, label
    # Never the internal field names.
    assert "not_eligible" not in page.split("</head>", 1)[-1].replace("verdict=not_eligible", "")


async def test_not_enough_data_is_never_presented_as_a_refusal(test_context):
    """The word a reader hears is "no". The page has to say otherwise, in its own words."""

    await _signup_and_verify(test_context, email="research-thin@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    page = (
        await test_context["client"].get(f"{RESEARCH_PATH}?verdict=not_enough_data")
    ).text

    assert "These coins are not refused" in page
    assert "QUIET" in page
    assert "LENDR" not in page  # the filter actually filters


async def test_the_counters_match_what_the_filter_shows(test_context):
    await _signup_and_verify(test_context, email="research-counts@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    from ai_market_monitor.services.automated_research_reader import (
        AutomatedResearchReader,
    )

    async with test_context["session_factory"]() as session:
        reader = AutomatedResearchReader(session)
        counts = await reader.counts()
        assert counts["all"] == 3
        for verdict in ("eligible", "not_eligible", "not_enough_data"):
            assert counts[verdict] == 1
            rows = await reader.rows(verdict=verdict)
            assert len(rows) == counts[verdict], verdict


async def test_one_coins_page_shows_the_words_the_verdict_rests_on(test_context):
    """A verdict a reader cannot check is an assertion, not a finding."""

    await _signup_and_verify(test_context, email="research-detail@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    page = (await test_context["client"].get(f"{RESEARCH_PATH}/LENDR")).text

    assert "Why we said this" in page
    assert "The project&#39;s own business is lending money." in page or (
        "The project's own business is lending money." in page
    )
    assert "lending protocol" in page  # the quotation itself
    assert "https://lendr.example/docs" in page  # and the page it came from
    assert "A machine read this, not a scholar" in page


async def test_a_coin_nobody_researched_has_no_page(test_context):
    await _signup_and_verify(test_context, email="research-missing@example.com")
    response = await test_context["client"].get(f"{RESEARCH_PATH}/NOTACOIN")
    assert response.status_code == 404


async def test_the_page_needs_an_account(test_context):
    """It is dashboard research, not a public claim about coins."""

    response = await test_context["client"].get(RESEARCH_PATH, follow_redirects=False)
    assert response.status_code in {302, 303, 307, 308, 401, 403}
