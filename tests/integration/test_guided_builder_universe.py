"""Choosing coins and a screening method, with no assistant and no hard-coded ids.

The Builder had the *actions* for these вЂ” ``select_watchlist``, ``select_methodology``,
``set_explicit_assets`` вЂ” but nothing told it what the legal answers were. A client with
no list of choices either hard-codes them, which puts a Sharia decision in JavaScript, or
asks the assistant, which is the AI-only dependency the Builder exists to remove.

These drive the real service and the real tables. The two properties that matter:

* every choice offered comes from a governed record, and only the ones a person may use;
* choosing one goes through the same option path the assistant uses, so every screening
  gate runs unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaMethodologyStatus
from ai_market_monitor.services.builder_universe import builder_universe_options
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import _act, _service, _user
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio


async def _watchlist(session, user_id, name: str, coins: list[str], *, default=False):
    row = ApprovedWatchlist(user_id=user_id, name=name, is_default=default)
    session.add(row)
    await session.flush()
    for coin in coins:
        session.add(
            ApprovedWatchlistAsset(
                watchlist_id=row.id,
                canonical_asset=coin,
                added_at=datetime.now(UTC),
            )
        )
    await session.flush()
    return row


async def _methodology(session, *, code: str, status: str, name: str | None = None):
    row = ShariaMethodology(
        code=code,
        name=name or f"Method {code}",
        version="1",
        description=f"How {code} decides.",
        status=status,
        governing_body="Test Board",
        reviewer_group="Test Reviewers",
    )
    session.add(row)
    await session.flush()
    return row


async def _draft(service, session, user, prefix: str):
    chat = await service.create_session(session, user.id)
    await _act(service, session, chat, "select_mode", f"{prefix}-mode", value="monitor")
    return chat


# ---------------------------------------------------------------------------
# Only governed, permitted records are offered.
# ---------------------------------------------------------------------------


async def test_only_active_screening_methods_are_offered(test_context) -> None:
    """A draft method is not approved and an archived one is withdrawn.

    Offering either would let somebody monitor under a ruling the platform does not
    stand behind вЂ” a Sharia status the governance process never granted.
    """

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        # Each is given a unique code so the assertion names these exact records rather
        # than every methodology the fixtures happen to have seeded.
        created = {}
        for label, status in (
            ("active", ShariaMethodologyStatus.ACTIVE.value),
            ("draft", ShariaMethodologyStatus.DRAFT.value),
            ("archived", ShariaMethodologyStatus.ARCHIVED.value),
        ):
            code = f"{label}_{uuid4().hex[:8]}"
            created[label] = code
            await _methodology(session, code=code, status=status)
        await session.commit()

        chat = await _draft(service, session, user, "methods")
        options = await builder_universe_options(
            session, user_id=user.id, draft=load_strategy_draft_v2(chat)
        )

        codes = {item.code for item in options.methodologies}
        assert created["active"] in codes
        assert created["draft"] not in codes
        assert created["archived"] not in codes


async def test_only_the_signed_in_persons_lists_are_offered(test_context) -> None:
    """A watchlist belongs to one account. Offering another's leaks its name and coins."""

    mine = await _user(test_context)
    theirs = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        await _watchlist(session, mine.id, "My List", ["BTC", "ETH"])
        await _watchlist(session, theirs.id, "Their Secret List", ["SOL"])
        await session.commit()

        chat = await _draft(service, session, mine, "lists")
        options = await builder_universe_options(
            session, user_id=mine.id, draft=load_strategy_draft_v2(chat)
        )

        names = {item.name for item in options.watchlists}
        assert names == {"My List"}
        assert options.watchlists[0].asset_count == 2


