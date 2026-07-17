from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class NavigationItem:
    label: str
    endpoint: str
    page: str
    icon: str | None = None
    active_pages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NavigationGroup:
    label: str
    items: tuple[NavigationItem, ...]


@dataclass(frozen=True, slots=True)
class PublicPageMetadata:
    page: str
    endpoint: str
    path: str
    title: str
    description: str
    template: str
    legal_review_required: bool = False


class PurchaseFaq(TypedDict):
    question: str
    answer: str


class HelpArticle(TypedDict):
    question: str
    answer: str


class HelpCategory(TypedDict):
    slug: str
    title: str
    icon: str
    articles: tuple[HelpArticle, ...]


SITE_NAME = "HilalMarkets"
SITE_DESCRIPTION = (
    "Sharia-screened crypto spot intelligence and evidence-led market monitoring "
    "for self-directed Muslim investors."
)
COOKIE_CONSENT_VERSION = 1


PUBLIC_NAVIGATION = (
    NavigationItem("Features", "public_features", "features"),
    NavigationItem("How It Works", "public_how_it_works", "how_it_works"),
    NavigationItem("How We Screen", "public_how_we_screen", "how_we_screen"),
    NavigationItem("Pricing", "public_pricing", "pricing"),
    NavigationItem("Help Center", "public_help", "help"),
)


FOOTER_NAVIGATION = (
    NavigationGroup(
        "Product",
        (
            NavigationItem("Features", "public_features", "features"),
            NavigationItem("How It Works", "public_how_it_works", "how_it_works"),
            NavigationItem("Pricing", "public_pricing", "pricing"),
            NavigationItem(
                "Screened Market",
                "screened_market_page",
                "screened_market",
            ),
        ),
    ),
    NavigationGroup(
        "Trust",
        (
            NavigationItem("How We Screen", "public_how_we_screen", "how_we_screen"),
            NavigationItem("Trust & Safety", "public_trust_safety", "trust_safety"),
            NavigationItem(
                "Risk Disclosure",
                "public_risk_disclosure",
                "risk_disclosure",
            ),
        ),
    ),
    NavigationGroup(
        "Company & Support",
        (
            NavigationItem("About", "public_about", "about"),
            NavigationItem("Help Center", "public_help", "help"),
            NavigationItem("Contact", "public_contact", "contact"),
        ),
    ),
    NavigationGroup(
        "Legal",
        (
            NavigationItem("Privacy", "public_privacy", "privacy"),
            NavigationItem("Terms", "public_terms", "terms"),
            NavigationItem("Cookie Policy", "public_cookies", "cookies"),
        ),
    ),
)


DASHBOARD_NAVIGATION = (
    NavigationGroup(
        "Discover",
        (
            NavigationItem("Home", "dashboard_home", "home", "home"),
            NavigationItem(
                "Sharia-Screened Market",
                "screened_market_page",
                "screened_market",
                "market",
                ("asset_passport",),
            ),
            NavigationItem(
                "Saved Assets",
                "saved_assets_page",
                "saved_assets",
                "watchlist",
            ),
        ),
    ),
    NavigationGroup(
        "Watch",
        (
            NavigationItem(
                "Watch Plans",
                "watchlists_page",
                "watchlists",
                "radar",
                (
                    "strategy_builder",
                    "strategy_detail",
                    "strategy_verify",
                    "strategy_versions",
                ),
            ),
            NavigationItem(
                "Check the Market Now",
                "dashboard_check_market",
                "check_market",
                "scan",
            ),
        ),
    ),
    NavigationGroup(
        "Review",
        (
            NavigationItem(
                "Opportunities & Evidence",
                "lifecycles_page",
                "activity",
                "activity",
                ("lifecycles", "alert_proof"),
            ),
            NavigationItem(
                "Compliance Changes",
                "compliance_changes_page",
                "compliance",
                "compliance",
            ),
        ),
    ),
    NavigationGroup(
        "Trust",
        (
            NavigationItem(
                "How We Screen",
                "methodology_page",
                "methodology",
                "methodology",
            ),
        ),
    ),
    NavigationGroup(
        "Account",
        (
            NavigationItem(
                "Integrations",
                "connections_page",
                "integrations",
                "integrations",
            ),
            NavigationItem(
                "Plan & Billing",
                "billing_page",
                "billing",
                "billing",
            ),
            NavigationItem("Settings", "settings_page", "settings", "settings"),
            NavigationItem("Support", "support_page", "support", "support"),
        ),
    ),
)


