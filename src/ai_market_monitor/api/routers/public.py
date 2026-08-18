from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.asset_logos import asset_logo
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.dashboard_paths import HOME_PATH
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_PRESENTATIONS,
    plan_offer,
    plan_offer_payload,
    promotion_is_active,
    visible_plan_comparison,
    visible_plan_comparison_headers,
    visible_public_plan_codes,
)
from ai_market_monitor.core.site_content import (
    COOKIE_CONSENT_VERSION,
    COOKIE_SETTINGS_PATH,
    PUBLIC_PAGE_BY_PAGE,
    PUBLIC_PAGES,
    PURCHASE_FAQS,
    SITE_DESCRIPTION,
    SITE_NAME,
    SOCIAL_LINKS,
    SOCIAL_PREVIEW_DESCRIPTION,
    SOCIAL_PREVIEW_TITLE,
    WAITLIST_ANCHOR,
    WAITLIST_BODY,
    WAITLIST_CTA_LABEL,
    WAITLIST_EYEBROW,
    WAITLIST_HEADLINE,
    HelpArticle,
    PurchaseFaq,
    footer_navigation,
    public_help_categories,
    public_navigation,
)
from ai_market_monitor.core.site_content import (
    social_image_url as build_social_image_url,
)
from ai_market_monitor.services.ai_setup_evaluator_control import (
    evaluator_fault_control_available,
)
from ai_market_monitor.services.billing import (
    BillingError,
    billing_provider_capabilities,
    configured_billing_provider,
)
from ai_market_monitor.services.public_site import PublicSiteReadService
from ai_market_monitor.services.web_auth import SESSION_COOKIE_NAME, WebAuthService

PACKAGE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
router = APIRouter(tags=["public"])


def _plan_limit(value: object) -> str:
    if isinstance(value, int) and value >= 100_000:
        return "Unlimited"
    return str(value)


templates.env.filters["plan_limit"] = _plan_limit
# The same owner the dashboard templates use. Registered on both environments because
# a shared macro must not behave differently depending on which router rendered it.
templates.env.globals["asset_logo"] = asset_logo


def _absolute_url(settings: Settings, path: str) -> str:
    return f"{str(settings.public_base_url).rstrip('/')}/{path.lstrip('/')}"


def _breadcrumb_json_ld(
    settings: Settings,
    *,
    page_title: str,
    page_path: str,
) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": _absolute_url(settings, "/"),
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": page_title,
                "item": _absolute_url(settings, page_path),
            },
        ],
    }


def _base_json_ld(settings: Settings) -> list[dict[str, Any]]:
    return [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": SITE_NAME,
            "url": _absolute_url(settings, "/"),
            "logo": _absolute_url(settings, "/static/hilal-markets-symbol.svg"),
            "email": settings.support_email,
        },
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": _absolute_url(settings, "/"),
            "description": SITE_DESCRIPTION,
        },
    ]


def _faq_json_ld(
    items: Sequence[PurchaseFaq | HelpArticle],
) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item["answer"],
                },
            }
            for item in items
        ],
    }


