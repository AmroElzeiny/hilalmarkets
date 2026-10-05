"""The research page renders through the real app, and shows only people's decisions.

The owner's rule from 5 October 2026: no decision reaches this page until a Hilal Markets
reviewer made it in System Brain. The machine's readings below are seeded on purpose —
three verdicts of every kind — and none of them may appear. Coins a reviewer decided do,
with the reviewer's reasons, the coin's own picture and a link to its Passport.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.core.dashboard_paths import RESEARCH_PATH
from ai_market_monitor.db.models import (
    AutomatedScreenRun,
    CoinEvidenceDocument,
    ProviderCoinProfile,
    ReviewDecision,
    User,
)
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.services.reviewer_passports import CitedPage, write_reviewer_passport
from tests.integration.test_dashboard_web import _signup_and_verify

LOGO = "https://s2.coinmarketcap.com/static/img/coins/64x64/9999.png"


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


async def _decide(session, symbol: str, name: str, *, approved: bool, reasons: list[str]):
    admin = User(display_name="Reviewer", role=UserRole.ADMIN)
    session.add(admin)
    await session.flush()
    decision = ReviewDecision(
        id=uuid4(), review_case_id=uuid4(), admin_user_id=admin.id, public_reasons=reasons
    )
    await write_reviewer_passport(
        session,
        symbol=symbol,
        name=name,
        approved=approved,
        decision=decision,
        reviewer_label="Hilal Markets reviewer",
        pages=[
            CitedPage(
                url=f"https://{symbol.lower()}.example/",
                title=name,
                category="official_website",
                own=True,
                read_at=datetime.now(UTC),
            )
        ],
    )
    await session.commit()


async def test_machine_verdicts_never_reach_the_page(test_context):
    await _signup_and_verify(test_context, email="research-machine@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)

    page = (await test_context["client"].get(RESEARCH_PATH)).text

    assert "New coins our reviewer checked" in page
    assert "No coin has been decided yet" in page
    for symbol in ("ZEBRA", "LENDR", "QUIET"):
        assert symbol not in page, symbol
    for machine_label in ("Looks clean", "Has a problem", "Not enough data"):
        assert machine_label not in page, machine_label


async def test_reviewer_decisions_reach_the_page_with_the_coins_own_picture(test_context):
    await _signup_and_verify(test_context, email="research-decided@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)
        session.add(ProviderCoinProfile(provider="coinmarketcap", symbol="LENDR", logo_url=LOGO))
        await _decide(
            session, "LENDR", "Lendr Finance", approved=False, reasons=["It lends money."]
        )
        await _decide(
            session, "ZEBRA", "Zebra Network", approved=True, reasons=["It runs a network."]
        )

    page = (await test_context["client"].get(RESEARCH_PATH)).text

    assert "Checked by a Hilal Markets reviewer" in page
    assert "Not approved" in page and "Approved by our reviewer" in page
    assert "LENDR" in page and "ZEBRA" in page
    assert "QUIET" not in page  # read by the machine, never decided
    assert f'data-asset-logo-provider-url="{LOGO}"' in page


@pytest.mark.parametrize("old", ["eligible", "not_eligible", "not_enough_data"])
async def test_an_old_bookmark_opens_the_list_rather_than_an_error(test_context, old):
    await _signup_and_verify(test_context, email=f"research-{old}@example.com")
    response = await test_context["client"].get(f"{RESEARCH_PATH}?verdict={old}")
    assert response.status_code == 200


async def test_the_counters_match_what_the_filter_shows(test_context):
    from ai_market_monitor.services.automated_research_reader import (
        AutomatedResearchReader,
    )

    async with test_context["session_factory"]() as session:
        await _seed_runs(session)
        await _decide(session, "LENDR", "Lendr Finance", approved=False, reasons=["It lends."])
        await _decide(session, "ZEBRA", "Zebra Network", approved=True, reasons=["A network."])
        reader = AutomatedResearchReader(session)
        everything = await reader.page()
        assert everything["research_counts"] == {"all": 2, "approved": 1, "not_approved": 1}
        for verdict in ("approved", "not_approved"):
            shown = await reader.page(verdict=verdict)
            assert len(shown["research_rows"]) == everything["research_counts"][verdict]


async def test_one_coins_page_shows_the_reviewers_reasons(test_context):
    await _signup_and_verify(test_context, email="research-detail@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)
        await _decide(
            session, "LENDR", "Lendr Finance", approved=False,
            reasons=["The project's own business is lending money."],
        )

    page = (await test_context["client"].get(f"{RESEARCH_PATH}/LENDR")).text

    assert "Why the reviewer decided this" in page
    assert "own business is lending money." in page
    assert "/passports/lendr" in page
    assert "lending protocol" not in page  # the machine's own quotation is not shown


async def test_a_coin_the_machine_read_but_nobody_decided_has_no_page(test_context):
    await _signup_and_verify(test_context, email="research-undecided@example.com")
    async with test_context["session_factory"]() as session:
        await _seed_runs(session)
    response = await test_context["client"].get(f"{RESEARCH_PATH}/ZEBRA")
    assert response.status_code == 404


async def test_the_page_needs_an_account(test_context):
    """It is dashboard research, not a public claim about coins."""

    response = await test_context["client"].get(RESEARCH_PATH, follow_redirects=False)
    assert response.status_code in {302, 303, 307, 308, 401, 403}
