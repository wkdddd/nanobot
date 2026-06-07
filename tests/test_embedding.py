"""Tests for embedding client and hybrid scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.context_index import (
    ContextIndex,
    IndexedChunk,
)
from nanobot.agent.embedding import (
    cosine_similarity,
    deserialize_embedding,
    serialize_embedding,
)


def test_cosine_similarity_identical_vectors():
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, b) == 0.0


def test_serialize_deserialize_embedding():
    vec = [0.1, 0.2, 0.3, -0.5, 1.0]
    data = serialize_embedding(vec)
    assert isinstance(data, bytes)
    assert len(data) == len(vec) * 4
    restored = deserialize_embedding(data)
    for a, b in zip(vec, restored):
        assert a == pytest.approx(b, abs=1e-6)


def test_context_index_embedding_column_exists(tmp_path: Path):
    index = ContextIndex(tmp_path)
    index._ensure_schema()
    import sqlite3
    conn = sqlite3.connect(str(index.db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    conn.close()
    assert "embedding" in cols


def test_store_and_retrieve_embeddings(tmp_path: Path):
    index = ContextIndex(tmp_path)
    index.sync_files(
        source_type="repo",
        files=[_write_test_file(tmp_path, "hello.py", "def hello():\n    pass\n")],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )

    missing = index.get_chunks_without_embedding("repo")
    assert len(missing) == 1
    path, start, end, kind, text = missing[0]

    vec = [0.1] * 16
    store = {(path, start, end, kind): serialize_embedding(vec)}
    index.store_embeddings("repo", store)

    missing_after = index.get_chunks_without_embedding("repo")
    assert len(missing_after) == 0


def test_semantic_search_returns_scores(tmp_path: Path):
    index = ContextIndex(tmp_path)
    index.sync_files(
        source_type="repo",
        files=[
            _write_test_file(tmp_path, "a.py", "def foo(): pass"),
            _write_test_file(tmp_path, "b.py", "def bar(): pass"),
        ],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )

    # Store embeddings
    missing = index.get_chunks_without_embedding("repo")
    assert len(missing) == 2
    vecs = [[0.9, 0.1, 0.0, 0.0] * 4, [0.1, 0.9, 0.0, 0.0] * 4]
    store = {}
    for (path, start, end, kind, _), vec in zip(missing, vecs):
        store[(path, start, end, kind)] = serialize_embedding(vec)
    index.store_embeddings("repo", store)

    # Search with a query embedding similar to first file
    query_vec = [0.85, 0.15, 0.0, 0.0] * 4
    scores = index.semantic_search("repo", query_vec)
    assert len(scores) == 2

    # First file should score higher
    keys = sorted(scores.keys(), key=lambda k: -scores[k])
    assert "a.py" in keys[0][0]


def test_hybrid_search_combines_scores(tmp_path: Path):
    index = ContextIndex(tmp_path)
    index.sync_files(
        source_type="repo",
        files=[
            _write_test_file(tmp_path, "config.py", "settings = {}"),
            _write_test_file(tmp_path, "utils.py", "def retry(): pass"),
        ],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )

    # Store embeddings - utils.py semantically closer to "retry logic"
    missing = index.get_chunks_without_embedding("repo")
    store = {}
    for path, start, end, kind, _ in missing:
        if "utils" in path:
            vec = [0.9, 0.8, 0.1, 0.0] * 4
        else:
            vec = [0.1, 0.1, 0.9, 0.0] * 4
        store[(path, start, end, kind)] = serialize_embedding(vec)
    index.store_embeddings("repo", store)

    query_vec = [0.85, 0.75, 0.1, 0.0] * 4
    semantic_scores = index.semantic_search("repo", query_vec)

    def score_fn(chunk, terms):
        from nanobot.agent.context_index import lexical_score
        return lexical_score(
            terms=terms, fields={"text": chunk.text}, weights={"text": 1}
        )

    # With semantic scores, utils.py should rank higher even if "config" matches lexically
    hits = index.search(
        source_type="repo",
        query="retry",
        max_hits=5,
        score_fn=score_fn,
        semantic_scores=semantic_scores,
        semantic_weight=0.6,
    )
    assert len(hits) >= 1
    assert "utils.py" in hits[0].chunk.path


# --- Helpers ---

def _write_test_file(workspace: Path, name: str, content: str) -> Path:
    path = workspace / name
    path.write_text(content, encoding="utf-8")
    return path


def _simple_chunker(path: Path, text: str) -> list[IndexedChunk]:
    rel = path.relative_to(path.parent).as_posix()
    return [
        IndexedChunk(
            source_type="repo",
            path=rel,
            start_line=1,
            end_line=len(text.splitlines()),
            text=text,
            kind="text",
        )
    ]
