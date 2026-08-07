from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class NavigationItem:
    label: str
    endpoint: str
    page: str
    icon: str | None = None
    active_pages: tuple[str, ...] = ()
    #: Name of the contextual guide step that points at this menu entry, if any.
    #: The sidebar renders its links from a loop, so a marker written straight into the
    #: template could never be unique. Declaring it here keeps one marker per name,
    #: which is the property the guide's exact targeting depends on.
    guide_target: str | None = None


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


SITE_NAME = "Hilal Markets"
SITE_DESCRIPTION = (
    "Screen halal assets, build your rules, and monitor setups without watching "
    "charts all day."
)
SOCIAL_PREVIEW_TITLE = "Halal Trading With Clarity"
SOCIAL_PREVIEW_DESCRIPTION = SITE_DESCRIPTION
SOCIAL_PREVIEW_PATH = "/static/hilalmarkets-social-preview.png"
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
                "Halal Assets",
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


#: Where every "join the waitlist" call to action goes while the site is pre-launch.
#: The form lives in the landing page, so a visitor on any other page is taken back to
#: it rather than to a second copy that could drift out of step with the first.
WAITLIST_ANCHOR = "/#waitlist"


#: Pages that only make sense once anybody can open an account.
#:
#: While the public site is in waitlist mode there is nothing to buy and no dashboard to
#: enter, so a menu entry pointing at either one is a promise the product cannot keep.
#: One set, read by every menu, so the header and the footer can never disagree about
#: what a visitor is allowed to reach.
WAITLIST_HIDDEN_PAGES = frozenset({"pricing", "screened_market"})


#: Every address that only works once the visitor has an account.
#:
#: Removing a page from the menus is not enough on its own: a link written into the body
#: of a page reaches the same place and no menu ever sees it. That is how a "Halal Assets"
#: button on How We Screen and a "Dashboard support" button on the Help Center kept
#: pointing into the product after the menus had been emptied. One list of prefixes, read
#: by the templates' own gate and by the invariant test that renders every public page, so
#: a page added later cannot invent its own idea of what counts as inside the product.
ACCOUNT_ONLY_PATH_PREFIXES = (
    "/dashboard",
    "/signin",
    "/signup",
    "/subscribe",
    "/checkout",
    "/billing",
    "/account",
)


def is_account_only_path(path: str) -> bool:
    """True when the address can only be used by somebody who already has an account."""

    normalized = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/") or normalized.startswith(prefix)
        for prefix in ACCOUNT_ONLY_PATH_PREFIXES
    )


#: The pre-launch message, written once.
#:
#: The header, the closing section on every page, the assistant and the landing form all
#: say the same thing because they all read these. Wording kept for a beginner: no
#: "onboarding", no "cohort", no "early access tier".
WAITLIST_EYEBROW = "Private beta"
WAITLIST_HEADLINE = "Hilal Markets is in a private beta."
WAITLIST_BODY = (
    "Accounts are invite-only while the beta runs, so you cannot sign up yet. "
    "Leave your email on the waitlist and we will contact you directly when a place "
    "opens. You can also tell us you are happy to help test the product first."
)
WAITLIST_CTA_LABEL = "Join the waitlist"


#: The Help Center article about plans, in each of the two states the product can be in.
#:
#: Before launch there is no Pricing page and no dashboard to open, so the launched answer
#: would send a reader to two places that do not exist for them.
_PLAN_ARTICLE_OPEN: HelpArticle = {
    "question": "Where do I manage my plan?",
    "answer": (
        "Open Plan & Billing in the dashboard. The limits there come from the "
        "same catalog as the public Pricing page."
    ),
}
_PLAN_ARTICLE_WAITLIST: HelpArticle = {
    "question": "What does Hilal Markets cost?",
    "answer": (
        "Plans and prices are not published while Hilal Markets is in its private "
        "beta, and nothing is charged during the beta. Join the waitlist on the home "
        "page and we will explain the plans before anything is ever payable."
    ),
}


