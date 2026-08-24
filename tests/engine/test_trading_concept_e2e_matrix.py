from collections import Counter

from ai_market_monitor.engine.capability_compatibility import compatibility_by_key
from ai_market_monitor.engine.concept_e2e import concept_e2e_rows, matrix_status_counts


def test_every_current_concept_has_an_e2e_status():
    rows = concept_e2e_rows()
    counts = matrix_status_counts(rows)

    assert len(rows) == 502
    assert sum(counts.values()) == len(rows)
    assert set(counts).issubset(
        {"GREEN", "YELLOW", "RED", "PROVIDER_REQUIRED", "PLANNED"}
    )
    assert counts["RED"] == 0
    assert counts["GREEN"] >= 300


def test_blocked_rows_are_exactly_the_feeds_this_deployment_cannot_read():
    """The matrix has to agree with the register, not carry its own idea of blocked.

    This used to assert a floor — "at least 100 rows are blocked" — which was a
    measurement of a defect written down as a requirement: 143 rows were blocked because
    a card *named* a feed, whether or not the product could read it. Opening the feeds
    the platform serves itself dropped that to 59, and a floor of 100 would have called
    the fix a regression. The truthful rule is that the two agree, whatever the number.
    """

    rows = concept_e2e_rows()
    compatibility = compatibility_by_key()

    blocked_in_matrix = {
        row["capability_key"]
        for row in rows
        if row["current_status"] == "PROVIDER_REQUIRED"
    }
    blocked_in_register = {
        key
        for key, row in compatibility.items()
        if row.availability == "provider_required"
    }

    assert blocked_in_matrix == blocked_in_register
    # And a blocked row must still name the feed it is waiting for, or nobody can act
    # on it.
    for row in rows:
        if row["current_status"] == "PROVIDER_REQUIRED":
            assert row["provider_required"], (
                f"{row['capability_key']} is blocked by nothing named"
            )


def test_available_concepts_are_not_exposed_as_partial_or_planned():
    rows = concept_e2e_rows()
    bad = [
        row
        for row in rows
        if row["availability"] == "available" and row["current_status"] != "GREEN"
    ]

    assert bad == []


def test_planned_concepts_are_hidden_from_normal_builder_add_paths():
    rows = concept_e2e_rows()

    for row in rows:
        if row["current_status"] == "PLANNED":
            assert row["manual_builder_add_status"] == "hidden"
            assert row["live_scanner_support"] == "no"


def test_matrix_counts_match_status_rows():
    rows = concept_e2e_rows()

    assert matrix_status_counts(rows) == Counter(row["current_status"] for row in rows)
