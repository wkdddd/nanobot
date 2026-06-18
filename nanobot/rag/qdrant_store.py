"""Optional Qdrant vector store for RAG."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from nanobot.rag.utils import ChunkKey, IndexedChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class QdrantVectorHit:
    key: ChunkKey
    score: float
    payload: dict[str, Any]


def stable_point_id(source_type: str, key: ChunkKey) -> str:
    raw = f"{source_type}:{key[0]}:{key[1]}:{key[2]}:{key[3]}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nanobot-rag:{raw}"))


def chunk_key(chunk: IndexedChunk) -> ChunkKey:
    return (chunk.path, int(chunk.start_line), int(chunk.end_line), chunk.kind)


class QdrantVectorStore:
    """Small wrapper around qdrant-client with lazy imports and safe fallbacks."""

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str = "",
        timeout: float = 30.0,
        dimensions: int = 1024,
        check_compatibility: bool = False,
    ) -> None:
        self.url = url
        self.collection = collection
        self.api_key = api_key
        self.timeout = timeout
        self.dimensions = dimensions
        self.check_compatibility = check_compatibility
        self._client: Any | None = None

    @classmethod
    def from_config(cls, config: Any, *, dimensions: int = 1024) -> "QdrantVectorStore | None":
        if not getattr(config, "enable", False):
            return None
        return cls(
            url=str(getattr(config, "url", "http://localhost:6333")),
            collection=str(getattr(config, "collection", "nanobot_rag_chunks")),
            api_key=str(getattr(config, "api_key", "") or ""),
            timeout=float(getattr(config, "timeout", 30.0) or 30.0),
            dimensions=dimensions,
            check_compatibility=bool(getattr(config, "check_compatibility", False)),
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except Exception as exc:  # pragma: no cover - exercised via fallback tests
            raise RuntimeError("qdrant-client is not installed") from exc
        kwargs: dict[str, Any] = {
            "url": self.url,
            "timeout": self.timeout,
            "check_compatibility": self.check_compatibility,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        self._client = QdrantClient(**kwargs)
        return self._client

    def ensure_collection(self) -> None:
        from qdrant_client import models

        client = self._get_client()
        exists = False
        try:
            exists = bool(client.collection_exists(self.collection))
        except AttributeError:
            try:
                client.get_collection(self.collection)
                exists = True
            except Exception:
                exists = False
        if exists:
            return
        client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(
                size=self.dimensions,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert_chunks(
        self,
        *,
        source_type: str,
        chunks: list[IndexedChunk],
        vectors: list[list[float] | None],
    ) -> int:
        from qdrant_client import models

        self.ensure_collection()
        points: list[Any] = []
        for chunk, vector in zip(chunks, vectors):
            if vector is None:
                continue
            if len(vector) != self.dimensions:
                logger.warning(
                    "⚠ Qdrant vector skipped: dim mismatch path=%s dim=%s expected=%s",
                    chunk.path,
                    len(vector),
                    self.dimensions,
                )
                continue
            key = chunk_key(chunk)
            payload = {
                "source_type": source_type,
                "path": chunk.path,
                "start_line": int(chunk.start_line),
                "end_line": int(chunk.end_line),
                "kind": chunk.kind,
                "title": chunk.title,
                "symbols": list(chunk.symbols),
                "content_hash": chunk.content_hash,
                "text": chunk.text,
                "chunk_key": list(key),
            }
            for symbol in chunk.symbols:
                if symbol.startswith("chapter:"):
                    payload["chapter"] = symbol.removeprefix("chapter:")
                elif symbol.startswith("section:"):
                    payload["section"] = symbol.removeprefix("section:")
                elif symbol.startswith("example_id:"):
                    payload["example_id"] = symbol.removeprefix("example_id:")
            points.append(
                models.PointStruct(
                    id=stable_point_id(source_type, key),
                    vector=vector,
                    payload=payload,
                )
            )

        if not points:
            return 0
        self._get_client().upsert(collection_name=self.collection, points=points)
        return len(points)

    def search(
        self,
        *,
        source_type: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[QdrantVectorHit]:
        from qdrant_client import models

        self.ensure_collection()
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="source_type",
                    match=models.MatchValue(value=source_type),
                )
            ]
        )
        client = self._get_client()
        try:
            rows = client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            ).points
        except AttributeError:
            rows = client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )

        hits: list[QdrantVectorHit] = []
        for row in rows:
            payload = dict(getattr(row, "payload", {}) or {})
            key = _payload_key(payload)
            if key is None:
                continue
            hits.append(
                QdrantVectorHit(
                    key=key,
                    score=float(getattr(row, "score", 0.0) or 0.0),
                    payload=payload,
                )
            )
        return hits

    def prune_missing(self, *, source_type: str, keep_point_ids: set[str]) -> int:
        from qdrant_client import models

        self.ensure_collection()
        client = self._get_client()
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="source_type",
                    match=models.MatchValue(value=source_type),
                )
            ]
        )
        removed: list[str] = []
        offset: Any | None = None
        while True:
            points, offset = client.scroll(
                collection_name=self.collection,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                point_id = str(getattr(point, "id"))
                if point_id not in keep_point_ids:
                    removed.append(point_id)
            if offset is None:
                break
        if removed:
            client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=removed),
            )
        return len(removed)


def _payload_key(payload: dict[str, Any]) -> ChunkKey | None:
    raw = payload.get("chunk_key")
    if isinstance(raw, list) and len(raw) == 4:
        return (str(raw[0]), int(raw[1]), int(raw[2]), str(raw[3]))
    path = payload.get("path")
    start_line = payload.get("start_line")
    end_line = payload.get("end_line")
    kind = payload.get("kind")
    if path is None or start_line is None or end_line is None or kind is None:
        return None
    return (str(path), int(start_line), int(end_line), str(kind))
