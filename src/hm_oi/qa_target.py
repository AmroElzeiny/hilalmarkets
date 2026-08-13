"""Which target the adversarial QA harness is pointed at, and what it may do there.

This is an *attacker*. It sends wording designed to make the product break its own
rules, and where the target supports it, it injects provider faults. Both are fine
against a throwaway copy and catastrophic against the real one, so the first thing the
harness does — before a single request — is work out which it is looking at.

Three separate refusals live here, and they fail for different reasons:

**Production, by address.** A host that is not loopback and not on the staging
allowlist is refused outright. There is no override flag. The product's own browser
suite has ``ALLOW_PROD_E2E``, and it exists because a read-only page check against a
staging URL is a reasonable thing to want. Nothing here is read-only in that sense, so
the same escape hatch would be a liability rather than a convenience.

**Production, by what the target says about itself.** ``GET /health`` reports
``environment`` — the server's own ``APP_ENV``. A tunnel, a port-forward or a hosts-file
entry can all put a production application behind ``127.0.0.1``, and the address alone
would say "local". So the address and the confession must *both* be non-production, and
the harness stops if they disagree with each other at all.

**Fault injection, by capability.** ``/health`` also reports
``evaluator_fault_control_available``, which the product computes from
``app_env == "test"`` plus two settings (``services/ai_setup_evaluator_control.py:37``).
A target that answers ``false`` cannot take a fault, and the harness must say so rather
than send the request and read the resulting refusal as a product defect. That is the
specific failure this module exists to prevent: an attacker that cannot tell "this
product is broken" from "this target does not support the thing I just tried" produces
findings that are entirely noise.

**Nothing here starts a server.** ``scripts/run_isolated_setup_chat_smoke.ps1`` already
brings up an isolated ``APP_ENV=test`` application with its own SQLite database, its own
secret key, mock providers, and every outbound channel switched off — and it restores the
caller's environment afterwards, which a second launcher would get wrong. This module
points at what that script started and checks it; :data:`ISOLATED_LAUNCHER` names it so
the runbook and the harness cannot drift.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlparse

__all__ = [
    "FaultInjectionUnsupported",
    "ISOLATED_LAUNCHER",
    "LOOPBACK_HOSTS",
    "PRODUCTION_HOST_MARKERS",
    "STAGING_HOST_ALLOWLIST",
    "SUPPORTED_FAULTS",
    "TargetKind",
    "TargetProfile",
    "TargetRefused",
    "classify_target",
    "describe_target",
    "probe_health",
    "require_fault_injection",
]

#: The launcher this harness reuses. Named here so the runbook, the tests and the
#: harness all refer to one script rather than three copies of its behaviour.
ISOLATED_LAUNCHER: Final[str] = "scripts/run_isolated_setup_chat_smoke.ps1"

#: Addresses that are this machine. Anything else needs to be on the staging allowlist.
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
)

#: Hosts a person may deliberately point this at. Deliberately empty in the checked-in
#: copy: there is no staging deployment for HilalMarkets today, and an allowlist with a
#: speculative entry in it is an allowlist somebody will eventually match by accident.
#: Adding one is a reviewed edit to this file, not a setting.
STAGING_HOST_ALLOWLIST: Final[frozenset[str]] = frozenset()

#: Shapes in a hostname that mean "this is the real thing". Checked in addition to the
#: allowlist, so widening the allowlist by mistake still cannot reach production.
PRODUCTION_HOST_MARKERS: Final[tuple[str, ...]] = (
    "hilalmarkets",
    "hilal-markets",
    "prod",
    "production",
    "live",
    "www.",
    "api.",
    "app.",
)

#: The faults the product's own evaluator control accepts, copied from
#: ``services/ai_setup_evaluator_control.py:13``. The harness cannot import it — the
#: engineering/product boundary check refuses that — so the contract is asserted by
#: ``tests/oi/test_invariant_adversarial_qa.py`` instead, which reads both and compares.
SUPPORTED_FAULTS: Final[frozenset[str]] = frozenset(
    {
        "timeout_once",
        "429_once",
        "empty_once",
        "invalid_json_once",
        "partial_json_once",
        "stream_disconnect_once",
    }
)

#: ``APP_ENV`` values a target may report. ``staging`` is here because the product treats
#: it as production-like (``core/config.py:930``) and this harness therefore treats a
#: staging target as attackable only when its address was explicitly allowlisted above.
_NON_PRODUCTION_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"test", "development"})


class TargetRefused(PermissionError):
    """This target may not be attacked.

    ``PermissionError`` rather than ``ValueError``: the request is usually perfectly
    sensible and the answer is still no.
    """


class FaultInjectionUnsupported(RuntimeError):
    """The target cannot take an injected fault.

    Separate from :class:`TargetRefused` on purpose. Refusing a target is a safety stop;
    this one means the attack simply does not apply here, and the correct outcome is to
    skip it and say so — never to record the target's refusal as a product finding.
    """


class TargetKind(StrEnum):
    """What sort of deployment this is."""

    #: ``APP_ENV=test`` with the evaluator controls on. The only kind that takes faults.
    ISOLATED_TEST = "isolated_test"
    #: A developer's own server. Real enough to attack, no fault injection.
    LOCAL = "local"
    #: An allowlisted non-production deployment.
    STAGING = "staging"
    #: Refused. Never attacked, never probed further.
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """One target, classified, with the evidence for the classification.

    ``evidence`` is carried rather than recomputed because it is what a person reads
    when they want to know why the harness refused to do something.
    """

    base_url: str
    host: str
    kind: TargetKind
    #: What the server said its ``APP_ENV`` was. ``None`` when ``/health`` was unreachable.
    app_env: str | None
    #: What ``/health`` reported for ``evaluator_fault_control_available``.
    fault_control_reported: bool
    evidence: tuple[str, ...] = ()

    @property
    def supports_fault_injection(self) -> bool:
        """Whether a fault may be *sent*. Not a promise that it will be *observed*.

        Both conditions are required. ``ISOLATED_TEST`` alone is not enough: the two
        evaluator settings can be off in a perfectly valid ``APP_ENV=test`` server, and
        the harness would then read the product's correct refusal as a defect.

        **The honest limit, measured on 2026-08-14.** ``/health`` answering ``true`` here
        means the server is *configured* to accept a fault. It is not proof that an
        injected fault reaches the model boundary and comes back marked. On this
        repository at ``211aecc5`` the flag was ``true``, the evaluator's doctor check
        passed, and the fault probe still came back without its
        ``X-HM-Eval-Fault-Applied`` marker — recorded as finding OI4-008.

        So a fault attack that produces nothing must be reported as **not verified**,
        never as "the product handled it correctly". Proving the difference needs the
        marker, and that is the evaluator's readiness gate, not this flag.
        """

        return self.kind is TargetKind.ISOLATED_TEST and self.fault_control_reported

    @property
    def is_production(self) -> bool:
        return self.kind is TargetKind.PRODUCTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "host": self.host,
            "kind": str(self.kind),
            "app_env": self.app_env,
            "fault_control_reported": self.fault_control_reported,
            "supports_fault_injection": self.supports_fault_injection,
            "evidence": list(self.evidence),
        }


def _host_of(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise TargetRefused(
            f"{base_url!r} is not a usable target address. Give a full URL, for example "
            "http://127.0.0.1:8124."
        )
    return (parsed.hostname or "").casefold()


def _address_verdict(host: str) -> tuple[bool, str]:
    """Whether the address alone is acceptable, and why."""

    if host in LOOPBACK_HOSTS:
        return True, f"host {host!r} is loopback"
    if host in STAGING_HOST_ALLOWLIST:
        return True, f"host {host!r} is on the staging allowlist"
    for marker in PRODUCTION_HOST_MARKERS:
        if marker in host:
            return False, f"host {host!r} contains {marker!r}, which names a real deployment"
    return False, (
        f"host {host!r} is neither loopback nor on the staging allowlist "
        "(hm_oi.qa_target.STAGING_HOST_ALLOWLIST)"
    )


def classify_target(
    base_url: str,
    *,
    app_env: str | None,
    fault_control_available: bool,
) -> TargetProfile:
    """Decide what this target is from its address and its own ``/health`` answer.

    Pure, so the whole decision table is testable without a server. The address and the
    reported environment must agree: a loopback address in front of a production
    application is exactly the case a tunnel produces, and it is the case where getting
    this wrong costs the most.
    """

    host = _host_of(base_url)
    address_ok, address_reason = _address_verdict(host)
    reported = (app_env or "").strip().casefold()
    evidence = [address_reason]

    if not address_ok:
        return TargetProfile(
            base_url=base_url,
            host=host,
            kind=TargetKind.PRODUCTION,
            app_env=reported or None,
            fault_control_reported=bool(fault_control_available),
            evidence=(*evidence, "address refused before anything was sent"),
        )

    if reported and reported not in _NON_PRODUCTION_ENVIRONMENTS:
        # The address said local, the server said production. Believe the server.
        evidence.append(
            f"the server reports APP_ENV={reported!r}, which is production-like; "
            "a loopback address in front of it means a tunnel or a port-forward"
        )
        return TargetProfile(
            base_url=base_url,
            host=host,
            kind=TargetKind.PRODUCTION,
            app_env=reported,
            fault_control_reported=bool(fault_control_available),
            evidence=tuple(evidence),
        )

    if not reported:
        evidence.append(
            "the target did not report an environment, so it is treated as an ordinary "
            "local server and no fault may be injected"
        )
        kind = TargetKind.STAGING if host in STAGING_HOST_ALLOWLIST else TargetKind.LOCAL
        return TargetProfile(
            base_url=base_url,
            host=host,
            kind=kind,
            app_env=None,
            fault_control_reported=False,
            evidence=tuple(evidence),
        )

    evidence.append(f"the server reports APP_ENV={reported!r}")
    if host in STAGING_HOST_ALLOWLIST:
        kind = TargetKind.STAGING
    elif reported == "test":
        kind = TargetKind.ISOLATED_TEST
    else:
        kind = TargetKind.LOCAL

    if kind is TargetKind.ISOLATED_TEST and not fault_control_available:
        evidence.append(
            "evaluator_fault_control_available is false, so the two evaluator settings "
            "are off; fault attacks will be skipped, not reported as defects"
        )

    return TargetProfile(
        base_url=base_url,
        host=host,
        kind=kind,
        app_env=reported,
        fault_control_reported=bool(fault_control_available),
        evidence=tuple(evidence),
    )


def probe_health(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Read ``GET /health``. Refuses the address before opening the connection.

    The address check runs first on purpose. Asking a production server what it is would
    already be a request to production, and "we only looked" is not a distinction worth
    relying on when the alternative costs nothing.
    """

    host = _host_of(base_url)
    address_ok, reason = _address_verdict(host)
    if not address_ok:
        raise TargetRefused(
            f"Refusing to open a connection to {base_url!r}: {reason}. This harness "
            "attacks a throwaway copy only. Start one with "
            f"{ISOLATED_LAUNCHER} and point it at that."
        )

    url = f"{base_url.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise TargetRefused(
            f"{url} did not answer ({exc}). The harness will not attack a target it "
            f"cannot identify. Start one with {ISOLATED_LAUNCHER}."
        ) from exc
    if not isinstance(payload, dict):
        raise TargetRefused(f"{url} answered something that is not a health document.")
    return payload


