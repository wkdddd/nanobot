"""Tests for RAG index and embedding integration."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.rag import IndexedChunk, RAGIndex


def test_rag_index_schema_has_embedding_column(tmp_path: Path):
    index = RAGIndex(tmp_path)
    index._ensure_schema()
    import sqlite3
    conn = sqlite3.connect(str(index.db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    conn.close()
    assert "embedding" in cols


def test_rag_index_fts5_table_exists(tmp_path: Path):
    index = RAGIndex(tmp_path)
    index._ensure_schema()
    import sqlite3
    conn = sqlite3.connect(str(index.db_path))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        ).fetchall()
    }
    conn.close()
    assert "chunks_fts" in tables


def test_sync_and_fts5_search(tmp_path: Path):
    index = RAGIndex(tmp_path)
    _write_test_file(tmp_path, "hello.py", "def hello_world():\n    print('hi')\n")
    _write_test_file(tmp_path, "utils.py", "def retry_logic():\n    pass\n")

    index.sync_files(
        source_type="repo",
        files=[tmp_path / "hello.py", tmp_path / "utils.py"],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )

    scores = index._fts5_search("repo", "retry", limit=10)
    assert len(scores) >= 1
    keys = list(scores.keys())
    assert any("utils.py" in k[0] for k in keys)


def test_sync_prune_missing(tmp_path: Path):
    index = RAGIndex(tmp_path)
    f1 = _write_test_file(tmp_path, "a.py", "x = 1")
    f2 = _write_test_file(tmp_path, "b.py", "y = 2")

    index.sync_files(
        source_type="repo",
        files=[f1, f2],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )
    assert index.count("repo") == 2

    # Only sync a.py — b.py should be pruned
    index.sync_files(
        source_type="repo",
        files=[f1],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )
    assert index.count("repo") == 1


def test_count(tmp_path: Path):
    index = RAGIndex(tmp_path)
    assert index.count("repo") == 0

    _write_test_file(tmp_path, "f.py", "code")
    index.sync_files(
        source_type="repo",
        files=[tmp_path / "f.py"],
        chunker=_simple_chunker,
        max_file_chars=80_000,
    )
    assert index.count("repo") == 1


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
