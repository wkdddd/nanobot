"""Cross-encoder reranking via external API."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def create_rerank_client_from_config(
    config: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> "RerankClient | None":
    if not getattr(config, "enable", False):
        return None
    values = env or os.environ
    api_key = getattr(config, "api_key", "") or values.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        return None
    return RerankClient(
        api_key=api_key,
        model=getattr(config, "model", "gte-rerank"),
        base_url=getattr(
            config, "base_url",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        top_n=getattr(config, "top_n", 20),
    )


class RerankClient:
    """Rerank candidates using a cross-encoder model via OpenAI-compatible API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gte-rerank",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        top_n: int = 20,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.top_n = top_n
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )
        return self._client

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """Rerank documents by relevance. Returns [(original_index, score)] descending.

        On failure, returns empty list (caller should keep original order).
        """
        if not documents:
            return []
        n = min(top_n or self.top_n, len(documents))
        client = self._get_client()
        try:
            resp = await client.post(
                "/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": n,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            return [
                (int(r["index"]), float(r.get("relevance_score", 0.0)))
                for r in sorted(results, key=lambda x: -x.get("relevance_score", 0.0))
            ]
        except Exception as e:
            logger.warning("Rerank API call failed: %s", e)
            return []
