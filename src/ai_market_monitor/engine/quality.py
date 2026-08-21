from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_market_monitor.engine.data_freshness import freshness_from_proof


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    name: str
    score: float
    maximum: float
    status: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 3),
            "maximum": round(self.maximum, 3),
            "status": self.status,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class DeterministicScore:
    score: float
    grade: str
    factors: list[ScoreFactor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(max(0, min(100, self.score)), 3),
            "grade": self.grade,
            "factors": [factor.to_dict() for factor in self.factors],
            "warnings": list(self.warnings),
            "deterministic": True,
        }


def alert_trust_score_from_proof(proof: dict[str, Any]) -> dict[str, Any]:
    """Score proof quality using only deterministic receipt fields."""

    conditions = [item for item in proof.get("conditions", []) if isinstance(item, dict)]
    mandatory = [item for item in conditions if bool(item.get("mandatory", item.get("blocking")))]
    optional = [
        item for item in conditions if not bool(item.get("mandatory", item.get("blocking")))
    ]
    factors = [
        _condition_factor("Mandatory rule pass rate", mandatory, 35),
        _condition_factor("Optional confirmations", optional, 10, empty_score=10),
        _data_freshness_factor(proof, 15),
        _candle_factor(proof, 10),
        _liquidity_factor(proof, 15),
        _risk_factor(proof, 15),
    ]
    warnings = [str(item) for item in proof.get("reliability_warnings", []) if item]
    warning_penalty = min(12, 3 * len(warnings))
    raw_score = sum(factor.score for factor in factors) - warning_penalty
    return DeterministicScore(
        score=max(0, min(100, raw_score)),
        grade=_grade(raw_score),
        factors=factors,
        warnings=warnings,
    ).to_dict()


def market_coverage_score(
    *,
    symbols_eligible: int,
    symbols_scanned: int,
    symbols_skipped: int = 0,
    data_failures: int = 0,
    timeframes_required: int = 1,
    timeframes_covered: int = 1,
    last_scan_at: datetime | None = None,
) -> dict[str, Any]:
    eligible = max(0, symbols_eligible)
    scanned = max(0, min(symbols_scanned, eligible)) if eligible else 0
    skipped = max(0, symbols_skipped)
    failures = max(0, data_failures)
    coverage_ratio = (scanned / eligible) if eligible else 0
    data_quality_ratio = max(0, scanned - failures) / scanned if scanned else 0
    timeframe_ratio = 0.0
    if scanned:
        timeframe_ratio = (
            max(0, min(timeframes_covered, timeframes_required)) / timeframes_required
            if timeframes_required
            else 1
        )
    score = (coverage_ratio * 70) + (data_quality_ratio * 20) + (timeframe_ratio * 10)
    stale = False
    if last_scan_at is not None:
        aware = last_scan_at if last_scan_at.tzinfo else last_scan_at.replace(tzinfo=UTC)
        stale = (datetime.now(UTC) - aware).total_seconds() > 3600
        if stale:
            score = max(0, score - 10)
    factors = [
        ScoreFactor(
            "Symbol coverage",
            coverage_ratio * 70,
            70,
            "passed" if coverage_ratio >= 0.9 else "partial" if scanned else "missing",
            f"{scanned} of {eligible} eligible symbols scanned.",
        ),
        ScoreFactor(
            "Data quality",
            data_quality_ratio * 20,
            20,
            "passed" if failures == 0 and scanned else "partial" if scanned else "missing",
            f"{failures} data failure(s); {skipped} skipped symbol(s).",
        ),
        ScoreFactor(
            "Timeframe coverage",
            timeframe_ratio * 10,
            10,
            "passed" if timeframe_ratio >= 1 else "partial",
            f"{timeframes_covered} of {timeframes_required} timeframe(s) covered.",
        ),
    ]
    warnings = ["Last scan is older than one hour."] if stale else []
    payload = DeterministicScore(
        score=score,
        grade=_grade(score),
        factors=factors,
        warnings=warnings,
    ).to_dict()
    return payload | {
        "symbols_eligible": eligible,
        "symbols_scanned": scanned,
        "symbols_skipped": skipped,
        "data_failures": failures,
        "coverage_percentage": round(coverage_ratio * 100, 3) if eligible else 0,
        "last_scan_at": last_scan_at.isoformat() if last_scan_at else None,
    }


