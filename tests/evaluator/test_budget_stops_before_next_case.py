"""EVAL-09 / INV-13: the run stops at the budget, and never loses paid-for work.

Both recorded runs overshot: `20260726T171424Z` spent $1.9012 against a $1.9000
budget, `20260727T081613Z` spent $1.0050 against $1.0000, and both reported
`STOPPED_BUDGET` only after the whole suite had finished. The budget was checked only
while *charging*, so the run always discovered it was over after already spending past
it, and there was no gate before starting the next case.

A second defect compounded it: `asyncio.gather` without `return_exceptions` discards
the results of every case in the chunk when one raises, so completed cases that had
already been paid for were thrown away.
"""

from __future__ import annotations

import pytest

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.failures import FailureClass, classify_exception, is_retryable
from hm_chatbot_eval.runner import BudgetExceeded, EvaluationRunner


def _runner(tmp_path, budget: float) -> EvaluationRunner:
    settings = Settings(eval_output_dir=tmp_path, eval_cache_db=tmp_path / "cache.sqlite3")
    return EvaluationRunner(settings, "test-run", budget)


def test_no_stop_while_budget_covers_another_case(tmp_path) -> None:
    runner = _runner(tmp_path, 1.0)
    runner.spent = 0.2
    runner._case_costs = [0.2]
    assert runner.stop_reason() is None


def test_stops_before_starting_a_case_the_budget_cannot_cover(tmp_path) -> None:
    """The point of the fix: the decision happens *before* the next case starts."""
    runner = _runner(tmp_path, 1.0)
    runner.spent = 0.9
    runner._case_costs = [0.3, 0.3, 0.3]
    reason = runner.stop_reason()
    assert reason is not None
    assert "cannot cover another case" in reason


def test_stops_once_the_budget_is_reached_exactly(tmp_path) -> None:
    runner = _runner(tmp_path, 1.0)
    runner.spent = 1.0
    assert runner.stop_reason() is not None


def test_an_unlimited_budget_never_stops(tmp_path) -> None:
    runner = _runner(tmp_path, 0.0)
    runner.spent = 10_000.0
    assert runner.stop_reason() is None
    assert runner.remaining_budget == float("inf")


def test_the_first_case_is_always_allowed_to_run(tmp_path) -> None:
    """With no completed case there is nothing to project from, so the run must not
    refuse to start."""
    runner = _runner(tmp_path, 1.0)
    assert runner.projected_case_cost() == 0.0
    assert runner.stop_reason() is None


def test_projection_tracks_what_cases_actually_cost(tmp_path) -> None:
    runner = _runner(tmp_path, 5.0)
    runner._case_costs = [0.10, 0.20, 0.30]
    assert runner.projected_case_cost() == pytest.approx(0.20)


def test_charging_past_the_hard_limit_still_raises(tmp_path) -> None:
    """The pre-flight gate is the normal path; the hard limit stays as a backstop so a
    single unexpectedly expensive case cannot run away."""
    runner = _runner(tmp_path, 1.0)
    with pytest.raises(BudgetExceeded):
        runner._charge(1.5)


def test_remaining_budget_never_goes_negative(tmp_path) -> None:
    runner = _runner(tmp_path, 1.0)
    runner.spent = 1.4
    assert runner.remaining_budget == 0.0


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # INV-13 names this one explicitly: a DNS failure is not a connect timeout.
        (
            "ConnectError: [Errno 11001] getaddrinfo failed",
            FailureClass.TARGET_DNS_RESOLUTION_FAILURE,
        ),
        ("ConnectError: Name or service not known", FailureClass.TARGET_DNS_RESOLUTION_FAILURE),
        ("ConnectTimeout: timed out", FailureClass.TARGET_CONNECT_TIMEOUT),
        ("ReadTimeout: ", FailureClass.TARGET_READ_TIMEOUT),
        ("JSONDecodeError: Unterminated string", FailureClass.TARGET_INVALID_JSON),
    ],
)
def test_transport_failures_are_classified_by_cause(message: str, expected: FailureClass) -> None:
    name, _, detail = message.partition(": ")
    exc = type(name, (Exception,), {})(detail)
    assert classify_exception(exc, role="target") == expected


def test_dns_and_rate_limit_are_retryable_but_invalid_json_is_not() -> None:
    """Retry only transient infrastructure.

    `TARGET_INVALID_JSON` is the class that failed `model_version_drift-001`. A body
    the target produced malformed once will be produced malformed again, so retrying
    it only burns budget.
    """
    assert is_retryable(FailureClass.TARGET_DNS_RESOLUTION_FAILURE)
    assert is_retryable(FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT)
    assert not is_retryable(FailureClass.TARGET_INVALID_JSON)
    assert not is_retryable(FailureClass.EVALUATOR_AUTH_FAILURE)
