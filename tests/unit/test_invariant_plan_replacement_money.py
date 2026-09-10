from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from ai_market_monitor.services.plan_replacements import (
    MANUAL_RETURN_HOURS,
    manual_return_window_words,
    money_owed_for_unused_time,
)


@pytest.mark.parametrize(
    ("paid", "elapsed_days", "expected"),
    (
        ("30.00", 0, "30.00"),
        ("30.00", 15, "15.00"),
        ("10.00", 29, "0.33"),
        ("7.00", 15, "3.50"),
        ("0.00", 15, "0.00"),
        ("-1.00", 15, "0.00"),
        ("30.00", 31, "0.00"),
    ),
)
def test_money_owed_uses_paid_amount_and_stays_inside_it(
    paid: str, elapsed_days: int, expected: str
) -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    result = money_owed_for_unused_time(
        paid_amount=Decimal(paid),
        period_start=start,
        period_end=start + timedelta(days=30),
        ended_at=start + timedelta(days=elapsed_days),
    )
    assert result == Decimal(expected)
    assert result >= 0
    assert result <= max(Decimal(paid), Decimal("0"))


def test_money_rounds_half_up_to_two_decimals() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    result = money_owed_for_unused_time(
        paid_amount=Decimal("1.00"),
        period_start=start,
        period_end=start + timedelta(seconds=8),
        ended_at=start + timedelta(seconds=7),
    )
    assert result == Decimal("0.13")


def test_the_customer_sentence_reads_the_one_time_constant() -> None:
    assert MANUAL_RETURN_HOURS == 48
    assert manual_return_window_words() == "within 48 hours"
