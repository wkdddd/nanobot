"""Lightweight persistent context index for repository and web references."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from nanobot.agent.rag.embedding import cosine_similarity, deserialize_embedding


@dataclass(slots=True)
class IndexedChunk:
    source_type: str
    path: str
    start_line: int
    end_line: int
    text: str
    kind: str = "text"
    symbols: list[str] = field(default_factory=list)
    title: str = ""
    url: str = ""
    query: str = ""
    fetched_at: str = ""
    mtime: float = 0.0
    content_hash: str = ""


@dataclass(slots=True)
class IndexedHit:
    chunk: IndexedChunk
    score: float
    reason: list[str] = field(default_factory=list)


ScoreFn = Callable[[IndexedChunk, list[str]], tuple[float, list[str]]]
ChunkerFn = Callable[[Path, str], list[IndexedChunk]]
ChunkKey = tuple[str, int, int, str]


class ContextIndex:
    """SQLite-backed chunk index shared by context retrieval tools."""

    _SCHEMA_VERSION = 2

    def __init__(self, workspace: Path, db_path: Path | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.db_path = db_path or self.workspace / ".nanobot" / "context_index.sqlite"

    def sync_files(
        self,
        *,
        source_type: str,
        files: Iterable[Path],
        chunker: ChunkerFn,
        max_file_chars: int,
        prune_missing: bool = True,
    ) -> None:
        self._ensure_schema()
        seen: set[str] = set()
        with self._connect() as conn:
            for path in files:
                rel = path.relative_to(self.workspace).as_posix()
                seen.add(rel)
                raw = self._read_bytes(path, max_file_chars=max_file_chars)
                if raw is None:
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                mtime = self._mtime(path)
                if self._is_current(conn, source_type, rel, digest, mtime):
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                chunks = chunker(path, text)
                self._replace_chunks(conn, source_type, rel, chunks, digest, mtime)

            if prune_missing:
                self._prune_missing(conn, source_type, seen)

    def search(
        self,
        *,
        source_type: str,
        query: str,
        max_hits: int,
        score_fn: ScoreFn,
        semantic_scores: dict[ChunkKey, float] | None = None,
        semantic_weight: float = 0.6,
    ) -> list[IndexedHit]:
        terms = query_terms(query)
        if not terms:
            return []
        self._ensure_schema()
        hits: list[IndexedHit] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_type, path, start_line, end_line, kind, text, symbols,
                       title, url, query, fetched_at, mtime, content_hash
                FROM chunks
                WHERE source_type = ?
                ORDER BY path, start_line
                """,
                (source_type,),
            ).fetchall()

        max_lexical = 0.0
        raw_scores: list[tuple[IndexedChunk, float, list[str]]] = []
        for row in rows:
            chunk = self._chunk_from_row(row)
            score, reason = score_fn(chunk, terms)
            if score > max_lexical:
                max_lexical = score
            raw_scores.append((chunk, score, reason))

        for chunk, lexical, reason in raw_scores:
            key = (chunk.path, chunk.start_line, chunk.end_line, chunk.kind)
            if semantic_scores:
                lex_norm = lexical / max_lexical if max_lexical > 0 else 0.0
                sem = semantic_scores.get(key, 0.0)
                final = (1 - semantic_weight) * lex_norm + semantic_weight * sem
                if final > 0:
                    hits.append(IndexedHit(chunk=chunk, score=final, reason=reason))
            elif lexical > 0:
                hits.append(IndexedHit(chunk=chunk, score=lexical, reason=reason))

        hits.sort(key=lambda hit: (-hit.score, hit.chunk.path, hit.chunk.start_line))
        return hits[:max_hits]

    def count(self, source_type: str) -> int:
        self._ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_type = ?",
                (source_type,),
            ).fetchone()
        return int(row[0]) if row else 0

    def get_chunks_without_embedding(self, source_type: str) -> list[tuple[str, int, int, str, str]]:
        """Return (path, start_line, end_line, kind, text) for chunks missing embeddings."""
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT path, start_line, end_line, kind, text
                FROM chunks
                WHERE source_type = ? AND embedding IS NULL
                ORDER BY path, start_line
                """,
                (source_type,),
            ).fetchall()
        return [(str(r[0]), int(r[1]), int(r[2]), str(r[3]), str(r[4])) for r in rows]

    def store_embeddings(
        self,
        source_type: str,
        embeddings: dict[ChunkKey, bytes],
    ) -> None:
        """Store serialized embedding vectors for chunks. Key: (path, start, end, kind)."""
        if not embeddings:
            return
        self._ensure_schema()
        with self._connect() as conn:
            for (path, start, end, kind), data in embeddings.items():
                conn.execute(
                    """
                    UPDATE chunks SET embedding = ?
                    WHERE source_type = ? AND path = ? AND start_line = ?
                          AND end_line = ? AND kind = ?
                    """,
                    (data, source_type, path, start, end, kind),
                )

    def semantic_search(
        self,
        source_type: str,
        query_embedding: list[float],
    ) -> dict[ChunkKey, float]:
        """Compute cosine similarity for all embedded chunks. Returns {key: score}."""
        self._ensure_schema()
        scores: dict[ChunkKey, float] = {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT path, start_line, end_line, kind, embedding
                FROM chunks
                WHERE source_type = ? AND embedding IS NOT NULL
                """,
                (source_type,),
            ).fetchall()
        for row in rows:
            data = row[4]
            if not data:
                continue
            chunk_vec = deserialize_embedding(data)
            sim = cosine_similarity(query_embedding, chunk_vec)
            if sim > 0:
                scores[(str(row[0]), int(row[1]), int(row[2]), str(row[3]))] = sim
        return scores

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    source_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    symbols TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    query TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding BLOB,
                    PRIMARY KEY (source_type, path, start_line, end_line, kind)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_source_path ON chunks(source_type, path)"
            )
            # Migrate from schema v1: add embedding column if missing
            cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            if "embedding" not in cols:
                conn.execute("ALTER TABLE chunks ADD COLUMN embedding BLOB")
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(self._SCHEMA_VERSION),),
            )

    @staticmethod
    def _read_bytes(path: Path, *, max_file_chars: int) -> bytes | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        if len(raw) > max_file_chars:
            return raw[:max_file_chars]
        return raw

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _is_current(
        conn: sqlite3.Connection,
        source_type: str,
        path: str,
        content_hash: str,
        mtime: float,
    ) -> bool:
        row = conn.execute(
            """
            SELECT content_hash, mtime
            FROM chunks
            WHERE source_type = ? AND path = ?
            LIMIT 1
            """,
            (source_type, path),
        ).fetchone()
        if not row:
            return False
        return row[0] == content_hash and float(row[1]) == float(mtime)

    @staticmethod
    def _replace_chunks(
        conn: sqlite3.Connection,
        source_type: str,
        path: str,
        chunks: list[IndexedChunk],
        content_hash: str,
        mtime: float,
    ) -> None:
        conn.execute(
            "DELETE FROM chunks WHERE source_type = ? AND path = ?",
            (source_type, path),
        )
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO chunks(
                    source_type, path, start_line, end_line, kind, text, symbols,
                    title, url, query, fetched_at, mtime, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_type,
                    path,
                    int(chunk.start_line),
                    int(chunk.end_line),
                    chunk.kind,
                    chunk.text,
                    json.dumps(chunk.symbols, ensure_ascii=False),
                    chunk.title,
                    chunk.url,
                    chunk.query,
                    chunk.fetched_at,
                    mtime,
                    content_hash,
                ),
            )

    @staticmethod
    def _prune_missing(conn: sqlite3.Connection, source_type: str, seen: set[str]) -> None:
        rows = conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE source_type = ?",
            (source_type,),
        ).fetchall()
        for row in rows:
            path = str(row[0])
            if path not in seen:
                conn.execute(
                    "DELETE FROM chunks WHERE source_type = ? AND path = ?",
                    (source_type, path),
                )

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row | tuple) -> IndexedChunk:
        symbols_raw = row[6] or "[]"
        try:
            symbols = json.loads(symbols_raw)
        except json.JSONDecodeError:
            symbols = []
        if not isinstance(symbols, list):
            symbols = []
        return IndexedChunk(
            source_type=str(row[0]),
            path=str(row[1]),
            start_line=int(row[2]),
            end_line=int(row[3]),
            kind=str(row[4]),
            text=str(row[5]),
            symbols=[str(item) for item in symbols],
            title=str(row[7] or ""),
            url=str(row[8] or ""),
            query=str(row[9] or ""),
            fetched_at=str(row[10] or ""),
            mtime=float(row[11] or 0.0),
            content_hash=str(row[12] or ""),
        )