def _public_context(
    request: Request,
    settings: Settings,
    *,
    page: str,
    title: str,
    description: str,
    path: str,
    legal_review_required: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    waitlist_mode = settings.waitlist_mode
    # One owner for the preview address, in core/site_content.py. It forces HTTPS for
    # any real host, which the previous inline expression did not: it copied
    # PUBLIC_BASE_URL's scheme, so a plain-HTTP base published an image no social
    # scraper will fetch.
    social_image_url = build_social_image_url(
        str(settings.public_base_url),
        str(settings.public_og_image_url) if settings.public_og_image_url else None,
    )
    telegram_username = (
        settings.telegram_bot_username.lstrip("@").strip()
        if settings.telegram_bot_username
        else None
    )
    json_ld = _base_json_ld(settings)
    if path != "/":
        json_ld.append(
            _breadcrumb_json_ld(
                settings,
                page_title=title,
                page_path=path,
            )
        )
    help_categories = public_help_categories(waitlist_mode=waitlist_mode)
    # Resolved once: the Jinja footer and the React footer are handed the same groups,
    # so a page hidden by the stage is hidden in both or in neither.
    footer_groups = footer_navigation(hidden_pages=settings.stage_exposure.hidden_pages)
    if page == "landing":
        json_ld.append(_faq_json_ld(PURCHASE_FAQS))
    elif page == "help":
        json_ld.append(
            _faq_json_ld(
                [
                    article
                    for category in help_categories
                    for article in category["articles"]
                ]
            )
        )
    if page in {"landing", "features", "pricing"}:
        application: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": SITE_NAME,
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "Web",
            "url": _absolute_url(settings, path),
            "description": description,
        }
        if settings.stage_exposure.advertises_pricing:
            # An Offer tells search engines the product can be bought today. Before the
            # product is open there is nothing to buy, so the claim is left out rather
            # than published with a price no visitor can act on.
            #
            # Gated on the stage, not on `waitlist_mode`. The boolean is false in three
            # of the four stages, so `internal` and `private_beta_invite` both published
            # a purchasable Offer for a product nobody could open an account on.
            application["offers"] = {
                "@type": "Offer",
                "price": str(PLAN_DEFINITIONS["demo"].monthly_price),
                "priceCurrency": PLAN_DEFINITIONS["demo"].currency,
            }
        json_ld.append(application)
    json_ld.append(
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title,
            "description": description,
            "url": _absolute_url(settings, path),
            "isPartOf": {
                "@type": "WebSite",
                "name": SITE_NAME,
                "url": _absolute_url(settings, "/"),
            },
            "primaryImageOfPage": {
                "@type": "ImageObject",
                "url": social_image_url,
                "width": 1200,
                "height": 630,
            },
            }
        )
    try:
        card_provider = configured_billing_provider(settings, "card")
    except BillingError:
        card_provider = None
    try:
        crypto_provider = configured_billing_provider(settings, "crypto")
    except BillingError:
        crypto_provider = None
    primary_billing_provider = card_provider or crypto_provider or settings.billing_provider
    plan_codes = visible_public_plan_codes(billing_enabled=settings.billing_enabled)
    # In waitlist mode the landing page shows no prices, so it is not handed any. A price
    # sitting in the source of a page that displays none is a price nobody maintains, and
    # it is the one that ends up quoted after the real one changes.
    public_pricing_plans = [] if waitlist_mode else [
        {
            "code": code,
            "name": PLAN_DEFINITIONS[code].name,
            "description": PUBLIC_PLAN_PRESENTATIONS[code].description,
            "button": PUBLIC_PLAN_PRESENTATIONS[code].cta_label,
            "badge": PUBLIC_PLAN_PRESENTATIONS[code].badge,
            "trialNote": PUBLIC_PLAN_PRESENTATIONS[code].trial_note,
            "visibleFeatures": list(PUBLIC_PLAN_PRESENTATIONS[code].visible_features),
            "additionalFeatures": list(
                PUBLIC_PLAN_PRESENTATIONS[code].additional_features
            ),
            "highlightedFeature": PUBLIC_PLAN_PRESENTATIONS[code].highlighted_feature,
            # `monthlyPrice`, `originalMonthlyPrice` and the two availability flags all
            # come from `plan_offer_payload`, so the landing page cannot show a price the
            # dashboard disagrees with.
            **plan_offer_payload(code),
        }
        for code in plan_codes
    ]
    annual_billing_supported = settings.billing_enabled and (
        (
            card_provider == "creem"
            and all(
                f"{plan_code}_annual" in settings.creem_product_ids
                for plan_code in ("trader", "pro")
            )
        )
        or (
            card_provider == "stripe"
            and all(
                f"{plan_code}_annual" in settings.stripe_price_ids
                for plan_code in ("trader", "pro")
            )
        )
    )
    return {
        "request": request,
        "settings": settings,
        "site_name": SITE_NAME,
        "page": page,
        "title": title,
        "description": description,
        "canonical_url": _absolute_url(settings, path),
        "social_title": SOCIAL_PREVIEW_TITLE,
        "social_description": SOCIAL_PREVIEW_DESCRIPTION,
        "og_image_url": social_image_url,
        "og_image_alt": (
            "Hilal Markets: Halal Trading With Clarity, from screened assets "
            "to user-defined monitoring rules."
        ),
        "robots_content": (
            "index,follow,max-image-preview:large,max-snippet:-1,"
            "max-video-preview:-1"
        ),
        "json_ld": json_ld,
        # The stage decides what a menu may point at, not `waitlist_mode`. Those two
        # only agree in the `public_waitlist` stage; in `internal` and
        # `private_beta_invite` the boolean is false while the stage still hides
        # Pricing and Halal Assets.
        "public_navigation": public_navigation(hidden_pages=settings.stage_exposure.hidden_pages),
        "footer_navigation": footer_groups,
        # Where the footer's "Cookie settings" link goes. One address, handed to both
        # footers, so the two cannot point at different places.
        "cookie_settings_path": COOKIE_SETTINGS_PATH,
        "social_links": SOCIAL_LINKS,
        # The copyright year. Computed, not written into the template: a hard-coded year
        # is wrong every January and nobody notices until a customer does.
        "current_year": datetime.now(UTC).year,
        # The same menu and the same channels, as plain paths, for the React pages.
        #
        # The React site draws its own header and footer, so without this it would hold
        # a second copy of the menu written by hand — and the two would disagree the
        # first time a page was added to one of them. They read one list now: this one.
        "site_chrome_runtime_config": {
            "footerGroups": [
                {
                    "label": group.label,
                    "items": [
                        {"label": item.label, "href": request.url_for(item.endpoint).path}
                        for item in group.items
                    ],
                }
                for group in footer_groups
            ],
            "social": [
                {"label": link.label, "handle": link.handle, "href": link.href}
                for link in SOCIAL_LINKS
            ],
            # Absolute, on the product's own hostname, whenever it has one. A plain path
            # here kept every visitor who pressed "Start free" on the marketing hostname
            # and served them the whole dashboard from there.
            "dashboardEntryHref": app_link(settings, "/dashboard-entry"),
            "signInHref": app_link(settings, "/signin"),
            "cookieSettingsHref": COOKIE_SETTINGS_PATH,
            "primaryCtaLabel": settings.stage_exposure.primary_cta_label,
        },
        # Pre-launch state of the public site. While it is on, every public page asks
        # the visitor to join the waitlist instead of offering an account or a plan.
        # The wording comes from one place so the header, the closing section on every
        # page and the assistant cannot describe the same state in three different ways.
        "waitlist_mode": waitlist_mode,
        "waitlist_url": WAITLIST_ANCHOR,
        "waitlist_eyebrow": WAITLIST_EYEBROW,
        "waitlist_headline": WAITLIST_HEADLINE,
        "waitlist_body": WAITLIST_BODY,
        "waitlist_cta_label": WAITLIST_CTA_LABEL,
        # The two ways into the product, on the product's own hostname when it has one.
        # See `app_link` below for why these are not plain paths.
        "dashboard_entry_url": app_link(settings, "/dashboard-entry"),
        "signin_url": app_link(settings, "/signin"),
        "signup_url": app_link(settings, "/signup"),
        "support_email": settings.support_email,
        "privacy_email": settings.site_privacy_contact_email or settings.support_email,
        "security_email": settings.site_security_contact_email,
        "telegram_url": (
            f"https://t.me/{telegram_username}?start=landing"
            if telegram_username
            else None
        ),
        "plans": {code: PLAN_DEFINITIONS[code] for code in plan_codes},
        "plan_presentations": PUBLIC_PLAN_PRESENTATIONS,
        # What each plan costs today and whether it can be bought. Read by the Jinja
        # pricing cards; the React landing page reads the same values out of
        # `public_pricing_plans` below.
        "plan_offers": {code: plan_offer(code) for code in plan_codes},
        "plan_offer_values": {code: plan_offer_payload(code) for code in plan_codes},
        "promotion_ends_at": PROMOTION_ENDS_AT.isoformat(),
        "promotion_active": promotion_is_active(),
        "plan_comparison": visible_plan_comparison(
            billing_enabled=settings.billing_enabled
        ),
        "plan_comparison_headers": visible_plan_comparison_headers(
            billing_enabled=settings.billing_enabled
        ),
        "public_pricing_plans": public_pricing_plans,
        "public_plan_comparison": (
            []
            if waitlist_mode
            else [
                list(row)
                for row in visible_plan_comparison(
                    billing_enabled=settings.billing_enabled
                )
            ]
        ),
        "billing_enabled": settings.billing_enabled,
        "billing_provider": primary_billing_provider,
        "billing_capabilities": billing_provider_capabilities(primary_billing_provider),
        "card_checkout_available": settings.billing_enabled and card_provider is not None,
        "crypto_checkout_available": settings.billing_enabled
        and crypto_provider is not None,
        "whatsapp_operational": settings.whatsapp_enabled,
        "annual_billing_supported": annual_billing_supported,
        "purchase_faqs": PURCHASE_FAQS,
        "help_categories": help_categories,
        "cookie_consent_version": (
            settings.cookie_consent_version or COOKIE_CONSENT_VERSION
        ),
        "legal_review_required": legal_review_required,
        "legal_name": settings.site_legal_name,
        "company_address": settings.site_company_address,
        "governing_law": settings.site_governing_law,
        "gtm_container_id": settings.google_tag_manager_container_id,
        "optional_analytics_enabled": settings.optional_analytics_enabled,
        "marketing_consent_enabled": settings.marketing_consent_enabled,
        "public_analytics_enabled": settings.public_analytics_enabled,
        "analytics_runtime_config": {
            "enabled": settings.public_analytics_enabled,
            "gtmId": settings.public_gtm_id,
            "metaPixelId": settings.vite_meta_pixel_id,
            "metaPixelEnabled": settings.vite_meta_pixel_enabled,
            "xPixelId": settings.vite_x_pixel_id,
            "xPixelEnabled": settings.vite_x_pixel_enabled,
            "siteUrl": settings.public_site_url,
            "debug": settings.vite_analytics_debug,
        },
        "public_chat_enabled": settings.public_chat_enabled,
        "site_visit_measurement_enabled": settings.site_visit_measurement_enabled,
        **extra,
    }


