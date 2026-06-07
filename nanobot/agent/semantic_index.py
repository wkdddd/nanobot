"""Shared semantic retrieval orchestration for context indexes."""

from __future__ import annotations

import logging
from typing import Protocol

from nanobot.agent.context_index import ChunkKey, ContextIndex
from nanobot.agent.embedding import serialize_embedding

logger = logging.getLogger(__name__)


class EmbeddingClientProtocol(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of chunk texts."""

    async def embed_query(self, query: str) -> list[float]:
        """Embed a retrieval query."""


class SemanticIndexService:
    """Embed missing chunks and compute semantic scores for a ContextIndex."""

    def __init__(
        self,
        index: ContextIndex,
        embedding_client: EmbeddingClientProtocol | None,
    ) -> None:
        self.index = index
        self.embedding_client = embedding_client

    async def compute_scores(
        self,
        *,
        source_type: str,
        query: str,
    ) -> dict[ChunkKey, float] | None:
        if self.embedding_client is None:
            return None
        try:
            missing = self.index.get_chunks_without_embedding(source_type)
            if missing:
                texts = [row[4] for row in missing]
                vecs = await self.embedding_client.embed_texts(texts)
                store: dict[ChunkKey, bytes] = {}
                for (path, start, end, kind, _text), vec in zip(missing, vecs):
                    store[(path, start, end, kind)] = serialize_embedding(vec)
                self.index.store_embeddings(source_type, store)

            query_vec = await self.embedding_client.embed_query(query)
            return self.index.semantic_search(source_type, query_vec)
        except Exception as exc:
            logger.warning("Semantic index scoring failed for %s: %s", source_type, exc)
            return None
