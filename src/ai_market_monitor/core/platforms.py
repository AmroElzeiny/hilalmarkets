from dataclasses import dataclass
from enum import StrEnum


class Platform(StrEnum):
    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    DISCORD = "discord"


class PlatformCapability(StrEnum):
    FULL_MONITOR_MANAGEMENT = "full_monitor_management"
    QUICK_MONITOR_CREATE = "quick_monitor_create"
    ALERT_DELIVERY = "alert_delivery"
    QUICK_SCAN = "quick_scan"
    FULL_SCAN_RESULTS = "full_scan_results"
    FULL_BILLING = "full_billing"
    BILLING_STATUS = "billing_status"
    FULL_ANALYTICS = "full_analytics"
    PROOF_VIEWER = "proof_viewer"
    PROOF_SUMMARY = "proof_summary"
    SUPPORT_TICKETS = "support_tickets"
    COMMUNITY_DELIVERY = "community_delivery"
    ROLE_SYNC = "role_sync"
    ADMIN_CONTROLS = "admin_controls"


@dataclass(frozen=True, slots=True)
class PlatformCapabilityRule:
    platform: Platform
    capability: PlatformCapability
    enabled: bool
    handoff_platform: Platform | None = None
    note: str = ""


PLATFORM_CAPABILITY_MATRIX: dict[Platform, dict[PlatformCapability, PlatformCapabilityRule]] = {
    Platform.DASHBOARD: {
        capability: PlatformCapabilityRule(
            Platform.DASHBOARD,
            capability,
            True,
            None,
            "Dashboard is the control center and billing source of truth.",
        )
        for capability in PlatformCapability
    },
    Platform.TELEGRAM: {
        PlatformCapability.QUICK_MONITOR_CREATE: PlatformCapabilityRule(
            Platform.TELEGRAM,
            PlatformCapability.QUICK_MONITOR_CREATE,
            True,
            Platform.DASHBOARD,
            "Simple monitor creation only; advanced editing opens Dashboard.",
        ),
        PlatformCapability.ALERT_DELIVERY: PlatformCapabilityRule(
            Platform.TELEGRAM, PlatformCapability.ALERT_DELIVERY, True
        ),
        PlatformCapability.QUICK_SCAN: PlatformCapabilityRule(
            Platform.TELEGRAM,
            PlatformCapability.QUICK_SCAN,
            True,
            Platform.DASHBOARD,
            "Quota and detailed results are owned by Dashboard/API.",
        ),
        PlatformCapability.BILLING_STATUS: PlatformCapabilityRule(
            Platform.TELEGRAM,
            PlatformCapability.BILLING_STATUS,
            True,
            Platform.DASHBOARD,
            "Telegram may show status and link to Dashboard billing.",
        ),
        PlatformCapability.PROOF_SUMMARY: PlatformCapabilityRule(
            Platform.TELEGRAM,
            PlatformCapability.PROOF_SUMMARY,
            True,
            Platform.DASHBOARD,
            "Full proof viewer opens Dashboard.",
        ),
        PlatformCapability.FULL_BILLING: PlatformCapabilityRule(
            Platform.TELEGRAM,
            PlatformCapability.FULL_BILLING,
            False,
            Platform.DASHBOARD,
            "No payment collection in Telegram.",
        ),
        PlatformCapability.FULL_ANALYTICS: PlatformCapabilityRule(
            Platform.TELEGRAM, PlatformCapability.FULL_ANALYTICS, False, Platform.DASHBOARD
        ),
        PlatformCapability.ADMIN_CONTROLS: PlatformCapabilityRule(
            Platform.TELEGRAM, PlatformCapability.ADMIN_CONTROLS, False, Platform.DASHBOARD
        ),
    },
    Platform.DISCORD: {
        PlatformCapability.ALERT_DELIVERY: PlatformCapabilityRule(
            Platform.DISCORD, PlatformCapability.ALERT_DELIVERY, True
        ),
        PlatformCapability.PROOF_SUMMARY: PlatformCapabilityRule(
            Platform.DISCORD,
            PlatformCapability.PROOF_SUMMARY,
            True,
            Platform.DASHBOARD,
            "Full proof viewer opens Dashboard.",
        ),
        PlatformCapability.COMMUNITY_DELIVERY: PlatformCapabilityRule(
            Platform.DISCORD,
            PlatformCapability.COMMUNITY_DELIVERY,
            True,
            Platform.DASHBOARD,
            "Plan-gated creator/community mode.",
        ),
        PlatformCapability.ROLE_SYNC: PlatformCapabilityRule(
            Platform.DISCORD,
            PlatformCapability.ROLE_SYNC,
            True,
            Platform.DASHBOARD,
            "Billing entitlements remain the source of truth.",
        ),
        PlatformCapability.BILLING_STATUS: PlatformCapabilityRule(
            Platform.DISCORD, PlatformCapability.BILLING_STATUS, True, Platform.DASHBOARD
        ),
        PlatformCapability.FULL_BILLING: PlatformCapabilityRule(
            Platform.DISCORD,
            PlatformCapability.FULL_BILLING,
            False,
            Platform.DASHBOARD,
            "No payment collection in Discord.",
        ),
        PlatformCapability.FULL_MONITOR_MANAGEMENT: PlatformCapabilityRule(
            Platform.DISCORD,
            PlatformCapability.FULL_MONITOR_MANAGEMENT,
            False,
            Platform.DASHBOARD,
        ),
        PlatformCapability.FULL_ANALYTICS: PlatformCapabilityRule(
            Platform.DISCORD, PlatformCapability.FULL_ANALYTICS, False, Platform.DASHBOARD
        ),
        PlatformCapability.ADMIN_CONTROLS: PlatformCapabilityRule(
            Platform.DISCORD, PlatformCapability.ADMIN_CONTROLS, False, Platform.DASHBOARD
        ),
    },
}


def capability_enabled(platform: Platform, capability: PlatformCapability) -> bool:
    rule = PLATFORM_CAPABILITY_MATRIX.get(platform, {}).get(capability)
    return bool(rule and rule.enabled)


def capability_rule(
    platform: Platform,
    capability: PlatformCapability,
) -> PlatformCapabilityRule:
    rule = PLATFORM_CAPABILITY_MATRIX.get(platform, {}).get(capability)
    if rule is None:
        return PlatformCapabilityRule(platform, capability, False, Platform.DASHBOARD)
    return rule
