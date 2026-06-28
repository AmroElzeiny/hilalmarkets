from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_market_monitor.db.models import Alert
from ai_market_monitor.db.models.enums import AlertType


@dataclass(frozen=True, slots=True)
class AlertConditionPresentation:
    name: str
    state: str
    actual_value: Any
    required_value: Any

    @property
    def passed(self) -> bool:
        return self.state in {"passed", "pass"}

    def line(self) -> str:
        status = "[PASS]" if self.passed else "[MISS]"
        return (
            f"{status} {self.name} "
            f"(actual: {self.actual_value if self.actual_value is not None else 'n/a'}, "
            f"required: {self.required_value if self.required_value is not None else 'n/a'})"
        )


@dataclass(frozen=True, slots=True)
class AlertActionPresentation:
    label: str
    action_id: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class AlertPresentation:
    alert_id: str
    strategy_id: str | None
    strategy_version: str | None
    alert_type: str
    title: str
    body: str
    symbol: str
    direction: str
    strategy: str
    exchange: str
    timeframe: str
    setup_score: float | None
    passed_conditions: list[AlertConditionPresentation]
    missing_conditions: list[AlertConditionPresentation]
    entry_zone: str
    stop: str
    stop_distance: str
    targets: list[str]
    reward_to_risk: str
    data_freshness: str
    trust_score: float | None
    trust_grade: str
    trust_summary: str
    setup_age: str
    lifecycle_state: str
    chart_reference: str | None
    proof_url: str | None
    dashboard_url: str | None
    actions: list[AlertActionPresentation] = field(default_factory=list)

    @property
    def has_trade_context(self) -> bool:
        return any(
            [
                self.entry_zone != "n/a",
                self.stop != "n/a",
                self.stop_distance != "n/a",
                bool(self.targets),
                self.reward_to_risk != "n/a",
            ]
        )

    @classmethod
    def from_alert(cls, alert: Alert, *, public_base_url: str | None = None) -> "AlertPresentation":
        proof = alert.proof_receipt or {}
        if alert.alert_type == AlertType.TRIAL:
            base = (public_base_url or "").rstrip("/")
            dashboard_url = f"{base}/dashboard/subscription" if base else None
            return cls(
                alert_id=str(alert.id),
                strategy_id=str(alert.strategy_version_id) if alert.strategy_version_id else None,
                strategy_version=str(proof.get("strategy_version") or "n/a"),
                alert_type=alert.alert_type.value,
                title=alert.title,
                body=alert.body,
                symbol="Account",
                direction="trial",
                strategy="Subscription",
                exchange="n/a",
                timeframe="n/a",
                setup_score=None,
                passed_conditions=[],
                missing_conditions=[],
                entry_zone="n/a",
                stop="n/a",
                stop_distance="n/a",
                targets=[],
                reward_to_risk="n/a",
                data_freshness="n/a",
                trust_score=None,
                trust_grade="n/a",
                trust_summary="Subscription alert; no market proof score.",
                setup_age="n/a",
                lifecycle_state=str(proof.get("trial_status") or "n/a"),
                chart_reference=None,
                proof_url=None,
                dashboard_url=dashboard_url,
                actions=[
                    AlertActionPresentation(
                        "Open Dashboard",
                        f"dashboard:{alert.id}",
                        dashboard_url,
                    )
                ]
                if dashboard_url
                else [],
            )
        conditions = [
            AlertConditionPresentation(
                name=str(condition.get("name") or condition.get("condition_id") or "Condition"),
                state=str(condition.get("state") or "unknown"),
                actual_value=condition.get("actual_value"),
                required_value=condition.get("required_value"),
            )
            for condition in proof.get("conditions", [])
            if isinstance(condition, dict)
        ]
        passed = [condition for condition in conditions if condition.passed]
        missing = [condition for condition in conditions if not condition.passed]
        raw_risk = proof.get("risk_calculation")
        risk: dict[str, Any] = raw_risk if isinstance(raw_risk, dict) else {}
        raw_entry_zone = proof.get("entry_zone")
        entry_zone: dict[str, Any] = raw_entry_zone if isinstance(raw_entry_zone, dict) else {}
        targets = [
            str(target.get("price") or target.get("value") or target)
            if isinstance(target, dict)
            else str(target)
            for target in proof.get("target_levels", [])
        ]
        base = (public_base_url or "").rstrip("/")
        proof_url = f"{base}/dashboard/lifecycles" if base else None
        dashboard_url = f"{base}/dashboard/lifecycles" if base else None
        replay_url = f"{base}/dashboard/lifecycles" if base else None
        improve_url = f"{base}/dashboard/monitors" if base else None
        score = proof.get("setup_completion_score")
        trust = proof.get("alert_trust_score")
        trust_payload: dict[str, Any] = trust if isinstance(trust, dict) else {}
        trust_score = trust_payload.get("score")
        trust_factors = [
            factor
            for factor in trust_payload.get("factors", [])
            if isinstance(factor, dict) and factor.get("status") not in {"passed", "not_applicable"}
        ]
        trust_summary = (
            "; ".join(str(factor.get("explanation")) for factor in trust_factors[:2])
            if trust_factors
            else "All major proof-quality factors are healthy."
        )
        latency = proof.get("data_latency_ms")
        market_data_timestamp = _parse_timestamp(proof.get("market_data_timestamp"))
        created_at = _aware(alert.created_at)
        setup_age = "n/a"
        if market_data_timestamp is not None:
            setup_age = _duration(created_at - market_data_timestamp)
        actions = [
            AlertActionPresentation("View Full Proof", f"proof:{alert.id}", proof_url),
            AlertActionPresentation("Open Chart", f"chart:{alert.id}", alert.chart_snapshot_url),
            AlertActionPresentation("Good Alert", f"feedback:good_alert:{alert.id}"),
            AlertActionPresentation("Too Early", f"feedback:too_early:{alert.id}"),
            AlertActionPresentation("Too Late", f"feedback:too_late:{alert.id}"),
            AlertActionPresentation("False Alert", f"feedback:false_alert:{alert.id}"),
            AlertActionPresentation("Open Replay", f"replay:{alert.id}", replay_url),
            AlertActionPresentation("Improve Monitor", f"improve:{alert.id}", improve_url),
            AlertActionPresentation(
                "Mute Monitor",
                f"mute_strategy:{alert.strategy_version_id}",
            ),
            AlertActionPresentation("Open Dashboard", f"dashboard:{alert.id}", dashboard_url),
        ]
        if alert.alert_type == AlertType.NEAR_MISS:
            actions.insert(
                5,
                AlertActionPresentation("Mute Near-Miss", f"mute_near_miss:{alert.id}"),
            )
        return cls(
            alert_id=str(alert.id),
            strategy_id=str(alert.strategy_version_id) if alert.strategy_version_id else None,
            strategy_version=str(proof.get("strategy_version") or "n/a"),
            alert_type=alert.alert_type.value,
            title=alert.title,
            body=alert.body,
            symbol=str(proof.get("symbol") or "Market"),
            direction=str(proof.get("direction") or "setup"),
            strategy=str(proof.get("strategy_name") or "Unknown strategy"),
            exchange=str(proof.get("exchange") or "unknown"),
            timeframe=str(proof.get("timeframe") or "unknown"),
            setup_score=float(score) if isinstance(score, int | float) else None,
            passed_conditions=passed,
            missing_conditions=missing,
            entry_zone=_entry_zone(entry_zone),
            stop=str(risk.get("stop_price") or proof.get("invalidation_level") or "n/a"),
            stop_distance=(
                f"{proof.get('stop_distance')}%"
                if proof.get("stop_distance") is not None
                else "n/a"
            ),
            targets=targets,
            reward_to_risk=str(proof.get("reward_to_risk") or risk.get("reward_to_risk") or "n/a"),
            data_freshness=f"{latency} ms" if latency is not None else "n/a",
            trust_score=float(trust_score) if isinstance(trust_score, int | float) else None,
            trust_grade=str(trust_payload.get("grade") or "n/a"),
            trust_summary=trust_summary,
            setup_age=setup_age,
            lifecycle_state=str(proof.get("setup_state") or "n/a"),
            chart_reference=alert.chart_snapshot_url or proof.get("chart_reference"),
            proof_url=proof_url,
            dashboard_url=dashboard_url,
            actions=actions,
        )

    def telegram_text(self) -> str:
        if self.alert_type == AlertType.TRIAL.value:
            return f"{self.title}\n\n{self.body}"
        passed = "\n".join(condition.line() for condition in self.passed_conditions) or "None"
        missing = "\n".join(condition.line() for condition in self.missing_conditions) or "None"
        score = f"{self.setup_score:.0f}%" if self.setup_score is not None else "n/a"
        targets = ", ".join(self.targets) or "n/a"
        trade_context = (
            (
                "User-defined trade context:\n"
                f"Entry zone: {self.entry_zone}\n"
                f"Stop: {self.stop} ({self.stop_distance})\n"
                f"Targets: {targets}\n"
                f"R:R: {self.reward_to_risk}\n"
            )
            if self.has_trade_context
            else "Research-only monitor: no user-defined entry, stop, target, or R:R context.\n"
        )
        return (
            f"Research match confirmed: {self.symbol}\n"
            f"Strategy: {self.strategy}\n"
            f"Exchange/timeframe: {self.exchange} {self.timeframe}\n"
            f"Required completion: {score}\n"
            f"{trade_context}"
            f"Data freshness: {self.data_freshness}\n"
            f"Alert trust: {self.trust_grade}"
            f"{f' ({self.trust_score:.0f}%)' if self.trust_score is not None else ''}\n"
            f"Setup age: {self.setup_age}\n"
            f"Lifecycle: {self.lifecycle_state}\n\n"
            f"Passed:\n{passed}\n\nMissing:\n{missing}"
        )


def _entry_zone(entry_zone: dict[str, Any]) -> str:
    low = entry_zone.get("low")
    high = entry_zone.get("high")
    if low is None and high is None:
        return "n/a"
    return f"{low} - {high}"


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _duration(delta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"
