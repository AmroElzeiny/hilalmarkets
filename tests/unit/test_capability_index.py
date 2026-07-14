from ai_market_monitor.engine.capability_index import CapabilityIndex


def test_registry_hash_is_stable_and_changes_with_approved_alias_artifact():
    index = CapabilityIndex()
    initial = index.snapshot
    repeated = index.install_alias_artifact({})
    changed = index.install_alias_artifact(
        {"reference_period_sweep": ["raid last week's floor"]},
        registry_version="test-alias-release",
    )

    assert repeated.registry_hash == initial.registry_hash
    assert changed.registry_hash != initial.registry_hash
    assert changed.registry_version == "test-alias-release"
    report = changed.resolver.resolve_prompt("raid last week's floor")
    assert report.fragments[0].candidates[0].capability_key == "reference_period_sweep"


def test_embedding_candidates_are_secondary_and_bound_to_current_hash():
    index = CapabilityIndex()
    snapshot = index.snapshot
    assert index.install_embeddings(
        registry_hash=snapshot.registry_hash,
        model="test-embedding",
        embeddings={
            "reference_period_sweep": [1.0, 0.0],
            "rsi_threshold": [0.0, 1.0],
        },
    )

    candidates = index.semantic_candidates("unusual weekly floor raid", [0.9, 0.1])
    assert candidates[0].capability_key == "reference_period_sweep"
    assert candidates[0].matched_on == ("embedding",)
    assert not index.install_embeddings(
        registry_hash="0" * 64,
        model="stale",
        embeddings={"reference_period_sweep": [1.0, 0.0]},
    )
