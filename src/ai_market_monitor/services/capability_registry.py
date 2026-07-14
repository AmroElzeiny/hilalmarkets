from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import CapabilityAliasProposal, CapabilityRegistryArtifact
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.capability_index import CapabilityIndex, get_capability_index


class OpenAIEmbeddingClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.openai_api_key is None:
            return []
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.capability_embedding_model,
            "input": texts,
            "dimensions": self.settings.capability_embedding_dimensions,
            "encoding_format": "float",
        }
        async with httpx.AsyncClient(
            base_url=str(self.settings.openai_base_url).rstrip("/"),
            timeout=max(60, self.settings.openai_timeout_seconds),
            transport=self.transport,
        ) as client:
            response = await client.post("/embeddings", headers=headers, json=payload)
        response.raise_for_status()
        rows = sorted(response.json().get("data") or [], key=lambda item: int(item["index"]))
        vectors = [list(map(float, row["embedding"])) for row in rows]
        return vectors if len(vectors) == len(texts) else []


class CapabilityRegistryService:
    def __init__(
        self,
        settings: Settings,
        *,
        index: CapabilityIndex | None = None,
        embedding_client: OpenAIEmbeddingClient | None = None,
    ) -> None:
        self.settings = settings
        self.index = index or get_capability_index()
        self.embedding_client = embedding_client or OpenAIEmbeddingClient(settings)

    async def initialize(self, session: AsyncSession) -> CapabilityRegistryArtifact:
        aliases = await self._approved_aliases(session)
        snapshot = self.index.install_alias_artifact(aliases)
        existing = await session.scalar(
            select(CapabilityRegistryArtifact)
            .where(
                CapabilityRegistryArtifact.registry_hash == snapshot.registry_hash,
                CapabilityRegistryArtifact.active.is_(True),
            )
            .order_by(CapabilityRegistryArtifact.created_at.desc())
        )
        if existing is not None:
            if existing.embeddings and existing.embedding_model:
                self.index.install_embeddings(
                    registry_hash=snapshot.registry_hash,
                    model=existing.embedding_model,
                    embeddings=existing.embeddings,
                )
            return existing
        return await self.publish(session, aliases=aliases)

    async def publish(
        self,
        session: AsyncSession,
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> CapabilityRegistryArtifact:
        aliases = aliases if aliases is not None else await self._approved_aliases(session)
        snapshot = self.index.install_alias_artifact(aliases)
        existing = await session.scalar(
            select(CapabilityRegistryArtifact).where(
                CapabilityRegistryArtifact.registry_hash == snapshot.registry_hash
            )
        )
        embeddings: dict[str, list[float]] = dict(existing.embeddings) if existing else {}
        embedding_model = existing.embedding_model if existing else None
        if (
            not embeddings
            and self.settings.capability_embeddings_enabled
            and self.settings.openai_api_key is not None
            and self.settings.app_env != "test"
        ):
            documents = [
                " | ".join(
                    filter(
                        None,
                        (
                            self.index.capability_document(capability),
                            " ".join(aliases.get(capability.key, [])),
                        ),
                    )
                )
                for capability in all_capabilities()
            ]
            try:
                vectors = await self.embedding_client.embed(documents)
            except httpx.HTTPError:
                vectors = []
            if vectors:
                embeddings = {
                    capability.key: vector
                    for capability, vector in zip(all_capabilities(), vectors, strict=True)
                }
                embedding_model = self.settings.capability_embedding_model
                self.index.install_embeddings(
                    registry_hash=snapshot.registry_hash,
                    model=embedding_model,
                    embeddings=embeddings,
                )
        await session.execute(
            update(CapabilityRegistryArtifact).values(active=False)
        )
        if existing is None:
            existing = CapabilityRegistryArtifact(
                registry_hash=snapshot.registry_hash,
                registry_version=snapshot.registry_version,
                aliases=aliases,
                embedding_model=embedding_model,
                embeddings=embeddings,
                active=True,
                created_at=datetime.now(UTC),
            )
            session.add(existing)
        else:
            existing.active = True
            existing.aliases = aliases
            existing.embedding_model = embedding_model
            existing.embeddings = embeddings
        await session.flush()
        return existing

    @staticmethod
    async def _approved_aliases(session: AsyncSession) -> dict[str, list[str]]:
        rows = list(
            (
                await session.scalars(
                    select(CapabilityAliasProposal).where(
                        CapabilityAliasProposal.status == "approved"
                    )
                )
            ).all()
        )
        aliases: dict[str, list[str]] = {}
        for row in rows:
            aliases.setdefault(row.capability_key, []).append(row.normalized_alias)
        return aliases


async def initialize_capability_registry(settings: Settings) -> dict[str, Any]:
    """Initialize once per API/worker process; startup remains available before migrations."""
    from ai_market_monitor.core.database import SessionFactory

    try:
        async with SessionFactory() as session:
            artifact = await CapabilityRegistryService(settings).initialize(session)
            await session.commit()
            return {
                "registry_hash": artifact.registry_hash,
                "registry_version": artifact.registry_version,
                "embedding_model": artifact.embedding_model,
                "embeddings": len(artifact.embeddings or {}),
            }
    except SQLAlchemyError as exc:
        return {
            "registry_hash": get_capability_index().registry_hash,
            "database_artifact": False,
            "error": type(exc).__name__,
        }