PUBLIC_PAGES = (
    PublicPageMetadata(
        "features",
        "public_features",
        "/features",
        "Features",
        (
            "Explore Sharia-screened discovery, guided Watch Plans, evidence, "
            "and compliance monitoring."
        ),
        "hilal/public/features.html",
    ),
    PublicPageMetadata(
        "how_it_works",
        "public_how_it_works",
        "/how-it-works",
        "How It Works",
        (
            "See how HilalMarkets turns a screened market idea into an approved, "
            "explainable Watch Plan."
        ),
        "hilal/public/how_it_works.html",
    ),
    PublicPageMetadata(
        "how_we_screen",
        "public_how_we_screen",
        "/how-we-screen",
        "How We Screen",
        (
            "Understand methodology-specific Sharia screening, evidence, review "
            "authority, and status changes."
        ),
        "hilal/public/how_we_screen.html",
    ),
    PublicPageMetadata(
        "pricing",
        "public_pricing",
        "/pricing",
        "Pricing",
        (
            "Compare HilalMarkets Free, Core, and Pro monitoring plans from the "
            "current product catalog."
        ),
        "hilal/public/pricing.html",
    ),
    PublicPageMetadata(
        "help",
        "public_help",
        "/help",
        "Help Center",
        (
            "Get clear answers about screened markets, Watch Plans, alerts, evidence, "
            "billing, and account safety."
        ),
        "hilal/public/help.html",
    ),
    PublicPageMetadata(
        "contact",
        "public_contact",
        "/contact",
        "Contact",
        (
            "Contact HilalMarkets product support, governance, partnerships, privacy, "
            "or security teams."
        ),
        "hilal/public/contact.html",
    ),
    PublicPageMetadata(
        "about",
        "public_about",
        "/about",
        "About",
        "Learn why HilalMarkets is building an evidence-led monitoring layer for Muslim investors.",
        "hilal/public/about.html",
    ),
    PublicPageMetadata(
        "trust_safety",
        "public_trust_safety",
        "/trust-safety",
        "Trust & Safety",
        (
            "Review HilalMarkets security, privacy, governance, data-integrity, and "
            "user-control boundaries."
        ),
        "hilal/public/trust_safety.html",
    ),
    PublicPageMetadata(
        "risk_disclosure",
        "public_risk_disclosure",
        "/risk-disclosure",
        "Risk Disclosure",
        (
            "Understand the limits and risks of crypto markets, data, alerts, and "
            "methodology-specific screening."
        ),
        "hilal/public/risk_disclosure.html",
        True,
    ),
    PublicPageMetadata(
        "privacy",
        "public_privacy",
        "/privacy",
        "Privacy Policy",
        "Learn how HilalMarkets handles account, product, support, and optional analytics data.",
        "hilal/public/privacy.html",
        True,
    ),
    PublicPageMetadata(
        "terms",
        "public_terms",
        "/terms",
        "Terms of Service",
        "Review the proposed terms and service boundaries for using HilalMarkets.",
        "hilal/public/terms.html",
        True,
    ),
    PublicPageMetadata(
        "cookies",
        "public_cookies",
        "/cookies",
        "Cookie Policy",
        (
            "Understand essential storage, optional analytics, consent choices, and "
            "preference withdrawal."
        ),
        "hilal/public/cookies.html",
        True,
    ),
)


PUBLIC_PAGE_BY_PAGE = {item.page: item for item in PUBLIC_PAGES}
PUBLIC_PAGE_BY_PATH = {item.path: item for item in PUBLIC_PAGES}


PURCHASE_FAQS: tuple[PurchaseFaq, ...] = (
    {
        "question": "Does HilalMarkets decide what is halal?",
        "answer": (
            "No. HilalMarkets applies disclosed, versioned methodologies to approved "
            "evidence. Screening remains methodology-specific and subject to qualified "
            "human governance."
        ),
    },
    {
        "question": "Does HilalMarkets place trades?",
        "answer": (
            "No. HilalMarkets monitors crypto spot markets and explains evidence. You "
            "retain every investment and trading decision."
        ),
    },
    {
        "question": "What happens when an asset changes status?",
        "answer": (
            "Your selected policy controls the response. An affected asset can be paused "
            "or removed while its evidence trail and impact remain visible."
        ),
    },
    {
        "question": "Do I need to understand indicators?",
        "answer": (
            "No. Guided mode starts with plain market behavior. Exact mechanics remain "
            "available for review under Advanced Controls."
        ),
    },
)


