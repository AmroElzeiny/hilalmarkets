from dataclasses import dataclass
from typing import Any

from ai_market_monitor.core.config import WHATSAPP_TEMPLATE_EVENTS, Settings
from ai_market_monitor.db.models.enums import AlertType
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.whatsapp.types import (
    WhatsAppSessionText,
    WhatsAppTemplateComponent,
    WhatsAppTemplateMessage,
    WhatsAppTemplateParameter,
)

WHATSAPP_OPPORTUNITY_EVENTS = frozenset(
    {"lifecycle_update", "confirmed_research_event"}
)


@dataclass(frozen=True, slots=True)
class WhatsAppTemplateSpec:
    event_type: str
    category: str
    variables: tuple[str, ...]


TEMPLATE_SPECS: dict[str, WhatsAppTemplateSpec] = {
    "connection_confirmation": WhatsAppTemplateSpec(
        "connection_confirmation", "account", ("display_name", "settings_url")
    ),
    "connection_test": WhatsAppTemplateSpec(
        "connection_test", "account", ("display_name", "settings_url")
    ),
    "account_notice": WhatsAppTemplateSpec(
        "account_notice", "account", ("notice_title", "dashboard_url")
    ),
    "trial_update": WhatsAppTemplateSpec(
        "trial_update", "subscription", ("trial_state", "billing_url")
    ),
    "subscription_update": WhatsAppTemplateSpec(
        "subscription_update", "subscription", ("subscription_state", "billing_url")
    ),
    "compliance_change": WhatsAppTemplateSpec(
        "compliance_change",
        "compliance",
        ("asset", "status", "methodology", "passport_url"),
    ),
    "evidence_update": WhatsAppTemplateSpec(
        "evidence_update", "evidence", ("asset", "evidence_state", "passport_url")
    ),
    "watchlist_paused": WhatsAppTemplateSpec(
        "watchlist_paused",
        "watchlist_health",
        ("watchlist_name", "reason", "dashboard_url"),
    ),
    "integration_failure": WhatsAppTemplateSpec(
        "integration_failure", "operational", ("channel", "reason", "settings_url")
    ),
    "lifecycle_update": WhatsAppTemplateSpec(
        "lifecycle_update",
        "lifecycle",
        ("symbol", "state", "monitor_name", "lifecycle_url"),
    ),
    "confirmed_research_event": WhatsAppTemplateSpec(
        "confirmed_research_event",
        "opportunity",
        ("symbol", "timeframe", "monitor_name", "proof_url"),
    ),
}

if frozenset(TEMPLATE_SPECS) != WHATSAPP_TEMPLATE_EVENTS:
    raise RuntimeError("WhatsApp template registry and configuration keys are out of sync")


class WhatsAppTemplateRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings

    def template_name(self, event_type: str, locale: str) -> str | None:
        if event_type not in TEMPLATE_SPECS:
            return None
        configured = self.settings.whatsapp_template_names.get(event_type)
        if isinstance(configured, str):
            return configured
        if not isinstance(configured, dict):
            return None
        language = locale.partition("_")[0]
        return (
            configured.get(locale)
            or configured.get(language)
            or configured.get(self.settings.whatsapp_default_language)
            or configured.get("default")
        )

    def build(
        self,
        *,
        event_type: str,
        locale: str,
        to: str,
        variables: dict[str, Any],
    ) -> WhatsAppTemplateMessage | None:
        spec = TEMPLATE_SPECS.get(event_type)
        name = self.template_name(event_type, locale)
        if spec is None or name is None:
            return None
        values: list[WhatsAppTemplateParameter] = []
        for key in spec.variables:
            value = _bounded_template_value(variables.get(key))
            if value is None:
                return None
            values.append(WhatsAppTemplateParameter(text=value))
        components = (
            [WhatsAppTemplateComponent(type="body", parameters=values)] if values else []
        )
        return WhatsAppTemplateMessage(
            to=to,
            name=name,
            language=locale,
            components=components,
        )


@dataclass(frozen=True, slots=True)
class WhatsAppRenderedAlert:
    event_type: str
    category: str
    session_body: str
    template_variables: dict[str, str]


