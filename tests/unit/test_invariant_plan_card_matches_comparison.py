"""Pricing-card claims stay inside the extended comparison table."""

from __future__ import annotations

import pytest

from ai_market_monitor.core.plans import (
    NOT_INCLUDED_WORD,
    PLAN_COMPARISON_COLUMNS,
    PUBLIC_PLAN_COMPARISON,
    PUBLIC_PLAN_PRESENTATIONS,
)


def _comparison_value(row_name: str, code: str) -> str:
    row = next(row for row in PUBLIC_PLAN_COMPARISON if row[0] == row_name)
    return row[1 + PLAN_COMPARISON_COLUMNS.index(code)]


@pytest.mark.parametrize(
    ("code", "highlight"),
    (
        ("demo", "Halal assets, passports, and monitors"),
        ("trader", "AI assistant with Plus limits"),
        ("pro", "Max limits across features"),
    ),
)
def test_each_plan_highlights_the_requested_comparison_claim(code: str, highlight: str) -> None:
    presentation = PUBLIC_PLAN_PRESENTATIONS[code]
    assert presentation.highlighted_feature == highlight
    assert highlight in presentation.visible_features


def test_composite_highlights_are_supported_by_the_table() -> None:
    assert _comparison_value("Halal Assets market", "demo") == "Included"
    assert _comparison_value("Evidence Passports", "demo") == "Full"
    assert _comparison_value("Active market monitors", "demo") == "1"

    assert _comparison_value("AI assistant", "pro") == "Max"
    assert _comparison_value("Strategy approvals", "pro") == "Unlimited"
    assert _comparison_value("Active market monitors", "pro") == "10"
    assert _comparison_value("Monitor notifications", "pro") == "Unlimited"


@pytest.mark.parametrize(
    ("code", "feature", "row_name", "table_value"),
    (
        ("demo", "Full Evidence Passports", "Evidence Passports", "Full"),
        (
            "demo",
            "Full methodology reports: reasons, sources, versions, and review dates",
            "Methodology reports",
            "Full",
        ),
        ("demo", "Favorite coins", "Favorite coins", "Included"),
        (
            "demo",
            "In-app, Telegram, and email notifications",
            "Halal status-change alerts",
            "In-app + Telegram + Email",
        ),
        ("trader", "AI assistant with Plus limits", "AI assistant", "Extended"),
        ("trader", "Full condition-level proof", "Condition proof", "Full"),
        ("trader", "Full Opportunity Journeys", "Opportunity Journeys", "Full"),
        (
            "trader",
            "Why wasn't I alerted? explanations",
            "Why wasn't I alerted?",
            "Included",
        ),
        ("trader", "Telegram monitor delivery", "Telegram monitor delivery", "Included"),
        ("pro", "AI assistant with Pro limits", "AI assistant", "Max"),
        ("pro", "Full condition-level proof", "Condition proof", "Full"),
        ("pro", "Full Opportunity Journeys", "Opportunity Journeys", "Full"),
        ("pro", "Why wasn't I alerted? explanations", "Why wasn't I alerted?", "Included"),
        ("pro", "Telegram monitor delivery", "Telegram monitor delivery", "Included"),
    ),
)
def test_each_named_card_feature_is_backed_by_a_table_cell(
    code: str,
    feature: str,
    row_name: str,
    table_value: str,
) -> None:
    presentation = PUBLIC_PLAN_PRESENTATIONS[code]
    card_features = presentation.visible_features + presentation.additional_features
    assert feature in card_features
    assert _comparison_value(row_name, code) == table_value
    assert table_value != NOT_INCLUDED_WORD


@pytest.mark.parametrize("code", PLAN_COMPARISON_COLUMNS)
def test_allowance_bullets_repeat_the_table_allowances(code: str) -> None:
    features = PUBLIC_PLAN_PRESENTATIONS[code].visible_features
    approvals = _comparison_value("Strategy approvals", code)
    monitors = _comparison_value("Active market monitors", code)
    notifications = _comparison_value("Monitor notifications", code)

    if approvals == "Unlimited":
        approval_bullet = "Unlimited strategy approvals"
    else:
        count, period = approvals.split(" ", 1)
        noun = "strategy" if count == "1" else "strategies"
        approval_bullet = f"Approve {count} {noun} {period}"
    assert approval_bullet in features
    assert any(feature.startswith(f"{monitors} active market monitor") for feature in features)
    assert any(
        feature == "Unlimited monitor alerts per day"
        if notifications == "Unlimited"
        else all(word in feature for word in notifications.split())
        for feature in features
    )


def test_removed_or_unlisted_claims_are_not_on_any_card() -> None:
    all_features = {
        feature
        for presentation in PUBLIC_PLAN_PRESENTATIONS.values()
        for feature in presentation.visible_features + presentation.additional_features
    }
    assert "AI assistant with Explore limits" not in all_features
    assert "Halal assets, methodologies, and evidence reports" not in all_features
    assert "Describe a market monitor in a prompt and the AI builds it" not in all_features
    assert "Standard email support" not in all_features
    assert "In-app, Telegram, and email monitor alerts" not in all_features


def test_unlimited_pro_alerts_are_a_normal_feature_not_the_highlight() -> None:
    presentation = PUBLIC_PLAN_PRESENTATIONS["pro"]
    assert "Unlimited monitor alerts per day" in presentation.visible_features
    assert presentation.highlighted_feature != "Unlimited monitor alerts per day"
