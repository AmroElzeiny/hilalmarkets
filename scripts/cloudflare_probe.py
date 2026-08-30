"""Ask Cloudflare what the site's edge is actually configured to do, and read pages behind it.

    python scripts/cloudflare_probe.py                     # zone configuration, read only
    python scripts/cloudflare_probe.py --page /system-brain  # fetch a page through Access

Two different credentials are involved and they are not interchangeable. Confusing them
is the reason this script exists to be read as much as run:

===========================  ==========================  ==================================
What you want                Credential                  How it travels
===========================  ==========================  ==================================
Read zone settings, DNS,     ``CLOUDFLARE_API_TOKEN``    ``Authorization: Bearer ...`` to
TLS mode, purge cache                                    ``api.cloudflare.com``
Open a page that sits        service token client ID     ``CF-Access-Client-Id`` and
behind Cloudflare Access     **and secret**              ``CF-Access-Client-Secret`` to the
                                                         site itself
===========================  ==========================  ==================================

An API token sent to the site does nothing at all. Cloudflare Access does not look at
``Authorization``; it looks for its own two headers or a login cookie, and without them it
answers ``302`` to the login screen no matter how powerful the API token is. That redirect
is the signal to check the service token, never a reason to widen the API token's scope.

Nothing here writes. Cache purge is deliberately not implemented: this script is the thing
you run when you are unsure, so it cannot be the thing that changes production.

Values are never printed. A credential is reported as present or absent, never echoed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_market_monitor.core.config import Settings  # noqa: E402

API = "https://api.cloudflare.com/client/v4"
TIMEOUT = httpx.Timeout(15.0)

#: Read-only endpoints worth knowing about, and the token permission each one needs. A
#: denial is reported rather than raised: an incomplete token should still answer every
#: question it *can*, and name the permission to add for the rest.
ZONE_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("zones/{zone}", "zone", "Zone -> Zone -> Read"),
    ("zones/{zone}/settings/ssl", "TLS mode", "Zone -> Zone Settings -> Read"),
    ("zones/{zone}/settings/always_use_https", "always HTTPS", "Zone -> Zone Settings -> Read"),
    ("zones/{zone}/dns_records", "DNS records", "Zone -> DNS -> Read"),
)


def _get(client: httpx.Client, path: str) -> tuple[int, Any]:
    response = client.get(f"{API}/{path}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _describe(label: str, body: Any) -> str:
    """One plain line per check. Shapes differ per endpoint, so each is named."""

    result = body.get("result") if isinstance(body, dict) else None
    if label == "zone" and isinstance(result, dict):
        plan = result.get("plan")
        plan_name = plan.get("name") if isinstance(plan, dict) else "?"
        return f"{result.get('name')}  status={result.get('status')}  plan={plan_name}"
    if isinstance(result, dict) and "value" in result:
        return str(result["value"])
    if isinstance(result, list):
        kinds = sorted({str(row.get("type")) for row in result if isinstance(row, dict)})
        proxied = sum(1 for row in result if isinstance(row, dict) and row.get("proxied"))
        return f"{len(result)} records ({', '.join(kinds)}), {proxied} proxied through Cloudflare"
    return "(no readable result)"


def exposed_hostnames(records: Any) -> list[str]:
    """Web records that answer with the origin's own address.

    This is the question underneath every other protection on the admin console. Cloudflare
    Access runs at Cloudflare's edge, so it can only guard a hostname whose traffic goes
    *through* Cloudflare. A record left unproxied publishes the origin address, and anyone
    who requests that hostname directly meets the application with no Access gate in front
    of it -- the login page never appears, because Cloudflare was never in the path.

    Only A, AAAA and CNAME are considered. MX and TXT cannot be proxied and their presence
    in the unproxied count is normal, not a finding.
    """

    if not isinstance(records, list):
        return []
    return sorted(
        str(row.get("name"))
        for row in records
        if isinstance(row, dict)
        and row.get("type") in {"A", "AAAA", "CNAME"}
        and not row.get("proxied")
    )


def probe_zone(settings: Settings) -> int:
    token = settings.cloudflare_api_token
    if token is None or not token.get_secret_value().strip():
        print("CLOUDFLARE_API_TOKEN is not set. Nothing to ask.")
        return 1
    zone = settings.cloudflare_zone_id.strip()

    headers = {"Authorization": f"Bearer {token.get_secret_value().strip()}"}
    with httpx.Client(headers=headers, timeout=TIMEOUT) as client:
        status, body = _get(client, "user/tokens/verify")
        ok = isinstance(body, dict) and body.get("success")
        print(f"token          : {'active' if ok else f'REJECTED (HTTP {status})'}")
        if not ok:
            return 1

        status, body = _get(client, "zones")
        zones = body.get("result") or [] if isinstance(body, dict) else []
        print(f"zones reachable: {len(zones)}")
        for row in zones:
            mark = " <- configured" if row.get("id") == zone else ""
            print(f"    {row.get('name')}  {row.get('status')}{mark}")

        if not zone:
            print("\nCLOUDFLARE_ZONE_ID is not set, so the per-zone checks are skipped.")
            return 1

        print()
        missing: list[str] = []
        exposed: list[str] = []
        for path, label, permission in ZONE_CHECKS:
            status, body = _get(client, path.format(zone=zone))
            if status == 200:
                print(f"{label:<14} : {_describe(label, body)}")
                if label == "DNS records":
                    exposed = exposed_hostnames(body.get("result"))
            else:
                print(f"{label:<14} : unavailable (HTTP {status})")
                missing.append(permission)

        if exposed:
            print("\nThese hostnames answer with the origin address, not Cloudflare's:")
            for hostname in exposed:
                print(f"    {hostname}")
            print(
                "Cloudflare Access cannot guard a hostname it never sees. If any of these\n"
                "serves the application, the admin console is reachable without the login\n"
                "gate. Proxy it (the orange cloud) or make sure it serves nothing."
            )
        if missing:
            print("\nAdd these token permissions to answer the rest:")
            for permission in sorted(set(missing)):
                print(f"    {permission}")
    return 0


def probe_page(settings: Settings, path: str) -> int:
    """Fetch one page through Cloudflare Access using the service token."""

    client_id = settings.system_brain_access_client_id.strip()
    secret = settings.system_brain_access_client_secret
    secret_value = secret.get_secret_value().strip() if secret is not None else ""
    print(f"client id      : {'set' if client_id else 'MISSING'}")
    print(f"client secret  : {'set' if secret_value else 'MISSING'}")
    if not client_id or not secret_value:
        print(
            "\nBoth halves of the service token are needed. Create one in Cloudflare:\n"
            "  Zero Trust -> Access -> Service Auth -> Create Service Token,\n"
            "then add it to the Access policy that guards this path."
        )
        return 1

    url = f"{str(settings.public_base_url).rstrip('/')}/{path.lstrip('/')}"
    headers = {"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": secret_value}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        response = client.get(url, headers=headers)

    print(f"\nGET {url}\n  HTTP {response.status_code}")
    location = response.headers.get("location", "")
    if "cloudflareaccess.com" in location:
        # The single most confusing outcome, so it is named rather than left as a 302.
        print(
            "  Cloudflare Access refused the service token and is asking for a login.\n"
            "  The token itself is fine; it is not on the Access policy for this path.\n"
            "  Add it: Zero Trust -> Access -> Applications -> this app -> Policies ->\n"
            "  a policy with Include -> Service Auth -> your token."
        )
        return 1
    if response.status_code == 403:
        # Two different gates answer 403 and the body does not always say which, so both
        # are named rather than guessing. Naming only the first sends someone to edit an
        # env file when the real answer is that a service token is not a signed-in admin.
        print(
            "  Past Cloudflare, refused by the application. Two gates can say this:\n"
            "    1. The Access check, if the client ID is not in\n"
            "       SYSTEM_BRAIN_ACCESS_SERVICE_TOKEN_IDS.\n"
            "    2. The admin check, which wants a signed-in ADMIN session. A service\n"
            "       token is not a person and never has one, so an admin-only page stays\n"
            "       shut to it by design. This is the expected answer for /system-brain."
        )
        return 1
    if response.status_code == 200:
        print(f"  Reached the page, {len(response.content)} bytes.")
        return 0
    print(f"  location={location or '(none)'}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", help="path to fetch through Access, e.g. /system-brain")
    arguments = parser.parse_args()
    settings = Settings()
    if arguments.page:
        return probe_page(settings, arguments.page)
    return probe_zone(settings)


if __name__ == "__main__":
    raise SystemExit(main())