async def _render_public_page(
    *,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    page: str,
) -> HTMLResponse:
    metadata = PUBLIC_PAGE_BY_PAGE[page]
    service = PublicSiteReadService(session, settings)
    extra: dict[str, Any] = {}
    if page == "how_we_screen":
        methodology = await service.active_methodology()
        extra["active_methodology"] = (
            service.methodology_view(methodology) if methodology else None
        )
    return templates.TemplateResponse(
        request=request,
        name=metadata.template,
        context=_public_context(
            request,
            settings,
            page=metadata.page,
            title=metadata.title,
            description=metadata.description,
            path=metadata.path,
            legal_review_required=metadata.legal_review_required,
            **extra,
        ),
    )


#: Where the product itself is served, when it has its own address.
#:
#: `APP_BASE_URL` is already the origin the product uses for its own links, its own
#: allowed request origins and its own payment return URLs. Naming the host in one place
#: and reading it here means the app hostname is configured once, not written twice and
#: left to drift.
#:
#: The path is the shared one, not a third copy of the string. This file used to hold
#: `"/main"` of its own, beside identical constants in two dashboard routers, so renaming
#: the page would have left the marketing host redirecting to an address nothing served.
MAIN_DASHBOARD_PATH = HOME_PATH


def app_host(settings: Settings) -> str | None:
    """The hostname the dashboard is served on, or ``None`` if it has none of its own.

    "None of its own" is the important half. Locally, and in any deployment that runs the
    whole product on one name, ``APP_BASE_URL`` and ``PUBLIC_BASE_URL`` are the same host
    — and taking the root over there would replace the landing page with a redirect to
    sign-in for every visitor, including the ones who have never heard of the product.
    So the root only becomes the dashboard when the two names really are different.
    """

    if settings.app_base_url is None:
        return None
    host = (settings.app_base_url.host or "").strip().lower()
    public = (settings.public_base_url.host or "").strip().lower()
    if not host or host == public:
        return None
    return host


