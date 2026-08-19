from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit

from ai_market_monitor.core.launch_stage import STAGE_EXPOSURE, LaunchStage


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
class TopbarAction:
    """One action the shared topbar draws on behalf of the page that declared it.

    Pages used to draw their own button in their own heading, which is why the same
    action appeared in three different places with three different names and one of
    them was always the odd one out. A page now *declares* what belongs at the top and
    the shared topbar draws it, so there is one shape, one target size, one focus ring
    and one place to change all of them.

    ``kind`` is only how loud it is. ``primary`` is the one thing this page most wants
    a person to do, and there is at most one of those; everything else is ``quiet``.
    """

    label: str
    href: str
    icon: str
    kind: str = "quiet"


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

#: Hosts where an ``https`` social URL would be wrong rather than safer.
#:
#: A developer machine has no certificate, so forcing the scheme there would produce
#: a link that fails to load locally while proving nothing.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def social_image_url(base_url: str, configured: str | None = None) -> str:
    """The absolute, HTTPS image address used by ``og:image`` and ``twitter:image``.

    Every social scraper needs an absolute address, and every one of them refuses a
    plain-HTTP image on an HTTPS page. The tag was being built from
    ``PUBLIC_BASE_URL`` alone, so any deployment whose base URL was not already
    ``https`` published a preview image nothing would fetch — and locally it
    published ``http://localhost:8000/...``, an address no scraper on earth can
    reach.

    So the scheme is forced here for every real host, and left alone for a local one
    where HTTPS would simply be broken.
    """

    candidate = (configured or "").strip() or (
        f"{base_url.rstrip('/')}/{SOCIAL_PREVIEW_PATH.lstrip('/')}"
    )
    parts = urlsplit(candidate)
    if parts.scheme == "https" or (parts.hostname or "") in _LOCAL_HOSTS:
        return candidate
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment))


#: How We Screen is deliberately missing from both menus below.
#:
#: The page itself is still served, and it is still in PUBLIC_PAGES and the sitemap, so a
#: saved bookmark or a search result keeps working. Nothing on the site leads a visitor to
#: it any more: not the header, not the footer, not the body of another page, and not the
#: assistant. Removing it from one of those four and leaving the others is the failure this
#: product keeps repeating, so all four are checked together by
#: `test_the_unlinked_page_is_unlinked_everywhere` in
#: tests/unit/test_invariant_public_waitlist_surface.py.
PUBLIC_NAVIGATION = (
    NavigationItem("Features", "public_features", "features"),
    NavigationItem("How It Works", "public_how_it_works", "how_it_works"),
    NavigationItem("Pricing", "public_pricing", "pricing"),
    NavigationItem("Help Center", "public_help", "help"),
)


#: The footer menu, in three groups: Product, Legal, Contact.
#:
#: **Not every served page is in it.** Pricing, Halal Assets, Risk Disclosure,
#: Trust & Safety, About and the Help Center were taken out of the footer deliberately;
#: each one is still served, still in :data:`PUBLIC_PAGES` and still in the sitemap, so a
#: bookmark or a search result keeps working. What changed is only that the footer no
#: longer leads there.
#:
#: One list, two renderers. The Jinja footer loops over this, and the React footer is
#: handed the same groups through the runtime config, so a link removed here disappears
#: from both at once. The React fallback in `components/SiteChrome.tsx` — used only for a
#: page opened without the server shell — has to be kept in step with it by hand, and
#: `test_both_footers_offer_the_same_menu` is what notices when it is not.
FOOTER_NAVIGATION = (
    NavigationGroup(
        "Product",
        (
            NavigationItem("Features", "public_features", "features"),
            NavigationItem("How it works", "public_how_it_works", "how_it_works"),
        ),
    ),
    NavigationGroup(
        "Legal",
        (
            NavigationItem("Terms of Use", "public_terms", "terms"),
            NavigationItem("Privacy Policy", "public_privacy", "privacy"),
            NavigationItem("Cookie Policy", "public_cookies", "cookies"),
        ),
    ),
    NavigationGroup(
        "Contact",
        (NavigationItem("Contact", "public_contact", "contact"),),
    ),
)


#: Where the "Cookie settings" link in the footer goes.
#:
#: It is a **link**, not a button, and this is the address it opens. The consent script
#: catches the click and opens the settings panel in place; a visitor with no scripting,
#: or one who opens it in a new tab, lands on the Cookie Policy with `settings=1`, which
#: the same script reads on load and opens the panel from. Either way the person reaches
#: the same panel, which is what a footer link has to promise.
#:
#: Written once because three places need it: both footers, and the test that checks
#: they agree.
COOKIE_SETTINGS_PATH = "/cookies?settings=1"


