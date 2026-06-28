from ai_market_monitor.engine.capability_compatibility import compatibility_report


def test_capability_registry_templates_are_classified_for_availability():
    rows = compatibility_report()

    assert rows
    assert {row.availability for row in rows}.issubset(
        {
            "available",
            "provider_required",
            "planned",
            "unsupported",
            "experimental",
        }
    )
    for row in rows:
        if row.availability == "available":
            assert row.template_valid, row.key
            assert row.evaluator_supported, row.key


def test_provider_required_capabilities_are_not_marked_fully_available():
    rows = compatibility_report()
    provider_rows = [
        row
        for row in rows
        if any("provider" in note for note in row.notes)
        or row.availability == "provider_required"
    ]

    assert provider_rows
    assert all(
        row.availability != "available"
        for row in provider_rows
        if "provider" in " ".join(row.notes)
    )
