"""No single container may be able to take the whole server down.

On 22 August 2026 the live server died twice within an hour. The kernel log names the
cause exactly:

    Out of memory: Killed process 564376 (celery) anon-rss:1428256kB   15:48:53
    Out of memory: Killed process 597555 (celery) anon-rss:1428884kB   16:50:20
    systemd invoked oom-killer                                        16:50:19
    Total swap = 0kB, 1023866 pages RAM (about 3.9 GB)

A Celery worker child had grown to 1.4 GB. Three things were missing at once, and any one
of them alone would have kept the site up:

1. Celery recycles a worker child that grows, but only when told to. Neither
   `worker_max_memory_per_child` nor `worker_max_tasks_per_child` was set, and neither was
   `worker_concurrency` — so Celery used one child per CPU, two on this server, and each
   was free to grow without limit.
2. No container had a memory limit, so the kernel had to pick a victim across the whole
   machine. It picked systemd, process 1. That is why SSH stopped answering and the
   machine could only be reached through Hetzner's rescue system.
3. The server had no swap, so there was no slack before the kernel had to start killing.
   Swap is a server setting and cannot be asserted from here; it is written down in
   `docs/OPERATIONS.md`.

The rules below cover the two that live in this repository, and they are asserted over
**every** service, not over the one that failed. A worker is not the only container that
can grow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.prod.yml"

#: The Hetzner CX22 this deployment runs on: 2 CPUs, about 3.9 GB of RAM, 40 GB of disk.
#: Read from the kernel's own count on the day it died — `1023866 pages RAM` at 4 KB each.
SERVER_RAM_MB = 3900

#: What must be left for the operating system, the page cache and anything not in a
#: container. Below this the server has no slack, which is the state it died in.
OPERATING_SYSTEM_RESERVE_MB = 400

COMPOSE: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
SERVICES: dict[str, Any] = COMPOSE["services"]
SERVICE_NAMES = sorted(SERVICES)


def memory_limit_mb(service: dict[str, Any]) -> int | None:
    """The service's memory ceiling in megabytes, or ``None`` if it has none.

    Compose accepts the limit in two places — `mem_limit` and
    `deploy.resources.limits.memory` — and both are honoured by `docker compose up`. One
    reader understands both, so a service is never reported as unlimited merely because
    it was written the other way.
    """
    raw = service.get("mem_limit")
    if raw is None:
        raw = (
            service.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("memory")
        )
    if raw is None:
        return None
    text = str(raw).strip().lower()
    for suffix, multiplier in (("gb", 1024), ("g", 1024), ("mb", 1), ("m", 1)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multiplier)
    if text.endswith(("kb", "k")):
        return int(float(text.rstrip("kb")) / 1024)
    return int(int(text) / 1024 / 1024)


@pytest.mark.parametrize("name", SERVICE_NAMES)
def test_every_service_has_a_memory_limit(name: str) -> None:
    """A container with no ceiling can kill PostgreSQL, or systemd, instead of itself."""
    limit = memory_limit_mb(SERVICES[name])
    assert limit is not None, (
        f"service '{name}' in docker-compose.prod.yml has no memory limit. Without one, "
        "the kernel chooses what to kill across the whole machine when this container "
        "grows — on 22 August 2026 it chose systemd and the server stopped answering."
    )
    assert limit > 0, f"service '{name}' has a memory limit of {limit} MB"


def test_the_limits_fit_on_the_server() -> None:
    """Ceilings that add up to more than the machine has are not ceilings."""
    limits = {name: memory_limit_mb(SERVICES[name]) or 0 for name in SERVICE_NAMES}
    total = sum(limits.values())
    budget = SERVER_RAM_MB - OPERATING_SYSTEM_RESERVE_MB
    breakdown = "  ".join(f"{name}={value}m" for name, value in sorted(limits.items()))
    assert total <= budget, (
        f"the memory limits add up to {total} MB but only {budget} MB is available "
        f"({SERVER_RAM_MB} MB of RAM less {OPERATING_SYSTEM_RESERVE_MB} MB for the "
        f"operating system).\n  {breakdown}\n"
        "Either lower a limit or move to a bigger server — and if the server did grow, "
        "SERVER_RAM_MB in this test is the place that records it."
    )


def test_redis_does_not_evict_queued_jobs() -> None:
    """Redis is the Celery broker, so eviction would silently drop background work.

    The safe answer to a full Redis is a bigger limit, never `--maxmemory` with an
    eviction policy: the jobs it drops are alerts a customer is waiting for, and nothing
    reports them as lost.
    """
    command = SERVICES["redis"].get("command", [])
    text = " ".join(command) if isinstance(command, list) else str(command)
    assert "maxmemory" not in text, (
        "redis has a maxmemory setting. It is the Celery broker here — evicting keys "
        "throws away queued background jobs with no error anywhere."
    )


def test_the_celery_worker_recycles_a_child_that_grows() -> None:
    """Celery's own limits must be set, and set in code rather than on a command line.

    On the command line they bind one way of starting a worker. In `app.conf` they bind
    every way: the compose file, a `celery` command typed by hand during an incident, and
    a local run.
    """
    from ai_market_monitor.worker import app

    assert app.conf.worker_max_memory_per_child, (
        "worker_max_memory_per_child is not set, so a worker child that grows is never "
        "replaced. This is what reached 1.4 GB on 22 August 2026."
    )
    assert app.conf.worker_max_tasks_per_child, (
        "worker_max_tasks_per_child is not set, so a slow leak that no single task causes "
        "is never cleared."
    )
    assert app.conf.worker_concurrency, (
        "worker_concurrency is not set, so Celery uses one child per CPU and the peak "
        "memory of the worker container depends on which server it lands on."
    )


def test_the_worker_container_can_hold_its_own_children() -> None:
    """The two limits have to agree, or one of them does nothing.

    Celery replaces a child only *after* the task that grew it has finished. So the
    container has to be able to hold every child at its ceiling at once, plus the parent
    process. If it cannot, Docker kills the container before Celery ever gets to recycle,
    and the setting that was supposed to prevent the outage never runs.
    """
    from ai_market_monitor.worker import app

    children = int(app.conf.worker_concurrency)
    per_child_mb = int(app.conf.worker_max_memory_per_child) / 1024
    container_mb = memory_limit_mb(SERVICES["worker"]) or 0
    needed = children * per_child_mb
    assert needed < container_mb, (
        f"{children} children at {per_child_mb:.0f} MB each need {needed:.0f} MB, but the "
        f"worker container is capped at {container_mb} MB. Docker would kill the "
        "container before Celery could recycle a grown child, so the Celery limit would "
        "never take effect. Lower the concurrency or the per-child limit, or raise the "
        "container limit."
    )
