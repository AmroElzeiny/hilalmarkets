"""Start the API the way production runs it.

    python -m ai_market_monitor.serve

Several worker processes, each retired after a bounded number of requests, with a parent
that starts a replacement whenever one goes away. This is what keeps the website up while
a process is being replaced — and what stops a leak ever reaching the container's memory
ceiling, whatever the leak turns out to be.

**Why this file exists rather than a longer command in docker-compose.prod.yml.** The
numbers would otherwise be written twice: once as Compose defaults and once in
`core/config.py`. Two copies of one number is how they drift, and the copy that loses is
always the one nobody reads. Here the Docker command names no numbers at all — it calls
this, and this reads `Settings`.

Everything is bounded by settings so an operator can change it without a code change:

===============================  =========================================================
`API_WORKER_PROCESSES`           how many workers serve at once
`API_WORKER_MAX_REQUESTS`        requests a worker serves before it retires
`API_WORKER_MAX_REQUESTS_JITTER` random extra requests, so they never retire together
===============================  =========================================================

The history is in `core/config.py` beside those fields: on 22 August 2026 the API ran as
one process, the kernel killed it at 694 MB, and the website went down with it.
"""

from __future__ import annotations

import uvicorn

from ai_market_monitor.core.config import Settings, get_settings

#: The address Caddy proxies to and the health check probes. Changing it means changing
#: `docker-compose.prod.yml` — the `expose`, and the healthcheck URL — at the same time.
HOST = "0.0.0.0"  # noqa: S104 - inside a container, published only through Caddy
PORT = 8000


def uvicorn_options(settings: Settings) -> dict[str, object]:
    """Exactly what is handed to uvicorn, as data so a test can read it.

    Keeping this separate from :func:`main` means the wiring can be checked without
    starting a server, which is the part that actually goes wrong — a setting that is
    read, formatted into a command, and then silently not passed on.
    """
    return {
        "host": HOST,
        "port": PORT,
        # Caddy terminates TLS and is the only thing that reaches this port, so the
        # forwarded headers it sets are the real client's.
        "proxy_headers": True,
        "forwarded_allow_ips": "*",
        "workers": settings.api_worker_processes,
        "limit_max_requests": settings.api_worker_max_requests,
        "limit_max_requests_jitter": settings.api_worker_max_requests_jitter,
        # Never in a deployed process: it watches the filesystem and it runs a single
        # worker, which would quietly undo everything above.
        "reload": False,
    }


def main() -> None:
    settings = get_settings()
    # The import string, not the imported object: uvicorn needs to import the app inside
    # each worker it starts, and handing it a live object silently limits it to one.
    uvicorn.run("ai_market_monitor.main:app", **uvicorn_options(settings))  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
