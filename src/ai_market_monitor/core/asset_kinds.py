"""What kind of asset a coin is — a native coin, a token or a network — in one vocabulary.

The identity check writes the kind (``services/sharia_identity.py``,
``services/sharia_identity_discovery.py``) and every Passport reads it back. They used
to keep separate word lists. The writer said ``native_coin``; the reader only knew
``native``, ``coin`` and ``native_asset``. So every native coin on every Passport — Bitcoin
included — was shown to the public as a "Token". Both sides import this module now, and
a kind the reader does not know is said to be not recorded, never guessed.

The network a coin lives on is cleaned up here too. For a token the identity check keeps
the price provider's platform id (``binance-smart-chain``); a reader should see the
network's name (``BNB Smart Chain``), not an internal id.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "KIND_NOT_RECORDED",
    "NATIVE_COIN",
    "NETWORK",
    "RECORDED_KINDS",
    "TOKEN",
    "asset_kind_label",
    "is_native_coin",
    "network_label",
]

#: The coin of its own blockchain: BTC on Bitcoin, ETH on Ethereum.
NATIVE_COIN: Final[str] = "native_coin"
#: A coin issued by a contract on somebody else's blockchain: USDC on Ethereum.
TOKEN: Final[str] = "token"
#: A blockchain network itself, recorded without a coin of its own.
NETWORK: Final[str] = "network"

#: The kinds the identity check accepts. A coin with any other kind fails the check.
RECORDED_KINDS: Final[frozenset[str]] = frozenset({NATIVE_COIN, TOKEN, NETWORK})

#: Every spelling of "native coin" a stored record may carry. ``native_coin`` is what the
#: identity check writes; the others are older records, still read correctly.
_NATIVE_SPELLINGS: Final[frozenset[str]] = frozenset(
    {NATIVE_COIN, "native", "coin", "native_asset"}
)
_TOKEN_SPELLINGS: Final[frozenset[str]] = frozenset({TOKEN})

#: What a reader is told when the kind is not recorded. Said plainly, not guessed.
KIND_NOT_RECORDED: Final[str] = "Type not recorded"

#: Platform ids whose plain capitalised form would be wrong. Every other id reads well
#: with each word capitalised (``arbitrum-one`` → ``Arbitrum One``).
_NETWORK_NAMES: Final[dict[str, str]] = {
    "binance-smart-chain": "BNB Smart Chain",
    "optimistic-ethereum": "Optimism",
    "polygon-pos": "Polygon PoS",
    "tron": "TRON",
    "zksync": "zkSync",
}

_ID_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[-_\s]+")


def _normal(asset_type: str | None) -> str:
    return (asset_type or "").strip().casefold()


def is_native_coin(asset_type: str | None) -> bool | None:
    """True for a native coin, False for a token, None when it is neither or unknown."""

    kind = _normal(asset_type)
    if kind in _NATIVE_SPELLINGS:
        return True
    if kind in _TOKEN_SPELLINGS:
        return False
    return None


def asset_kind_label(asset_type: str | None) -> str:
    """The kind of asset, in the words a reader sees: "Native coin", "Token", "Network"."""

    native = is_native_coin(asset_type)
    if native is True:
        return "Native coin"
    if native is False:
        return "Token"
    kind = _normal(asset_type)
    if kind == NETWORK:
        return "Network"
    if kind in {"", "unknown"}:
        return KIND_NOT_RECORDED
    return kind.replace("_", " ").capitalize()


def network_label(network: str | None) -> str | None:
    """The network's name for a reader, or None when no network is recorded.

    A name already written for people (``Bitcoin``, ``The Open Network``) is kept as it
    is. Only an all-lowercase platform id is turned into a name.
    """

    text = (network or "").strip()
    if not text:
        return None
    if text != text.lower():
        return text
    if text in _NETWORK_NAMES:
        return _NETWORK_NAMES[text]
    return " ".join(word.capitalize() for word in _ID_SEPARATORS.split(text) if word)
