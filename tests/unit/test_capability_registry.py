from datetime import UTC, datetime

from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import CapabilityAliasProposal
from ai_market_monitor.engine.capability_index import CapabilityIndex
from ai_market_monitor.services.capability_registry import CapabilityRegistryService


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]


async def test_registry_artifact_is_hash_cached_with_aliases_and_embeddings(test_context):
    settings = Settings(
        app_env="development",
        app_secret_key=SecretStr("registry-test-secret-at-least-thirty-two-characters"),
        openai_api_key=SecretStr("test-key"),
        capability_embedding_dimensions=64,
    )
    index = CapabilityIndex()
    embeddings = FakeEmbeddingClient()
    async with test_context["session_factory"]() as session:
        session.add(
            CapabilityAliasProposal(
                alias="raid the weekly floor",
                normalized_alias="raid the weekly floor",
                capability_key="reference_period_sweep",
                status="approved",
                evidence_count=3,
                source_event_ids=[],
                reviewed_at=datetime.now(UTC),
            )
        )
        await session.flush()
        service = CapabilityRegistryService(
            settings,
            index=index,
            embedding_client=embeddings,
        )
        first = await service.initialize(session)
        await session.commit()
        second = await service.initialize(session)

        assert first.id == second.id
        assert first.registry_hash == index.registry_hash
        assert first.registry_version.startswith("registry-")
        assert first.aliases["reference_period_sweep"] == ["raid the weekly floor"]
        assert first.embedding_model == "text-embedding-3-small"
        assert len(first.embeddings) > 400
        assert embeddings.calls == 1
        report = index.resolver.resolve_prompt("raid the weekly floor")
        assert report.fragments[0].candidates[0].capability_key == "reference_period_sweep"
