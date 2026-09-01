"""The Hilal Markets Methodology as a published standard: what it is, and who it admits.

Three modules already exist around this idea and none of them could publish it:

    ``sharia_conditions``          which rules the owner approved, with their evidence
    ``sharia_automated_screen``    what those rules do to a set of facts
    ``sharia_evidence_screen``     how a project's own web pages become those facts

What was missing is the fourth thing a *standard* needs: a record saying which coins it
covers, on what date, and by which of its own admission routes. This module owns that,
and nothing else in the product may decide it.

**Two admission routes, and they are never blurred together.**

``REGULATOR_FLOOR``
    A coin the Shariah Advisory Council of the Securities Commission of Malaysia has
    published as Shariah-compliant. This is not inheriting somebody else's answer: it is
    this methodology's *own* published rule that a regulator's approval is a floor it may
    never fall below, which
    ``test_sc_malaysia_is_the_ruler_the_screen_is_measured_against`` already enforces on
    the rule itself. A coin admitted this way carries the regulator's citation, and the
    automated reading is not what put it in the list.

``AUTOMATED_SCREEN``
    A coin no authority has ruled on, read by machine from up to eighty of the project's
    own pages and refused by nothing. The reasons and the sentences behind them travel
    with the admission.

**What this module refuses to do.** It never writes a Shariah *ruling*. Every row it
publishes carries ``human_reviewed: false`` and the disclosure sentence, the methodology
record says in its own description that no scholar has reviewed it, and
:func:`is_automated` keeps it out of every place a result would be silently mixed with an
authority's — the aggregate view and the product default. A person who wants an
authority's answer chooses an authority; nothing here can put this answer in front of
somebody who did not ask for it.

**Publishing is the owner's act, recorded.** :func:`publish` reads a file that a person
edited and committed. It does not decide who is admitted; the file does, exactly as
``sharia_condition_decisions.json`` decides which rules are live. Two files, two acts:
writing a rule down, and turning it on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import (
    ShariaAssetStatus,
    ShariaMethodologyStatus,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_DISPLAY_NAME,
    METHODOLOGY_PACKAGE_ID,
    METHODOLOGY_SYSTEM_CODE,
)
from ai_market_monitor.services.sharia_conditions import (
    FAMILY_TITLE_AR,
    Condition,
    Detection,
    Family,
    applied_conditions,
    approved_conditions,
    out_of_reach_conditions,
    register_summary,
)

#: The file a person edits to admit a coin. Beside this module, exactly like
#: ``sharia_condition_decisions.json`` sits beside the register it governs.
ADMISSIONS_FILE = "hilal_methodology_admissions.json"

#: The version this standard publishes under. It changes when the *rule* changes — an
#: approval in the register, or a change to the crawl's reach — never when a coin is
#: added, because adding a coin does not change what the standard means.
METHODOLOGY_VERSION = "2026.08-hm.1"

#: Where a reader can see the whole thing. One owner: the route, the notice beside every
#: result, and the methodology record itself all read this.
METHODOLOGY_PUBLIC_PATH = "/hilal-methodology"

#: Said in the methodology record itself, so the warning cannot be lost by a surface that
#: forgets to render the notice partial.
UNDER_DEVELOPMENT_NOTICE = (
    "This standard is still under development. It is applied by machine and no Shariah "
    "advisor stands behind it. It is not a fatwa and it is not the decision of a "
    "Shariah board."
)

#: The regulator whose published list this standard treats as a floor.
REGULATOR_CODE = "SC_MALAYSIA_SAC_REFERENCE"
REGULATOR_NAME = "Shariah Advisory Council, Securities Commission Malaysia"
REGULATOR_URL = "https://www.sc.com.my/digital-assets"


class Admission(StrEnum):
    """How a coin came to be in this standard's list. Never mixed, never inferred."""

    #: Published as Shariah-compliant by the Malaysian regulator, which this standard's
    #: own rule treats as a floor.
    REGULATOR_FLOOR = "regulator_floor"
    #: Read by machine from the project's own pages and refused by nothing.
    AUTOMATED_SCREEN = "automated_screen"


