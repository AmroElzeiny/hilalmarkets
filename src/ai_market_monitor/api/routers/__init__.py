from ai_market_monitor.api.routers.admin import router as admin_router
from ai_market_monitor.api.routers.billing import router as billing_router
from ai_market_monitor.api.routers.dashboard import router as dashboard_router
from ai_market_monitor.api.routers.dashboard_api import router as dashboard_api_router
from ai_market_monitor.api.routers.discord import router as discord_router
from ai_market_monitor.api.routers.investigations import router as investigations_router
from ai_market_monitor.api.routers.on_demand import router as on_demand_router
from ai_market_monitor.api.routers.onboarding import router as onboarding_router
from ai_market_monitor.api.routers.public import router as public_router
from ai_market_monitor.api.routers.status import router as status_router
from ai_market_monitor.api.routers.telegram import router as telegram_router

__all__ = [
    "admin_router",
    "billing_router",
    "dashboard_api_router",
    "dashboard_router",
    "discord_router",
    "investigations_router",
    "on_demand_router",
    "onboarding_router",
    "public_router",
    "status_router",
    "telegram_router",
]
