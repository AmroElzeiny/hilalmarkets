"""Every loop-bound singleton must be released between Celery task loops.

A Celery prefork worker runs each task inside its own ``asyncio.run``. Anything that
binds to the loop that created it — an ``httpx.AsyncClient`` and the sockets under it, an
``asyncio.Lock``, a Redis client — is unusable in the next task's loop. In production this
appeared as ``RuntimeError: Event loop is closed`` on every Telegram poll after a worker
process's first one, and it would have taken the scheduled scanner down the same way,
because market data and OpenAI reach the network through the same pool.

The crash itself needs a real socket, so these tests do not reproduce it. They assert the
rule that prevents it: after a task, the runtime holds none of the objects it built, and
the worker's own cleanup path is what makes that happen. A fix that released the HTTP
client but left the breaker or the module lock behind fails here, and so does one that
repairs the runtime while nobody calls it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services import provider_runtime

#: Every object the runtime caches between calls. Adding a new cached singleton without a
#: release path fails the sweep below rather than waiting to be found in production.
CACHED_SINGLETONS = ("_pool", "_breaker")


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test")


@pytest.fixture(autouse=True)
def _clean_runtime() -> Any:
    """No test may inherit another's cached objects, in either direction."""

    def reset() -> None:
        provider_runtime._pool = None
        provider_runtime._breaker = None
        provider_runtime._lock = asyncio.Lock()

    reset()
    yield
    reset()


async def _acquire_everything(settings: Settings) -> None:
    await provider_runtime.provider_pool(settings)
    await provider_runtime.provider_breaker(settings)


@pytest.mark.parametrize("name", CACHED_SINGLETONS)
def test_release_drops_every_cached_singleton(name: str, settings: Settings) -> None:
    async def one_task() -> None:
        await _acquire_everything(settings)
        assert getattr(provider_runtime, name) is not None
        await provider_runtime.release_provider_runtime_for_loop()

    asyncio.run(one_task())

    assert getattr(provider_runtime, name) is None, f"{name} survived the release"


def test_release_replaces_the_module_lock(settings: Settings) -> None:
    """The lock guarding the singletons is itself loop-bound, so it must be replaced.

    Keeping it would leave the next loop reaching for an object owned by a dead one — the
    same defect, moved one level down into the thing that was supposed to protect it.
    """

    async def one_task() -> int:
        await _acquire_everything(settings)
        before = id(provider_runtime._lock)
        await provider_runtime.release_provider_runtime_for_loop()
        assert id(provider_runtime._lock) != before
        return id(provider_runtime._lock)

    first = asyncio.run(one_task())
    second = asyncio.run(one_task())
    assert first != second


def test_release_closes_the_pool_it_drops(settings: Settings) -> None:
    """Dropping the reference is not enough; the sockets have to be closed too."""

    captured: list[Any] = []

    async def one_task() -> None:
        captured.append(await provider_runtime.provider_pool(settings))
        await provider_runtime.release_provider_runtime_for_loop()

    asyncio.run(one_task())

    assert captured[0]._closed is True


@pytest.mark.parametrize("name", CACHED_SINGLETONS)
def test_a_second_task_loop_gets_a_fresh_object(name: str, settings: Settings) -> None:
    """Two successive ``asyncio.run`` calls, exactly as the worker makes them."""

    async def one_task() -> int:
        await _acquire_everything(settings)
        obtained = id(getattr(provider_runtime, name))
        await provider_runtime.release_provider_runtime_for_loop()
        return obtained

    first = asyncio.run(one_task())
    second = asyncio.run(one_task())

    assert first != second, f"{name} was reused across two event loops"


def test_the_worker_cleanup_path_releases_the_runtime(settings: Settings) -> None:
    """The integration point, and the line the defect was actually missing.

    ``_run_with_worker_cleanup`` wraps every Celery task. It released the database engine
    and nothing else. Asserting only on ``release_provider_runtime_for_loop`` would leave
    that omission invisible, because the function existed and worked — nobody called it.
    """

    from ai_market_monitor.worker import _run_with_worker_cleanup

    async def a_task_that_calls_a_provider() -> dict:
        await _acquire_everything(settings)
        return {"ok": True}

    result = asyncio.run(_run_with_worker_cleanup(a_task_that_calls_a_provider()))

    assert result == {"ok": True}
    for name in CACHED_SINGLETONS:
        assert getattr(provider_runtime, name) is None, f"worker cleanup left {name} behind"


def test_worker_cleanup_still_releases_when_the_task_fails() -> None:
    """A failing task must not leak a loop-bound pool into the next one.

    The cleanup lives in a ``finally`` for this reason: the tasks most likely to have
    opened a connection are the ones that then failed using it.
    """

    from ai_market_monitor.worker import _run_with_worker_cleanup

    settings = Settings(app_env="test")

    async def a_task_that_fails() -> dict:
        await _acquire_everything(settings)
        raise ValueError("provider blew up")

    with pytest.raises(ValueError, match="provider blew up"):
        asyncio.run(_run_with_worker_cleanup(a_task_that_fails()))

    for name in CACHED_SINGLETONS:
        assert getattr(provider_runtime, name) is None, f"a failed task left {name} behind"


def test_release_is_safe_when_nothing_was_ever_acquired() -> None:
    """The worker runs this after *every* task, including those that never used a provider.

    A cleanup path that raised would turn a healthy task into a failed one.
    """

    asyncio.run(provider_runtime.release_provider_runtime_for_loop())


def test_release_is_safe_twice_in_one_loop() -> None:
    """Cleanup runs in a ``finally``; a repeated call must stay harmless."""

    async def twice() -> None:
        await provider_runtime.release_provider_runtime_for_loop()
        await provider_runtime.release_provider_runtime_for_loop()

    asyncio.run(twice())


def test_the_alert_cooldown_is_not_cleared_by_a_release(settings: Settings) -> None:
    """Per-task cleanup must not reset the operator-alert cooldown.

    ``shutdown_provider_runtime`` clears it because the process is ending. Doing the same
    once per task would turn "one message per provider per fifteen minutes" into one per
    call, which is the noise that cooldown exists to prevent.
    """

    async def one_task() -> None:
        await provider_runtime.provider_pool(settings)
        provider_runtime._auth_alerted_at["telegram"] = 123.0
        await provider_runtime.release_provider_runtime_for_loop()

    try:
        asyncio.run(one_task())
        assert provider_runtime._auth_alerted_at.get("telegram") == 123.0
    finally:
        provider_runtime._auth_alerted_at.clear()
