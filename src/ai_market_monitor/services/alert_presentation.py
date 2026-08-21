from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_market_monitor.core.dashboard_paths import (
    COMPLIANCE_CHANGES_PATH,
    LIFECYCLES_PATH,
    OPPORTUNITIES_PATH,
)
from ai_market_monitor.db.models import Alert
from ai_market_monitor.db.models.enums import AlertType
from ai_market_monitor.services.product_language import freshness_words as _freshness_words
from ai_market_monitor.services.sharia_screening import sharia_evidence_from_proof


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


#: What the buttons on an alert are called, in the words a person reads.
#:
#: One owner, for two reasons. They were written twice — here and again in
#: `telegram/service.py` — so the same button had two definitions waiting to drift. And
#: what they said was wrong for this product's readers: "🔄 View lifecycle" carries an
#: emoji the brand rules exclude and "lifecycle" is a word from inside the machine.
#: Email made that visible, because these labels become the buttons in the email too.
#:
#: The keys beside them (`dashboard_lifecycle:`, `mute_symbol:`) are what the Telegram
#: callback handler matches on. Those are machine identifiers and must never change —
#: renaming one would break every button already sitting in somebody's chat history.
ACTION_LABELS = {
    "opportunity": "See what happened",
    "dashboard": "Open dashboard",
    "mute": "Stop messages about this coin",
    "subscription": "Open your account",
    "evidence": "See the evidence",
    "affected": "Which of my lists this touches",
}


