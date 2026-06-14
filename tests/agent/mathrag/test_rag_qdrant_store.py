from __future__ import annotations

import sys
import types
from dataclasses import dataclass

from nanobot.agent.rag.qdrant_store import (
    QdrantMathVectorStore,
    chunk_key,
    stable_point_id,
)
from nanobot.agent.rag.utils import IndexedChunk
from nanobot.config.schema import QdrantConfig


class _ModelFactory:
    def __getattr__(self, name: str):
        @dataclass
        class _Model:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        if name == "Distance":
            return type("Distance", (), {"COSINE": "Cosine"})
        return _Model


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.collections: set[str] = set()
        self.upserts: list[tuple[str, list]] = []
        self.deleted: list[str] = []
        self.scroll_rows: list = []

    def collection_exists(self, collection: str) -> bool:
        return collection in self.collections

    def create_collection(self, collection_name: str, vectors_config) -> None:
        self.collections.add(collection_name)
        self.vectors_config = vectors_config

    def upsert(self, collection_name: str, points: list) -> None:
        self.upserts.append((collection_name, points))

    def scroll(self, **kwargs):
        return self.scroll_rows, None

    def delete(self, collection_name: str, points_selector) -> None:
        self.deleted.extend(points_selector.points)


def _install_fake_qdrant(monkeypatch):
    holder: dict[str, _FakeClient] = {}

    def factory(**kwargs):
        client = _FakeClient(**kwargs)
        holder["client"] = client
        return client

    module = types.ModuleType("qdrant_client")
    module.QdrantClient = factory
    module.models = _ModelFactory()
    monkeypatch.setitem(sys.modules, "qdrant_client", module)
    return holder


def test_qdrant_store_from_config_uses_defaults() -> None:
    assert QdrantMathVectorStore.from_config(QdrantConfig()) is None

    store = QdrantMathVectorStore.from_config(
        QdrantConfig(enable=True, url="http://qdrant:6333", apiKey="sk-test"),
        dimensions=1024,
    )

    assert store is not None
    assert store.url == "http://qdrant:6333"
    assert store.collection == "nanobot_math_chunks"
    assert store.api_key == "sk-test"
    assert store.dimensions == 1024


def test_stable_point_id_and_chunk_key_are_deterministic() -> None:
    chunk = IndexedChunk(
        source_type="math",
        path="lesson.md",
        start_line=7,
        end_line=9,
        kind="formula",
        text="$x^2$",
    )
    key = chunk_key(chunk)

    assert key == ("lesson.md", 7, 9, "formula")
    assert stable_point_id("math", key) == stable_point_id("math", key)
    assert stable_point_id("math", key) != stable_point_id("web", key)


def test_qdrant_upsert_builds_payload_and_skips_bad_vectors(monkeypatch) -> None:
    holder = _install_fake_qdrant(monkeypatch)
    store = QdrantMathVectorStore(
        url="http://localhost:6333",
        collection="math",
        dimensions=3,
    )
    chunk = IndexedChunk(
        source_type="math",
        path="lesson.md",
        start_line=1,
        end_line=3,
        kind="example_question",
        text="例题：求极限",
        title="重要极限",
        symbols=["chapter:极限", "example_id:abc"],
        content_hash="hash1",
    )

    count = store.upsert_chunks(
        source_type="math",
        chunks=[chunk, chunk],
        vectors=[[0.1, 0.2, 0.3], [0.1]],
    )

    client = holder["client"]
    assert count == 1
    assert client.upserts[0][0] == "math"
    point = client.upserts[0][1][0]
    assert point.payload["path"] == "lesson.md"
    assert point.payload["chapter"] == "极限"
    assert point.payload["example_id"] == "abc"
    assert point.payload["chunk_key"] == ["lesson.md", 1, 3, "example_question"]