@dataclass(frozen=True, slots=True)
class SocialLink:
    """One channel, named once for every renderer of the footer.

    ``handle`` is what a person reads; ``href`` is where it goes. The addresses in
    :data:`SOCIAL_LINKS` are placeholders until the accounts are opened, and they are
    the only thing that has to change then — both footers read that tuple, so neither
    holds an address of its own to be forgotten.
    """

    label: str
    handle: str
    href: str


#: Placeholder addresses. Replace the three `href` values when the accounts are opened;
#: nothing else in the site needs to change, because both footers read this tuple.
SOCIAL_LINKS: tuple[SocialLink, ...] = (
    SocialLink("Instagram", "@hilalmarkets", "https://instagram.com/hilalmarkets"),
    SocialLink("X", "@hilalmarkets", "https://x.com/hilalmarkets"),
    SocialLink("Threads", "@hilalmarkets", "https://threads.net/@hilalmarkets"),
)


#: Where every "join the waitlist" call to action goes while the site is pre-launch.
#: The form lives in the landing page, so a visitor on any other page is taken back to
#: it rather than to a second copy that could drift out of step with the first.
WAITLIST_ANCHOR = "/#waitlist"


#: Pages that only make sense once anybody can open an account.
#:
#: **This is a view of the launch stage, not a second list.** It used to be its own
#: `frozenset({"pricing", "screened_market"})` sitting beside an identical set in
#: `core/launch_stage.py`, and the two were read by different surfaces: the stage table
#: by nothing at all, this one by the header, the footer, the sitemap and the public
#: assistant — each gated on `waitlist_mode` rather than on the stage.
#:
#: They agreed on their contents and still disagreed on behaviour. `waitlist_mode` is
#: only true in the `public_waitlist` stage, so at `internal` or `private_beta_invite`
#: every one of those surfaces hid nothing: Pricing and Halal Assets appeared in the
#: menus and in `sitemap.xml` while the stage's own exposure table said they were
#: hidden and that pricing was not advertised.
#:
#: Kept as a name because it reads well at the call sites, but there is one definition
#: now, and the surfaces below take the stage's set rather than deriving their own.
WAITLIST_HIDDEN_PAGES = STAGE_EXPOSURE[LaunchStage.PUBLIC_WAITLIST].hidden_pages


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
    # Named in full rather than left to a "starts with /dashboard" rule. Guessing from
    # the shape of a name cuts both ways: a rule loose enough to catch this one also
    # claims an innocent /signup-guide article and fails a build over a public page.
    "/dashboard-entry",
    # Home, and the address it used to have. Both are signed-in pages that do not sit
    # under /dashboard, so neither was recognised here and a public page could have
    # linked straight into the product without this list noticing.
    "/home",
    "/main",
    "/signin",
    "/signup",
    "/subscribe",
    "/checkout",
    "/billing",
    "/account",
)


def is_account_only_path(address: str) -> bool:
    """True when the address can only be used by somebody who already has an account.

    A prefix matches the address itself or a child of it, and nothing else. Every other
    account-only address has to be named in ACCOUNT_ONLY_PATH_PREFIXES, which is the
    point: the list is the decision, and it is written down rather than inferred.

    **A whole address is read, not only a path.** Links into the product are absolute
    when the dashboard has a hostname of its own — `https://app.hilalmarkets.com/signin`
    is the same door as `/signin`, and reading only the second spelling meant a link
    written the first way walked straight past this check. The scheme and host are
    dropped and the path is judged, which is the same decision either way.
    """

    path = urlsplit(address).path if "//" in address else address
    normalized = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
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
    "Accounts are invite-only while the beta runs. Leave your email on the waitlist "
    "and we will contact you directly when a place opens. We ask for nothing else."
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


def public_navigation(*, hidden_pages: frozenset[str]) -> tuple[NavigationItem, ...]:
    """Header menu for the public site, minus whatever the launch stage hides.

    Takes the set rather than a boolean on purpose. The boolean was ``waitlist_mode``,
    which is true in exactly one of the four stages, so two stages that hide pricing
    got a menu that advertised it.
    """

    if not hidden_pages:
        return PUBLIC_NAVIGATION
    return tuple(item for item in PUBLIC_NAVIGATION if item.page not in hidden_pages)


def footer_navigation(*, hidden_pages: frozenset[str]) -> tuple[NavigationGroup, ...]:
    """Footer menu for the public site, using the same hidden-page set as the header."""

    if not hidden_pages:
        return FOOTER_NAVIGATION
    groups = []
    for group in FOOTER_NAVIGATION:
        items = tuple(item for item in group.items if item.page not in hidden_pages)
        if items:
            groups.append(NavigationGroup(group.label, items))
    return tuple(groups)


