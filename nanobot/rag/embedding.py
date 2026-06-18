"""Embedding client for semantic search via OpenAI-compatible API."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_DASHSCOPE_MAX_INPUT_CHARS = 2048


def create_embedding_client_from_config(
    config: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> "EmbeddingClient | None":
    if not getattr(config, "enable", False):
        return None
    values = env or os.environ
    api_key = (
        _config_value(config, "api_key", "apiKey")
        or values.get("DASHSCOPE_API_KEY", "")
    )
    if not api_key:
        return None
    return EmbeddingClient(
        api_key=api_key,
        model=getattr(config, "model", "text-embedding-v3"),
        base_url=_config_value(config, "base_url", "apiBase")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions=getattr(config, "dimensions", 1024),
        batch_size=getattr(config, "batch_size", 25),
        max_input_chars=getattr(config, "max_input_chars", _DASHSCOPE_MAX_INPUT_CHARS),
    )


def _config_value(config: Any, *names: str) -> Any:
    for name in names:
        value = getattr(config, name, None)
        if value:
            return value
    return None


class EmbeddingClient:
    """Embedding via OpenAI-compatible API (DashScope, OpenAI, etc.)."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v1",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions: int = 1024,
        batch_size: int = 25,
        max_input_chars: int = _DASHSCOPE_MAX_INPUT_CHARS,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.max_input_chars = max(1, int(max_input_chars))
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    async def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        """Batch embed texts. Returns None per item on failure (no zero-vector fallback)."""
        if not texts:
            return []
        client = self._get_client()
        all_embeddings: list[list[float] | None] = [None] * len(texts)
        normalized = [
            (idx, prepared)
            for idx, text in enumerate(texts)
            if (prepared := self._prepare_input(text)) is not None
        ]
        for i in range(0, len(normalized), self.batch_size):
            batch = normalized[i: i + self.batch_size]
            batch_indexes = [idx for idx, _ in batch]
            batch_texts = [text for _, text in batch]
            try:
                response = await client.embeddings.create(
                    model=self.model,
                    input=batch_texts,
                    dimensions=self.dimensions,
                )
                for original_index, item in zip(batch_indexes, response.data):
                    all_embeddings[original_index] = item.embedding
            except Exception as e:
                logger.warning("Embedding API call failed: %s", e)
        return all_embeddings

    async def embed_query(self, query: str) -> list[float] | None:
        """Embed a single query string. Returns None on failure."""
        if self._prepare_input(query) is None:
            return None
        results = await self.embed_texts([query])
        return results[0] if results else None

    def _prepare_input(self, text: str) -> str | None:
        prepared = str(text).strip()
        if not prepared:
            return None
        return prepared[: self.max_input_chars]