class WhatsAppAlertRenderer:
    @staticmethod
    def render(
        presentation: AlertPresentation,
        *,
        dashboard_url: str,
    ) -> WhatsAppRenderedAlert:
        event_type = _event_type(presentation.alert_type)
        category = TEMPLATE_SPECS[event_type].category
        if event_type == "compliance_change":
            status = _plain(presentation.lifecycle_state)
            methodology = _plain(presentation.sharia_methodology or "Recorded in Passport")
            passport_url = presentation.sharia_passport_url or dashboard_url
            body = (
                "Shariah screening update\n"
                f"Asset: {_plain(presentation.symbol)}\n"
                f"Status: {status}\n"
                f"Methodology: {methodology}\n"
                f"Review the evidence: {passport_url}"
            )
            variables = {
                "asset": _plain(presentation.symbol),
                "status": status,
                "methodology": methodology,
                "passport_url": passport_url,
            }
        elif event_type == "trial_update":
            state = _plain(presentation.lifecycle_state)
            body = (
                "Hilal Markets account update\n"
                f"Trial status: {state}\n"
                f"Review billing and limits: {dashboard_url}"
            )
            variables = {"trial_state": state, "billing_url": dashboard_url}
        elif event_type == "integration_failure":
            reason = _plain(presentation.title)
            body = (
                "Hilal Markets delivery update\n"
                f"Status: {reason}\n"
                f"Review integrations: {dashboard_url}"
            )
            variables = {
                "channel": "Hilal Markets",
                "reason": reason,
                "settings_url": dashboard_url,
            }
        elif event_type == "confirmed_research_event":
            passed = len(presentation.passed_conditions)
            total = passed + len(presentation.missing_conditions)
            sharia_line = _sharia_line(presentation)
            body = (
                "Research monitor update\n"
                f"{_plain(presentation.symbol)} | {_plain(presentation.timeframe)}\n"
                f"Monitor: {_plain(presentation.strategy)}\n"
                f"Required conditions: {passed}/{total} passed\n"
                f"{sharia_line}"
                f"Review sealed evidence: {dashboard_url}\n"
                "Decision support only. No trade execution."
            )
            variables = {
                "symbol": _plain(presentation.symbol),
                "timeframe": _plain(presentation.timeframe),
                "monitor_name": _plain(presentation.strategy),
                "proof_url": dashboard_url,
            }
        else:
            state = _plain(presentation.lifecycle_state)
            passed = len(presentation.passed_conditions)
            total = passed + len(presentation.missing_conditions)
            body = (
                "Monitor lifecycle update\n"
                f"{_plain(presentation.symbol)} | {state}\n"
                f"Monitor: {_plain(presentation.strategy)}\n"
                f"Conditions: {passed}/{total} passed\n"
                f"Review the lifecycle: {dashboard_url}\n"
                "Decision support only. No trade execution."
            )
            variables = {
                "symbol": _plain(presentation.symbol),
                "state": state,
                "monitor_name": _plain(presentation.strategy),
                "lifecycle_url": dashboard_url,
            }
        return WhatsAppRenderedAlert(
            event_type=event_type,
            category=category,
            session_body=body[:4096],
            template_variables=variables,
        )

    @staticmethod
    def session_message(to: str, body: str) -> WhatsAppSessionText:
        return WhatsAppSessionText(to=to, body=body[:4096], preview_url=False)


def _event_type(alert_type: str) -> str:
    if alert_type == AlertType.TRIAL.value:
        return "trial_update"
    if alert_type == AlertType.COMPLIANCE.value:
        return "compliance_change"
    if alert_type == AlertType.FAILURE.value:
        return "integration_failure"
    if alert_type == AlertType.CONFIRMED.value:
        return "confirmed_research_event"
    return "lifecycle_update"


def _plain(value: Any) -> str:
    normalized = " ".join(str(value or "Not recorded").split())
    return normalized[:240]


def _bounded_template_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized[:1024] if normalized else None


def _sharia_line(presentation: AlertPresentation) -> str:
    if not presentation.sharia_status:
        return ""
    status = _plain(presentation.sharia_status).replace("_", " ")
    freshness = _plain(presentation.sharia_reviewed_at or "review date in Passport")
    return f"Shariah status: {status} | Evidence: {freshness}\n"
