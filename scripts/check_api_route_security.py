from ai_market_monitor.api.route_security import (
    audit_versioned_api_routes,
    iter_versioned_api_routes,
)
from ai_market_monitor.main import app


def main() -> int:
    routes = iter_versioned_api_routes(app)
    if not routes:
        print("FAIL: the route-security audit discovered no /api/v1 routes.")
        return 1
    violations = audit_versioned_api_routes(app)
    if violations:
        print("Unprotected /api/v1 routes:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("PASS: every /api/v1 route is authenticated or explicitly annotated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
