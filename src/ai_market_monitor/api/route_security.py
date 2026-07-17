from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

from fastapi.routing import APIRoute


class ApiRouteExposure(StrEnum):
    PUBLIC = "public"
    SIGNED_WEBHOOK = "signed_webhook"


_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])

AUTHENTICATED_DEPENDENCIES = frozenset(
    {
        "get_admin_principal",
        "get_dashboard_principal",
        "get_onboarding_principal",
        "get_user_principal",
        "require_admin",
        "require_system_brain_access",
    }
)


def public_api(reason: str) -> Callable[[_Endpoint], _Endpoint]:
    """Mark an API route as intentionally public for the release security audit."""

    return _annotate(ApiRouteExposure.PUBLIC, reason)


def signed_webhook(reason: str) -> Callable[[_Endpoint], _Endpoint]:
    """Mark a route authenticated by a provider signature rather than a user session."""

    return _annotate(ApiRouteExposure.SIGNED_WEBHOOK, reason)


def route_exposure(route: Any) -> tuple[ApiRouteExposure, str] | None:
    exposure = getattr(route.endpoint, "__api_route_exposure__", None)
    reason = getattr(route.endpoint, "__api_route_exposure_reason__", None)
    if not exposure or not reason:
        return None
    return ApiRouteExposure(exposure), str(reason)


def route_dependency_names(route: Any) -> set[str]:
    def collect(dependant: Any) -> set[str]:
        names: set[str] = set()
        for dependency in dependant.dependencies:
            name = getattr(dependency.call, "__name__", "")
            if name:
                names.add(name)
            names.update(collect(dependency))
        return names

    dependant = getattr(route, "dependant", None)
    return collect(dependant) if dependant is not None else set()


def iter_versioned_api_routes(
    application: Any,
) -> list[tuple[str, Any]]:
    discovered: list[tuple[str, Any]] = []
    for route in application.routes:
        if isinstance(route, APIRoute):
            if route.path.startswith("/api/v1"):
                discovered.append((route.path, route))
            continue

        # FastAPI 0.128+ keeps included routers lazy. Its effective contexts carry
        # the final path and the include-level dependencies used at request time.
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if not callable(effective_route_contexts):
            continue
        for context in effective_route_contexts():
            if not isinstance(getattr(context, "original_route", None), APIRoute):
                continue
            if context.path.startswith("/api/v1"):
                discovered.append((context.path, context))
    return discovered


def audit_versioned_api_routes(application: Any) -> list[str]:
    violations: list[str] = []
    for path, route in iter_versioned_api_routes(application):
        if route_dependency_names(route) & AUTHENTICATED_DEPENDENCIES:
            continue
        if route_exposure(route) is not None:
            continue
        methods = ",".join(sorted(route.methods or set()))
        violations.append(f"{methods} {path} ({route.endpoint.__name__})")
    return violations


def _annotate(
    exposure: ApiRouteExposure,
    reason: str,
) -> Callable[[_Endpoint], _Endpoint]:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("An API exposure annotation requires a reason.")

    def decorator(endpoint: _Endpoint) -> _Endpoint:
        endpoint.__dict__["__api_route_exposure__"] = exposure.value
        endpoint.__dict__["__api_route_exposure_reason__"] = normalized_reason
        return endpoint

    return decorator
