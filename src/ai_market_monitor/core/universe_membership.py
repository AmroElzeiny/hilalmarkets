"""What "which markets does this setup watch" means, per universe mode.

Three modes answer that question three different ways, and until now each caller decided
for itself. The screening gate froze the current answer into the definition; the resolver
read that frozen answer back as if the user had authored it; approval bound to a symbol
list for one mode and to nothing for another. An ``eligible_market`` monitor — the whole
point of which is that it follows the governed halal universe as it changes — could be
permanently pinned to the assets that happened to be eligible on the day it was approved.

So the three contracts live here, once, and every caller imports them.

======================  ==========================  =============================
mode                    membership                  what approval binds
======================  ==========================  =============================
``explicit_assets``     fixed, authored by the user the exact symbol set
``approved_watchlist``  fixed, from a Favorites list the list's content identity
``eligible_market``     dynamic, governed           the policy + methodology
======================  ==========================  =============================

"Fixed" means runtime may **remove** a market — compliance moved, data is unusable — but
may never **add** one the user did not approve. "Dynamic" means runtime re-resolves the
whole eligible universe every cycle, and a market entering is a governed compliance event,
not a silent edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.db.models.enums import ShariaUniverseMode

#: How membership is decided.
#:
#: ``fixed_authored``   — the symbols the user named, and only those
#: ``fixed_watchlist``  — whatever the approved Favorites list held **at approval**
#: ``governed_dynamic`` — every currently eligible market on the exchange
MembershipKind = Literal["fixed_authored", "fixed_watchlist", "governed_dynamic"]


@dataclass(frozen=True, slots=True)
class MembershipContract:
    """The one description of a mode's membership rule."""

    mode: ShariaUniverseMode
    kind: MembershipKind
    #: True when membership can move with no user edit. Only ``eligible_market``.
    #:
    #: This used to read ``mode != explicit_assets``, which called a Favorites list
    #: dynamic. A Favorites list is *editable*, which is not the same thing: editing it
    #: is a user action that must force a fresh review, and calling it dynamic told the
    #: user their approval covered changes it does not cover.
    dynamic: bool
    #: True when the runtime may add a market that was not in the reviewed set.
    runtime_may_add: bool
    #: Which authored field is the membership source, for error text and for tests.
    source_field: str
    #: One sentence for a beginner, shown on the approval screen.
    approval_sentence: str

    @property
    def requires_reapproval_on_membership_change(self) -> bool:
        return not self.dynamic


_CONTRACTS: dict[ShariaUniverseMode, MembershipContract] = {
    ShariaUniverseMode.EXPLICIT_ASSETS: MembershipContract(
        mode=ShariaUniverseMode.EXPLICIT_ASSETS,
        kind="fixed_authored",
        dynamic=False,
        runtime_may_add=False,
        source_field="universe.include_symbols",
        approval_sentence=(
            "This setup watches only the markets you picked. To watch a different "
            "market, add it and approve the setup again."
        ),
    ),
    ShariaUniverseMode.APPROVED_WATCHLIST: MembershipContract(
        mode=ShariaUniverseMode.APPROVED_WATCHLIST,
        kind="fixed_watchlist",
        dynamic=False,
        runtime_may_add=False,
        source_field="sharia_policy.approved_watchlist_id",
        approval_sentence=(
            "This setup watches the markets that were in your Favorites list when you "
            "approved it. If you change that list, approve the setup again to use the "
            "new list."
        ),
    ),
    ShariaUniverseMode.ELIGIBLE_MARKET: MembershipContract(
        mode=ShariaUniverseMode.ELIGIBLE_MARKET,
        kind="governed_dynamic",
        dynamic=True,
        runtime_may_add=True,
        source_field="sharia_policy.methodology_id",
        approval_sentence=(
            "This setup watches every market that passes the screening you chose. That "
            "list can change on its own as our review of each market changes."
        ),
    ),
}


def membership_contract(mode: ShariaUniverseMode | str | None) -> MembershipContract:
    """The contract for one mode. Unknown or absent fails closed to the fixed one.

    Failing closed matters: an unrecognised mode treated as dynamic would let a runtime
    add markets nobody approved. Treated as fixed, the worst case is a setup that watches
    less than the user expected, which is visible rather than silent.
    """

    if isinstance(mode, str):
        try:
            mode = ShariaUniverseMode(mode)
        except ValueError:
            return _CONTRACTS[ShariaUniverseMode.EXPLICIT_ASSETS]
    if mode is None:
        return _CONTRACTS[ShariaUniverseMode.EXPLICIT_ASSETS]
    return _CONTRACTS.get(mode, _CONTRACTS[ShariaUniverseMode.EXPLICIT_ASSETS])


def is_dynamic_membership(mode: ShariaUniverseMode | str | None) -> bool:
    """The single answer to "can this universe change without the user?"."""

    return membership_contract(mode).dynamic


def membership_source_symbols(
    mode: ShariaUniverseMode | str | None,
    *,
    authored_include_symbols: list[str],
) -> list[str] | None:
    """The authored symbol list a mode uses, or ``None`` when it uses none.

    ``None`` is the important answer. Both ``eligible_market`` and ``approved_watchlist``
    take membership from somewhere other than ``include_symbols``, so a resolver that
    reads that field for them is reading a value nobody authored — which is exactly how a
    dynamic universe got frozen to a stale resolution.
    """

    contract = membership_contract(mode)
    if contract.kind != "fixed_authored":
        return None
    return list(authored_include_symbols)
