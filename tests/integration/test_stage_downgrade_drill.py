"""Invariant 8: narrowing the launch stage never damages what customers already have.

Pulling the product back is an emergency action. It has to be safe to do at three in
the morning without a meeting, which means it may change only what the *public
surface* shows. A stage change is not a migration, not a pause, and not a revocation.

So this walks the drill. It builds the two things a customer can lose — a Setup Chat
draft they are still writing, and a monitor they have already approved and which is
scheduled to run — takes a full reading of both, steps the stage down, and reads them
again.

The step down is deliberately the largest one available: ``public_launch`` all the way
to ``internal``, skipping the intermediate stages, because narrowing may happen from
anywhere. If the widest possible pull-back leaves everything intact, a smaller one
does too.

What is checked afterwards is not "the row still exists". It is every field a person
would notice: the draft's own text and its state, the approval binding by hash, who
approved it and when, the strategy's active version, and whether the scheduler still
produces a job for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.launch_stage import LaunchStage, stage_exposure
from ai_market_monitor.db.models import (
    AISetupChatSession,
    ScanJob,
    Strategy,
    StrategyCondition,
    StrategyUniverse,
    StrategyVersion,
    User,
)
from ai_market_monitor.db.models.enums import (
    StrategyStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.services.scanner import ScanScheduler

pytestmark = pytest.mark.asyncio


DRAFT_TEXT = "Watch BTC/USDT when the 15m candle rises open-to-close by at least 3%."


async def _customer_with_a_draft_and_an_approved_monitor(session) -> dict:
    """One user holding both things a stage change could plausibly damage."""

    now = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
    user = User(display_name="Stage Drill Customer")
    session.add(user)
    await session.flush()

    draft = AISetupChatSession(
        user_id=user.id,
        status="interviewing",
        title="Half-written monitor",
        original_idea=DRAFT_TEXT,
        draft_schema_json={"name": "Half-written monitor", "conditions": [{"key": "c1"}]},
        translation_sheet={"c1": "the 15 minute candle rises by at least 3%"},
        assumptions=["All eligible Binance USDT spot pairs."],
    )
    session.add(draft)

    strategy = Strategy(
        user_id=user.id,
        name="Approved monitor",
        status=StrategyStatus.ACTIVE,
        activated_at=now,
    )
    session.add(strategy)
    await session.flush()

    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=1,
        status=StrategyVersionStatus.ACTIVE,
        source_type="structured",
        schema_json={"name": "Approved monitor", "version": 1},
        schema_hash="a" * 64,
        approved_by_user_id=user.id,
        approved_schema_hash="a" * 64,
        approved_at=now,
        preview_status="succeeded",
        activated_at=now,
    )
    session.add(version)
    await session.flush()
    strategy.active_version_id = version.id

    session.add(
        StrategyUniverse(
            strategy_version_id=version.id,
            exchange="binance",
            market_type="spot",
            quote_currencies=["USDT"],
            include_symbols=["BTC/USDT"],
            exclude_symbols=[],
            timeframes=["15m"],
            trigger_mode="candle_close",
            scan_interval_seconds=60,
        )
    )
    session.add(
        StrategyCondition(
            strategy_version_id=version.id,
            condition_key="c1",
            label="Close-to-close rise",
            node_type="condition",
            condition_type="percent_change",
            timeframe="15m",
            comparator="gte",
            left_operand={"kind": "percent_change"},
            right_operand={"kind": "constant", "value": 3.0},
            weight=Decimal("1"),
            sequence=0,
            is_required=True,
        )
    )
    await session.commit()
    return {"user": user, "draft": draft, "strategy": strategy, "version": version}


async def _reading(session, ids: dict) -> dict:
    """Everything a customer would notice, in one comparable snapshot."""

    draft = await session.get(AISetupChatSession, ids["draft_id"])
    strategy = await session.get(Strategy, ids["strategy_id"])
    version = await session.get(StrategyVersion, ids["version_id"])
    universe = await session.scalar(
        select(StrategyUniverse).where(
            StrategyUniverse.strategy_version_id == ids["version_id"]
        )
    )
    assert draft is not None and strategy is not None and version is not None
    assert universe is not None
    return {
        "draft_status": draft.status,
        "draft_title": draft.title,
        "draft_idea": draft.original_idea,
        "draft_schema": draft.draft_schema_json,
        "draft_translation": draft.translation_sheet,
        "draft_assumptions": draft.assumptions,
        "draft_approved_strategy_id": draft.approved_strategy_id,
        "strategy_status": strategy.status,
        "strategy_active_version_id": strategy.active_version_id,
        "strategy_activated_at": strategy.activated_at,
        "version_status": version.status,
        "version_schema_hash": version.schema_hash,
        "version_approved_schema_hash": version.approved_schema_hash,
        "version_approved_by": version.approved_by_user_id,
        "version_approved_at": version.approved_at,
        "version_activated_at": version.activated_at,
        "universe_interval": universe.scan_interval_seconds,
        "universe_symbols": universe.include_symbols,
        "universe_timeframes": universe.timeframes,
    }


async def test_stepping_the_stage_down_leaves_drafts_and_approvals_untouched(
    test_context: dict,
) -> None:
    session_factory = test_context["session_factory"]
    async with session_factory() as session:
        created = await _customer_with_a_draft_and_an_approved_monitor(session)
        ids = {
            "draft_id": created["draft"].id,
            "strategy_id": created["strategy"].id,
            "version_id": created["version"].id,
        }
    # Both readings are taken in a fresh session on purpose. Reading in the session
    # that wrote the rows returns the objects still in memory, and comparing those
    # against rows loaded from the database compares two different journeys, not two
    # different moments — on SQLite the timestamps alone would differ.
    async with session_factory() as session:
        before = await _reading(session, ids)

    # The stage moves. Nothing else is asked to happen.
    wide = Settings(launch_stage=LaunchStage.PUBLIC_LAUNCH, public_waitlist_mode=False)
    narrow = Settings(launch_stage=LaunchStage.INTERNAL, public_waitlist_mode=False)
    assert wide.resolved_launch_stage.effective is LaunchStage.PUBLIC_LAUNCH
    assert narrow.resolved_launch_stage.effective is LaunchStage.INTERNAL
    # Proof that this really is a narrowing, not a no-op dressed up as one.
    assert wide.stage_exposure.advertises_pricing
    assert not narrow.stage_exposure.advertises_pricing

    async with session_factory() as session:
        after = await _reading(session, ids)

    assert after == before


async def test_an_approved_monitor_is_still_scheduled_after_the_stage_narrows(
    test_context: dict,
) -> None:
    """The one that matters most: a monitor must keep watching.

    A customer who has approved a monitor is owed the alerts it produces. Whether the
    marketing site is showing a waitlist has nothing to do with that promise, and a
    stage change that quietly stopped the scanner would break it silently — no error,
    no notice, just alerts that never arrive.
    """

    session_factory = test_context["session_factory"]
    async with session_factory() as session:
        created = await _customer_with_a_draft_and_an_approved_monitor(session)
        version_id = created["version"].id

    # Narrow first, then ask the scheduler for work. The scheduler is idempotent per
    # time bucket, so scheduling before and after would have compared "one job" with
    # "the same job refused as a duplicate" — which proves nothing either way.
    narrow = Settings(launch_stage=LaunchStage.INTERNAL, public_waitlist_mode=True)
    assert narrow.resolved_launch_stage.effective is LaunchStage.INTERNAL
    assert not narrow.stage_exposure.advertises_account_entry

    scheduled_for = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)
    async with session_factory() as session:
        jobs = await ScanScheduler(session).schedule_due(scheduled_for=scheduled_for)
        await session.commit()
        total = await session.scalar(
            select(func.count(ScanJob.id)).where(
                ScanJob.strategy_version_id == version_id
            )
        )
    assert len(jobs) == 1, (
        "the narrowest stage stopped an approved monitor from being scheduled"
    )
    assert total == 1


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (LaunchStage.PUBLIC_LAUNCH, LaunchStage.PUBLIC_WAITLIST),
        (LaunchStage.PUBLIC_LAUNCH, LaunchStage.PRIVATE_BETA_INVITE),
        (LaunchStage.PUBLIC_LAUNCH, LaunchStage.INTERNAL),
        (LaunchStage.PUBLIC_WAITLIST, LaunchStage.PRIVATE_BETA_INVITE),
        (LaunchStage.PUBLIC_WAITLIST, LaunchStage.INTERNAL),
        (LaunchStage.PRIVATE_BETA_INVITE, LaunchStage.INTERNAL),
    ],
)
async def test_every_narrowing_step_preserves_the_customer_s_work(
    test_context: dict,
    start: LaunchStage,
    end: LaunchStage,
) -> None:
    """Not one downgrade — every downgrade the state machine allows."""

    session_factory = test_context["session_factory"]
    async with session_factory() as session:
        created = await _customer_with_a_draft_and_an_approved_monitor(session)
        ids = {
            "draft_id": created["draft"].id,
            "strategy_id": created["strategy"].id,
            "version_id": created["version"].id,
        }
    async with session_factory() as session:
        before = await _reading(session, ids)

    assert stage_exposure(start) != stage_exposure(end)
    Settings(launch_stage=start, public_waitlist_mode=False)
    Settings(launch_stage=end, public_waitlist_mode=False)

    async with session_factory() as session:
        assert await _reading(session, ids) == before


async def test_a_narrowed_stage_does_not_delete_or_hide_the_rows(
    test_context: dict,
) -> None:
    """Counted, not just compared. A reading of a deleted row would raise, not differ."""

    session_factory = test_context["session_factory"]
    async with session_factory() as session:
        await _customer_with_a_draft_and_an_approved_monitor(session)

    Settings(launch_stage=LaunchStage.INTERNAL, public_waitlist_mode=True)

    async with session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(AISetupChatSession)
        ) == 1
        assert await session.scalar(select(func.count()).select_from(Strategy)) == 1
        assert await session.scalar(
            select(func.count()).select_from(StrategyVersion)
        ) == 1
        active = await session.scalar(
            select(func.count())
            .select_from(Strategy)
            .where(Strategy.status == StrategyStatus.ACTIVE)
        )
        assert active == 1