def public_navigation(*, waitlist_mode: bool) -> tuple[NavigationItem, ...]:
    """Header menu for the public site, with account-only pages removed in waitlist mode."""

    if not waitlist_mode:
        return PUBLIC_NAVIGATION
    return tuple(item for item in PUBLIC_NAVIGATION if item.page not in WAITLIST_HIDDEN_PAGES)


def footer_navigation(*, waitlist_mode: bool) -> tuple[NavigationGroup, ...]:
    """Footer menu for the public site, using the same hidden-page set as the header."""

    if not waitlist_mode:
        return FOOTER_NAVIGATION
    groups = []
    for group in FOOTER_NAVIGATION:
        items = tuple(
            item for item in group.items if item.page not in WAITLIST_HIDDEN_PAGES
        )
        if items:
            groups.append(NavigationGroup(group.label, items))
    return tuple(groups)


DASHBOARD_NAVIGATION = (
    NavigationGroup(
        "Discover",
        (
            NavigationItem("Home", "dashboard_home", "home", "home"),
            NavigationItem(
                "Halal Assets",
                "screened_market_page",
                "screened_market",
                "market",
                ("asset_passport",),
                guide_target="nav-halal-assets",
            ),
        ),
    ),
    NavigationGroup(
        "Watch",
        (
            NavigationItem(
                "Watchlists",
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
                "Trading Assistant",
                "dashboard_check_market",
                "check_market",
                "scan",
                guide_target="nav-trading-assistant",
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
                guide_target="nav-opportunities",
            ),
        ),
    ),
    NavigationGroup(
        "Account",
        (
            NavigationItem(
                "Notifications",
                "connections_page",
                "integrations",
                "bell",
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
            "Explore Sharia-screened discovery, guided Watchlists, evidence, "
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
            "See how HilalMarkets turns an idea from Halal Assets into an approved, "
            "explainable Watchlist."
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
        "Review current HilalMarkets access, limits, and billing availability.",
        "hilal/public/pricing.html",
    ),
    PublicPageMetadata(
        "help",
        "public_help",
        "/help",
        "Help Center",
        (
            "Get clear answers about Halal Assets, Watchlists, alerts, evidence, "
            "and account safety."
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
    ),
    PublicPageMetadata(
        "privacy",
        "public_privacy",
        "/privacy",
        "Privacy Policy",
        "Learn how Hilal Markets handles account, product, support, and optional analytics data.",
        "hilal/public/privacy.html",
    ),
    PublicPageMetadata(
        "terms",
        "public_terms",
        "/terms",
        "Terms of Use",
        "Review the terms and service boundaries for using Hilal Markets.",
        "hilal/public/terms.html",
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
                    "preserves the change and shows its effect on your Watchlists."
                ),
            },
        ),
    },
    {
        "slug": "watch-plans",
        "title": "Watchlists and market scans",
        "icon": "radar",
        "articles": (
            {
                "question": "What is a Watchlist?",
                "answer": (
                    "A Watchlist is your approved set of measurable market conditions, "
                    "screened universe, alert timing, and compliance-change behavior."
                ),
            },
            {
                "question": "What is Trading Assistant?",
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
            _PLAN_ARTICLE_OPEN,
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


def public_help_categories(*, waitlist_mode: bool) -> tuple[HelpCategory, ...]:
    """Help Center articles, answering the plan question for the state the product is in.

    The Help Center, the assistant's grounded facts and the site menus all read this one
    function, so a reader cannot be told to open Plan & Billing on a page whose menu no
    longer offers a way in.
    """

    if not waitlist_mode:
        return HELP_CATEGORIES
    categories: list[HelpCategory] = []
    for category in HELP_CATEGORIES:
        articles = tuple(
            _PLAN_ARTICLE_WAITLIST if article is _PLAN_ARTICLE_OPEN else article
            for article in category["articles"]
        )
        title = (
            "Account and privacy"
            if category["slug"] == "account"
            else category["title"]
        )
        categories.append(
            {
                "slug": category["slug"],
                "title": title,
                "icon": category["icon"],
                "articles": articles,
            }
        )
    return tuple(categories)


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
