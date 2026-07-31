"""The market-data promise, enforced again at run time, one market at a time.

Before approval the platform checks whether it can actually read candles for the markets
a setup will watch. That check makes one of two promises, recorded in the approval:

``verified_all``
    Every market × every timeframe this setup needs was checked.

``policy_verified_runtime_fail_closed``
    A bounded sample was checked. Which markets are watched can be larger than the
    sample, or can change, so **each market is checked again when it runs**.

The second promise was written down and displayed, but nothing enforced it. The worker
re-resolved the universe every cycle and evaluated whatever came back, so a market that
had never been checked — one that entered the eligible universe after approval — was
evaluated on whatever data came back, including none.

This module is where the promise is kept. :func:`skip_reason` returns the reason a market
must not be evaluated this cycle, or ``None``. It refuses in three cases and invents
nothing:

* the candles are missing, stale, out of order or too few — for either promise
* the promise said everything was checked and this market was not in that list
* there is no recorded promise at all, and the caller asked for one
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.schemas.screening_execution import PreflightContract

#: Why a market was skipped. Recorded on the scan result, so "nothing fired" can be told
#: apart from "we refused to look".
UNUSABLE_DATA = "market_data_unusable"
CONTRACT_INCOMPLETE = "preflight_contract_incomplete"
CONTRACT_MISSING = "preflight_contract_missing"


def _pair(symbol: str, timeframe: str) -> str:
    return f"{symbol.strip().upper()}@{timeframe.strip()}"


@dataclass(frozen=True, slots=True)
class RuntimeDataContract:
    """What was promised about market data for this approved version."""

    contract: PreflightContract
    #: Every ``SYMBOL@timeframe`` the pre-approval check actually covered.
    verified_pairs: frozenset[str]
    required_timeframes: tuple[str, ...]
    #: True when the approval carried no market-data evidence at all. Kept as a fact
    #: rather than guessed away: an older approved version predates the record, and the
    #: honest answer is "unknown", which the caller turns into "check everything".
    recorded: bool = True

    @classmethod
    def from_approval_evidence(
        cls,
        payload: dict[str, Any] | None,
    ) -> RuntimeDataContract:
        """Read the contract off an approved version. Anything unusable fails closed.

        Fails closed to ``policy_verified_runtime_fail_closed`` with an empty verified
        set — the strictest reading — so a missing or damaged record means *every* market
        is checked at run time, never that every market is assumed fine.
        """
        manifest = ((payload or {}).get("preflight_manifest") or {}) if payload else {}
        if not isinstance(manifest, dict) or not manifest:
            return cls(
                contract="policy_verified_runtime_fail_closed",
                verified_pairs=frozenset(),
                required_timeframes=(),
                recorded=False,
            )
        raw_contract = manifest.get("contract")
        contract: PreflightContract = (
            raw_contract
            if raw_contract
            in {"verified_all", "policy_verified_runtime_fail_closed", "not_required"}
            else "policy_verified_runtime_fail_closed"
        )
        pairs = manifest.get("verified_pairs")
        verified = frozenset(
            _pair(*item.rsplit("@", 1))
            for item in (pairs if isinstance(pairs, list) else [])
            if isinstance(item, str) and "@" in item
        )
        timeframes = manifest.get("required_timeframes")
        return cls(
            contract=contract,
            verified_pairs=verified,
            required_timeframes=tuple(
                item for item in (timeframes if isinstance(timeframes, list) else []) if item
            ),
            recorded=True,
        )

    def covers(self, symbol: str, timeframes: list[str]) -> bool:
        """Was this exact market, on every timeframe it needs, part of the promise?"""
        if not timeframes:
            return False
        return all(_pair(symbol, timeframe) in self.verified_pairs for timeframe in timeframes)

    def claims_complete_universe(self) -> bool:
        """True only when the promise really was "every market was checked"."""
        return self.recorded and self.contract == "verified_all"


def skip_reason(
    contract: RuntimeDataContract,
    *,
    symbol: str,
    timeframes: list[str],
    data_is_usable: bool,
    data_reason: str | None = None,
) -> str | None:
    """Why this market must not be evaluated this cycle, or ``None`` to proceed.

    This is the per-symbol fail-closed gate the ``policy_verified_runtime_fail_closed``
    contract promises. It never substitutes, pads or guesses data: the only two outcomes
    are "evaluate on what was actually read" and "skip, with the reason recorded".
    """

    if not data_is_usable:
        return data_reason or UNUSABLE_DATA
    if contract.claims_complete_universe() and not contract.covers(symbol, timeframes):
        # The approval said every market had been checked, and this one had not. The
        # universe grew after approval. Evaluating it would make the recorded promise
        # false, so it waits for a fresh check instead.
        return CONTRACT_INCOMPLETE
    return None
