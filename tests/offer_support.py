"""A dated offer for tests that check how an offer with an end date behaves.

The running offer (``core/plans.CURRENT_OFFER``, HILAL30) has no end date, so it never
ends by itself and draws no countdown. The rules for an offer that *does* end — the price
goes back to normal at the deadline, the countdown and the crossed-out price disappear
together — still hold for the next dated offer, and these tests keep them checked by
installing one.

Every reader goes through ``core/plans.current_offer()``, so replacing the module value
changes the answer everywhere at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_market_monitor.core import plans

#: When the test offer stops. Far enough ahead that no real clock reaches it.
TEST_OFFER_ENDS_AT = datetime(2031, 1, 1, tzinfo=UTC)
BEFORE_TEST_OFFER_ENDS = TEST_OFFER_ENDS_AT - timedelta(days=1)
AFTER_TEST_OFFER_ENDS = TEST_OFFER_ENDS_AT

TEST_OFFER = plans.PriceOffer(
    code="HILAL30", percent=Decimal("30"), ends_at=TEST_OFFER_ENDS_AT
)


@contextmanager
def installed_offer(offer: plans.PriceOffer | None) -> Iterator[plans.PriceOffer | None]:
    """Run the block with ``offer`` as the running offer, then put the real one back."""

    real = plans.CURRENT_OFFER
    plans.CURRENT_OFFER = offer
    try:
        yield offer
    finally:
        plans.CURRENT_OFFER = real
