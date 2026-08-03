from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AISetupChatSession
from ai_market_monitor.services.setup_chat_launch import SetupChatLaunchService, SetupLaunchError


class _CostSession:
    def __init__(self, recorded_spend: float) -> None:
        self.recorded_spend = recorded_spend

    async def scalar(self, _statement):
        return self.recorded_spend


class _RefusingRedis:
    async def eval(self, *_args):
        return "-1"


class _UnavailableRedis:
    async def eval(self, *_args):
        raise RedisError("redis unavailable")


class _AtomicReservationRedis:
    def __init__(self) -> None:
        self.outstanding = 0.0

    async def eval(self, _script, _key_count, _key, *arguments):
        if len(arguments) == 4:
            recorded, reserve, limit, _ttl = map(float, arguments)
            if recorded + self.outstanding + reserve > limit:
                return "-1"
            self.outstanding += reserve
            return str(self.outstanding)
        reserve, _ttl = map(float, arguments)
        self.outstanding = max(0.0, self.outstanding - reserve)
        return str(self.outstanding)


class _MessageOwner:
    async def _append_message(self, *_args, **_kwargs):
        return SimpleNamespace(id=uuid4())


def test_deployed_environment_rejects_legacy_writable_setup_path():
    settings = Settings()
    payload = settings.model_dump(mode="python")
    payload.update(
        {
            "app_env": "staging",
            "setup_chat_launch_v2_enabled": True,
            "setup_chat_legacy_test_compat_enabled": True,
        }
    )

    with pytest.raises(ValidationError, match="forbidden outside local tests"):
        Settings.model_validate(payload)


def test_launch_v2_cannot_be_disabled():
    settings = Settings()
    payload = settings.model_dump(mode="python")
    payload["setup_chat_launch_v2_enabled"] = False

    with pytest.raises(ValidationError, match="must remain true"):
        Settings.model_validate(payload)


def test_private_beta_allowlist_rejects_invalid_user_ids():
    settings = Settings()
    payload = settings.model_dump(mode="python")
    payload["setup_chat_private_beta_user_ids"] = ["not-a-user-id"]

    with pytest.raises(ValidationError, match="contains an invalid UUID"):
        Settings.model_validate(payload)


@pytest.mark.asyncio
async def test_emergency_switch_blocks_without_creating_a_fallback_turn():
    settings = Settings(setup_chat_emergency_disabled=True)
    service = SetupChatLaunchService(settings, owner=None)
    chat = AISetupChatSession(user_id=uuid4())

    with pytest.raises(SetupLaunchError, match="temporarily paused") as captured:
        await service.handle(
            None,  # type: ignore[arg-type]
            chat,
            message="Build a monitor",
            option_key=None,
            option_value=None,
            option_label=None,
            client_message_id=None,
        )
    assert captured.value.code == "SETUP_CHAT_EMERGENCY_DISABLED"


@pytest.mark.asyncio
async def test_private_beta_allowlist_is_per_user_and_fail_closed():
    allowed = uuid4()
    settings = Settings(setup_chat_private_beta_user_ids=[str(allowed)])
    service = SetupChatLaunchService(settings, owner=None)
    chat = AISetupChatSession(user_id=uuid4())

    with pytest.raises(SetupLaunchError) as captured:
        await service.handle(
            None,  # type: ignore[arg-type]
            chat,
            message="Build a monitor",
            option_key=None,
            option_value=None,
            option_label=None,
            client_message_id=None,
        )
    assert captured.value.code == "SETUP_CHAT_PRIVATE_BETA_NOT_ENABLED"


@pytest.mark.asyncio
async def test_daily_cost_budget_blocks_before_a_provider_can_be_called():
    settings = Settings(
        setup_agent_max_estimated_cost_usd_per_turn=0.10,
        setup_agent_max_estimated_cost_usd_per_user_day=0.10,
    )
    service = SetupChatLaunchService(settings, owner=None)

    with pytest.raises(SetupLaunchError) as captured:
        await service._reserve_user_cost_budget(  # noqa: SLF001 - boundary invariant
            _CostSession(0.01),  # type: ignore[arg-type]
            uuid4(),
        )

    assert captured.value.code == "SETUP_CHAT_DAILY_COST_BUDGET_REACHED"


