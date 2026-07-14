from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from ai_market_monitor.api.routers import (
    activity_router,
    admin_router,
    billing_router,
    dashboard_api_router,
    dashboard_router,
    discord_router,
    investigations_router,
    on_demand_router,
    onboarding_router,
    public_router,
    sharia_router,
    status_router,
    system_brain_router,
    telegram_router,
)
from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.logging import configure_logging
from ai_market_monitor.core.startup import validate_runtime_configuration

PACKAGE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    validate_runtime_configuration(settings)
    configure_logging(settings.log_level)
    from ai_market_monitor.services.capability_registry import initialize_capability_registry

    await initialize_capability_registry(settings)
    try:
        yield
    finally:
        from ai_market_monitor.api.dependencies import get_market_data_provider

        await get_market_data_provider().close()


def create_app() -> FastAPI:
    settings = get_settings()
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

    @application.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(perf_counter() - started) * 1000:.3f}"
        return response

    application.include_router(public_router)
    application.include_router(dashboard_router)
    application.include_router(activity_router, prefix="/api/v1")
    application.include_router(dashboard_api_router, prefix="/api/v1")
    application.include_router(onboarding_router, prefix="/api/v1")
    application.include_router(on_demand_router, prefix="/api/v1")
    application.include_router(investigations_router, prefix="/api/v1")
    application.include_router(billing_router, prefix="/api/v1")
    application.include_router(discord_router, prefix="/api/v1")
    application.include_router(status_router, prefix="/api/v1")
    application.include_router(telegram_router, prefix="/api/v1")
    application.include_router(admin_router, prefix="/api/v1")
    application.include_router(sharia_router, prefix="/api/v1")
    application.include_router(system_brain_router)
    return application


app = create_app()
