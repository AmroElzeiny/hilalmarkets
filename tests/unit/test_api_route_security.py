from fastapi import APIRouter, Depends, FastAPI

from ai_market_monitor.api.route_security import (
    audit_versioned_api_routes,
    iter_versioned_api_routes,
    route_exposure,
)
from ai_market_monitor.main import app


def test_route_audit_traverses_lazily_included_fastapi_routers() -> None:
    application = FastAPI()
    router = APIRouter()

    def get_user_principal() -> None:
        return None

    @router.get("/private", dependencies=[Depends(get_user_principal)])
    async def private_route() -> dict[str, bool]:
        return {"ok": True}

    application.include_router(router, prefix="/api/v1")

    routes = iter_versioned_api_routes(application)
    assert [path for path, _route in routes] == ["/api/v1/private"]
    assert audit_versioned_api_routes(application) == []


def test_every_api_route_is_authenticated_or_explicitly_annotated() -> None:
    routes = iter_versioned_api_routes(app)
    assert routes, "The route-security audit did not discover any /api/v1 routes."
    unsecured = audit_versioned_api_routes(app)
    assert not unsecured, (
        "Every /api/v1 route must have an authenticated principal dependency or an "
        "explicit public/signed-webhook annotation:\n" + "\n".join(unsecured)
    )


def test_public_route_annotations_have_a_specific_reason() -> None:
    vague = {
        "public",
        "public endpoint",
        "webhook",
        "signed webhook",
    }
    invalid: list[str] = []
    for path, route in iter_versioned_api_routes(app):
        annotation = route_exposure(route)
        if annotation is None:
            continue
        _exposure, reason = annotation
        if len(reason) < 20 or reason.lower() in vague:
            invalid.append(f"{path}: {reason!r}")
    assert not invalid, "API exposure reasons must be specific:\n" + "\n".join(invalid)