class Outcome(StrEnum):
    """What this standard concluded about one coin. Three answers, never two.

    A standard that only records the coins it accepted cannot be checked: a reader has
    no way to tell a coin it refused from a coin it never looked at. Every coin this
    standard has read is listed with the answer it actually got, and each answer maps to
    exactly one :class:`ShariaAssetStatus` — see :attr:`AdmittedAsset.status`.
    """

    #: Nothing in the approved conditions refused it, on enough of its own pages.
    ADMITTED = "admitted"
    #: An approved condition refused it, with the sentence that did so.
    REFUSED = "refused"
    #: Too little of the project's own writing could be read to say either way. A
    #: request for research, and never shown as a refusal.
    NOT_ENOUGH_DATA = "not_enough_data"


class AdmissionError(RuntimeError):
    """The admissions file is missing, malformed, or names something that cannot exist.

    Raised at read time, before a single row is published. A half-valid file must stop
    the whole publication: a coin admitted with no route, or with a reason naming a rule
    that is not in the register, is a claim nobody can check afterwards.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Source:
    """One page that was read, and when."""

    url: str
    title: str
    category: str
    retrieved_at: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "category": self.category,
            "retrieved_at": self.retrieved_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AdmittedAsset:
    """One coin this standard has read, the answer it got, and the whole of why."""

    symbol: str
    name: str
    admission: Admission
    outcome: Outcome
    decided_on: date
    #: Plain sentences a reader can check. For the regulator route this is the citation;
    #: for the automated route it is what the screen found the project saying about
    #: itself, or the sentence that refused it.
    reasons: tuple[str, ...]
    sources: tuple[Source, ...]
    #: How many of the project's own pages the reading rested on. Zero for the regulator
    #: route, which rests on a published decision rather than on a crawl.
    pages_read: int = 0
    primary_pages_read: int = 0
    #: Codes of the approved conditions that refused it. Empty unless refused.
    matched_conditions: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise AdmissionError("admission_symbol_missing", "An admission has no symbol.")
        if not self.reasons:
            raise AdmissionError(
                "admission_reason_missing",
                f"{self.symbol}: a record with no reason cannot be checked by anybody.",
            )
        if not self.sources and self.outcome is not Outcome.NOT_ENOUGH_DATA:
            # A coin nobody could read has nothing to cite, and that *is* the finding.
            # Every other outcome is a claim, and a claim with no source is an assertion.
            raise AdmissionError(
                "admission_source_missing",
                f"{self.symbol}: a decided record with no source is an assertion.",
            )
        if (
            self.admission is Admission.REGULATOR_FLOOR
            and self.outcome is not Outcome.ADMITTED
        ):
            # The floor rule only ever admits. If the reading disagreed with the
            # regulator, the rule says the reading is wrong — it may not be recorded as
            # a refusal under the regulator's name.
            raise AdmissionError(
                "regulator_floor_must_admit",
                f"{self.symbol}: the regulator floor admits; it never refuses.",
            )

    @property
    def is_admitted(self) -> bool:
        return self.outcome is Outcome.ADMITTED

    @property
    def status(self) -> ShariaAssetStatus:
        """The one place an outcome becomes a status word.

        An admitted coin is **never** plain :attr:`ShariaAssetStatus.ELIGIBLE`. A reader
        scanning a list sees the status word long before they see any notice, and a
        machine reading of a website must not wear the same word an authority's decision
        wears. The qualification carried on every row says what the reservation is.
        """

        if self.outcome is Outcome.ADMITTED:
            return ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
        if self.outcome is Outcome.REFUSED:
            return ShariaAssetStatus.EXCLUDED
        return ShariaAssetStatus.INSUFFICIENT_INFORMATION

    @property
    def qualifications(self) -> list[str]:
        if self.outcome is not Outcome.ADMITTED:
            return [UNDER_DEVELOPMENT_NOTICE]
        return [UNDER_DEVELOPMENT_NOTICE, _admission_qualification(self.admission)]

    @property
    def exclusion_reasons(self) -> list[dict[str, Any]]:
        if self.outcome is not Outcome.REFUSED:
            return []
        return [
            {"code": code, "reason": reason}
            for code, reason in zip(
                self.matched_conditions or ("automated_screen",) * len(self.reasons),
                self.reasons,
                strict=False,
            )
        ]

    def summary(self) -> str:
        if self.admission is Admission.REGULATOR_FLOOR:
            lead = (
                f"{self.name} ({self.symbol}) is published as Shariah-compliant by "
                f"{REGULATOR_NAME}, and this standard treats a regulator's approval as "
                "a floor it may not fall below."
            )
        elif self.outcome is Outcome.ADMITTED:
            lead = (
                f"{self.name} ({self.symbol}) was read by machine from "
                f"{self.pages_read} pages, {self.primary_pages_read} of them written by "
                "the project about itself, and none of the approved conditions refused it."
            )
        elif self.outcome is Outcome.REFUSED:
            lead = (
                f"{self.name} ({self.symbol}) was read by machine from "
                f"{self.pages_read} pages, and the project's own words describe a "
                "business an approved condition refuses."
            )
        else:
            lead = (
                f"Too little of what {self.name} ({self.symbol}) publishes about itself "
                "could be read, so this standard has not judged it. That is not a "
                "refusal and it is not a pass."
            )
        return f"{lead} {UNDER_DEVELOPMENT_NOTICE}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "admission": self.admission.value,
            "outcome": self.outcome.value,
            "decided_on": self.decided_on.isoformat(),
            "reasons": list(self.reasons),
            "sources": [item.as_dict() for item in self.sources],
            "pages_read": self.pages_read,
            "primary_pages_read": self.primary_pages_read,
            "matched_conditions": list(self.matched_conditions),
            "note": self.note,
        }


def _admission_qualification(admission: Admission) -> str:
    if admission is Admission.REGULATOR_FLOOR:
        return (
            "Admitted because the Malaysian regulator publishes it as Shariah-compliant. "
            "The regulator publishes no coin-by-coin reasoning, and none is invented here."
        )
    return (
        "Admitted by an automatic reading of the project's own website. Some questions "
        "cannot be answered that way and were skipped rather than guessed; skipping is "
        "not passing."
    )


def _payload() -> Any:
    try:
        raw = (
            resources.files("ai_market_monitor.services")
            .joinpath(ADMISSIONS_FILE)
            .read_text(encoding="utf-8")
        )
    except (OSError, ModuleNotFoundError) as exc:  # pragma: no cover - packaging fault
        raise AdmissionError(
            "admissions_file_missing",
            f"{ADMISSIONS_FILE} could not be read.",
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdmissionError(
            "admissions_file_invalid",
            f"{ADMISSIONS_FILE} is not valid JSON.",
        ) from exc


@lru_cache(maxsize=1)
def admitted_assets() -> tuple[AdmittedAsset, ...]:
    """Every coin this standard covers, in the order the file lists them.

    Cached, for the same reason the register's decisions are: a reader at import time and
    a reader during a request must see one answer, and the file does not change while the
    process runs.
    """

    rows = _payload()
    if not isinstance(rows, list):
        raise AdmissionError(
            "admissions_file_invalid",
            f"{ADMISSIONS_FILE} must hold a list of admissions.",
        )
    seen: set[str] = set()
    assets: list[AdmittedAsset] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AdmissionError(
                "admissions_row_invalid", "An admission row is not an object."
            )
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol in seen:
            raise AdmissionError(
                "admission_duplicate",
                f"{symbol} is admitted twice; one coin has one admission.",
            )
        seen.add(symbol)
        try:
            admission = Admission(str(row.get("admission") or ""))
        except ValueError as exc:
            raise AdmissionError(
                "admission_route_unknown",
                f"{symbol}: {row.get('admission')!r} is not an admission route.",
            ) from exc
        try:
            outcome = Outcome(str(row.get("outcome") or ""))
        except ValueError as exc:
            raise AdmissionError(
                "admission_outcome_unknown",
                f"{symbol}: {row.get('outcome')!r} is not an outcome this standard has.",
            ) from exc
        assets.append(
            AdmittedAsset(
                symbol=symbol,
                name=str(row.get("name") or symbol).strip(),
                admission=admission,
                outcome=outcome,
                decided_on=_as_date(symbol, row.get("decided_on")),
                reasons=tuple(
                    str(item).strip() for item in row.get("reasons") or [] if str(item).strip()
                ),
                sources=tuple(_source(symbol, item) for item in row.get("sources") or []),
                pages_read=int(row.get("pages_read") or 0),
                primary_pages_read=int(row.get("primary_pages_read") or 0),
                matched_conditions=tuple(
                    str(item).strip()
                    for item in row.get("matched_conditions") or []
                    if str(item).strip()
                ),
                note=str(row.get("note") or "").strip(),
            )
        )
    return tuple(assets)


def _as_date(symbol: str, value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise AdmissionError(
            "admission_date_invalid",
            f"{symbol}: {value!r} is not a date this admission can be dated by.",
        ) from exc


def _source(symbol: str, payload: Any) -> Source:
    if not isinstance(payload, dict):
        raise AdmissionError(
            "admission_source_invalid", f"{symbol}: a source is not an object."
        )
    url = str(payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise AdmissionError(
            "admission_source_invalid",
            f"{symbol}: {url!r} is not an address a reader can open.",
        )
    return Source(
        url=url,
        title=str(payload.get("title") or url)[:300].strip() or url,
        category=str(payload.get("category") or "website").strip(),
        retrieved_at=_as_date(symbol, payload.get("retrieved_at")),
    )


def assets_by_outcome(outcome: Outcome) -> tuple[AdmittedAsset, ...]:
    return tuple(item for item in admitted_assets() if item.outcome is outcome)


def admitted_by(admission: Admission) -> tuple[AdmittedAsset, ...]:
    """Coins this standard **admitted** through one route.

    Only admitted ones. A refused coin has a route in the file — the route says how it
    was looked at — but calling it "admitted by the automated screen" would be exactly
    the wrong sentence.
    """

    return tuple(
        item
        for item in admitted_assets()
        if item.admission is admission and item.is_admitted
    )


def admitted_symbols() -> frozenset[str]:
    """The coins this standard accepts. Never the coins it merely read."""

    return frozenset(item.symbol for item in admitted_assets() if item.is_admitted)


def assessed_symbols() -> frozenset[str]:
    """Every coin this standard has an answer for, whatever that answer is."""

    return frozenset(item.symbol for item in admitted_assets())


def is_automated(code: str | ShariaMethodology | None) -> bool:
    """Whether this is the automated standard — the one predicate, read everywhere.

    Two decisions rest on it and both fail silently if they are written separately:

    * the aggregate view must not put a machine reading beside an authority's decision,
      because the aggregate is exactly the place a reader stops being able to tell them
      apart;
    * the product default must never fall through to it, because ``default_methodology``
      orders by the newest effective date and this standard is the newest thing there is.
    """

    if code is None:
        return False
    value = code.code if isinstance(code, ShariaMethodology) else str(code)
    return value == METHODOLOGY_SYSTEM_CODE


# --------------------------------------------------------------------------------
# What the published record says this standard is
# --------------------------------------------------------------------------------


def published_criteria() -> tuple[Condition, ...]:
    """The rules that actually decide a coin here — approved *and* readable from a page.

    Taken from the register rather than restated, so the criteria the public page lists
    and the criteria the screen applies are the same tuple. A restatement would be the
    duplicate-parser failure this codebase keeps paying for, in its most damaging form:
    a published promise that no longer matches the rule.
    """

    return applied_conditions()


def skipped_criteria() -> tuple[Condition, ...]:
    """Approved rules the automatic reading cannot settle, and therefore skips."""

    return out_of_reach_conditions()


def criteria_by_family() -> dict[Family, tuple[Condition, ...]]:
    applied = published_criteria()
    return {
        family: tuple(item for item in applied if item.family is family)
        for family in Family
        if any(item.family is family for item in applied)
    }


def methodology_description() -> str:
    counts = register_summary()
    approved = counts["by_status"]["approved"]
    return (
        f"{METHODOLOGY_DISPLAY_NAME} is Hilal Markets' own screening standard. "
        f"{approved} of {counts['total']} written conditions are approved, "
        f"each carrying the verse, hadith or standard behind it; "
        f"{counts['applied']} of them can be settled by reading up to 80 pages of a "
        f"project's own website, and {counts['out_of_reach']} cannot and are skipped "
        "rather than guessed. It admits a coin by one of two routes: the Malaysian "
        "regulator publishes it as Shariah-compliant, or the automatic reading found "
        f"nothing that refuses it. {UNDER_DEVELOPMENT_NOTICE} {AUTOMATED_DISCLOSURE}"
    )


def methodology_rules() -> dict[str, Any]:
    """The versioned contract this standard publishes, built from the register itself."""

    outcomes = ["pass", "qualification", "fail", "not_applicable", "needs_evidence"]
    criteria = [
        {
            "key": "project_own_description",
            "label": "What the project says it does",
            "description": (
                "Read up to 80 pages the project publishes about itself and record what "
                "kind of business it describes."
            ),
            "required": True,
            "allowed_outcomes": outcomes,
            "evidence_categories": ["project_own_pages"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "needs_evidence"],
        },
        {
            "key": "approved_condition_screen",
            "label": "The approved conditions",
            "description": (
                "Apply every approved condition that can be settled from those pages. "
                "A condition refuses only when the project's own words support it on "
                "enough of its own pages."
            ),
            "required": True,
            "allowed_outcomes": outcomes,
            "evidence_categories": ["project_own_pages", "approved_conditions"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail", "needs_evidence"],
        },
        {
            "key": "regulator_floor",
            "label": "The regulator floor",
            "description": (
                "A coin the Malaysian Shariah Advisory Council publishes as compliant is "
                "admitted, and this standard may not refuse it."
            ),
            "required": False,
            "allowed_outcomes": outcomes,
            "evidence_categories": ["official_external_reference"],
            "qualification_rules": {"written_reason_required": True},
            "blocking_outcomes": ["fail"],
        },
    ]
    return {
        "schema_version": "1",
        "criteria_version": f"hilal-conditions.{_criteria_hash()}",
        "source_family": "hilal_markets_automated",
        "source_adapter": "hilal_markets",
        "executable": True,
        "spot_only": True,
        "human_reviewed": False,
        "under_development": True,
        "shariah_advisor_behind_it": False,
        "disclosure": AUTOMATED_DISCLOSURE,
        "development_notice": UNDER_DEVELOPMENT_NOTICE,
        "public_page": METHODOLOGY_PUBLIC_PATH,
        "package_id": METHODOLOGY_PACKAGE_ID,
        "page_budget": 80,
        "approved_conditions": [item.code for item in approved_conditions()],
        "applied_conditions": [item.code for item in published_criteria()],
        "skipped_conditions": [item.code for item in skipped_criteria()],
        "admission_routes": [item.value for item in Admission],
        "regulator_floor_code": REGULATOR_CODE,
        # Deliberately not in any aggregate. Named here so the exclusion is visible in
        # the published record and not only in code.
        "excluded_from_aggregate": True,
        "required_criteria": criteria,
        "use_cases": [
            {
                "key": "spot_holding_screen",
                "label": "Holding the coin on a spot market",
                "description": (
                    "The only use this standard speaks about. It says nothing about "
                    "lending it, staking it through a third party, or any derivative."
                ),
                "required": True,
                "allowed_decisions": [
                    "covered",
                    "qualified",
                    "not_covered",
                    "not_applicable",
                    "under_review",
                    "excluded",
                ],
                "criterion_keys": ["project_own_description", "approved_condition_screen"],
                "evidence_categories": ["project_own_pages"],
                "default_scope": "Spot purchase and holding only.",
                "execution_blocking_decisions": ["not_covered", "excluded", "under_review"],
            }
        ],
    }


def evidence_requirements() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "mandatory_source_categories": [
            "project_own_pages",
            "approved_conditions",
            "official_external_reference",
        ],
        "minimum_evidence_completeness": 1.0,
        "maximum_source_age_days": 365,
        "critical_missing_fields": [
            "automated_screen.verdict",
            "automated_screen.pages_read",
            "admission.route",
        ],
        "contradiction_policy": "block_any_unresolved",
        "review_cadence_days": 90,
        "human_review_performed": False,
    }


def _criteria_hash() -> str:
    """A fingerprint of the rules in force, so a changed register changes the version.

    Built from the approved codes and their phrases. Approving a condition, or widening
    the words one looks for, changes what this standard *is* — and a published standard
    whose contents changed under a fixed version string is unciteable.
    """

    payload = [
        {"code": item.code, "phrases": list(item.phrases)}
        for item in approved_conditions()
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def page_payload() -> dict[str, Any]:
    """Everything the public methodology page draws, from the register and the file.

    Handed to the page at render time rather than written into the bundle. A page that
    carried its own copy of "68 approved conditions" would be correct on the day it was
    built and wrong on the day one was approved — and the wrong version is the one a
    reader would believe, because it is the one on the website.
    """

    counts = register_summary()
    families = criteria_by_family()
    return {
        "code": METHODOLOGY_SYSTEM_CODE,
        "name": METHODOLOGY_DISPLAY_NAME,
        "version": METHODOLOGY_VERSION,
        "disclosure": AUTOMATED_DISCLOSURE,
        "developmentNotice": UNDER_DEVELOPMENT_NOTICE,
        "pageBudget": 80,
        "counts": {
            "total": counts["total"],
            "approved": counts["by_status"]["approved"],
            "proposed": counts["by_status"]["proposed"],
            "applied": counts["applied"],
            "skipped": counts["out_of_reach"],
            "evidence": sum(len(item.evidence) for item in approved_conditions()),
        },
        "regulator": {
            "code": REGULATOR_CODE,
            "name": REGULATOR_NAME,
            "url": REGULATOR_URL,
        },
        "families": [
            {
                "key": family.value,
                "titleAr": FAMILY_TITLE_AR[family],
                "count": len(members),
                "conditions": [
                    {
                        "code": item.code,
                        "titleAr": item.title_ar,
                        "reason": item.reason_en,
                        "agreement": item.agreement.value,
                        "evidence": [
                            {"kind": proof.kind.value, "reference": proof.reference}
                            for proof in item.evidence
                        ],
                    }
                    for item in members
                ],
            }
            for family, members in families.items()
        ],
        "skipped": [
            {
                "code": item.code,
                "titleAr": item.title_ar,
                "reason": item.reason_en,
                "why": (
                    "needs a person"
                    if item.detection is Detection.MANUAL
                    else "needs figures nobody publishes"
                ),
            }
            for item in skipped_criteria()
        ],
        "coins": [
            {
                "symbol": item.symbol,
                "name": item.name,
                "admission": item.admission.value,
                "outcome": item.outcome.value,
                "decidedOn": item.decided_on.isoformat(),
                "pagesRead": item.pages_read,
                "primaryPagesRead": item.primary_pages_read,
                "reasons": list(item.reasons),
                "sources": [source.url for source in item.sources],
            }
            for item in admitted_assets()
        ],
        "otherMethodologies": [
            {
                "code": REGULATOR_CODE,
                "name": "SC Malaysia SAC Digital Assets Reference",
                "authority": REGULATOR_NAME,
                "url": REGULATOR_URL,
                "admitted": True,
                "coins": len(admitted_by(Admission.REGULATOR_FLOOR)),
                "why": (
                    "A financial regulator, and the only one in the set. Its published "
                    "approvals are a floor this standard may not fall below, so every "
                    "coin on its list is admitted without being re-read."
                ),
            },
            {
                "code": "FASSET_SHARIAH_REPORTS",
                "name": "Fasset Shariah Reports",
                "authority": "Fasset",
                "url": "https://www.fasset.com/shariah-reports/",
                "admitted": False,
                "coins": 0,
                "why": (
                    "Publishes both the assets it accepts and the assets it refuses, "
                    "which is what the rules here were measured against. It is a "
                    "measuring stick, not a source of admissions: nothing is admitted "
                    "because Fasset accepted it."
                ),
            },
            {
                "code": "SHARIAH_REVIEW_BUREAU",
                "name": "Shariah Review Bureau",
                "authority": "Shariyah Review Bureau W.L.L.",
                "url": "https://shariyah.net/cryptocurrency/",
                "admitted": False,
                "coins": 0,
                "why": (
                    "A commercial research house, not a regulator, and its reports are "
                    "not ours to republish. Its results stay under its own name."
                ),
            },
        ],
    }


def register_view() -> dict[str, Any]:
    """Everything a page needs to explain this standard, from the register itself."""

    counts = register_summary()
    return {
        "code": METHODOLOGY_SYSTEM_CODE,
        "name": METHODOLOGY_DISPLAY_NAME,
        "version": METHODOLOGY_VERSION,
        "path": METHODOLOGY_PUBLIC_PATH,
        "disclosure": AUTOMATED_DISCLOSURE,
        "development_notice": UNDER_DEVELOPMENT_NOTICE,
        "page_budget": 80,
        "counts": counts,
        "families": [
            {
                "key": family.value,
                "applied": [item.code for item in members],
            }
            for family, members in criteria_by_family().items()
        ],
        "admitted": {
            route.value: [item.symbol for item in admitted_by(route)] for route in Admission
        },
        "outcomes": {
            outcome.value: [item.symbol for item in assets_by_outcome(outcome)]
            for outcome in Outcome
        },
    }


# --------------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------------


@dataclass(slots=True)
class PublishResult:
    """What one publication did, counted the way a reader would ask."""

    methodology_created: bool = False
    assessments_written: int = 0
    assessments_unchanged: int = 0
    sources_written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "methodology_created": self.methodology_created,
            "assessments_written": self.assessments_written,
            "assessments_unchanged": self.assessments_unchanged,
            "sources_written": self.sources_written,
        }


async def ensure_methodology(session: AsyncSession) -> tuple[ShariaMethodology, bool]:
    """The active methodology row for this standard, created if it is not there yet.

    An older version of the same code is archived rather than deleted, exactly as the
    import pack does for an authority: a decision recorded under a version keeps that
    version for ever, or the record stops being readable years later.
    """

    now = datetime.now(UTC)
    existing = await session.scalar(
        select(ShariaMethodology).where(
            ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE,
            ShariaMethodology.version == METHODOLOGY_VERSION,
        )
    )
    if existing is not None:
        existing.description = methodology_description()
        existing.rules_json = methodology_rules()
        existing.evidence_requirements_json = evidence_requirements()
        if existing.status is not ShariaMethodologyStatus.ACTIVE:
            existing.status = ShariaMethodologyStatus.ACTIVE
            existing.effective_to = None
        return existing, False

    for older in await session.scalars(
        select(ShariaMethodology).where(
            ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE,
            ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE,
        )
    ):
        older.status = ShariaMethodologyStatus.ARCHIVED
        older.effective_to = now

    methodology = ShariaMethodology(
        code=METHODOLOGY_SYSTEM_CODE,
        name=METHODOLOGY_DISPLAY_NAME,
        version=METHODOLOGY_VERSION,
        description=methodology_description(),
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Hilal Markets",
        # Named for what it is. Writing a review group here would be the single most
        # misleading sentence in the product: there is no group, and the whole point of
        # this standard is that it says so.
        reviewer_group="No Shariah advisor — automated screen, under development",
        published_at=now,
        effective_from=now,
        rules_json=methodology_rules(),
        evidence_requirements_json=evidence_requirements(),
    )
    session.add(methodology)
    await session.flush()
    return methodology, True


async def publish(session: AsyncSession) -> PublishResult:
    """Write the standard and every admission in the file. Idempotent.

    An admission whose reasons and sources have not changed is left alone, so running
    this twice does not produce a second assessment with a later review date — which
    would make every coin look freshly re-read when nothing had been read at all.
    """

    result = PublishResult()
    methodology, created = await ensure_methodology(session)
    result.methodology_created = created
    now = datetime.now(UTC)

    existing = {
        row.canonical_asset: row
        for row in await session.scalars(
            select(AssetShariaAssessment).where(
                AssetShariaAssessment.methodology_id == methodology.id,
                AssetShariaAssessment.valid_until.is_(None),
            )
        )
    }

    for asset in admitted_assets():
        fingerprint = _fingerprint(asset)
        current = existing.get(asset.symbol)
        if current is not None and _stored_fingerprint(current) == fingerprint:
            result.assessments_unchanged += 1
            continue
        if current is not None:
            current.valid_until = now
        assessment = AssetShariaAssessment(
            canonical_asset=asset.symbol,
            asset_name=asset.name[:160],
            methodology_id=methodology.id,
            status=asset.status,
            summary=asset.summary(),
            qualifications=asset.qualifications,
            exclusion_reasons=asset.exclusion_reasons,
            evidence_snapshot={
                "methodology": METHODOLOGY_SYSTEM_CODE,
                "methodology_version": METHODOLOGY_VERSION,
                "human_reviewed": False,
                "disclosure": AUTOMATED_DISCLOSURE,
                "development_notice": UNDER_DEVELOPMENT_NOTICE,
                "public_page": METHODOLOGY_PUBLIC_PATH,
                "admission": asset.admission.value,
                "outcome": asset.outcome.value,
                "matched_conditions": list(asset.matched_conditions),
                "reasons": list(asset.reasons),
                "pages_read": asset.pages_read,
                "primary_pages_read": asset.primary_pages_read,
                "skipped_conditions": [item.code for item in skipped_criteria()],
                "note": asset.note,
                "fingerprint": fingerprint,
            },
            # Not a person, and it must not read like one. Every other methodology puts
            # a reviewer's name here; this one puts the machine, so a reader comparing
            # two passports sees the difference without being told.
            reviewed_by="Hilal Markets automated screen (no human reviewer)",
            reviewed_by_user_id=None,
            reviewed_at=now,
            valid_from=now,
            valid_until=None,
            supersedes_assessment_id=current.id if current is not None else None,
        )
        session.add(assessment)
        await session.flush()
        result.assessments_written += 1
        for source in asset.sources:
            session.add(
                ShariaEvidenceSource(
                    assessment_id=assessment.id,
                    source_type="project_page"
                    if asset.admission is Admission.AUTOMATED_SCREEN
                    else "official_external_reference",
                    title=source.title[:300],
                    publisher=asset.name[:200]
                    if asset.admission is Admission.AUTOMATED_SCREEN
                    else REGULATOR_NAME[:200],
                    source_url=source.url[:1000],
                    published_at=None,
                    retrieved_at=datetime.combine(source.retrieved_at, datetime.min.time(), UTC),
                    evidence_category=(
                        "project_own_pages"
                        if asset.admission is Admission.AUTOMATED_SCREEN
                        else "official_external_reference"
                    ),
                    evidence_summary=_source_summary(asset, source),
                    source_hash=hashlib.sha256(
                        f"{assessment.id}:{source.url}".encode()
                    ).hexdigest(),
                )
            )
            result.sources_written += 1
    await session.flush()
    return result


def _source_summary(asset: AdmittedAsset, source: Source) -> str:
    if asset.admission is Admission.REGULATOR_FLOOR:
        return (
            f"{REGULATOR_NAME} publishes {asset.symbol} as Shariah-compliant on this "
            "page. No coin-by-coin reasoning is published there and none is invented."
        )
    return (
        f"Read from {asset.name}'s own {source.category.replace('_', ' ')} on "
        f"{source.retrieved_at.isoformat()} while screening {asset.symbol}."
    )


def _fingerprint(asset: AdmittedAsset) -> str:
    """What would have to change for the record to be genuinely different."""

    payload = {
        "admission": asset.admission.value,
        "outcome": asset.outcome.value,
        "reasons": list(asset.reasons),
        "sources": [item.url for item in asset.sources],
        "criteria": _criteria_hash(),
        "version": METHODOLOGY_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _stored_fingerprint(assessment: AssetShariaAssessment) -> str:
    snapshot = assessment.evidence_snapshot or {}
    return str(snapshot.get("fingerprint") or "")


def admissions_payload() -> Sequence[Mapping[str, Any]]:
    """The file's own content, for a test or a report that must quote it exactly."""

    return [item.as_dict() for item in admitted_assets()]


__all__ = [
    "ADMISSIONS_FILE",
    "METHODOLOGY_DISPLAY_NAME",
    "METHODOLOGY_PUBLIC_PATH",
    "METHODOLOGY_SYSTEM_CODE",
    "METHODOLOGY_VERSION",
    "REGULATOR_CODE",
    "REGULATOR_NAME",
    "UNDER_DEVELOPMENT_NOTICE",
    "Admission",
    "AdmissionError",
    "AdmittedAsset",
    "Outcome",
    "PublishResult",
    "Source",
    "admissions_payload",
    "admitted_assets",
    "admitted_by",
    "admitted_symbols",
    "assessed_symbols",
    "assets_by_outcome",
    "criteria_by_family",
    "ensure_methodology",
    "evidence_requirements",
    "is_automated",
    "methodology_description",
    "methodology_rules",
    "page_payload",
    "publish",
    "published_criteria",
    "register_view",
    "skipped_criteria",
]
