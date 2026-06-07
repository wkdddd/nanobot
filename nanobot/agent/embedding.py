"""Embedding client for semantic search via DashScope OpenAI-compatible API."""

from __future__ import annotations

import logging
import os
import struct
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def serialize_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize_embedding(data: bytes) -> list[float]:
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


def create_embedding_client_from_config(
    config: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> "EmbeddingClient | None":
    """Create an embedding client from config, returning None when disabled."""
    if not getattr(config, "enable", False):
        return None
    values = env or os.environ
    api_key = getattr(config, "api_key", "") or values.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        return None
    return EmbeddingClient(
        api_key=api_key,
        model=getattr(config, "model", "text-embedding-v3"),
        base_url=getattr(
            config,
            "base_url",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        dimensions=getattr(config, "dimensions", 1024),
        batch_size=getattr(config, "batch_size", 25),
    )


class EmbeddingClient:
    """DashScope embedding via OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v3",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions: int = 1024,
        batch_size: int = 25,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embed texts, respecting batch_size limit."""
        if not texts:
            return []
        client = self._get_client()
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            try:
                response = await client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimensions,
                )
                all_embeddings.extend(item.embedding for item in response.data)
            except Exception as e:
                logger.warning("Embedding API call failed: %s", e)
                all_embeddings.extend([[0.0] * self.dimensions] * len(batch))
        return all_embeddings

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        results = await self.embed_texts([query])
        return results[0] if results else [0.0] * self.dimensions
