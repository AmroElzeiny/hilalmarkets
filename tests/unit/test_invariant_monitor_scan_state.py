"""A monitor that is checking the market must never be reported as never having looked.

The Monitors page, Home and the older Watchlists page all asked one question — "when did
this monitor last look at the market?" — of the newest ``scan_jobs`` row. On a live
monitor the newest row is the check that has **not finished yet**: the scheduler creates
the next one every interval, so ``completed_at`` on that row is empty nearly all the
time. Every one of those screens therefore said "Not looked yet" about a monitor that had
already finished dozens of checks, and Home said the market had never been looked at.

The other half of the same mistake is the opposite one. A ``failed`` job and a
``canceled`` job both get a ``completed_at`` stamped on the way out, and neither read the
market. "Is ``completed_at`` set?" says yes for both.

So the rule tested here is not "read a different row". It is:

* **the states in which a job counts as a finished check are named in exactly one
  place**, and
* **every screen asks that owner**, never a row it ordered itself.

Parametrised across all six job states, in both directions, so a fix that only helps the
reported case fails.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ai_market_monitor.api.routers.dashboard_test import _watchlist_view
from ai_market_monitor.db.models.enums import ScanJobStatus
from ai_market_monitor.services.monitor_scan_state import (
    CHECK_FINISHED_STATUSES,
    CHECK_IN_FLIGHT_STATUSES,
    NO_SCANS,
    MonitorScanState,
)
from ai_market_monitor.services.product_language import how_long_ago

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/ai_market_monitor"

#: Every state a job row can be in. Named one by one rather than as a set, so a new
#: state added to the enum has to be classified here before this file will pass.
ALL_STATES = [pytest.param(state, id=state.value) for state in ScanJobStatus]


def _job(status: ScanJobStatus, *, completed_at: datetime | None):
    """A stand-in for one stored row, carrying only what a reader is allowed to use."""

    return type(
        "J",
        (),
        {
            "status": status,
            "completed_at": completed_at,
            "scheduled_for": datetime.now(UTC) - timedelta(minutes=7),
        },
    )()


def _card(scan_state: MonitorScanState) -> dict:
    """One monitor, with a health score high enough to be visible if it is read."""

    class _Strategy:
        id = "44444444-4444-4444-4444-444444444444"
        name = "A monitor that is really running"
        description = None
        active_version_id = uuid4()

        class status:  # noqa: N801 - a stand-in for the stored enum
            value = "active"

    return {
        "strategy": _Strategy(),
        # The shape `edge_health` really returns: a plain dictionary. A stand-in object
        # with attributes is what the older tests used, and it is why nobody noticed
        # that the card read this payload with `getattr` and always saw nothing.
        "health": {"score": 95, "main_issue": "x", "main_issue_component": "Data coverage"},
        "scan_state": scan_state,
        "methodology": None,
        "main_bottleneck": None,
        "eligible_asset_count": 0,
        "pending_repair": None,
    }


# ── The two states are named once ──────────────────────────────────────────────────


@pytest.mark.parametrize("state", ALL_STATES)
def test_every_job_state_is_classified_exactly_once(state):
    """A state counts as a finished check, or as one in progress, or as neither.

    Never as both. The two lists are what every screen reads, so an overlap would let
    one monitor be "checking now" and "last checked" from the same row.
    """

    assert not (state in CHECK_FINISHED_STATUSES and state in CHECK_IN_FLIGHT_STATUSES)


@pytest.mark.parametrize("state", ALL_STATES)
def test_only_a_check_that_read_the_market_counts_as_looking(state):
    """`failed` and `canceled` stamp a finishing time and read nothing.

    Counting them would put "Looked 3 minutes ago" on a monitor whose every check was
    thrown away before it touched the market — the same untrue sentence as "Not looked
    yet", pointing the other way.
    """

    counts = state in CHECK_FINISHED_STATUSES
    assert counts is (state in {ScanJobStatus.SUCCEEDED, ScanJobStatus.PARTIAL}), state


# ── A finished check is found even while a newer one is in progress ────────────────


IN_FLIGHT = [pytest.param(state, id=state.value) for state in CHECK_IN_FLIGHT_STATUSES]
FINISHED = [pytest.param(state, id=state.value) for state in CHECK_FINISHED_STATUSES]


@pytest.mark.parametrize("in_flight_state", IN_FLIGHT)
@pytest.mark.parametrize("finished_state", FINISHED)
def test_a_running_monitor_reports_the_check_that_finished(finished_state, in_flight_state):
    """The production shape: a completed check, and a newer one already under way.

    This is what every live monitor looks like almost all of the time. The page used to
    read the newer row, find an empty finishing time and say "Not looked yet" forever.
    """

    finished_at = datetime.now(UTC) - timedelta(minutes=4)
    state = MonitorScanState(
        last_completed=_job(finished_state, completed_at=finished_at),
        in_flight=_job(in_flight_state, completed_at=None),
        latest=_job(in_flight_state, completed_at=None),
    )

    assert state.last_checked_at == finished_at
    assert state.has_ever_checked is True
    assert state.is_checking_now is True

    view = _watchlist_view(_card(state), scanning_enabled=True)
    assert view["last_checked_exact"] == finished_at
    # The moment the card reports is the finished check's own moment — asserted through
    # the owner of the wording rather than against a fixed string, so a slow suite
    # cannot drift past it and turn a real rule into a clock reading.
    assert view["last_checked"] == how_long_ago(finished_at)
    assert view["working"]["label"] != "Not looked yet"


@pytest.mark.parametrize("state", ALL_STATES)
def test_a_monitor_with_no_finished_check_says_so_whatever_is_in_flight(state):
    """Nothing finished yet is still an honest "not looked yet", in every state."""

    scan_state = MonitorScanState(
        last_completed=None,
        in_flight=_job(state, completed_at=None) if state in CHECK_IN_FLIGHT_STATUSES else None,
        latest=_job(state, completed_at=None),
    )

    view = _watchlist_view(_card(scan_state), scanning_enabled=True)
    assert view["last_checked"] is None
    assert view["last_checked_exact"] is None
    assert view["working"]["label"] == "Not looked yet"


def test_a_monitor_with_no_version_has_no_checks():
    assert NO_SCANS.has_ever_checked is False
    assert NO_SCANS.is_checking_now is False
    assert NO_SCANS.last_checked_at is None


# ── "A check is running now" is only said while one really is ──────────────────────


@pytest.mark.parametrize("state", ALL_STATES)
def test_a_first_check_is_only_called_running_while_one_is(state):
    """A job that failed and left nothing behind is a row, not a check in progress.

    Reading "is there a row?" made the page promise "The first check of the market is
    running now" about a monitor whose only attempt had already been thrown away.
    """

    scan_state = MonitorScanState(
        last_completed=None,
        in_flight=_job(state, completed_at=None) if state in CHECK_IN_FLIGHT_STATUSES else None,
        latest=_job(state, completed_at=None),
    )

    detail = _watchlist_view(_card(scan_state), scanning_enabled=True)["working"]["detail"]
    running_now = "The first check of the market is running now."
    assert (detail == running_now) is (state in CHECK_IN_FLIGHT_STATUSES), state


# ── One owner, and no screen may order its own rows ────────────────────────────────


#: The only module allowed to name the states that mean "this check read the market".
#:
#: `services/trials.py` had written the same two names out by hand for its coverage
#: figure. Two copies of one vocabulary is the failure this codebase keeps repeating: a
#: state added to one list and not the other makes two screens disagree about the same
#: monitor, and neither of them is obviously wrong.
_OWNS_FINISHED_STATES = "services/monitor_scan_state.py"


#: The states whose meaning belongs to the owner, and the name of the list to import.
#:
#: "Finished a check" and "still in progress" are both read by screens, so both are
#: shared vocabulary. `FAILED` and `CANCELED` are not listed: a module is free to ask
#: about a failure for its own reasons — operational health counts failed jobs, and that
#: is a different question from whether a person's monitor looked at the market.
_SHARED_VOCABULARY = {
    **{status.name: "CHECK_FINISHED_STATUSES" for status in CHECK_FINISHED_STATUSES},
    **{status.name: "CHECK_IN_FLIGHT_STATUSES" for status in CHECK_IN_FLIGHT_STATUSES},
}


def test_which_states_mean_a_finished_check_are_written_in_one_place():
    """Nothing outside the owner may name these states for itself.

    `services/scanner.py` is not excused for being clever: it *sets* the states, so it
    names them, and that is the one other legitimate use. Everything that asks "did this
    count as a check" or "is one running" imports the list.

    Two modules had already written a copy — `services/trials.py` for the trial coverage
    figure and `services/scan_retention.py` for what may be deleted. Neither was wrong
    yet, and that is the point: a copy is wrong only later, quietly, on one screen.
    """

    setters = {"services/scanner.py", "db/models/enums.py"}
    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == _OWNS_FINISHED_STATES or relative in setters:
            continue
        source = path.read_text(encoding="utf-8")
        if "ScanJobStatus" not in source:
            continue
        tree = ast.parse(source)
        offenders.extend(
            f"{relative}:{node.lineno} ({_SHARED_VOCABULARY[node.attr]})"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ScanJobStatus"
            and node.attr in _SHARED_VOCABULARY
        )

    assert offenders == [], (
        "these decide for themselves what a scan job's state means, instead of importing "
        f"the list named beside each one from {_OWNS_FINISHED_STATES}: {offenders}"
    )


def test_no_page_picks_its_own_scan_job_row():
    """Every screen asks the owner. A screen that orders rows decides meaning again.

    That is exactly how this broke: one query in a router, ordered by when the row was
    *created*, handed to a reader asking when a check *finished*.
    """

    offenders: list[str] = []
    for path in (SOURCE / "api/routers").rglob("*.py"):
        relative = path.relative_to(SOURCE).as_posix()
        source = path.read_text(encoding="utf-8")
        if "ScanJob" not in source:
            continue
        tree = ast.parse(source)
        offenders.extend(
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ScanJob"
            and node.attr in {"created_at", "completed_at", "scheduled_for", "status"}
        )

    assert offenders == [], (
        "these order or filter scan_jobs inside a page instead of asking "
        f"{_OWNS_FINISHED_STATES}: {offenders}"
    )


def test_the_card_reader_never_touches_a_row_itself():
    """`_watchlist_view` reads the resolved moment, never a row's own field."""

    source = (SOURCE / "api/routers/dashboard_test.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_watchlist_view"
    )
    body = ast.unparse(function)

    assert "completed_at" not in body
    assert "scan_state.last_checked_at" in body
    assert "scan_state.is_checking_now" in body
