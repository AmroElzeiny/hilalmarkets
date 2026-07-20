from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import get_db_session
from ai_market_monitor.core.plans import PLAN_DEFINITIONS, visible_public_plan_codes
from ai_market_monitor.core.site_content import (
    COOKIE_CONSENT_VERSION,
    FOOTER_NAVIGATION,
    HELP_CATEGORIES,
    PUBLIC_NAVIGATION,
    PUBLIC_PAGE_BY_PAGE,
    PUBLIC_PAGES,
    PURCHASE_FAQS,
    SITE_DESCRIPTION,
    SITE_NAME,
    HelpArticle,
    PurchaseFaq,
)
from ai_market_monitor.services.billing import billing_provider_capabilities
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
            "logo": _absolute_url(settings, "/static/hilalmarkets-logo-mark.svg"),
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
    if page == "landing":
        json_ld.append(_faq_json_ld(PURCHASE_FAQS))
    elif page == "help":
        json_ld.append(
            _faq_json_ld(
                [
                    article
                    for category in HELP_CATEGORIES
                    for article in category["articles"]
                ]
            )
        )
    if page in {"landing", "features", "pricing"}:
        json_ld.append(
            {
                "@context": "https://schema.org",
                "@type": "SoftwareApplication",
                "name": SITE_NAME,
                "applicationCategory": "FinanceApplication",
                "operatingSystem": "Web",
                "url": _absolute_url(settings, path),
                "description": description,
                "offers": {
                    "@type": "Offer",
                    "price": str(PLAN_DEFINITIONS["demo"].monthly_price),
                    "priceCurrency": PLAN_DEFINITIONS["demo"].currency,
                },
            }
        )
    return {
        "request": request,
        "settings": settings,
        "site_name": SITE_NAME,
        "page": page,
        "title": title,
        "description": description,
        "canonical_url": _absolute_url(settings, path),
        "og_image_url": str(settings.public_og_image_url) if settings.public_og_image_url else None,
        "json_ld": json_ld,
        "public_navigation": PUBLIC_NAVIGATION,
        "footer_navigation": FOOTER_NAVIGATION,
        "dashboard_entry_url": "/dashboard-entry",
        "support_email": settings.support_email,
        "privacy_email": settings.site_privacy_contact_email or settings.support_email,
        "security_email": settings.site_security_contact_email,
        "telegram_url": (
            f"https://t.me/{telegram_username}?start=landing"
            if telegram_username
            else None
        ),
        "plans": {
            code: PLAN_DEFINITIONS[code]
            for code in visible_public_plan_codes(billing_enabled=settings.billing_enabled)
        },
        "billing_enabled": settings.billing_enabled,
        "billing_provider": settings.billing_provider,
        "billing_capabilities": billing_provider_capabilities(settings.billing_provider),
        "purchase_faqs": PURCHASE_FAQS,
        "help_categories": HELP_CATEGORIES,
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
            "ga4MeasurementId": settings.vite_ga4_measurement_id,
            "metaPixelId": settings.vite_meta_pixel_id,
            "metaPixelEnabled": settings.vite_meta_pixel_enabled,
            "siteUrl": settings.public_site_url,
            "debug": settings.vite_analytics_debug,
        },
        "public_chat_enabled": settings.public_chat_enabled,
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


@router.get("/", response_class=HTMLResponse, include_in_schema=False, name="public_home")
async def landing_page(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="hilal/public/index.html",
        context=_public_context(
            request,
            settings,
            page="landing",
            title="Strategy monitoring for Muslim crypto traders",
            description=(
                "Build trading strategies, explore Shariah-screened assets, and "
                "monitor every setup in one place."
            ),
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
) -> HTMLResponse:
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
    paths = ["/", *(item.path for item in PUBLIC_PAGES)]
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
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "hilalmarkets",
        "environment": settings.app_env,
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
