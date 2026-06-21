"""Embedding client for semantic search via OpenAI-compatible API."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any

from loguru import logger

from nanobot.utils.log_style import log_event

_DASHSCOPE_MAX_INPUT_CHARS = 1024


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
        base_url=_config_value(config, "base_url", "baseUrl", "apiBase")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        dimensions=getattr(config, "dimensions", 1024),
        batch_size=_config_value(config, "batch_size", "batchSize") or 25,
        max_input_chars=_config_value(config, "max_input_chars", "maxInputChars")
        or _DASHSCOPE_MAX_INPUT_CHARS,
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
        self._auth_failed = False
        self._auth_error_reported = False

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
            if self._auth_failed:
                break
            batch = normalized[i: i + self.batch_size]
            batch_indexes = [idx for idx, _ in batch]
            batch_texts = [text for _, text in batch]
            try:
                response = await client.embeddings.create(
                    model=self.model,
                    input=batch_texts,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                for original_index, item in zip(batch_indexes, response.data):
                    all_embeddings[original_index] = item.embedding
            except Exception as e:
                if _is_auth_error(e):
                    self._auth_failed = True
                    message = (
                        "Embedding API authentication failed; semantic search is disabled "
                        "for this process. Check rag.embedding.apiKey or DASHSCOPE_API_KEY. "
                        f"base_url={self.base_url!r} model={self.model!r} "
                        f"dimensions={self.dimensions} api_key={_mask_key(self.api_key)!r} "
                        f"error={e}"
                    )
                    log_event(
                        logger,
                        "error",
                        "rag.embedding.auth_failed",
                        status="failed",
                        base_url=self.base_url,
                        model=self.model,
                        dimensions=self.dimensions,
                        api_key=_mask_key(self.api_key),
                        reason=e,
                    )
                    self._print_terminal_error_once(message)
                    break
                log_event(
                    logger,
                    "warning",
                    "rag.embedding.call_failed",
                    status="failed",
                    model=self.model,
                    reason=e,
                )
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

    def _print_terminal_error_once(self, message: str) -> None:
        if self._auth_error_reported:
            return
        self._auth_error_reported = True
        try:
            print(f"[nanobot] ERROR: {message}", file=sys.stderr, flush=True)
        except Exception:
            pass


def _is_auth_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return True
    body = str(error).lower()
    return any(
        marker in body
        for marker in (
            "invalid_api_key",
            "incorrect api key",
            "apikey-error",
            "unauthorized",
            "authentication",
        )
    )


def _mask_key(api_key: str) -> str:
    key = str(api_key or "")
    if len(key) <= 8:
        return "***" if key else ""
    return f"{key[:4]}...{key[-4:]}"
