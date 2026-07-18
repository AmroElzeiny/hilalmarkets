import html

from ai_market_monitor.engine.models import EvaluationResult
from ai_market_monitor.telegram.types import NearMissListItem


def escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def render_near_miss_item(item: NearMissListItem, index: int) -> str:
    badge = " - one condition left" if len(item.missing) == 1 and item.score < 100 else ""
    return f"{index}. {escape(item.symbol)} - {item.score:.0f}% ({escape(item.trend)}){badge}"


def render_near_miss_list(items: list[NearMissListItem]) -> str:
    if not items:
        return "No Near-Miss setups matched that threshold."
    return "Near-Miss Radar\n\n" + "\n".join(
        render_near_miss_item(item, index + 1) for index, item in enumerate(items)
    )


def render_near_miss_detail(item: NearMissListItem) -> str:
    passed = "\n".join(f"[PASS] {escape(rule)}" for rule in item.passed) or "None yet"
    missing = "\n".join(f"[WAIT] {escape(rule)}" for rule in item.missing) or "None"
    chart = f"\nChart: {escape(item.chart_reference)}" if item.chart_reference else ""
    badge = (
        "\nBadge: One condition remaining" if len(item.missing) == 1 and item.score < 100 else ""
    )
    return (
        f"{escape(item.symbol)} - {item.score:.0f}% complete\n\n"
        f"Passed:\n{passed}\n\nMissing:\n{missing}\n\n"
        f"Status: {escape(item.trend)}{badge}{chart}"
    )


def render_confirmed_alert(result: EvaluationResult) -> str:
    proof = result.proof_receipt()
    trust = proof.get("alert_trust_score") or {}
    trust_score = trust.get("score")
    trust_label = (
        f"{trust.get('grade', 'n/a')} ({float(trust_score):.0f}%)"
        if isinstance(trust_score, (int, float))
        else str(trust.get("grade", "n/a"))
    )
    risk = result.risk
    conditions = "\n".join(
        f"{'[PASS]' if condition.passed else '[WAIT]'} {escape(condition.name)}"
        for condition in result.conditions
    )
    if risk is None:
        risk_summary = (
            "Research-only monitor: no user-defined entry, stop, target, or R:R context.\n"
        )
    else:
        target_prices: list[float] = []
        for target in risk.targets:
            price = target.get("price")
            if isinstance(price, (int, float)):
                target_prices.append(float(price))
        risk_summary = (
            "User-defined trade context:\n"
            f"Entry zone: {risk.entry_zone_low:.6g} - {risk.entry_zone_high:.6g}\n"
            f"Stop: {risk.stop_price:.6g} ({risk.stop_distance_percent:.2f}%)\n"
            f"Targets: {', '.join(str(round(price, 6)) for price in target_prices)}\n"
            f"R:R: {risk.reward_to_risk:.2f}\n"
        )
    required_completion_value = proof.get(
        "required_completion_percent", proof["setup_completion_score"]
    )
    required_completion = (
        float(required_completion_value)
        if isinstance(required_completion_value, (int, float, str))
        else 0.0
    )
    return (
        f"Research match confirmed: {escape(result.symbol)}\n"
        f"Strategy: {escape(result.strategy_name)} v{escape(result.strategy_version)}\n"
        f"Exchange: {escape(result.exchange)} | Timeframe: {escape(result.timeframe)}\n"
        f"Required completion: {required_completion:.0f}%\n"
        f"Alert trust: {escape(trust_label)}\n"
        f"{risk_summary}\n"
        f"Proof summary:\n{conditions}"
    )


def render_lifecycle_update(result: EvaluationResult) -> str:
    state = result.setup_state.value if result.setup_state else "not_created"
    return (
        f"Monitor update: {escape(result.symbol)}\n"
        f"State: {escape(state)}\n"
        f"Reason: {escape(result.outcome.value)}\n"
        f"Completion: {result.near_miss.current_score:.0f}%"
    )