@pytest.mark.asyncio
async def test_atomic_cost_reservation_refusal_fails_closed():
    settings = Settings(
        setup_agent_max_estimated_cost_usd_per_turn=0.10,
        setup_agent_max_estimated_cost_usd_per_user_day=1.00,
    )
    service = SetupChatLaunchService(settings, owner=None)
    service._preflight_redis = _RefusingRedis()  # type: ignore[assignment]

    with pytest.raises(SetupLaunchError) as captured:
        await service._reserve_user_cost_budget(  # noqa: SLF001 - boundary invariant
            _CostSession(0),  # type: ignore[arg-type]
            uuid4(),
        )

    assert captured.value.code == "SETUP_CHAT_DAILY_COST_BUDGET_REACHED"


@pytest.mark.asyncio
async def test_redis_outage_does_not_become_a_setup_chat_semantic_failure():
    settings = Settings(
        setup_agent_max_estimated_cost_usd_per_turn=0.10,
        setup_agent_max_estimated_cost_usd_per_user_day=1.00,
    )
    service = SetupChatLaunchService(settings, owner=None)
    service._preflight_redis = _UnavailableRedis()  # type: ignore[assignment]

    reservation = await service._reserve_user_cost_budget(  # noqa: SLF001
        _CostSession(0),  # type: ignore[arg-type]
        uuid4(),
    )
    assert reservation is None


@pytest.mark.asyncio
async def test_only_concurrent_turns_consume_the_atomic_reservation() -> None:
    settings = Settings(
        setup_agent_max_estimated_cost_usd_per_turn=0.10,
        setup_agent_max_estimated_cost_usd_per_user_day=0.15,
    )
    service = SetupChatLaunchService(settings, owner=None)
    redis = _AtomicReservationRedis()
    service._preflight_redis = redis  # type: ignore[assignment]
    session = _CostSession(0)
    user_id = uuid4()

    first = await service._reserve_user_cost_budget(  # noqa: SLF001
        session,  # type: ignore[arg-type]
        user_id,
    )
    assert first is not None
    assert redis.outstanding == pytest.approx(0.10)
    with pytest.raises(SetupLaunchError):
        await service._reserve_user_cost_budget(  # noqa: SLF001
            session,  # type: ignore[arg-type]
            user_id,
        )

    await service._release_user_cost_reservation(first)  # noqa: SLF001
    assert redis.outstanding == 0


@pytest.mark.asyncio
async def test_settled_turns_do_not_accumulate_false_daily_cost() -> None:
    """Regression for evaluator run 20260803T115953Z stopping after eight cases."""

    settings = Settings(
        setup_agent_max_estimated_cost_usd_per_turn=0.10,
        setup_agent_max_estimated_cost_usd_per_user_day=2.00,
    )
    service = SetupChatLaunchService(settings, owner=None)
    redis = _AtomicReservationRedis()
    service._preflight_redis = redis  # type: ignore[assignment]
    session = _CostSession(0)
    user_id = uuid4()

    # The old implementation retained every $0.10 reservation until midnight and
    # falsely blocked turn 21. Settled reservations must remain reusable.
    for _ in range(30):
        reservation = await service._reserve_user_cost_budget(  # noqa: SLF001
            session,  # type: ignore[arg-type]
            user_id,
        )
        assert reservation is not None
        assert ":v2:" in reservation.redis_key
        await service._release_user_cost_reservation(reservation)  # noqa: SLF001

    assert redis.outstanding == 0
    after_settlement = await service._reserve_user_cost_budget(  # noqa: SLF001
        session,  # type: ignore[arg-type]
        user_id,
    )
    assert after_settlement is not None
    await service._release_user_cost_reservation(after_settlement)  # noqa: SLF001
    assert redis.outstanding == 0


@pytest.mark.asyncio
async def test_handle_releases_reservation_when_the_turn_raises(monkeypatch) -> None:
    service = SetupChatLaunchService(Settings(), owner=_MessageOwner())
    chat = AISetupChatSession(user_id=uuid4())
    sentinel = object()
    released: list[object] = []

    async def reserve(*_args):
        return sentinel

    async def release(value):
        released.append(value)

    async def fail_turn(*_args, **_kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(service, "_reserve_user_cost_budget", reserve)
    monkeypatch.setattr(service, "_release_user_cost_reservation", release)
    monkeypatch.setattr(service, "_run_agent_turn", fail_turn)

    with pytest.raises(RuntimeError, match="provider failed"):
        await service.handle(
            SimpleNamespace(),  # type: ignore[arg-type]
            chat,
            message="Build a monitor",
            option_key=None,
            option_value=None,
            option_label=None,
            client_message_id=None,
        )

    assert released == [sentinel]