HELP_CATEGORIES: tuple[HelpCategory, ...] = (
    {
        "slug": "screening",
        "title": "Screening and evidence",
        "icon": "passport",
        "articles": (
            {
                "question": "What does Sharia-screened mean here?",
                "answer": (
                    "It means an asset met a disclosed methodology using the evidence "
                    "and version shown in its Evidence Passport. It is not a universal "
                    "religious ruling."
                ),
            },
            {
                "question": "Why can a screening status change?",
                "answer": (
                    "Projects, evidence, and methodologies can change. Compliance Watch "
                    "preserves the change and shows its effect on your Watch Plans."
                ),
            },
        ),
    },
    {
        "slug": "watch-plans",
        "title": "Watch Plans and market checks",
        "icon": "radar",
        "articles": (
            {
                "question": "What is a Watch Plan?",
                "answer": (
                    "A Watch Plan is your approved set of measurable market conditions, "
                    "screened universe, alert timing, and compliance-change behavior."
                ),
            },
            {
                "question": "What is Check the Market Now?",
                "answer": (
                    "It runs the same validated rules once against the eligible screened "
                    "universe. It does not create continuous monitoring unless you choose to."
                ),
            },
        ),
    },
    {
        "slug": "alerts",
        "title": "Alerts and investigations",
        "icon": "bell",
        "articles": (
            {
                "question": "Why did I not receive an alert?",
                "answer": (
                    "Open the lifecycle investigation to separate failed market rules, "
                    "policy exclusions, data issues, cooldowns, and delivery failures."
                ),
            },
            {
                "question": "What evidence is attached to an alert?",
                "answer": (
                    "The proof records the strategy version, rule outcomes, market values, "
                    "timestamps, screening context, data freshness, and delivery result."
                ),
            },
        ),
    },
    {
        "slug": "account",
        "title": "Account, billing, and privacy",
        "icon": "settings",
        "articles": (
            {
                "question": "Where do I manage my plan?",
                "answer": (
                    "Open Plan & Billing in the dashboard. The limits there come from the "
                    "same catalog as the public Pricing page."
                ),
            },
            {
                "question": "Can I change optional cookie choices?",
                "answer": (
                    "Yes. Use Cookie Settings in the public footer at any time. Essential "
                    "security storage remains enabled."
                ),
            },
        ),
    },
)


SHARIA_STATUS_PRESENTATION = {
    "eligible": {
        "label": "Eligible",
        "badge": "eligible",
        "plain_language": "Screened as eligible under the stated methodology.",
    },
    "eligible_with_qualifications": {
        "label": "Eligible with qualifications",
        "badge": "qualified",
        "plain_language": "Included with qualification context that should be reviewed.",
    },
    "under_review": {
        "label": "Under review",
        "badge": "review",
        "plain_language": "Not included in default monitoring while evidence is reviewed.",
    },
    "disputed": {
        "label": "Disputed",
        "badge": "review",
        "plain_language": "Approved methodologies or reviewers have a material disagreement.",
    },
    "excluded": {
        "label": "Excluded",
        "badge": "excluded",
        "plain_language": "Excluded under the stated methodology and version.",
    },
    "insufficient_information": {
        "label": "Insufficient information",
        "badge": "neutral",
        "plain_language": "Required evidence is not complete enough for a status.",
    },
}


LIFECYCLE_PRESENTATION = {
    "candidate_detected": "Detected",
    "detected": "Detected",
    "forming": "Forming",
    "near_confirmation": "Near match",
    "armed": "Confirmation pending",
    "confirmed": "Confirmed",
    "alert_sent": "Alert delivered",
    "suppressed": "Completed without alert",
    "blocked": "Blocked",
    "data_unavailable": "Data unavailable",
    "invalidated": "Invalidated",
    "expired": "Expired",
    "completed": "Ended",
    "closed": "Ended",
}


PROHIBITED_ANALYTICS_PROPERTIES = frozenset(
    {
        "email",
        "user_email",
        "full_name",
        "raw_prompt",
        "strategy_text",
        "watch_plan_text",
        "credential",
        "token",
        "reviewer_note",
        "support_attachment",
        "asset_holdings",
    }
)
