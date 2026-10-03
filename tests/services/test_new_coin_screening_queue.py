"""Every waiting coin is read, coins that read nothing are tried again, and the reviewer
sees what was read.

Written after 3 October 2026, when the System Brain's new-coin tasks looked empty: the
sweep read twenty-five coins a day against more than two hundred new ones, ten coins had
nothing read because their address was rewritten, those ten were never tried again, and
the task list and page drew a research file new coins never have.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime, timedelta

import pytest
from markupsafe import escape
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AutomatedScreenRun,
    ProviderCoinProfile,
    ReviewCase,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.services.automated_screen_pipeline import (
    AutomatedScreenPipeline,
    PipelineResult,
    coins_to_screen,
)
from ai_market_monitor.services.coin_evidence_crawler import EvidenceFolder
from ai_market_monitor.services.coin_terms_ai_review import (
    COUNTED_AI_STATES,
    MAX_AI_ATTEMPTS,
    RETRY_AFTER,
    RETRY_AI_STATES,
    AIReview,
    AIState,
)
from ai_market_monitor.services.sharia_admin_dashboard import ShariaAdminDashboardService
from ai_market_monitor.services.sharia_research import FETCH_FAILURE_WORDS
from tests.coin_review_fakes import FakeAI, FakeCrawler, FakeProvider

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)

#: Every state the AI reviewer can write onto a run.
ALL_AI_STATES = typing.get_args(AIState)
NEVER_RETRIED = sorted(set(ALL_AI_STATES) - RETRY_AI_STATES)


def _profile(symbol: str, market_cap: float | None = None) -> ProviderCoinProfile:
    return ProviderCoinProfile(
        provider="coinmarketcap",
        symbol=symbol,
        research_state="researched",
        market_cap_usd=market_cap,
    )


def _run(symbol: str, state: str, attempts: int, decided: datetime) -> AutomatedScreenRun:
    return AutomatedScreenRun(
        symbol=symbol,
        verdict="not_enough_data",
        ai_review_state=state,
        ai_review_attempts=attempts,
        decided_at=decided,
    )


# --------------------------------------------------------------------------------
# Which coins are owed a reading, and in what order.
# --------------------------------------------------------------------------------


async def test_never_read_coins_come_first_biggest_first(test_context):
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                _profile("SMALL", 1_000),
                _profile("BIG", 9_000_000),
                _profile("UNSIZED_B"),
                _profile("UNSIZED_A"),
                _profile("RETRY", 50_000_000),
                _run("RETRY", "failed", 1, NOW - RETRY_AFTER - timedelta(minutes=1)),
            ]
        )
        await session.flush()

        order = await coins_to_screen(session, now=NOW)

    # A retry never jumps ahead of a coin that was never read, however big it is.
    assert order == ["BIG", "SMALL", "UNSIZED_A", "UNSIZED_B", "RETRY"]


@pytest.mark.parametrize("state", sorted(RETRY_AI_STATES))
async def test_a_reading_that_did_not_happen_is_tried_again_the_next_day(test_context, state):
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                _profile("DUE"),
                _profile("TOO_SOON"),
                _profile("USED_UP"),
                _run("DUE", state, MAX_AI_ATTEMPTS - 1, NOW - RETRY_AFTER),
                _run("TOO_SOON", state, 0, NOW - RETRY_AFTER + timedelta(minutes=5)),
                _run("USED_UP", state, MAX_AI_ATTEMPTS, NOW - timedelta(days=30)),
            ]
        )
        await session.flush()

        assert await coins_to_screen(session, now=NOW) == ["DUE"]


@pytest.mark.parametrize("state", NEVER_RETRIED)
async def test_a_coin_with_its_report_is_not_read_again(test_context, state):
    async with test_context["session_factory"]() as session:
        session.add_all(
            [_profile("DONE"), _run("DONE", state, 0, NOW - timedelta(days=30))]
        )
        await session.flush()

        assert await coins_to_screen(session, now=NOW) == []


def test_nothing_read_is_a_state_that_is_tried_again():
    """Ten coins sat on an empty report for ever because this state was not retried."""

    assert "skipped_no_pages" in RETRY_AI_STATES


@pytest.mark.parametrize("state", sorted(set(ALL_AI_STATES) & RETRY_AI_STATES))
def test_every_retried_state_spends_an_attempt(state):
    """A retry that costs nothing is a retry that never ends."""

    assert state in COUNTED_AI_STATES


@pytest.mark.parametrize("state", ALL_AI_STATES)
async def test_the_pipeline_counts_attempts_for_exactly_the_counted_states(
    test_context, state
):
    async with test_context["session_factory"]() as session:
        pipeline = AutomatedScreenPipeline(
            session,
            test_context["settings"],
            coinmarketcap=FakeProvider(),
            crawler=FakeCrawler(),
            ai_reviewer=FakeAI(AIReview(state=state), test_context["settings"]),
        )
        await pipeline.run(["NEWX"])
        run = await session.scalar(select(AutomatedScreenRun))

        assert run.ai_review_attempts == (1 if state in COUNTED_AI_STATES else 0)


# --------------------------------------------------------------------------------
# One sweep after another until nothing is waiting.
# --------------------------------------------------------------------------------


class _Pipeline:
    """Stands in for the real pipeline: 'reads' a fixed number of coins per sweep."""

    finished_per_sweep = 2
    fail = False
    seen: list[list[str]] = []

    def __init__(self, session, settings, **_):
        self.session = session

    async def run(self, symbols, *, limit=None, time_budget_seconds=None):
        wanted = list(symbols)[:limit]
        type(self).seen.append(wanted)
        result = PipelineResult()
        if self.fail:
            result.failed = dict.fromkeys(wanted, "provider_down")
            return result
        for symbol in wanted[: self.finished_per_sweep]:
            self.session.add(_run(symbol, "completed", 1, datetime.now(UTC)))
            result.eligible += 1
        result.deferred = wanted[self.finished_per_sweep :]
        await self.session.flush()
        return result


@pytest.fixture
def sweep(monkeypatch, test_context):
    from ai_market_monitor import worker as worker_module
    from ai_market_monitor.core import database
    from ai_market_monitor.services import automated_screen_pipeline

    queued: list[bool] = []
    _Pipeline.seen = []
    _Pipeline.fail = False
    monkeypatch.setattr(database, "SessionFactory", test_context["session_factory"])
    monkeypatch.setattr(automated_screen_pipeline, "AutomatedScreenPipeline", _Pipeline)
    monkeypatch.setattr(worker_module, "_queue_next_screen_sweep", lambda: queued.append(True))
    monkeypatch.setattr(worker_module.settings, "unscreened_research_enabled", True)
    monkeypatch.setattr(worker_module.settings, "coinmarketcap_enabled", True)
    monkeypatch.setattr(worker_module.settings, "automated_screen_batch_limit", 3)
    return worker_module, queued


async def _waiting(test_context, symbols):
    async with test_context["session_factory"]() as session:
        session.add_all([_profile(symbol) for symbol in symbols])
        await session.commit()


async def test_sweeps_keep_coming_until_every_coin_is_read(test_context, sweep):
    worker_module, queued = sweep
    symbols = [f"C{n:02d}" for n in range(7)]
    await _waiting(test_context, symbols)

    rounds = 0
    while True:
        rounds += 1
        before = len(queued)
        payload = await worker_module._screen_researched_coins()
        if len(queued) == before:
            break
        assert payload["next_sweep"] == "queued"
        assert rounds < 10, "the sweeps never stopped"

    async with test_context["session_factory"]() as session:
        read = set((await session.scalars(select(AutomatedScreenRun.symbol))).all())
    assert read == set(symbols)
    assert payload["still_waiting"] == 0
    # No coin was handed to two sweeps.
    started = [s for batch in _Pipeline.seen for s in batch[: _Pipeline.finished_per_sweep]]
    assert len(started) == len(set(started))


async def test_a_sweep_that_finished_nothing_does_not_queue_another(test_context, sweep):
    """An outage must not spin the worker; the daily beat tries again."""

    worker_module, queued = sweep
    _Pipeline.fail = True
    await _waiting(test_context, ["A", "B", "C", "D"])

    payload = await worker_module._screen_researched_coins()

    assert queued == []
    assert payload["stored"] == 0


async def test_a_second_sweep_while_one_runs_is_refused(test_context, sweep, monkeypatch):
    worker_module, queued = sweep
    await _waiting(test_context, ["A"])

    class _Held:
        async def __aenter__(self):
            return False

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr(worker_module, "_single_run", lambda key, seconds: _Held())

    assert await worker_module._screen_researched_coins() == {"status": "already_running"}
    assert _Pipeline.seen == []


# --------------------------------------------------------------------------------
# What the reviewer sees.
# --------------------------------------------------------------------------------


async def _screened(test_context, review: AIReview, crawler=None):
    async with test_context["session_factory"]() as session:
        pipeline = AutomatedScreenPipeline(
            session,
            test_context["settings"],
            coinmarketcap=FakeProvider(),
            crawler=crawler or FakeCrawler(),
            ai_reviewer=FakeAI(review, test_context["settings"]),
        )
        await pipeline.run(["NEWX"])
        await session.commit()
        return await session.scalar(select(AutomatedScreenRun))


class _NothingReadable(FakeCrawler):
    async def gather(self, symbol, *, website=None, provider_links=None):
        return EvidenceFolder(
            symbol=symbol, failures={"https://newx.example/": "robots_unavailable"}
        )


async def test_the_task_list_shows_the_coin_and_the_pages_read(test_context):
    run = await _screened(test_context, AIReview(state="completed", model="m"))
    async with test_context["session_factory"]() as session:
        rows = await ShariaAdminDashboardService(session).list_cases()
    row = next(item for item in rows if item["id"] == run.review_case_id)

    assert row["symbol"] == "NEWX" and row["asset_name"] == "Coin NEWX"
    assert row["evidence_state"] == "current"
    assert row["evidence_note"] == "2 own pages read, 2 in all"
    # The sentence names the coin, not the task's title.
    assert "Coin NEWX's own pages" in row["why"] or "for Coin NEWX." in row["why"]
    assert "New coin check:" not in row["why"]


@pytest.mark.parametrize(
    ("review", "crawler", "expected"),
    [
        (AIReview(state="failed"), None, "incomplete"),
        (AIReview(state="skipped_no_pages"), _NothingReadable(), "unavailable"),
    ],
)
async def test_the_task_list_says_when_a_reading_is_missing(
    test_context, review, crawler, expected
):
    run = await _screened(test_context, review, crawler)
    async with test_context["session_factory"]() as session:
        rows = await ShariaAdminDashboardService(session).list_cases()
    row = next(item for item in rows if item["id"] == run.review_case_id)

    assert row["evidence_state"] == expected


async def _admin(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="reviewer", role=UserRole.ADMIN)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="reviewer@example.com",
                normalized_identifier="reviewer@example.com",
                display_identifier="reviewer@example.com",
                is_verified=True,
                is_primary=True,
            )
        )
        await session.commit()
        return user


async def test_the_review_page_shows_the_pages_read_and_no_empty_research_file(test_context):
    admin = await _admin(test_context)
    run = await _screened(test_context, AIReview(state="completed", model="m"))

    html = (
        await test_context["client"].get(
            f"/dashboard/system-brain/cases/{run.review_case_id}",
            headers={"X-User-ID": str(admin.id)},
        )
    ).text

    assert 'data-testid="coin-report-pages"' in html
    assert "https://newx.example/about" in html
    assert "NEWX" in html
    assert "New coin check: Coin NEWX (NEWX)" not in html.split("<h3>")[1].split("</h3>")[0]
    # The research-file panels a new coin never has are not drawn as empty boxes.
    assert "No evidence is attached" not in html
    assert "No AI summary yet" not in html
    assert 'data-testid="ai-field-assistance"' not in html


@pytest.mark.parametrize("code", sorted(FETCH_FAILURE_WORDS))
async def test_a_page_that_could_not_be_read_says_why_in_plain_words(test_context, code):
    class _Fails(FakeCrawler):
        async def gather(self, symbol, *, website=None, provider_links=None):
            return EvidenceFolder(symbol=symbol, failures={"https://newx.example/": code})

    admin = await _admin(test_context)
    run = await _screened(test_context, AIReview(state="skipped_no_pages"), _Fails())
    html = (
        await test_context["client"].get(
            f"/dashboard/system-brain/cases/{run.review_case_id}",
            headers={"X-User-ID": str(admin.id)},
        )
    ).text

    assert escape(FETCH_FAILURE_WORDS[code].capitalize()) in html
    assert code.replace("_", " ") not in html


async def test_the_type_filter_offers_every_kind_of_case(test_context):
    from ai_market_monitor.db.models.enums import ReviewCaseType

    admin = await _admin(test_context)
    html = (
        await test_context["client"].get(
            "/dashboard/system-brain/cases", headers={"X-User-ID": str(admin.id)}
        )
    ).text

    for kind in ReviewCaseType:
        assert f'<option value="{kind.value}"' in html, kind
    assert 'value="evidence_refresh"' not in html


async def test_review_case_is_filed_for_every_coin(test_context):
    run = await _screened(test_context, AIReview(state="skipped_no_pages"), _NothingReadable())
    async with test_context["session_factory"]() as session:
        assert await session.get(ReviewCase, run.review_case_id) is not None
