"""Which trade side a card belongs to, for tests that build one card at a time.

A trade-quality card is a sentence about a trade — "my stop is no wider than two ATR",
"the next resistance is far enough away". A trade has a side, and the risk model refuses
to invent one: a monitor compiled with no bias is answered ``direction_ambiguous``, which
is the "never invert" rule doing its job.

So a test that builds one of those cards on its own has to say which setup it belongs to,
the way a person marking a rule as a buy or a sell setup does. Some are side-specific in
their reading — "how far is the next **support**" is produced only for a sell setup and
its resistance sibling only for a buy one — so the honest assertion is *readable on the
side it belongs to*, and the caller tries both rather than carrying a hand-written map
that goes stale when the next side-specific card is registered.
"""

from __future__ import annotations

from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeV2, StrategyBias

#: Cards whose value the risk model produces. Read from the capability register, so a
#: trade-quality card added later is covered without anybody editing this file.
RISK_CARD_KEYS = frozenset(
    capability.key
    for capability in all_capabilities()
    if capability.provider_required == "risk_context"
)

BOTH_SIDES: tuple[StrategyBias, ...] = (StrategyBias.LONG, StrategyBias.SHORT)
NO_SIDE: tuple[StrategyBias, ...] = (StrategyBias.NEUTRAL,)


def trade_sides_for(capability_key: str) -> tuple[StrategyBias, ...]:
    """The setups this card has to be tried in before it can be called unreadable."""

    return BOTH_SIDES if capability_key in RISK_CARD_KEYS else NO_SIDE


def with_bias(node: ConditionNodeV2, bias: StrategyBias) -> ConditionNodeV2:
    """The same rule, marked as belonging to a buy or a sell setup."""

    if bias is StrategyBias.NEUTRAL:
        return node
    return node.model_copy(update={"strategy_bias": bias})
