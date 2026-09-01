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

#: One uvicorn worker with the application loaded, measured on the live server at 273 MB.
#: Rounded up, because a ceiling sized to the exact measurement has no room for a normal
#: busy minute.
API_WORKER_BASELINE_MB = 300

#: The uvicorn parent. It supervises the workers and does not serve requests, so it never
#: loads the heavy parts. Generous on purpose.
API_PARENT_PROCESS_MB = 80

#: The Celery parent process, which holds the whole application without running tasks.
#: Measured on the live server: the idle worker container was 670 MB for a parent plus two
#: children, and the scheduler — a bare beat process with the same application loaded —
#: was 67 MB. 200 MB is the deliberately generous figure between the two.
CELERY_PARENT_PROCESS_MB = 200

#: The highest the worker container has actually been measured using, in megabytes. It was
#: killed at about this figure on 22 August 2026, part way through a scan. This is a
#: *measurement*, not an estimate: a scan holds the exchange's whole market list — measured
#: separately at 139 MB — for the length of the run, on top of the recycle threshold and
#: the parent process.
WORKER_MEASURED_PEAK_MB = 700

#: A headless Chromium reading one page at a time, with images, GPU and extensions
#: switched off. The Shariah source resolver starts one per sweep to read project blogs
#: that only exist after their JavaScript has run, and it runs **in the worker
#: container** — so it is the worker's ceiling that has to hold it.
#:
#: Deliberately generous, and deliberately not the same event as the scan peak above: a
#: scan and a source sweep are two different tasks, and `celery_worker_concurrency` is 1,
#: so the worker never runs both at once. What must fit at the same time is the Celery
#: parent, the one child running the sweep, and the browser it started.
BROWSER_PROCESS_MB = 300

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


def test_the_worker_container_holds_what_one_task_has_been_seen_to_use() -> None:
    """Celery's per-child limit cannot save a single task, so the ceiling must.

    `worker_max_memory_per_child` is checked **between** tasks. One scan that allocates a
    lot in a single run walks straight past it and is killed by Docker instead — which is
    what happened on 22 August 2026, at about 700 MB against a 768 MB ceiling. The killed
    scan is then retried, grows the same way, and is killed again.

    So the ceiling is not sized from the recycle threshold, which is a between-tasks
    number. It is sized from the largest amount the worker has actually been measured
    using, with room left over: a ceiling reached by a normal scan is not a safety net,
    it is a scheduled outage.
    """
    ceiling = memory_limit_mb(SERVICES["worker"]) or 0
    assert ceiling * 0.75 >= WORKER_MEASURED_PEAK_MB, (
        f"the worker container is capped at {ceiling} MB, but one scan has been measured "
        f"at {WORKER_MEASURED_PEAK_MB} MB. Celery cannot recycle a child in the middle of "
        "the task that is growing it, so Docker kills the container first and the scan is "
        "retried into the same wall. Raise the ceiling or lower what one scan holds."
    )


def test_the_worker_container_holds_a_browser_as_well_as_its_children() -> None:
    """Turning page rendering on puts a whole browser inside the worker's ceiling.

    `sharia_source_browser_render_enabled` was switched on on 1 September 2026 so the
    resolver can read a project's blog when the page only exists after its JavaScript has
    run. That browser is a separate process tree, it lives in the worker container, and
    the container's limit is what stops it taking the machine down — the same rule as
    every other service here.

    So the sum that has to fit is the Celery parent, one child running the sweep, and one
    Chromium. If it does not, the honest answers are a smaller page budget, a lower
    per-child limit, or a bigger server — never leaving the browser unbounded and hoping.
    """

    from ai_market_monitor.core.config import get_settings
    from ai_market_monitor.worker import app

    if not get_settings().sharia_source_browser_render_enabled:
        pytest.skip("page rendering is switched off, so no browser ever starts")

    children = int(app.conf.worker_concurrency)
    per_child_mb = int(app.conf.worker_max_memory_per_child) / 1024
    needed = CELERY_PARENT_PROCESS_MB + children * per_child_mb + BROWSER_PROCESS_MB
    ceiling = memory_limit_mb(SERVICES["worker"]) or 0
    assert needed <= ceiling, (
        f"the worker needs {needed:.0f} MB — {CELERY_PARENT_PROCESS_MB} MB parent, "
        f"{children} child(ren) at {per_child_mb:.0f} MB, and a {BROWSER_PROCESS_MB} MB "
        f"browser — but the container is capped at {ceiling} MB. Docker would kill the "
        "container part way through a source sweep, and the sweep would be retried into "
        "the same wall."
    )


def test_the_browser_page_budget_is_bounded() -> None:
    """A browser with no page limit is an unbounded amount of somebody else's HTML.

    One browser per sweep is only safe while the number of pages it draws is finite. The
    limit is what turns "a batch of unreadable addresses" into a run that stops and says
    so, instead of one that keeps starting renderers on a machine with no swap.
    """

    from ai_market_monitor.core.config import get_settings

    budget = get_settings().sharia_source_browser_render_max_pages
    assert budget > 0, "SHARIA_SOURCE_BROWSER_RENDER_MAX_PAGES must bound the run"


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


