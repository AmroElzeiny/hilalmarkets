"""Size and long-range movement: one spelling, and never a zero for "not known"."""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.sharia import LiveSpotMarketQuote
from ai_market_monitor.services.coinmarketcap import MarketRow
from ai_market_monitor.services.market_numbers import FIELD_NAMES, numbers_from


def _row(**overrides: object) -> MarketRow:
    values: dict[str, object] = {
        "symbol": "ANY",
        "cmc_id": 1,
        "name": "Any",
        "rank": 12,
        "market_cap_usd": 5e9,
        "fully_diluted_market_cap_usd": 7e9,
        "volume_24h_usd": 1e8,
        "percent_change_7d": 3.5,
        "percent_change_30d": -8.25,
        "percent_change_90d": 40.0,
        "circulating_supply": 1e9,
        "max_supply": 2e9,
    }
    values.update(overrides)
    return MarketRow(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider_field", sorted(FIELD_NAMES))
def test_every_field_the_translation_names_exists_on_the_provider_row(provider_field):
    """A mapping onto a field the provider does not have is a column of silent `None`.

    This is not hypothetical: the first version of this table read `market_cap` and
    `volume_24h`, and the real names are `market_cap_usd` and `volume_24h_usd`. Every
    row would have carried an empty size for ever, and the page would have drawn
    "Not known" against every coin without anything failing.
    """

    assert hasattr(_row(), provider_field), provider_field


@pytest.mark.parametrize("product_field", sorted(FIELD_NAMES.values()))
def test_the_page_can_carry_every_field_the_translation_produces(product_field):
    """The provider's spelling stops at the translation; the schema uses the product's."""

    assert product_field in LiveSpotMarketQuote.model_fields, product_field


def test_the_translation_carries_the_values_through():
    numbers = numbers_from(_row())
    assert numbers["market_cap_usd"] == 5e9
    assert numbers["fully_diluted_usd"] == 7e9
    assert numbers["market_rank"] == 12
    assert numbers["percentage_30d"] == -8.25


def test_a_coin_the_provider_has_not_measured_carries_nothing_rather_than_zero():
    """A zero would say the coin is worth nothing, which is a claim nobody made."""

    numbers = numbers_from(_row(market_cap_usd=None, rank=None, percent_change_30d=None))
    assert numbers["market_cap_usd"] is None
    assert numbers["market_rank"] is None
    assert numbers["percentage_30d"] is None


def test_the_new_fields_default_to_not_known_on_a_quote():
    """A quote built without provider numbers must not invent them."""

    quote = LiveSpotMarketQuote(
        symbol="ANY/USDT",
        canonical_asset="ANY",
        asset_name="Any",
        exchange="binance",
        quote_asset="USDT",
        data_available=True,
        updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    for field in FIELD_NAMES.values():
        assert getattr(quote, field) is None, field
