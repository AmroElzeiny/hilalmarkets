"""What publishing our own screening standard is allowed to do, and what it never is.

This standard is different in kind from the three imported ones: nobody reviewed its
answers. Every rule here exists because that difference is easy to lose — in a merged
view, in a default nobody chose, in a status word borrowed from an authority's decision,
or in a warning that a surface simply forgot to draw.

Each test names the failure it prevents, because a rule whose reason is forgotten is a
rule somebody deletes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.services import hilal_methodology as hm
from ai_market_monitor.services.sharia_automated_screen import METHODOLOGY_SYSTEM_CODE
from ai_market_monitor.services.sharia_conditions import (
    Detection,
    Status,
    applied_conditions,
    approved_conditions,
    out_of_reach_conditions,
    status_of,
)

ASSETS = hm.admitted_assets()
REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------------
# The file itself
# --------------------------------------------------------------------------------


def test_the_standard_covers_at_least_one_coin():
    """Without this, every rule below passes by having nothing to check."""

    assert len(ASSETS) >= 20
    assert hm.admitted_symbols()


@pytest.mark.parametrize("asset", ASSETS, ids=lambda item: item.symbol)
def test_every_record_says_why(asset):
    """A record with no reason is an assertion nobody can check or argue with."""

    assert asset.reasons
    assert all(reason.strip() for reason in asset.reasons)


@pytest.mark.parametrize(
    "asset",
    [item for item in ASSETS if item.outcome is not hm.Outcome.NOT_ENOUGH_DATA],
    ids=lambda item: item.symbol,
)
def test_every_decided_record_cites_a_page(asset):
    """A decided coin names a page a reader can open.

    The exception is deliberate: a coin filed under *not enough data* has nothing to
    cite, and that absence **is** the finding.
    """

    assert asset.sources
    assert all(source.url.startswith("https://") for source in asset.sources)


def test_one_coin_has_one_record():
    symbols = [item.symbol for item in ASSETS]
    assert len(symbols) == len(set(symbols))


def test_the_shipped_file_matches_the_module_that_reads_it():
    """The file on disk and the objects the product uses are the same thing."""

    path = REPO / "src" / "ai_market_monitor" / "services" / hm.ADMISSIONS_FILE
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == len(ASSETS)
    assert [row["symbol"] for row in rows] == [item.symbol for item in ASSETS]


# --------------------------------------------------------------------------------
# The regulator floor
# --------------------------------------------------------------------------------


def test_the_regulator_floor_admits_the_whole_published_list():
    """Every coin the Malaysian regulator publishes is in, or the floor is not a floor.

    The screen already may not *refuse* an SC Malaysia asset — that is
    `test_sc_malaysia_is_the_ruler_the_screen_is_measured_against`. This is the other
    half: the published standard has to actually carry them.
    """

    pack = (
        REPO
        / "HilalMarkets_Sharia_Methodology_Import_Pack"
        / "HilalMarkets_Sharia_Methodology_Import_Pack"
        / "data"
        / "sc_malaysia_compliant_assets.json"
    )
    published = {
        str(row.get("canonical_symbol_candidate") or row["symbol_source"]).upper()
        for row in json.loads(pack.read_text(encoding="utf-8"))
    }
    admitted = {item.symbol for item in hm.admitted_by(hm.Admission.REGULATOR_FLOOR)}
    assert published <= admitted


@pytest.mark.parametrize(
    "asset",
    [item for item in ASSETS if item.admission is hm.Admission.REGULATOR_FLOOR],
    ids=lambda item: item.symbol,
)
def test_the_regulator_route_only_ever_admits(asset):
    """The floor rule admits; it never refuses.

    If the machine reading disagreed with the regulator, this standard's own published
    rule says the reading is wrong. Recording a refusal under the regulator's route
    would put our disagreement out under their name.
    """

    assert asset.outcome is hm.Outcome.ADMITTED
    assert asset.pages_read == 0


def test_a_coin_is_never_admitted_twice_by_two_routes():
    """One coin, one record, one route.

    Two records would let the product show a regulator's approval beside a machine
    reading of the same coin, and a reader would have no way to know which of the two
    the list was actually using.
    """

    machine = {item.symbol for item in ASSETS if item.admission is hm.Admission.AUTOMATED_SCREEN}
    regulator = {item.symbol for item in ASSETS if item.admission is hm.Admission.REGULATOR_FLOOR}
    assert not (machine & regulator)


# --------------------------------------------------------------------------------
# Status words
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("asset", ASSETS, ids=lambda item: item.symbol)
def test_a_machine_reading_never_wears_the_word_an_authority_wears(asset):
    """No admitted coin here is plain `eligible`.

    A reader scanning a list sees the status word long before any notice. If a machine
    reading of a website and a Shariah board's published decision both said "Eligible",
    the list would be telling the reader they are the same kind of claim.
    """

    assert asset.status is not ShariaAssetStatus.ELIGIBLE


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (hm.Outcome.ADMITTED, ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS),
        (hm.Outcome.REFUSED, ShariaAssetStatus.EXCLUDED),
        (hm.Outcome.NOT_ENOUGH_DATA, ShariaAssetStatus.INSUFFICIENT_INFORMATION),
    ],
)
def test_each_outcome_has_exactly_one_status(outcome, status):
    """One mapping, in one place. Two would eventually disagree about a refusal."""

    for asset in ASSETS:
        if asset.outcome is outcome:
            assert asset.status is status


def test_not_enough_data_is_never_a_refusal():
    """Silence must never become a "no".

    A coin nobody could read has not been refused. `INSUFFICIENT_INFORMATION` is outside
    the statuses the market list shows by default, so it is not presented as eligible
    either — it is simply absent until somebody finds it a source.
    """

    for asset in ASSETS:
        if asset.outcome is hm.Outcome.NOT_ENOUGH_DATA:
            assert asset.status is not ShariaAssetStatus.EXCLUDED
            assert not asset.exclusion_reasons


@pytest.mark.parametrize("asset", ASSETS, ids=lambda item: item.symbol)
def test_every_record_carries_the_under_development_warning(asset):
    """On the row itself, not only on the page that lists it.

    A qualification travels with an assessment into the Passport, the alert proof and
    the exported report. A warning that lived only in a template would be missing from
    every one of those.
    """

    assert hm.UNDER_DEVELOPMENT_NOTICE in asset.qualifications
    assert hm.UNDER_DEVELOPMENT_NOTICE in asset.summary()


def test_the_warning_says_the_two_things_that_matter():
    """Both halves, in the one sentence every surface repeats."""

    notice = hm.UNDER_DEVELOPMENT_NOTICE.lower()
    assert "under development" in notice
    assert "no shariah advisor" in notice
    assert "not a fatwa" in notice


# --------------------------------------------------------------------------------
# Keeping it out of places nobody chose it
# --------------------------------------------------------------------------------


def test_the_predicate_knows_this_standard_and_no_other():
    assert hm.is_automated(METHODOLOGY_SYSTEM_CODE) is True
    assert hm.is_automated("SC_MALAYSIA_SAC_REFERENCE") is False
    assert hm.is_automated("ALL_APPROVED_METHODOLOGIES") is False
    assert hm.is_automated(None) is False


def test_the_aggregate_view_excludes_it():
    """`ALL_APPROVED_METHODOLOGIES` picks one winner per coin across every standard.

    Left in, a machine reading would win outright for every coin no authority has ever
    assessed — most of the market — and the aggregate is exactly the view where a reader
    stops being able to tell whose answer they are reading.

    Asserted against the source of `_winning_assessments` rather than a live database,
    because the failure is a missing line in a list comprehension and that is what this
    has to notice.
    """

    source = (
        REPO / "src" / "ai_market_monitor" / "services" / "sharia_screening.py"
    ).read_text(encoding="utf-8")
    aggregate = source.split("if aggregate:", 1)[1].split("methodology_ids = [", 1)[0]
    assert "is_automated(row)" in aggregate


def test_the_product_default_can_never_fall_through_to_it():
    """`default_methodology` orders by the newest effective date.

    This standard is by construction the newest row in the table, so publishing it would
    otherwise have made it the default for every user with no saved preference — on the
    day it was published, silently. A settings value happens to prevent that today; a
    rule that only holds while a setting is filled in is not a rule.
    """

    source = (
        REPO / "src" / "ai_market_monitor" / "services" / "sharia_screening.py"
    ).read_text(encoding="utf-8")
    default = source.split("async def default_methodology", 1)[1].split(
        "async def resolve_methodology", 1
    )[0]
    assert "AUTOMATED_METHODOLOGY_CODE" in default


# --------------------------------------------------------------------------------
# The published contract
# --------------------------------------------------------------------------------


def test_the_published_criteria_are_the_register_itself():
    """Not a restatement of it.

    A page listing criteria that a separate list of code applies is the duplicate-parser
    failure this codebase keeps paying for, in its most damaging form: a published
    promise that no longer matches the rule.
    """

    assert hm.published_criteria() == applied_conditions()
    assert hm.skipped_criteria() == out_of_reach_conditions()


def test_the_rules_record_names_every_approved_condition():
    rules = hm.methodology_rules()
    assert rules["approved_conditions"] == [item.code for item in approved_conditions()]
    assert rules["applied_conditions"] == [item.code for item in applied_conditions()]
    assert rules["skipped_conditions"] == [item.code for item in out_of_reach_conditions()]
    assert rules["human_reviewed"] is False
    assert rules["under_development"] is True
    assert rules["shariah_advisor_behind_it"] is False
    assert rules["excluded_from_aggregate"] is True
    assert rules["page_budget"] == 80


def test_the_version_changes_when_the_rules_change():
    """The criteria fingerprint is built from what is approved, not from a date.

    A standard whose contents changed under a fixed version string cannot be cited: a
    reader who wrote down "v2026.08-hm.1" would have no way to know the rule behind it
    had moved.
    """

    rules = hm.methodology_rules()
    fingerprint = rules["criteria_version"]
    assert fingerprint.startswith("hilal-conditions.")
    assert len(fingerprint.split(".", 1)[1]) == 12


def test_the_record_never_claims_a_reviewer():
    """The one sentence in the whole product that must not sound like a person."""

    from ai_market_monitor.services.hilal_methodology import ensure_methodology  # noqa: F401

    source = (
        REPO / "src" / "ai_market_monitor" / "services" / "hilal_methodology.py"
    ).read_text(encoding="utf-8")
    assert 'reviewer_group="No Shariah advisor' in source
    assert 'reviewed_by="Hilal Markets automated screen (no human reviewer)"' in source


def test_the_description_states_the_reach_and_the_skipping():
    description = hm.methodology_description()
    assert "80 pages" in description
    assert "skipped rather than guessed" in description
    assert "no shariah advisor" in description.lower()


# --------------------------------------------------------------------------------
# What the public page is handed
# --------------------------------------------------------------------------------


def test_the_page_is_handed_the_live_register_not_a_copy():
    payload = hm.page_payload()
    assert payload["counts"]["applied"] == len(applied_conditions())
    assert payload["counts"]["skipped"] == len(out_of_reach_conditions())
    assert payload["counts"]["approved"] == len(approved_conditions())
    listed = [
        condition["code"]
        for family in payload["families"]
        for condition in family["conditions"]
    ]
    assert sorted(listed) == sorted(item.code for item in applied_conditions())


def test_the_page_lists_every_coin_including_the_ones_it_refused():
    """A list of only the winners cannot be checked.

    Without the refusals and the unread coins, a reader has no way to tell a coin this
    standard rejected from a coin it never looked at.
    """

    payload = hm.page_payload()
    assert len(payload["coins"]) == len(ASSETS)
    outcomes = {coin["outcome"] for coin in payload["coins"]}
    assert "refused" in outcomes or "not_enough_data" in outcomes


def test_the_page_never_shows_a_proposed_condition_as_a_rule():
    """A rule the owner has not approved changes nothing and must not be published as
    though it did."""

    payload = hm.page_payload()
    for family in payload["families"]:
        for condition in family["conditions"]:
            assert status_of(condition["code"]) is Status.APPROVED


def test_the_page_says_which_other_standards_are_admitted_and_which_are_not():
    payload = hm.page_payload()
    admitted = [item for item in payload["otherMethodologies"] if item["admitted"]]
    assert len(admitted) == 1
    assert admitted[0]["code"] == hm.REGULATOR_CODE
    assert admitted[0]["coins"] == len(hm.admitted_by(hm.Admission.REGULATOR_FLOOR))
    for item in payload["otherMethodologies"]:
        assert item["why"].strip()


@pytest.mark.parametrize("item", out_of_reach_conditions(), ids=lambda c: c.code)
def test_every_skipped_rule_says_why_it_is_skipped(item):
    payload = hm.page_payload()
    entry = next(row for row in payload["skipped"] if row["code"] == item.code)
    expected = "needs a person" if item.detection is Detection.MANUAL else "needs figures"
    assert entry["why"].startswith(expected)