def is_app_host(request: Request, settings: Settings) -> bool:
    """Whether this request arrived on the dashboard's own hostname.

    The port is ignored on purpose: `app.hilalmarkets.com` and
    `app.hilalmarkets.com:8000` are the same site, and a local run that forgets the port
    would otherwise be served the marketing page instead of the product.
    """

    wanted = app_host(settings)
    if wanted is None:
        return False
    return (request.url.hostname or "").strip().lower() == wanted


def app_link(settings: Settings, path: str) -> str:
    """A link into the product, on the product's own hostname when it has one.

    The whole point of `APP_BASE_URL` is that the dashboard is served at
    `https://app.hilalmarkets.com`. That was half true: the *root* of that hostname
    served the dashboard, but every way into the product from the marketing site was a
    plain path — "Start free", "Sign in", "Open dashboard" — so a visitor who pressed one
    stayed on `hilalmarkets.com` and used the whole product from there. Two hostnames
    served the same signed-in pages, and the one named after the product was the one
    almost nobody reached.

    When the two names are the same — locally, and in any single-domain install — this
    returns the plain path, so nothing changes and no absolute URL is written into a page
    that does not need one.
    """

    host = app_host(settings)
    if host is None:
        return path
    return f"{str(settings.app_base_url).rstrip('/')}{path}"