@dataclass(frozen=True, slots=True)
class AlertActionPresentation:
    label: str
    action_id: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class AlertFactPresentation:
    """One thing this alert states.

    Three parts, and the split matters:

    * ``key`` is what the fact *is*. Stable, never shown to anybody, and how a channel
      picks out the one fact it needs without matching on wording.
    * ``label`` is the plain-words name a person reads.
    * ``value`` is what the alert claims.

    The value is the claim, and it is written once. The label is allowed to differ per
    channel — the Telegram message keeps the terse wording its readers already know,
    while an email written for a beginner says "How fresh the numbers were" rather than
    "Data freshness". What must never differ is the number beside it.
    """

    key: str
    label: str
    value: str


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
    sharia_status: str | None = None
    sharia_methodology: str | None = None
    sharia_reviewed_at: str | None = None
    sharia_passport_url: str | None = None
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
                strategy_id=str(proof.get("strategy_id")) if proof.get("strategy_id") else None,
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
                        ACTION_LABELS["subscription"],
                        f"dashboard:{alert.id}",
                        dashboard_url,
                    )
                ]
                if dashboard_url
                else [],
            )
        if alert.alert_type == AlertType.COMPLIANCE:
            base = (public_base_url or "").rstrip("/")
            asset = str(proof.get("canonical_asset") or "Asset")
            passport_url = f"{base}/dashboard/market/{asset.lower()}" if base else None
            activity_url = (
                f"{base}{COMPLIANCE_CHANGES_PATH}" if base else None
            )
            return cls(
                alert_id=str(alert.id),
                strategy_id=str(proof.get("strategy_id")) if proof.get("strategy_id") else None,
                strategy_version=str(proof.get("methodology_version") or "n/a"),
                alert_type=alert.alert_type.value,
                title=alert.title,
                body=alert.body,
                symbol=asset,
                direction="screening status",
                strategy=str(proof.get("strategy_name") or "Halal Assets"),
                exchange="spot",
                timeframe="status change",
                setup_score=None,
                passed_conditions=[],
                missing_conditions=[],
                entry_zone="n/a",
                stop="n/a",
                stop_distance="n/a",
                targets=[],
                reward_to_risk="n/a",
                data_freshness=str(proof.get("reviewed_at") or "n/a"),
                trust_score=None,
                trust_grade="evidence-backed review",
                trust_summary="Status comes from the recorded methodology review workflow.",
                setup_age="n/a",
                lifecycle_state=str(proof.get("new_status") or "updated"),
                chart_reference=None,
                proof_url=passport_url,
                dashboard_url=activity_url,
                actions=[
                    *(
                        [
                            AlertActionPresentation(
                                ACTION_LABELS["evidence"],
                                f"sharia_passport:{asset}",
                                passport_url,
                            )
                        ]
                        if passport_url
                        else []
                    ),
                    *(
                        [
                            AlertActionPresentation(
                                ACTION_LABELS["affected"],
                                f"compliance_activity:{alert.id}",
                                activity_url,
                            )
                        ]
                        if activity_url
                        else []
                    ),
                ],
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
        proof_url = f"{base}{LIFECYCLES_PATH}" if base else None
        dashboard_url = f"{base}{OPPORTUNITIES_PATH}" if base else None
        screening = sharia_evidence_from_proof(proof)
        raw_screening_asset = screening.get("asset")
        screening_asset = (
            raw_screening_asset if isinstance(raw_screening_asset, dict) else {}
        )
        methodology_code = screening.get("methodology_code")
        methodology_version = screening.get("methodology_version")
        methodology_parts = [
            str(value) for value in (methodology_code, methodology_version) if value
        ]
        sharia_methodology = " v".join(methodology_parts) if methodology_parts else None
        sharia_status = screening_asset.get("status") or screening.get("status")
        sharia_reviewed_at = screening_asset.get("reviewed_at")
        screening_asset_code = screening_asset.get("canonical_asset") or proof.get("symbol")
        sharia_passport_url = (
            f"{base}/dashboard/market/{str(screening_asset_code).partition('/')[0].lower()}"
            if base and screening_asset_code and sharia_status
            else None
        )
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
        market_data_timestamp = _parse_timestamp(proof.get("market_data_timestamp"))
        created_at = _aware(alert.created_at)
        setup_age = "n/a"
        if market_data_timestamp is not None:
            setup_age = _duration(created_at - market_data_timestamp)
        actions = [
            AlertActionPresentation(
                ACTION_LABELS["opportunity"], f"dashboard_lifecycle:{alert.id}", dashboard_url
            ),
            AlertActionPresentation(
                ACTION_LABELS["dashboard"], f"dashboard:{alert.id}", dashboard_url
            ),
            AlertActionPresentation(ACTION_LABELS["mute"], f"mute_symbol:{alert.id}"),
        ]
        return cls(
            alert_id=str(alert.id),
            strategy_id=str(proof.get("strategy_id")) if proof.get("strategy_id") else None,
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
            data_freshness=_freshness_words(proof),
            trust_score=float(trust_score) if isinstance(trust_score, int | float) else None,
            trust_grade=str(trust_payload.get("grade") or "n/a"),
            trust_summary=trust_summary,
            setup_age=setup_age,
            lifecycle_state=str(proof.get("setup_state") or "n/a"),
            chart_reference=alert.chart_snapshot_url or proof.get("chart_reference"),
            proof_url=proof_url,
            dashboard_url=dashboard_url,
            sharia_status=str(sharia_status) if sharia_status else None,
            sharia_methodology=sharia_methodology,
            sharia_reviewed_at=(
                str(sharia_reviewed_at) if sharia_reviewed_at else None
            ),
            sharia_passport_url=sharia_passport_url,
            actions=actions,
        )

    # ── What this alert says, once, for every channel ─────────────────────────
    #
    # Telegram and email describe the same alert. They lay it out differently — one is
    # plain text in a chat, the other is a page — but what the alert *claims* must be
    # written in exactly one place, or the two channels eventually tell one person two
    # different things about one event and neither is obviously wrong.
    #
    # So the claims live in the three methods below, and each channel only decides how
    # to arrange them. A channel that built its own line out of `proof_receipt` would be
    # a second opinion about the same evidence.

    def key_facts(self) -> list[AlertFactPresentation]:
        """What was monitored, and what the platform read."""

        score = f"{self.setup_score:.0f}%" if self.setup_score is not None else "n/a"
        trust = self.trust_grade
        if self.trust_score is not None:
            trust = f"{trust} ({self.trust_score:.0f}%)"
        return [
            AlertFactPresentation("watchlist", "Watchlist", self.strategy),
            AlertFactPresentation("version", "Which version of it", self.strategy_version or "n/a"),
            AlertFactPresentation(
                "market", "Exchange and candle size", f"{self.exchange} {self.timeframe}"
            ),
            AlertFactPresentation("completion", "How much of your list was met", score),
            AlertFactPresentation("freshness", "How fresh the numbers were", self.data_freshness),
            AlertFactPresentation("trust", "How reliable this reading is", trust),
            AlertFactPresentation("age", "How old this setup is", self.setup_age),
            # The stored state, not a translation of it. What this state *means* in
            # plain words is owned by `product_language.opportunity_presentation`, and
            # a channel writing for beginners resolves it there rather than this
            # module growing a second set of words for the same states.
            AlertFactPresentation("stage", "Stage it reached", self.lifecycle_state),
        ]

    def trade_context_facts(self) -> list[AlertFactPresentation]:
        """The entry, stop and targets a person set for themselves.

        Empty when they set none. It is never filled in with a default: an invented
        stop price is a number somebody could act on that nobody chose.
        """

        if not self.has_trade_context:
            return []
        return [
            AlertFactPresentation("entry_zone", "Entry zone", self.entry_zone),
            AlertFactPresentation("stop", "Stop", f"{self.stop} ({self.stop_distance})"),
            AlertFactPresentation("targets", "Targets", ", ".join(self.targets) or "n/a"),
            AlertFactPresentation("reward_to_risk", "R:R", self.reward_to_risk),
        ]

    def screening_facts(self) -> list[AlertFactPresentation]:
        """The stored screening status this alert was evaluated against.

        Empty when the proof carries none. Never inferred, never softened, and never
        restated as a conclusion of its own — it is a record of what was published at
        the moment the alert was raised.
        """

        if not self.sharia_status:
            return []
        return [
            AlertFactPresentation(
                "screening_status",
                "Screening status at evaluation",
                self.sharia_status.replace("_", " "),
            ),
            AlertFactPresentation(
                "methodology", "Methodology", self.sharia_methodology or "recorded in proof"
            ),
            AlertFactPresentation(
                "reviewed_at", "Last reviewed", self.sharia_reviewed_at or "not recorded"
            ),
            AlertFactPresentation(
                "passport_url",
                "Evidence Passport",
                self.sharia_passport_url or "available in dashboard proof",
            ),
        ]

    @property
    def is_account_notice(self) -> bool:
        """True for the alerts that are about the account rather than about a market."""

        return self.alert_type in {AlertType.TRIAL.value, AlertType.COMPLIANCE.value}

    #: Said when somebody set no entry, stop or target for themselves. It is a fact
    #: about the monitor, so it is written here rather than by each channel.
    RESEARCH_ONLY_NOTE = (
        "Research-only monitor: no user-defined entry, stop, target, or R:R context."
    )

    def telegram_text(self) -> str:
        if self.is_account_notice:
            return f"{self.title}\n\n{self.body}"
        passed = "\n".join(condition.line() for condition in self.passed_conditions) or "None"
        missing = "\n".join(condition.line() for condition in self.missing_conditions) or "None"
        trade_context = (
            "User-defined trade context:\n"
            + "".join(f"{fact.label}: {fact.value}\n" for fact in self.trade_context_facts())
            if self.has_trade_context
            else f"{self.RESEARCH_ONLY_NOTE}\n"
        )
        screening_context = "".join(
            f"{fact.label}: {fact.value}\n" for fact in self.screening_facts()
        )
        # Picked out by `key`, not by wording. This message keeps the terse labels its
        # readers already know, and the beginner-facing labels can be reworded for email
        # without silently rewriting a chat message somebody is used to reading.
        fact = {item.key: item.value for item in self.key_facts()}
        return (
            f"Research match confirmed: {self.symbol}\n"
            f"Strategy: {fact['watchlist']}\n"
            f"Strategy version: {fact['version']}\n"
            f"Exchange/timeframe: {fact['market']}\n"
            f"Required completion: {fact['completion']}\n"
            f"{trade_context}"
            f"{screening_context}"
            f"Data freshness: {fact['freshness']}\n"
            f"Alert trust: {fact['trust']}\n"
            f"Setup age: {fact['age']}\n"
            f"Lifecycle: {fact['stage']}\n\n"
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
