from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnsupportedCapabilityDependency:
    category: str
    matched_term: str
    explanation: str


class CapabilityExtensionScopeError(ValueError):
    def __init__(self, dependency: UnsupportedCapabilityDependency):
        super().__init__(dependency.explanation)
        self.dependency = dependency
        self.code = "custom_capability_provider_required"


_PROVIDER_ONLY_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "news_or_sentiment",
        re.compile(
            r"\b(?:news|headline|sentiment|social\s+media|twitter|reddit|telegram\s+mentions?)\b",
            re.IGNORECASE,
        ),
        "News, sentiment, and social activity require a configured external data provider.",
    ),
    (
        "on_chain_or_wallets",
        re.compile(
            r"\b(?:on[ -]?chain|whale\s+wallets?|wallet\s+flows?|wallet\s+activity)\b",
            re.IGNORECASE,
        ),
        "On-chain and wallet activity cannot be derived from closed OHLCV candles.",
    ),
    (
        "derivatives",
        re.compile(
            r"\b(?:open\s+interest|funding\s+rates?|liquidation(?:s|\s+heatmaps?)?)\b",
            re.IGNORECASE,
        ),
        "Derivatives data is outside the crypto spot OHLCV capability boundary.",
    ),
    (
        "macro",
        re.compile(
            r"\b(?:macroeconomic|macro\s+(?:data|release|event)|cpi|fomc|"
            r"federal\s+reserve|interest\s+rate\s+decision)\b",
            re.IGNORECASE,
        ),
        "Macroeconomic events require a configured external event provider.",
    ),
    (
        "order_flow",
        re.compile(
            r"\b(?:order\s*book|order\s+flow|hidden\s+order\s+flow|cvd|"
            r"cumulative\s+volume\s+delta|bid\s*ask\s+imbalance)\b",
            re.IGNORECASE,
        ),
        "Order-book and order-flow mechanics cannot be certified from closed OHLCV candles.",
    ),
    (
        "exchange_account",
        re.compile(
            r"\b(?:exchange\s+account|account\s+balance|open\s+positions?|private\s+orders?)\b",
            re.IGNORECASE,
        ),
        "Private exchange-account data is not available to the monitoring capability builder.",
    ),
)


def provider_only_dependency(source_fragment: str) -> UnsupportedCapabilityDependency | None:
    normalized = " ".join(source_fragment.split())[:5000]
    for category, pattern, explanation in _PROVIDER_ONLY_PATTERNS:
        match = pattern.search(normalized)
        if match is not None:
            return UnsupportedCapabilityDependency(
                category=category,
                matched_term=match.group(0),
                explanation=explanation,
            )
    return None


def require_ohlcv_computable_scope(source_fragment: str) -> None:
    dependency = provider_only_dependency(source_fragment)
    if dependency is not None:
        raise CapabilityExtensionScopeError(dependency)