def query_terms(query: str) -> list[str]:
    raw = re.findall(
        r"[A-Za-z_][A-Za-z0-9_./:-]*|[\u4e00-\u9fff]+",
        query.lower(),
    )
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "怎么",
        "如何",
        "什么",
        "一个",
        "这个",
        "那个",
    }
    terms = [term for term in raw if len(term) >= 2 and term not in stop]
    return list(dict.fromkeys(terms))


def best_snippet(text: str, terms: list[str], *, start_line: int, snippet_lines: int) -> str:
    lines = text.replace("\r\n", "\n").splitlines()
    if not lines:
        return ""

    best_index = 0
    best_score = 0
    lowered_terms = [term.lower() for term in terms]
    for index, line in enumerate(lines):
        low = line.lower()
        score = sum(1 for term in lowered_terms if term in low)
        if score > best_score:
            best_score = score
            best_index = index

    half = max(1, snippet_lines // 2)
    start = max(0, best_index - half)
    end = min(len(lines), start + snippet_lines)
    return "\n".join(
        f"{start_line + line_no}| {lines[line_no]}"
        for line_no in range(start, end)
    )


def lexical_score(
    *,
    terms: list[str],
    fields: dict[str, str],
    weights: dict[str, float],
    repeated_text_weight: float = 1.0,
) -> tuple[float, list[str]]:
    score = 0.0
    reason: list[str] = []
    lowered = {name: value.lower() for name, value in fields.items()}
    for term in terms:
        for name, value in lowered.items():
            if term not in value:
                continue
            weight = weights.get(name, 1.0)
            if name == "text":
                score += min(value.count(term), 8) * repeated_text_weight
            else:
                score += weight
            reason.append(f"{name}:{term}")
    return score, list(dict.fromkeys(reason))

