import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from ai_market_monitor.api.request_guards import apply_request_guards
from ai_market_monitor.api.routers import (
    activity_router,
    admin_router,
    billing_router,
    dashboard_api_router,
    dashboard_router,
    dashboard_test_router,
    hilal_chat_router,
    investigations_router,
    main_dashboard_router,
    monitor_canvas_router,
    on_demand_router,
    onboarding_router,
    public_chat_router,
    public_forms_router,
    public_router,
    sharia_router,
    site_analytics_router,
    status_router,
    system_brain_router,
    telegram_router,
    whatsapp_router,
)
from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.logging import configure_logging
from ai_market_monitor.core.startup import validate_runtime_configuration
from ai_market_monitor.observability.asgi import record_http_request

PACKAGE_DIR = Path(__file__).resolve().parent


async def _write_measurements_down(settings: Settings) -> None:
    """Periodically store this web process's measurements, for as long as it runs.

    Recording stays in memory so that it cannot slow a request down or fail one. That
    only becomes durable if something writes it out, and a web process has no
    scheduler of its own — so it keeps this one timer.

    Every failure is swallowed on purpose. Losing the database for a minute must cost
    a delayed measurement, never a dead web process; the numbers stay in memory and go
    out on the next pass.
    """

    from ai_market_monitor.core.database import SessionFactory
    from ai_market_monitor.observability.durable_metrics import flush_metrics_once

    interval = max(settings.observability_flush_interval_seconds, 5)
    policy = settings.metric_retention_policy
    while True:
        try:
            await asyncio.sleep(interval)
            async with SessionFactory() as session:
                await flush_metrics_once(session, policy=policy)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).warning(
                "Could not write operational measurements down", exc_info=True
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    validate_runtime_configuration(settings)
    configure_logging(settings.log_level)
    from ai_market_monitor.services.capability_registry import initialize_capability_registry

    await initialize_capability_registry(settings)
    flusher = asyncio.create_task(_write_measurements_down(settings))
    try:
        yield
    finally:
        flusher.cancel()
        with suppress(asyncio.CancelledError):
            await flusher
        from ai_market_monitor.api.dependencies import get_market_data_provider
        from ai_market_monitor.services.provider_runtime import shutdown_provider_runtime

        provider = get_market_data_provider()
        close = getattr(provider, "close", None)
        if close is not None:
            await close()
        # The pooled provider clients are process-scoped, so they are closed here rather
        # than per call. Skipping this leaks sockets across a reload until the process is
        # killed, which looks like a slow connection leak nobody can attribute.
        await shutdown_provider_runtime()


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Crypto spot-market monitoring and decision support. "
            "The service does not execute trades."
        ),
        lifespan=lifespan,
    )
    static_directory = PACKAGE_DIR / "static"
    if static_directory.is_dir():
        application.mount("/static", StaticFiles(directory=static_directory), name="static")

    # The Builder's own contract — every mechanic, every parameter, every option the
    # server permits — is 2 MB of JSON, and it was being sent uncompressed on every
    # visit to a page that draws a rule. It compresses to about 64 KB, because the
    # payload is mostly the same option lists repeated 512 times.
    #
    # Applied to the whole application rather than one route: the same is true of the
    # dashboard's HTML, its stylesheets and its scripts. Starlette leaves
    # `text/event-stream` alone by default, so the System Brain's live stream is
    # unaffected, and a client that does not offer gzip still receives plain bytes.
    application.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    @application.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        # The one place an HTTP request is measured. The timing was already here for
        # the response header; the metrics ride the same measurement rather than a
        # second middleware taking its own clock reading of the same request.
        started = perf_counter()
        guarded = await apply_request_guards(request, settings)
        if guarded is not None:
            elapsed_ms = (perf_counter() - started) * 1000
            guarded.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.3f}"
            # A guard rejection is still an answered request. Leaving it out would
            # hide every rate-limit and CSRF refusal from the availability objective.
            record_http_request(
                scope=request.scope,
                method=request.method,
                status_code=guarded.status_code,
                duration_ms=elapsed_ms,
            )
            return guarded
        response = await call_next(request)
        elapsed_ms = (perf_counter() - started) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.3f}"
        record_http_request(
            scope=request.scope,
            method=request.method,
            status_code=response.status_code,
            duration_ms=elapsed_ms,
        )
        return response

    application.include_router(public_router)
    application.include_router(public_chat_router, prefix="/api/v1")
    application.include_router(public_forms_router, prefix="/api/v1")
    application.include_router(site_analytics_router, prefix="/api/v1")
    application.include_router(dashboard_router)
    application.include_router(dashboard_test_router)
    application.include_router(main_dashboard_router)
    application.include_router(activity_router, prefix="/api/v1")
    application.include_router(dashboard_api_router, prefix="/api/v1")
    application.include_router(hilal_chat_router, prefix="/api/v1")
    application.include_router(monitor_canvas_router, prefix="/api/v1")
    application.include_router(onboarding_router, prefix="/api/v1")
    application.include_router(on_demand_router, prefix="/api/v1")
    application.include_router(investigations_router, prefix="/api/v1")
    application.include_router(billing_router, prefix="/api/v1")
    application.include_router(status_router, prefix="/api/v1")
    application.include_router(telegram_router, prefix="/api/v1")
    if settings.whatsapp_enabled:
        application.include_router(whatsapp_router, prefix="/api/v1")
    application.include_router(admin_router, prefix="/api/v1")
    application.include_router(sharia_router, prefix="/api/v1")
    application.include_router(system_brain_router)
    return application


app = create_app()