async def test_an_empty_list_is_offered_with_the_reason_rather_than_hidden(
    test_context,
) -> None:
    """Hiding it would look like the list was deleted. Saying it is empty is actionable."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        await _watchlist(session, user.id, "Empty List", [])
        await session.commit()

        chat = await _draft(service, session, user, "empty")
        options = await builder_universe_options(
            session, user_id=user.id, draft=load_strategy_draft_v2(chat)
        )

        empty = next(item for item in options.watchlists if item.name == "Empty List")
        assert empty.asset_count == 0
        assert empty.to_dict()["empty_reason"]


# ---------------------------------------------------------------------------
# Choosing one, with no model call, through the governed path.
# ---------------------------------------------------------------------------


async def test_a_watchlist_is_chosen_and_stored_with_no_model_call(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        row = await _watchlist(session, user.id, "Majors", ["BTC", "ETH"], default=True)
        await session.commit()

        chat = await _draft(service, session, user, "pick")
        before = (planner.plan_calls, planner.reply_calls)

        await _act(service, session, chat, "select_universe", "pick-u", value="approved_watchlist")
        await _act(service, session, chat, "select_watchlist", "pick-w", value=str(row.id))

        draft = load_strategy_draft_v2(chat)
        assert str(draft.sharia_policy.approved_watchlist_id) == str(row.id)
        assert (planner.plan_calls, planner.reply_calls) == before

        options = await builder_universe_options(session, user_id=user.id, draft=draft)
        chosen = [item for item in options.watchlists if item.selected]
        assert [item.name for item in chosen] == ["Majors"]


async def test_a_screening_method_is_chosen_and_stored_with_no_model_call(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await _draft(service, session, user, "meth")
        options = await builder_universe_options(
            session, user_id=user.id, draft=load_strategy_draft_v2(chat)
        )
        assert options.methodologies, "the seeded methodology should be offered"
        target = options.methodologies[0]
        before = (planner.plan_calls, planner.reply_calls)

        await _act(
            service, session, chat, "select_methodology", "meth-1",
            value=target.methodology_id,
        )

        draft = load_strategy_draft_v2(chat)
        assert str(draft.sharia_policy.methodology_id) == target.methodology_id
        assert (planner.plan_calls, planner.reply_calls) == before

        after = await builder_universe_options(session, user_id=user.id, draft=draft)
        assert [item.methodology_id for item in after.methodologies if item.selected] == [
            target.methodology_id
        ]


async def test_explicit_coins_are_recorded_and_read_back(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await _draft(service, session, user, "explicit")
        before = (planner.plan_calls, planner.reply_calls)

        await _act(
            service, session, chat, "select_universe", "explicit-u", value="explicit_assets"
        )
        await _act(service, session, chat, "set_explicit_assets", "explicit-a", value="BTC, ETH")

        draft = load_strategy_draft_v2(chat)
        options = await builder_universe_options(session, user_id=user.id, draft=draft)
        assert options.universe_mode == "explicit_assets"
        # The server canonicalises a typed coin into a tradable pair, so the stored
        # value is BTC/USDT rather than BTC. That is the resolution the screening and
        # the provider both use, and the Builder reads back what was actually stored.
        assert any(item.startswith("BTC") for item in options.explicit_assets)
        assert any(item.startswith("ETH") for item in options.explicit_assets)
        assert (planner.plan_calls, planner.reply_calls) == before


# ---------------------------------------------------------------------------
# The step says what is missing instead of going blank.
# ---------------------------------------------------------------------------


async def test_choosing_a_list_with_none_saved_explains_what_to_do(test_context) -> None:
    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await _draft(service, session, user, "none")
        await _act(service, session, chat, "select_universe", "none-u", value="approved_watchlist")

        options = await builder_universe_options(
            session, user_id=user.id, draft=load_strategy_draft_v2(chat)
        )
        assert options.watchlists == ()
        assert options.notices
        assert any("Favorites" in item for item in options.notices)


async def test_the_options_payload_never_carries_another_persons_identifiers(
    test_context,
) -> None:
    """A response the client renders must contain only this account's own records."""

    mine = await _user(test_context)
    theirs = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        other = await _watchlist(session, theirs.id, "Theirs", ["SOL"])
        await session.commit()

        chat = await _draft(service, session, mine, "leak")
        payload = (
            await builder_universe_options(
                session, user_id=mine.id, draft=load_strategy_draft_v2(chat)
            )
        ).to_dict()

        assert str(other.id) not in str(payload)
        assert "Theirs" not in str(payload)
        assert str(theirs.id) not in str(payload)