def describe_target(base_url: str, *, timeout: float = 5.0) -> TargetProfile:
    """Probe a running target and classify it. Raises rather than returning a production profile."""

    payload = probe_health(base_url, timeout=timeout)
    profile = classify_target(
        base_url,
        app_env=str(payload.get("environment") or "") or None,
        fault_control_available=bool(payload.get("evaluator_fault_control_available")),
    )
    if profile.is_production:
        raise TargetRefused(
            f"Refusing {base_url!r}: "
            + "; ".join(profile.evidence)
            + ". Nothing in this phase runs against production."
        )
    return profile


def require_fault_injection(profile: TargetProfile, fault: str) -> str:
    """Check a fault may be sent to this target, and that the product knows the name.

    Returns the fault so a caller can write ``fault = require_fault_injection(...)`` and
    have no path that sends an unchecked value.
    """

    name = str(fault or "").strip()
    if name not in SUPPORTED_FAULTS:
        raise FaultInjectionUnsupported(
            f"{name!r} is not a fault the product accepts. Supported: "
            + ", ".join(sorted(SUPPORTED_FAULTS))
        )
    if profile.is_production:
        raise TargetRefused("Fault injection against production is refused.")
    if not profile.supports_fault_injection:
        raise FaultInjectionUnsupported(
            f"This target ({profile.kind.value}, APP_ENV={profile.app_env!r}) does not "
            "accept injected faults. Its refusal is correct behaviour and must not be "
            "recorded as a finding. Restart the target with "
            f"{ISOLATED_LAUNCHER} -EnableFaults to run fault attacks."
        )
    return name


#: A database address that is not the harness's own throwaway file. Used by the launcher
#: check so a target started by hand against the developer's working database is caught
#: before the harness writes to it.
_SHARED_DATABASE_RE: Final[re.Pattern[str]] = re.compile(
    r"postgres(?:ql)?(?:\+\w+)?://|mysql://|ai_market_monitor\.db",
    re.IGNORECASE,
)


def assert_disposable_database(database_url: str | None) -> None:
    """Refuse a target wired to a database somebody would miss.

    The isolated launcher points at a file under ``test-results/`` that it deletes on
    every run. Anything else — a Postgres URL, or the repository's own
    ``ai_market_monitor.db`` — is a database with real work in it, and this harness
    creates accounts and drafts as it goes.
    """

    value = str(database_url or "").strip()
    if not value:
        return
    if _SHARED_DATABASE_RE.search(value):
        raise TargetRefused(
            "This target is wired to a shared database "
            f"({value.split('://')[0]}...). The QA harness creates accounts and edits "
            "drafts; it needs the disposable SQLite file the isolated launcher makes. "
            f"Use {ISOLATED_LAUNCHER}."
        )
