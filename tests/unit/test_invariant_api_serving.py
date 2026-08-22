"""What `python -m ai_market_monitor.serve` actually hands to uvicorn.

The failure this guards against is quiet: a setting that is read, looks right in the
config file, and is then simply not passed on. Nothing errors, the server starts, and the
protection everybody believes is running is not. So the options are built as data by
`serve.uvicorn_options`, and this reads them.

Background: on 22 August 2026 the API ran as one process and the kernel killed it twice.
Several workers keep the website up while one is replaced; retiring a worker on a request
count stops any of them growing into the container's memory ceiling.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
import uvicorn

from ai_market_monitor import serve
from ai_market_monitor.core.config import Settings, get_settings

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"


def options(**overrides: object) -> dict[str, object]:
    settings = get_settings().model_copy(update=overrides)
    return serve.uvicorn_options(settings)


def test_every_setting_reaches_uvicorn() -> None:
    """The three numbers are passed through, not merely read."""
    chosen = options(
        api_worker_processes=7,
        api_worker_max_requests=1234,
        api_worker_max_requests_jitter=56,
    )
    assert chosen["workers"] == 7
    assert chosen["limit_max_requests"] == 1234
    assert chosen["limit_max_requests_jitter"] == 56


def test_uvicorn_accepts_every_option_by_that_name() -> None:
    """A silent typo here would disable the protection and start the server anyway.

    `uvicorn.run` takes `**kwargs`-shaped keyword arguments, so a misspelled option is not
    a crash — it is ignored. Checking the names against uvicorn's own signature is the
    only thing that catches it.
    """
    accepted = set(inspect.signature(uvicorn.run).parameters)
    unknown = set(options()) - accepted
    assert not unknown, (
        f"uvicorn.run does not accept {sorted(unknown)}. A name uvicorn does not know is "
        "ignored in silence, and the setting behind it does nothing."
    )


def test_the_proxy_headers_are_trusted_because_caddy_is_in_front() -> None:
    """Without these, every client address is Caddy's and rate limiting counts one person."""
    chosen = options()
    assert chosen["proxy_headers"] is True
    assert chosen["forwarded_allow_ips"] == "*"


def test_reload_is_never_on() -> None:
    """`reload` silently forces a single worker, undoing everything else here."""
    assert options()["reload"] is False


def test_the_app_is_passed_as_an_import_string() -> None:
    """Handing uvicorn a live app object limits it to one worker, without complaint."""
    source = inspect.getsource(serve.main)
    assert '"ai_market_monitor.main:app"' in source, (
        "serve.main must pass the app as an import string. uvicorn imports the app inside "
        "each worker it starts; given an object it can only run one."
    )


#: Every `reverse_proxy` block in the Caddyfile — the public site and the app subdomain.
PROXY_BLOCKS = [
    block
    for block in re.findall(
        r"reverse_proxy[^\n]*\{(.*?)\n\t\}", CADDYFILE.read_text(encoding="utf-8"), re.S
    )
]


def test_there_is_a_proxy_block_to_check() -> None:
    """If the file is reshaped and this stops matching, the rules below silently pass."""
    assert len(PROXY_BLOCKS) >= 2, (
        "the Caddyfile no longer has a reverse_proxy block per site, or the shape this "
        "test reads has changed. The retry rules below would pass without checking."
    )


@pytest.mark.parametrize("index", range(len(PROXY_BLOCKS)))
def test_a_retiring_worker_is_retried_rather_than_shown_as_an_error(index: int) -> None:
    """A replaced worker must never reach a customer as 502.

    The API runs several worker processes and retires each after a bounded number of
    requests. Caddy keeps pooled connections to them, and a pooled connection to the
    worker being replaced dies mid-request. Without a retry that is an error page: on
    22 August 2026 the 502 timestamps matched worker start times to the second.

    Asserted for every site in the file, not for the one that was reported. The report
    named app.hilalmarkets.com; the public site had exactly the same gap.
    """
    block = PROXY_BLOCKS[index]
    assert "lb_try_duration" in block, (
        "a reverse_proxy block has no lb_try_duration, so a request that arrives while a "
        "worker is being replaced is answered 502 instead of being retried against the "
        "worker that is already listening."
    )


def test_the_retry_window_outlasts_a_worker_restart() -> None:
    """A retry window shorter than the gap it covers is decoration."""
    for block in PROXY_BLOCKS:
        match = re.search(r"lb_try_duration\s+(\d+)(ms|s)", block)
        assert match, "lb_try_duration must be written with a unit, such as 5s"
        seconds = int(match.group(1)) / (1000 if match.group(2) == "ms" else 1)
        assert seconds >= 2, (
            f"lb_try_duration is {seconds}s. A uvicorn worker takes longer than that to "
            "be replaced, so the retry would give up before the new one is listening."
        )


def test_the_port_matches_what_the_container_exposes() -> None:
    """serve.py, the compose `expose`, and the healthcheck URL are one number in three
    places, and the other two are not in Python."""
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.prod.yml").read_text(
        encoding="utf-8"
    )
    assert f'- "{serve.PORT}"' in compose, (
        f"serve.PORT is {serve.PORT} but the api service does not expose it."
    )
    healthcheck_ports = set(re.findall(r"http://127\.0\.0\.1:(\d+)/health", compose))
    assert healthcheck_ports == {str(serve.PORT)}, (
        f"the healthcheck probes {healthcheck_ports} but the server listens on "
        f"{serve.PORT}. The container would be reported unhealthy for ever."
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("api_worker_processes", 0),
        ("api_worker_max_requests", 0),
        ("api_worker_max_requests_jitter", -1),
    ],
)
def test_a_meaningless_value_is_refused_at_startup(field: str, bad_value: int) -> None:
    """Refused when the settings are built, not accepted and quietly ignored later."""
    with pytest.raises(ValueError):
        Settings(**{field: bad_value})