@router.get("/", response_class=HTMLResponse, include_in_schema=False, name="public_home")
async def landing_page(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    """The marketing home page — or the product, on the product's own hostname.

    One deployment answers on two names. On `hilalmarkets.com` the root is the landing
    page; on `app.hilalmarkets.com` it is the dashboard, and somebody who is not signed
    in is taken to sign-in from there by the dashboard's own guard rather than by a
    second copy of that rule here.
    """

    if is_app_host(request, settings):
        return RedirectResponse(MAIN_DASHBOARD_PATH, status_code=307)
    return templates.TemplateResponse(
        request=request,
        name="hilal/public/index.html",
        context=_public_context(
            request,
            settings,
            page="landing",
            title=SOCIAL_PREVIEW_TITLE,
            description=SOCIAL_PREVIEW_DESCRIPTION,
            path="/",
        ),
    )


@router.get(
    "/features",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_features",
)
async def features(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="features",
    )


@router.get(
    "/how-it-works",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_how_it_works",
)
async def how_it_works(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="how_it_works",
    )


@router.get(
    "/how-we-screen",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_how_we_screen",
)
async def how_we_screen(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="how_we_screen",
    )


@router.get(
    "/pricing",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_pricing",
)
async def pricing(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if settings.waitlist_mode:
        # The plans and the comparison table are hidden together with every other way
        # to buy. An old link, a bookmark or a search result lands on the waitlist
        # instead of on prices nobody can pay yet.
        return RedirectResponse(WAITLIST_ANCHOR, status_code=303)
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="pricing",
    )


@router.get(
    "/help",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_help",
)
async def help_center(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="help",
    )


@router.get(
    "/contact",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_contact",
)
async def contact(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    metadata = PUBLIC_PAGE_BY_PAGE["contact"]
    return templates.TemplateResponse(
        request=request,
        name=metadata.template,
        context=_public_context(
            request,
            settings,
            page=metadata.page,
            title=metadata.title,
            description=metadata.description,
            path=metadata.path,
        ),
    )


@router.get(
    "/about",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_about",
)
async def about(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="about",
    )


@router.get(
    "/trust-safety",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_trust_safety",
)
async def trust_safety(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="trust_safety",
    )


@router.get(
    "/risk-disclosure",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_risk_disclosure",
)
async def risk_disclosure(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="risk_disclosure",
    )


@router.get(
    "/privacy",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_privacy",
)
async def privacy(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="privacy",
    )


@router.get(
    "/terms",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_terms",
)
async def terms(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="terms",
    )


@router.get(
    "/cookies",
    response_class=HTMLResponse,
    include_in_schema=False,
    name="public_cookies",
)
async def cookies(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return await _render_public_page(
        request=request,
        session=session,
        settings=settings,
        page="cookies",
    )


@router.get("/faq", include_in_schema=False, name="legacy_faq")
async def legacy_faq() -> RedirectResponse:
    return RedirectResponse("/help", status_code=308)


@router.get("/risk", include_in_schema=False, name="legacy_risk")
async def legacy_risk() -> RedirectResponse:
    return RedirectResponse("/risk-disclosure", status_code=308)


@router.get("/sitemap.xml", include_in_schema=False, name="public_sitemap")
async def sitemap(settings: Settings = Depends(get_settings)) -> Response:
    # A page that redirects is not a page to index. The same hidden-page set that empties
    # the menus keeps those addresses out of the sitemap, so the header, the footer and
    # search engines are told the same thing.
    hidden = settings.stage_exposure.hidden_pages
    paths = ["/", *(item.path for item in PUBLIC_PAGES if item.page not in hidden)]
    locations = "".join(
        f"<url><loc>{_absolute_url(settings, path)}</loc></url>" for path in paths
    )
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{locations}</urlset>"
    )
    return Response(payload, media_type="application/xml")


@router.get("/robots.txt", include_in_schema=False, name="public_robots")
async def robots(settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    payload = "\n".join(
        (
            "User-agent: *",
            "Allow: /",
            "Disallow: /dashboard",
            "Disallow: /system-brain",
            "Disallow: /api/",
            f"Sitemap: {_absolute_url(settings, '/sitemap.xml')}",
            "",
        )
    )
    return PlainTextResponse(payload)


@router.get("/dashboard-entry", include_in_schema=False, name="public_dashboard_entry")
async def dashboard_entry(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    user = await WebAuthService(session, settings).current_user(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    return RedirectResponse("/dashboard" if user is not None else "/signup", status_code=303)


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "hilalmarkets",
        "environment": settings.app_env,
        # This exposes no credential or control surface. It lets the evaluator
        # reject a misconfigured target before any paid run, instead of inferring
        # availability solely from APP_ENV=test.
        "evaluator_fault_control_available": evaluator_fault_control_available(settings),
    }


@router.get("/health/deep")
async def deep_health(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    checks: dict[str, str] = {
        "database": "ok",
        "redis": "ok",
        "sharia_admin_notifications": (
            "ok"
            if settings.telegram_enabled and settings.sharia_admin_telegram_chat_id
            else "degraded"
        ),
        "sharia_ai_research": (
            "ok"
            if settings.openai_api_key is not None
            and settings.sharia_ai_service_tier == "flex"
            else "degraded"
        ),
        "sharia_source_policy": (
            "ok"
            if settings.sharia_scraper_obey_robots
            and settings.sharia_scraper_concurrency == 1
            else "degraded"
        ),
    }

    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "error"

    redis_client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await redis_client.ping()
    except Exception:
        checks["redis"] = "error"
    finally:
        await redis_client.aclose()

    status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return {
        "status": status,
        "service": "hilalmarkets",
        "environment": settings.app_env,
        "checks": checks,
    }
