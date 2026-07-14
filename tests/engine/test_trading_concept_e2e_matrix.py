from collections import Counter

from ai_market_monitor.engine.concept_e2e import concept_e2e_rows, matrix_status_counts


def test_every_current_concept_has_an_e2e_status():
    rows = concept_e2e_rows()
    counts = matrix_status_counts(rows)

    assert len(rows) == 492
    assert sum(counts.values()) == len(rows)
    assert set(counts).issubset(
        {"GREEN", "YELLOW", "RED", "PROVIDER_REQUIRED", "PLANNED"}
    )
    assert counts["RED"] == 0
    assert counts["GREEN"] >= 300
    assert counts["PROVIDER_REQUIRED"] >= 100


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