#: The side menu.
#:
#: Nine destinations in three groups, and every one of them opens the redesigned page
#: rather than an older copy of it — which is now the only page at that address. The
#: redesigned pages used to live at `/dashboard-test/...` while an older copy of each one
#: answered at `/dashboard/...`; this menu opened the redesigned ones and every Telegram
#: button, WhatsApp reply and alert email opened the older ones, so a person following a
#: message from us and a person using this menu saw two different screens with the same
#: name. There is one of each now. See `api/routers/dashboard_paths.py`.
#:
#: `icon` is a name in `hilalmarkets-icons.js`, the product's single icon table. The menu
#: used to have a second icon system of its own — eleven `.nav-icon-*` rules masking
#: eleven files that nothing else on the site used — so adding an entry meant adding an
#: icon in two places and a mismatch drew an empty square with nothing reporting it.
DASHBOARD_NAVIGATION = (
    NavigationGroup(
        "Overview",
        (
            #: The front page. It answers "what is happening right now" over the same
            #: read models the deeper pages own, so it can never disagree with the page
            #: it links to.
            #:
            #: Called **Home**, and it answers at `/home`. It used to be called "Main" at
            #: `/main`, which is an internal word for a page whose whole job is to be the
            #: obvious place to start. The old address is kept as a permanent redirect —
            #: it is written into sent email, into Telegram buttons and into the
            #: `target_path` column of two tables, and none of those can be corrected
            #: after the fact. See `core/dashboard_paths.py`.
            NavigationItem("Home", "main_dashboard_page", "main", "gauge"),
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
        "Your monitors",
        (
            NavigationItem(
                "Monitors",
                "watchlists_page",
                "monitors",
                "radar",
                (
                    "strategy_builder",
                    "strategy_detail",
                    "strategy_verify",
                    "strategy_versions",
                ),
            ),
            #: The visual canvas, named for what a person does on it rather than for
            #: what the page is called internally. It sits directly after Monitors
            #: because it is where one is drawn before it becomes one.
            NavigationItem(
                "Create a monitor",
                "monitor_canvas_page",
                "monitor_canvas",
                "circle_plus",
                guide_target="nav-create-monitor",
            ),
            NavigationItem(
                "Opportunities",
                "opportunities_page",
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
                "Plan and billing",
                "subscription_page",
                "billing",
                "billing",
            ),
            NavigationItem("Settings", "settings_page", "settings", "settings"),
            NavigationItem("Support", "support_page", "support", "support"),
        ),
    ),
)


def dashboard_page_identity(page: str) -> dict[str, str] | None:
    """Which menu entry the current page belongs to, for the topbar to say out loud.

    The topbar used to show a search box and two buttons and nothing else, so a person
    arriving from a link had no way to tell which of nine pages they were on except by
    reading the menu. This returns the group, the name and the icon of the entry that is
    highlighted, taken from the same navigation data the menu draws — never a second
    list, which is how the two would come to disagree.

    ``None`` for a page that is not in the menu. The topbar then shows the page title on
    its own rather than inventing a group for it.
    """

    for group in DASHBOARD_NAVIGATION:
        for item in group.items:
            if page == item.page or page in item.active_pages:
                return {
                    "group": group.label,
                    "label": item.label,
                    "icon": item.icon or "spark",
                }
    return None


PUBLIC_PAGES = (
    PublicPageMetadata(
        "features",
        "public_features",
        "/features",
        "Features",
        (
            "Explore Shariah-screened discovery, guided Watchlists, evidence, "
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
            "See how Hilal Markets turns an idea from Halal Assets into an approved, "
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
            "Understand methodology-specific Shariah screening, evidence, review "
            "authority, and status changes."
        ),
        "hilal/public/how_we_screen.html",
    ),
    PublicPageMetadata(
        "pricing",
        "public_pricing",
        "/pricing",
        "Pricing",
        "Review current Hilal Markets access, limits, and billing availability.",
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
            "Contact Hilal Markets product support, governance, partnerships, privacy, "
            "or security teams."
        ),
        "hilal/public/contact.html",
    ),
    PublicPageMetadata(
        "about",
        "public_about",
        "/about",
        "About",
        "Learn why Hilal Markets is building an evidence-led monitoring layer for "
        "Muslim investors.",
        "hilal/public/about.html",
    ),
    PublicPageMetadata(
        "trust_safety",
        "public_trust_safety",
        "/trust-safety",
        "Trust & Safety",
        (
            "Review Hilal Markets security, privacy, governance, data-integrity, and "
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
        "question": "Does Hilal Markets decide what is halal?",
        "answer": (
            "No. Hilal Markets applies disclosed, versioned methodologies to approved "
            "evidence. Screening remains methodology-specific and subject to qualified "
            "human governance."
        ),
    },
    {
        "question": "Does Hilal Markets place trades?",
        "answer": (
            "No. Hilal Markets monitors crypto spot markets and explains evidence. You "
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
                "question": "What does Shariah-screened mean here?",
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
