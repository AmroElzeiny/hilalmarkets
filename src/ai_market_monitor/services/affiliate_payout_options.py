"""Where an affiliate payout can be sent, and the rule that decides what is offered.

One owner, because the same list is needed in four places — the form a person picks from,
the validator that refuses anything else, the System Brain row an administrator reads,
and the test that holds the fee rule. Written out four times it would drift, and the
drift would be silent: a coin removed from the form but still accepted by the validator
is a payout to a network nobody meant to support.

**The rule is the fee cap, not the list.** A payout of five dollars loses a real share of
itself to a two-dollar withdrawal, so every pair offered here must cost at most
:data:`MAXIMUM_NETWORK_FEE_USD` to send. The list is what currently satisfies that rule.

``typical_fee_usd`` is the withdrawal fee Binance and Bybit charge for that pair, as an
approximate dollar figure. It moves with the coin's price and with the exchange's own
schedule, so it is written down where it can be seen and corrected rather than assumed —
and ``tests/unit/test_invariant_affiliate_payouts.py`` fails the moment a pair is added
that breaks the cap. **Re-check these before a pricing change; a stale number here means
an affiliate is quoted a fee that is not the fee.**
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The most a payout may cost to send. Above this the fee is a meaningful part of the
#: smallest payout somebody can request, and the option does not belong on the form.
MAXIMUM_NETWORK_FEE_USD = Decimal("0.20")

#: The least an affiliate may ask for at once.
MINIMUM_PAYOUT_USD = Decimal("5.00")

#: Where somebody writes if they want paying another way. A real address, not a form:
#: another method is a conversation, and pretending otherwise would mean building a
#: bank-transfer flow nobody has agreed to.
ALTERNATIVE_METHOD_EMAIL = "office@hilalmarkets.com"


@dataclass(frozen=True, slots=True)
class PayoutNetwork:
    key: str
    #: What the network is called on an exchange's withdrawal screen. A person choosing
    #: it has to recognise it there, so this uses the exchange's own wording.
    label: str
    #: Roughly what it costs to send this coin over this network, in dollars.
    typical_fee_usd: Decimal
    #: What an address on this network looks like, for the person to check against.
    address_hint: str


@dataclass(frozen=True, slots=True)
class PayoutCurrency:
    key: str
    label: str
    #: Plain words for somebody who does not know the coin.
    plain_words: str
    networks: tuple[PayoutNetwork, ...]


#: Networks, defined once and shared by every coin that runs on them, so a fee correction
#: is one edit rather than one edit per coin.
_BEP20 = PayoutNetwork(
    "bep20",
    "BNB Smart Chain (BEP20)",
    Decimal("0.10"),
    "Starts with 0x",
)
_ARBITRUM = PayoutNetwork(
    "arbitrum",
    "Arbitrum One (ARB)",
    Decimal("0.15"),
    "Starts with 0x",
)
_TRC20 = PayoutNetwork(
    "trc20",
    "Tron (TRC20)",
    Decimal("0.20"),
    "Starts with T",
)
_LITECOIN = PayoutNetwork(
    "litecoin",
    "Litecoin (LTC)",
    Decimal("0.02"),
    "Starts with L, M or ltc1",
)
_STELLAR = PayoutNetwork(
    "stellar",
    "Stellar (XLM)",
    Decimal("0.01"),
    "Starts with G, and needs a memo",
)
_ALGORAND = PayoutNetwork(
    "algorand",
    "Algorand (ALGO)",
    Decimal("0.01"),
    "58 characters, capital letters and numbers",
)


PAYOUT_CURRENCIES: tuple[PayoutCurrency, ...] = (
    PayoutCurrency(
        "USDT",
        "USDT",
        "A dollar-tracking coin. The steadiest choice.",
        (_BEP20, _ARBITRUM, _TRC20),
    ),
    PayoutCurrency(
        "USDC",
        "USDC",
        "Another dollar-tracking coin.",
        (_BEP20, _ARBITRUM),
    ),
    PayoutCurrency(
        "BNB",
        "BNB",
        "The coin of the BNB Smart Chain network.",
        (_BEP20,),
    ),
    PayoutCurrency(
        "LTC",
        "Litecoin",
        "One of the oldest coins. Sending it is very cheap.",
        (_LITECOIN,),
    ),
    # The three extra coins. Each is here for the same reason: it is widely traded, it is
    # on both Binance and Bybit, and its own network costs a cent or two to use.
    PayoutCurrency(
        "TRX",
        "TRON",
        "The coin of the Tron network.",
        (_TRC20,),
    ),
    PayoutCurrency(
        "XLM",
        "Stellar Lumens",
        "Built for cheap transfers. Remember the memo.",
        (_STELLAR,),
    ),
    PayoutCurrency(
        "ALGO",
        "Algorand",
        "Fast and very cheap to send.",
        (_ALGORAND,),
    ),
)


PAYOUT_CURRENCY_BY_KEY: dict[str, PayoutCurrency] = {
    currency.key: currency for currency in PAYOUT_CURRENCIES
}


def network_for(currency_key: str, network_key: str) -> PayoutNetwork | None:
    """The network, only if this coin really runs on it.

    Refuses rather than guesses. Accepting a coin-and-network pair that is not offered is
    how money is sent to a chain the receiving wallet cannot see.
    """

    currency = PAYOUT_CURRENCY_BY_KEY.get(currency_key)
    if currency is None:
        return None
    for network in currency.networks:
        if network.key == network_key:
            return network
    return None


def payout_options_payload() -> list[dict[str, object]]:
    """The catalogue as the page draws it."""

    return [
        {
            "key": currency.key,
            "label": currency.label,
            "plain_words": currency.plain_words,
            "networks": [
                {
                    "key": network.key,
                    "label": network.label,
                    "typical_fee_usd": f"{network.typical_fee_usd:.2f}",
                    "address_hint": network.address_hint,
                }
                for network in currency.networks
            ],
        }
        for currency in PAYOUT_CURRENCIES
    ]