def _condition_factor(
    name: str,
    conditions: list[dict[str, Any]],
    maximum: float,
    *,
    empty_score: float = 0,
) -> ScoreFactor:
    if not conditions:
        return ScoreFactor(
            name, empty_score, maximum, "not_applicable", "No conditions in this group."
        )
    passed = sum(1 for item in conditions if item.get("state") == "passed")
    ratio = passed / len(conditions)
    return ScoreFactor(
        name,
        ratio * maximum,
        maximum,
        "passed" if ratio >= 1 else "partial" if ratio > 0 else "failed",
        f"{passed} of {len(conditions)} condition(s) passed.",
    )


def _data_freshness_factor(proof: dict[str, Any], maximum: float) -> ScoreFactor:
    """Was this alert built on the newest candle, or on one the market had moved past?

    Graded in candles, never in bare milliseconds. The thresholds here used to be 5 s,
    60 s and 300 s of absolute age, which is a scale for tick data: a five-minute monitor
    reading a candle the instant it closed still scored "stale", and an hourly one scored
    zero every single time. See :mod:`ai_market_monitor.engine.data_freshness`.
    """

    freshness = freshness_from_proof(proof)
    return ScoreFactor(
        "Data freshness",
        maximum * freshness.ratio,
        maximum,
        freshness.status,
        freshness.explain(),
    )


def _candle_factor(proof: dict[str, Any], maximum: float) -> ScoreFactor:
    closed = proof.get("candle_closed")
    if closed is True:
        return ScoreFactor(
            "Candle completeness", maximum, maximum, "passed", "Signal candle is closed."
        )
    if closed is False:
        return ScoreFactor(
            "Candle completeness",
            maximum * 0.5,
            maximum,
            "intrabar",
            "Signal came from an active intrabar candle.",
        )
    return ScoreFactor(
        "Candle completeness", maximum * 0.7, maximum, "unknown", "Candle state unavailable."
    )


def _liquidity_factor(proof: dict[str, Any], maximum: float) -> ScoreFactor:
    liquidity = proof.get("liquidity_information")
    metrics = liquidity if isinstance(liquidity, dict) else {}
    spread = proof.get("spread_bps", metrics.get("spread_bps"))
    quote_volume = metrics.get("quote_volume_24h")
    score = maximum
    notes: list[str] = []
    if isinstance(spread, int | float):
        notes.append(f"spread {spread} bps")
        if spread > 50:
            score -= maximum * 0.5
        elif spread > 25:
            score -= maximum * 0.2
    else:
        score -= maximum * 0.15
        notes.append("spread unavailable")
    if isinstance(quote_volume, int | float):
        notes.append(f"24h quote volume {quote_volume:g}")
        if quote_volume <= 0:
            score -= maximum * 0.5
    else:
        score -= maximum * 0.15
        notes.append("24h quote volume unavailable")
    score = max(0, score)
    return ScoreFactor(
        "Market liquidity",
        score,
        maximum,
        "passed" if score >= maximum * 0.85 else "partial" if score else "failed",
        "; ".join(notes),
    )


def _risk_factor(proof: dict[str, Any], maximum: float) -> ScoreFactor:
    risk_validation = proof.get("risk_validation")
    risk = proof.get("risk_calculation")
    risk_ok = isinstance(risk_validation, dict) and risk_validation.get("state") == "passed"
    risk_available = isinstance(risk, dict) and risk.get("stop_price") is not None
    reward = proof.get("reward_to_risk")
    if risk_ok and risk_available and isinstance(reward, int | float) and reward > 0:
        return ScoreFactor(
            "Risk validity", maximum, maximum, "passed", f"Reward-to-risk: {reward:g}."
        )
    if risk_available:
        return ScoreFactor(
            "Risk validity",
            maximum * 0.55,
            maximum,
            "partial",
            "Risk calculation exists, but validation is incomplete.",
        )
    return ScoreFactor("Risk validity", 0, maximum, "failed", "Risk calculation unavailable.")


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 55:
        return "D"
    return "F"