def test_the_api_runs_more_than_one_worker() -> None:
    """One process means one kill takes the whole website down.

    That is what happened on 22 August 2026: the API ran as a single `uvicorn` process,
    the kernel killed it at 694 MB, and the site was down until Docker restarted the
    container. With a second worker, the other one keeps serving.
    """
    from ai_market_monitor.core.config import get_settings

    assert get_settings().api_worker_processes >= 2, (
        "the API is configured to run a single worker. One process is one point of "
        "failure: when it is killed or restarted, the website is down for as long as it "
        "takes to come back."
    )


def test_the_api_workers_retire_before_they_can_grow_into_the_ceiling() -> None:
    """Recycling is the cure for a leak nobody has found yet.

    A worker that retires on a request count never lives long enough to reach the
    container's memory ceiling, whatever is leaking. This is deliberately a defence that
    does not depend on knowing the cause — as of writing, the cause is not known.
    """
    from ai_market_monitor.core.config import get_settings

    settings = get_settings()
    assert settings.api_worker_max_requests > 0, (
        "API workers are never retired, so any leak eventually reaches the ceiling."
    )


def test_the_api_workers_do_not_all_retire_at_the_same_moment() -> None:
    """Jitter is the whole difference between a rolling restart and an outage.

    Workers start together and serve roughly equal shares of traffic, so without jitter
    they reach the retirement count at nearly the same moment and go together — which is
    the outage this was meant to prevent, arriving on a schedule instead of by surprise.
    """
    from ai_market_monitor.core.config import get_settings

    settings = get_settings()
    if settings.api_worker_processes < 2:
        pytest.skip("only one worker, so there is nothing to stagger")
    assert settings.api_worker_max_requests_jitter > 0, (
        "API_WORKER_MAX_REQUESTS_JITTER is zero, so every worker retires at the same "
        "moment and the site goes down each time it happens."
    )


def test_the_api_container_can_hold_the_workers_it_is_told_to_run() -> None:
    """The worker count and the container ceiling have to agree.

    Same trap as the Celery one: two limits that each look sensible alone, and a container
    that dies before the mechanism inside it can work.
    """
    from ai_market_monitor.core.config import get_settings

    workers = get_settings().api_worker_processes
    needed = API_PARENT_PROCESS_MB + workers * API_WORKER_BASELINE_MB
    ceiling = memory_limit_mb(SERVICES["api"]) or 0
    assert needed <= ceiling, (
        f"{workers} API workers at {API_WORKER_BASELINE_MB} MB each plus a "
        f"{API_PARENT_PROCESS_MB} MB parent need {needed} MB, but the api container is "
        f"capped at {ceiling} MB. It would be killed while merely idling."
    )
    # And there must be real room left over, or the ceiling is reached by normal traffic
    # rather than by something going wrong.
    assert needed <= ceiling * 0.75, (
        f"{workers} idle API workers already use {needed} MB of the {ceiling} MB ceiling. "
        "A ceiling should be reached by a fault, not by a busy afternoon."
    )


def test_the_docker_command_does_not_repeat_the_worker_numbers() -> None:
    """One owner for the numbers: `serve.py` reads Settings, Compose names nothing.

    Writing `--workers 2` into the Compose command as well would create a second copy that
    silently wins over the setting, and the setting would then be a knob connected to
    nothing.
    """
    command = SERVICES["api"].get("command", "")
    text = " ".join(command) if isinstance(command, list) else str(command)
    assert "ai_market_monitor.serve" in text, (
        "the api command no longer starts through serve.py, which is the only reader of "
        "the worker settings."
    )
    for flag in ("--workers", "--limit-max-requests"):
        assert flag not in text, (
            f"the api command spells out {flag}. That is a second copy of a number that "
            "already lives in core/config.py, and the copy in the command wins."
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
    container has to hold every child at its ceiling **at the same time**, and the parent
    process as well — Celery's prefork pool is one parent plus `worker_concurrency`
    children, and the parent has the whole application loaded too.

    If the total can exceed the container, Docker kills the container first and Celery
    never reaches the point where it would have recycled the grown child. The setting
    reads as present and does nothing, which is worse than not having it: it looks like
    the outage is prevented.

    The first version of this test left the parent out of the sum. Measurements from the
    live server on 22 August 2026 — the worker container at 670 MB while idle — showed
    that the parent is far too big to ignore.
    """
    from ai_market_monitor.worker import app

    children = int(app.conf.worker_concurrency)
    per_child_mb = int(app.conf.worker_max_memory_per_child) / 1024
    container_mb = memory_limit_mb(SERVICES["worker"]) or 0
    needed = CELERY_PARENT_PROCESS_MB + children * per_child_mb
    assert needed <= container_mb, (
        f"the worker needs {needed:.0f} MB — {CELERY_PARENT_PROCESS_MB} MB for the parent "
        f"process plus {children} children at {per_child_mb:.0f} MB each — but the "
        f"container is capped at {container_mb} MB. Docker would kill the container "
        "before Celery could recycle a grown child, so the Celery limit would never take "
        "effect. Lower the concurrency or the per-child limit, or raise the container "
        "limit."
    )
