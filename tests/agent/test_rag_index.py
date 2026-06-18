from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from nanobot.rag.index import RAGIndex
from nanobot.rag.qdrant_store import QdrantVectorHit
from nanobot.rag.utils import IndexedChunk, IndexedHit


class _EmbeddingClient:
    dimensions = 3

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    async def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


@dataclass
class _VectorStore:
    fail_search: bool = False
    search_key: tuple[str, int, int, str] = ("src/app.py", 1, 1, "text")
    upsert_count: int | None = None

    def __post_init__(self) -> None:
        self.upserted: list[IndexedChunk] = []
        self.searched = 0

    def upsert_chunks(
        self,
        *,
        source_type: str,
        chunks: list[IndexedChunk],
        vectors: list[list[float] | None],
    ) -> int:
        self.upserted.extend(chunks)
        if self.upsert_count is not None:
            return self.upsert_count
        return len([vector for vector in vectors if vector is not None])

    def search(
        self,
        *,
        source_type: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[QdrantVectorHit]:
        if self.fail_search:
            raise RuntimeError("qdrant unavailable")
        self.searched += 1
        return [
            QdrantVectorHit(
                key=self.search_key,
                score=0.91,
                payload={},
            )
        ]

    def prune_missing(self, *, source_type: str, keep_point_ids: set[str]) -> int:
        return 0


class _CapturingReranker:
    def __init__(self) -> None:
        self.documents: list[str] = []

    async def rerank(self, query: str, documents: list[str], top_n: int):
        self.documents = documents
        return [(0, 0.9)]


@pytest.mark.asyncio
async def test_rerank_document_includes_math_metadata(tmp_path) -> None:
    reranker = _CapturingReranker()
    index = RAGIndex(tmp_path, rerank_client=reranker)
    hit = IndexedHit(
        chunk=IndexedChunk(
            source_type="math",
            path="lesson.md",
            start_line=10,
            end_line=12,
            kind="example_solution",
            text="由重要极限可知答案为 1。",
            title="重要极限例题",
            symbols=["chapter:极限", "example_id:e1"],
        ),
        score=1.0,
        reason=["hybrid"],
    )

    result = await index.rerank("sin x / x", [hit], 1)

    assert result == [hit]
    assert reranker.documents
    doc = reranker.documents[0]
    assert "标题: 重要极限例题" in doc
    assert "来源: lesson.md:10-12" in doc
    assert "类型: example_solution" in doc
    assert "chapter:极限" in doc
    assert "由重要极限可知答案为 1。" in doc


def _write_repo_file(tmp_path: Path, text: str = "def review_target():\n    return 'ok'\n") -> Path:
    path = tmp_path / "src" / "app.py"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def _chunk_file(path: Path, text: str) -> list[IndexedChunk]:
    return [
        IndexedChunk(
            source_type="repo",
            path=path.as_posix(),
            start_line=1,
            end_line=1,
            kind="text",
            text=text,
        )
    ]


@pytest.mark.asyncio
async def test_search_prefers_qdrant_dense_candidates(tmp_path) -> None:
    path = _write_repo_file(tmp_path, "def dense_only_candidate():\n    return 'ok'\n")
    vector_store = _VectorStore()
    index = RAGIndex(
        tmp_path,
        embedding_client=_EmbeddingClient(),
        vector_store=vector_store,
    )
    index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    hits = await index.search(source_type="repo", query="unmatched semantic query", max_hits=3)

    assert hits
    assert hits[0].chunk.path == "src/app.py"
    assert "qdrant" in hits[0].reason
    assert vector_store.upserted


@pytest.mark.asyncio
async def test_search_falls_back_to_lexical_when_qdrant_fails(tmp_path) -> None:
    path = _write_repo_file(tmp_path, "def lexical_target():\n    return 'ok'\n")
    index = RAGIndex(
        tmp_path,
        embedding_client=_EmbeddingClient(),
        vector_store=_VectorStore(fail_search=True),
    )
    index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    hits = await index.search(source_type="repo", query="lexical_target", max_hits=3)

    assert hits
    assert hits[0].chunk.path == "src/app.py"
    assert "bm25" in hits[0].reason


@pytest.mark.asyncio
async def test_search_orders_qdrant_before_lexical_candidates(tmp_path) -> None:
    dense_path = _write_repo_file(tmp_path, "def dense_candidate():\n    return 'ok'\n")
    lexical_path = tmp_path / "src" / "lexical.py"
    lexical_path.write_text("def lexical_target():\n    return 'ok'\n", encoding="utf-8")
    vector_store = _VectorStore(search_key=("src/app.py", 1, 1, "text"))
    index = RAGIndex(
        tmp_path,
        embedding_client=_EmbeddingClient(),
        vector_store=vector_store,
    )
    index.sync_files(
        source_type="repo",
        files=[dense_path, lexical_path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    hits = await index.search(source_type="repo", query="lexical_target", max_hits=2)

    assert [hit.chunk.path for hit in hits][:2] == ["src/app.py", "src/lexical.py"]
    assert hits[0].reason == ["qdrant"]
    assert "bm25" in hits[1].reason


def test_sync_files_backfills_qdrant_for_current_sqlite_chunks(tmp_path) -> None:
    path = _write_repo_file(tmp_path, "def existing_sqlite_chunk():\n    return 'ok'\n")
    first_index = RAGIndex(tmp_path, embedding_client=_EmbeddingClient())
    first_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    vector_store = _VectorStore()
    second_index = RAGIndex(
        tmp_path,
        embedding_client=_EmbeddingClient(),
        vector_store=vector_store,
    )
    second_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    assert [chunk.path for chunk in vector_store.upserted] == ["src/app.py"]

    second_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    assert [chunk.path for chunk in vector_store.upserted] == ["src/app.py"]


def test_sync_files_skips_unchanged_files_before_chunking(tmp_path) -> None:
    path = _write_repo_file(tmp_path, "def unchanged_cache_hit():\n    return 'ok'\n")
    index = RAGIndex(tmp_path)
    calls = 0

    def chunker(path: Path, text: str) -> list[IndexedChunk]:
        nonlocal calls
        calls += 1
        return _chunk_file(path, text)

    index.sync_files(
        source_type="repo",
        files=[path],
        chunker=chunker,
        max_file_chars=1000,
    )
    index.sync_files(
        source_type="repo",
        files=[path],
        chunker=chunker,
        max_file_chars=1000,
    )

    assert calls == 1


def test_sync_files_retries_qdrant_backfill_after_incomplete_upsert(tmp_path) -> None:
    path = _write_repo_file(tmp_path, "def retry_qdrant_sync():\n    return 'ok'\n")
    first_index = RAGIndex(tmp_path, embedding_client=_EmbeddingClient())
    first_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    vector_store = _VectorStore(upsert_count=0)
    second_index = RAGIndex(
        tmp_path,
        embedding_client=_EmbeddingClient(),
        vector_store=vector_store,
    )
    second_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )
    vector_store.upsert_count = None
    second_index.sync_files(
        source_type="repo",
        files=[path],
        chunker=_chunk_file,
        max_file_chars=1000,
    )

    assert [chunk.path for chunk in vector_store.upserted] == ["src/app.py", "src/app.py"]
