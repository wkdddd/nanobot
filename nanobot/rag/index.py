"""RAGIndex — unified retrieval index with FTS5 lexical + hnswlib semantic search."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterable

from nanobot.rag.utils import ChunkerFn, ChunkKey, IndexedChunk, IndexedHit, chunk_from_row

logger = logging.getLogger(__name__)


def _vec_norm(vec: list[float]) -> float:
    return sum(x * x for x in vec) ** 0.5


def _cosine_sim(a: list[float], b: list[float], a_norm: float) -> float:
    b_norm = _vec_norm(b)
    if a_norm == 0 or b_norm == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (a_norm * b_norm)


def _rerank_document_text(hit: IndexedHit) -> str:
    chunk = hit.chunk
    symbols = ", ".join(chunk.symbols)
    parts = [
        f"标题: {chunk.title}" if chunk.title else "",
        f"来源: {chunk.path}:{chunk.start_line}-{chunk.end_line}",
        f"类型: {chunk.kind}",
        f"符号: {symbols}" if symbols else "",
        chunk.text,
    ]
    return "\n".join(part for part in parts if part).strip()


class RAGIndex:
    """Unified RAG index: SQLite storage + FTS5 lexical search + hnswlib ANN."""

    _SCHEMA_VERSION = 3

    def __init__(
        self,
        workspace: Path,
        db_path: Path | None = None,
        *,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        dimensions: int = 1024,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.db_path = db_path or self.workspace / ".nanobot" / "context_index.sqlite"
        self.embedding_client = embedding_client
        self.rerank_client = rerank_client
        self.dimensions = dimensions
        self._hnsw_indexes: dict[str, _HnswHandle] = {}

    # ─── Index maintenance ───────────────────────────────────────────────

    def sync_files(
        self,
        *,
        source_type: str,
        files: Iterable[Path],
        chunker: ChunkerFn,
        max_file_chars: int,
        prune_missing: bool = True,
        skip_embed_filter: Callable[[str, str], bool] | None = None,
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
                file_skip = bool(skip_embed_filter and skip_embed_filter(rel, text))
                self._replace_chunks(
                    conn, source_type, rel, chunks, digest, mtime,
                    skip_embedding=file_skip,
                )

            if prune_missing:
                self._prune_missing(conn, source_type, seen)

    def count(self, source_type: str) -> int:
        self._ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_type = ?", (source_type,)
            ).fetchone()
        return int(row[0]) if row else 0

    def list_chunks(self, source_type: str) -> list[IndexedChunk]:
        """Load all chunks for a source type in deterministic order."""
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_type, path, start_line, end_line, kind, text, symbols,
                       title, url, query, fetched_at, mtime, content_hash
                FROM chunks
                WHERE source_type = ?
                ORDER BY path, start_line, end_line, kind
                """,
                (source_type,),
            ).fetchall()
        return [chunk_from_row(row) for row in rows]

    # ─── Retrieval ───────────────────────────────────────────────────────

    async def search(
        self,
        *,
        source_type: str,
        query: str,
        max_hits: int = 20,
        semantic_weight: float = 0.6,
    ) -> list[IndexedHit]:
        from nanobot.rag.utils import query_terms

        terms = query_terms(query)
        if not terms:
            return []

        self._ensure_schema()

        # Stage 1: FTS5 BM25 lexical candidates
        lexical_hits = self._fts5_search(source_type, query, limit=100)

        # Stage 2: On-demand semantic scoring of BM25 candidates
        semantic_scores: dict[ChunkKey, float] = {}
        if self.embedding_client and lexical_hits:
            semantic_scores = await self._embed_and_score_candidates(
                source_type, query, set(lexical_hits.keys())
            )

        # Stage 3: Hybrid scoring
        hits = self._merge_scores(
            lexical_hits, semantic_scores, semantic_weight=semantic_weight
        )
        hits.sort(key=lambda h: -h.score)
        candidates = hits[: max_hits * 3]

        # Stage 4: Rerank (optional)
        if self.rerank_client and candidates:
            candidates = await self._rerank(query, candidates, max_hits)
        else:
            candidates = candidates[:max_hits]

        return candidates

    # ─── FTS5 lexical search ─────────────────────────────────────────────

    def lexical_search(
        self, source_type: str, query: str, *, limit: int = 100
    ) -> list[IndexedHit]:
        scores = self._fts5_search(source_type, query, limit=limit)
        chunks_by_key = self._load_chunks_by_keys(set(scores), source_type=source_type)
        hits = [
            IndexedHit(chunk=chunk, score=scores[key], reason=["bm25"])
            for key, chunk in chunks_by_key.items()
            if key in scores
        ]
        hits.sort(key=lambda h: -h.score)
        return hits

    def _fts5_search(
        self, source_type: str, query: str, *, limit: int = 100
    ) -> dict[ChunkKey, float]:
        from nanobot.rag.utils import query_terms

        terms = query_terms(query)
        if not terms:
            return {}

        fts_query = " OR ".join(f'"{t}"' for t in terms)
        scores: dict[ChunkKey, float] = {}
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT c.path, c.start_line, c.end_line, c.kind, bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    JOIN chunks c ON c.rowid = chunks_fts.rowid
                    WHERE chunks_fts MATCH ? AND c.source_type = ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, source_type, limit),
                ).fetchall()
            except Exception:
                rows = []

        for row in rows:
            key: ChunkKey = (str(row[0]), int(row[1]), int(row[2]), str(row[3]))
            scores[key] = -float(row[4])

        # Fallback: LIKE-based search for CJK or when FTS5 returns nothing
        if not scores:
            scores = self._like_search(source_type, terms, limit=limit)

        return scores

    def _like_search(
        self, source_type: str, terms: list[str], *, limit: int = 100
    ) -> dict[ChunkKey, float]:
        scores: dict[ChunkKey, float] = {}
        with self._connect() as conn:
            for term in terms:
                pattern = f"%{term}%"
                rows = conn.execute(
                    """
                    SELECT path, start_line, end_line, kind
                    FROM chunks
                    WHERE source_type = ? AND (text LIKE ? OR path LIKE ? OR symbols LIKE ?)
                    LIMIT ?
                    """,
                    (source_type, pattern, pattern, pattern, limit),
                ).fetchall()
                for row in rows:
                    key: ChunkKey = (str(row[0]), int(row[1]), int(row[2]), str(row[3]))
                    scores[key] = scores.get(key, 0.0) + 1.0
        return scores

    # ─── Semantic on-demand embedding ─────────────────────────────────────

    async def _embed_and_score_candidates(
        self,
        source_type: str,
        query: str,
        candidate_keys: set[ChunkKey],
    ) -> dict[ChunkKey, float]:
        """Embed query + BM25 candidates on-demand, return cosine similarities."""
        if not self.embedding_client or not candidate_keys:
            return {}

        query_vec = await self.embedding_client.embed_query(query)
        if query_vec is None:
            return {}

        # Load candidate texts that need embedding
        to_embed: list[tuple[ChunkKey, str]] = []
        cached: dict[ChunkKey, list[float]] = {}

        with self._connect() as conn:
            for key in candidate_keys:
                row = conn.execute(
                    """
                    SELECT text, embedding FROM chunks
                    WHERE source_type = ? AND path = ? AND start_line = ?
                      AND end_line = ? AND kind = ?
                    """,
                    (source_type, key[0], key[1], key[2], key[3]),
                ).fetchone()
                if not row:
                    continue
                text, emb_blob = row
                if emb_blob:
                    n = len(emb_blob) // 4
                    cached[key] = list(struct.unpack(f"{n}f", emb_blob))
                elif text and text.strip():
                    to_embed.append((key, text))

        # Batch embed only those without cached embeddings
        if to_embed:
            texts = [t for _, t in to_embed]
            vecs = await self.embedding_client.embed_texts(texts)

            with self._connect() as conn:
                for (key, _), vec in zip(to_embed, vecs):
                    if vec is None:
                        continue
                    cached[key] = vec
                    data = struct.pack(f"{len(vec)}f", *vec)
                    conn.execute(
                        """
                        UPDATE chunks SET embedding = ?
                        WHERE source_type = ? AND path = ? AND start_line = ?
                          AND end_line = ? AND kind = ?
                        """,
                        (data, source_type, key[0], key[1], key[2], key[3]),
                    )

        # Compute cosine similarity
        scores: dict[ChunkKey, float] = {}
        q_norm = _vec_norm(query_vec)
        if q_norm == 0:
            return {}
        for key, vec in cached.items():
            sim = _cosine_sim(query_vec, vec, q_norm)
            if sim > 0:
                scores[key] = sim

        return scores

    # ─── Hybrid merge ────────────────────────────────────────────────────

    def _merge_scores(
        self,
        lexical: dict[ChunkKey, float],
        semantic: dict[ChunkKey, float],
        *,
        semantic_weight: float,
    ) -> list[IndexedHit]:
        all_keys = set(lexical) | set(semantic)
        if not all_keys:
            return []

        max_lex = max(lexical.values()) if lexical else 1.0
        max_sem = max(semantic.values()) if semantic else 1.0

        hits: list[IndexedHit] = []
        chunks_by_key = self._load_chunks_by_keys(all_keys)

        for key in all_keys:
            chunk = chunks_by_key.get(key)
            if not chunk:
                continue
            lex_norm = (lexical.get(key, 0.0) / max_lex) if max_lex > 0 else 0.0
            sem_norm = (semantic.get(key, 0.0) / max_sem) if max_sem > 0 else 0.0

            if semantic:
                score = (1 - semantic_weight) * lex_norm + semantic_weight * sem_norm
            else:
                score = lex_norm

            if score > 0:
                reason = []
                if key in lexical:
                    reason.append("bm25")
                if key in semantic:
                    reason.append("semantic")
                hits.append(IndexedHit(chunk=chunk, score=score, reason=reason))

        return hits

    def _load_chunks_by_keys(
        self, keys: set[ChunkKey], *, source_type: str | None = None
    ) -> dict[ChunkKey, IndexedChunk]:
        if not keys:
            return {}
        result: dict[ChunkKey, IndexedChunk] = {}
        with self._connect() as conn:
            for key in keys:
                if source_type is None:
                    row = conn.execute(
                        """
                        SELECT source_type, path, start_line, end_line, kind, text, symbols,
                               title, url, query, fetched_at, mtime, content_hash
                        FROM chunks
                        WHERE path = ? AND start_line = ? AND end_line = ? AND kind = ?
                        """,
                        (key[0], key[1], key[2], key[3]),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT source_type, path, start_line, end_line, kind, text, symbols,
                               title, url, query, fetched_at, mtime, content_hash
                        FROM chunks
                        WHERE source_type = ? AND path = ? AND start_line = ?
                          AND end_line = ? AND kind = ?
                        """,
                        (source_type, key[0], key[1], key[2], key[3]),
                    ).fetchone()
                if row:
                    result[key] = chunk_from_row(row)
        return result

    # ─── Rerank ──────────────────────────────────────────────────────────

    async def rerank(
        self, query: str, candidates: list[IndexedHit], max_hits: int
    ) -> list[IndexedHit]:
        if self.rerank_client and candidates:
            return await self._rerank(query, candidates, max_hits)
        return candidates[:max_hits]

    async def _rerank(
        self, query: str, candidates: list[IndexedHit], max_hits: int
    ) -> list[IndexedHit]:
        docs = [_rerank_document_text(c) for c in candidates]
        ranked = await self.rerank_client.rerank(query, docs, top_n=max_hits)
        if not ranked:
            return candidates[:max_hits]
        return [candidates[idx] for idx, _ in ranked if idx < len(candidates)]

    # ─── HNSW index management ───────────────────────────────────────────

    def _get_hnsw(self, source_type: str) -> "_HnswHandle":
        if source_type not in self._hnsw_indexes:
            index_dir = self.db_path.parent
            dim = self._detect_embedding_dim(source_type) or self.dimensions
            handle = _HnswHandle(
                index_path=index_dir / f"rag_{source_type}.hnsw",
                keys_path=index_dir / f"rag_{source_type}_keys.json",
                dim=dim,
            )
            if not handle.load():
                self._rebuild_hnsw(source_type, handle)
            self._hnsw_indexes[source_type] = handle
        return self._hnsw_indexes[source_type]

    def _detect_embedding_dim(self, source_type: str) -> int | None:
        """Detect actual embedding dimension from stored data."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT embedding FROM chunks "
                    "WHERE source_type = ? AND embedding IS NOT NULL LIMIT 1",
                    (source_type,),
                ).fetchone()
            if row and row[0]:
                return len(row[0]) // 4
        except Exception:
            pass
        return None

    def _rebuild_hnsw(self, source_type: str, handle: "_HnswHandle") -> None:
        """Rebuild HNSW index from existing embeddings in SQLite."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT path, start_line, end_line, kind, embedding
                FROM chunks WHERE source_type = ? AND embedding IS NOT NULL
                """,
                (source_type,),
            ).fetchall()

        if not rows:
            return

        keys: list[ChunkKey] = []
        vecs: list[list[float]] = []
        for row in rows:
            data = row[4]
            if not data:
                continue
            count = len(data) // 4
            vec = list(struct.unpack(f"{count}f", data))
            keys.append((str(row[0]), int(row[1]), int(row[2]), str(row[3])))
            vecs.append(vec)

        if keys:
            handle.build(keys, vecs)
            handle.save()

    # ─── SQLite infrastructure ───────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                )
            """)
            conn.execute("""
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
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_source_path ON chunks(source_type, path)"
            )
            # FTS5 virtual table for BM25 lexical search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    path, symbols, text,
                    content='chunks',
                    content_rowid='rowid'
                )
            """)
            # Triggers to keep FTS5 in sync
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, path, symbols, text)
                    VALUES (new.rowid, new.path, new.symbols, new.text);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, path, symbols, text)
                    VALUES ('delete', old.rowid, old.path, old.symbols, old.text);
                END
            """)
            # Migrate: add embedding column if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            if "embedding" not in cols:
                conn.execute("ALTER TABLE chunks ADD COLUMN embedding BLOB")
            if "skip_embedding" not in cols:
                conn.execute("ALTER TABLE chunks ADD COLUMN skip_embedding INTEGER DEFAULT 0")
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
        conn: sqlite3.Connection, source_type: str, path: str, content_hash: str, mtime: float
    ) -> bool:
        row = conn.execute(
            "SELECT content_hash, mtime FROM chunks WHERE source_type = ? AND path = ? LIMIT 1",
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
        skip_embedding: bool = False,
    ) -> None:
        from nanobot.rag.chunk_filter import is_chunk_valid

        conn.execute(
            "DELETE FROM chunks WHERE source_type = ? AND path = ?", (source_type, path)
        )
        for chunk in chunks:
            chunk_skip = skip_embedding or not is_chunk_valid(chunk.text, chunk.kind)
            conn.execute(
                """
                INSERT INTO chunks(
                    source_type, path, start_line, end_line, kind, text, symbols,
                    title, url, query, fetched_at, mtime, content_hash, skip_embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_type, path, int(chunk.start_line), int(chunk.end_line),
                    chunk.kind, chunk.text,
                    json.dumps(chunk.symbols, ensure_ascii=False),
                    chunk.title, chunk.url, chunk.query, chunk.fetched_at,
                    mtime, content_hash,
                    1 if chunk_skip else 0,
                ),
            )

    def _prune_missing(
        self, conn: sqlite3.Connection, source_type: str, seen: set[str]
    ) -> None:
        rows = conn.execute(
            "SELECT DISTINCT path FROM chunks WHERE source_type = ?", (source_type,)
        ).fetchall()
        removed_keys: set[ChunkKey] = set()
        for row in rows:
            path = str(row[0])
            if path not in seen:
                # Collect keys for hnsw removal
                chunk_rows = conn.execute(
                    "SELECT path, start_line, end_line, kind FROM chunks "
                    "WHERE source_type = ? AND path = ?",
                    (source_type, path),
                ).fetchall()
                for cr in chunk_rows:
                    removed_keys.add((str(cr[0]), int(cr[1]), int(cr[2]), str(cr[3])))
                conn.execute(
                    "DELETE FROM chunks WHERE source_type = ? AND path = ?",
                    (source_type, path),
                )
        if removed_keys and source_type in self._hnsw_indexes:
            self._hnsw_indexes[source_type].remove_keys(removed_keys)


# ─── HNSW Handle ─────────────────────────────────────────────────────────────


class _HnswHandle:
    """Thin wrapper around hnswlib index with key-to-id mapping."""

    def __init__(self, index_path: Path, keys_path: Path, dim: int) -> None:
        self.index_path = index_path
        self.keys_path = keys_path
        self.dim = dim
        self._index: "Any | None" = None
        self._key_to_id: dict[ChunkKey, int] = {}
        self._id_to_key: dict[int, ChunkKey] = {}
        self._next_id: int = 0

    def load(self) -> bool:
        if not self.index_path.exists() or not self.keys_path.exists():
            return False
        try:
            import hnswlib
            idx = hnswlib.Index(space="cosine", dim=self.dim)
            idx.load_index(str(self.index_path))
            with open(self.keys_path) as f:
                keys_data = json.load(f)
            self._key_to_id = {tuple(k): v for k, v in keys_data["key_to_id"]}
            self._id_to_key = {v: tuple(k) for k, v in keys_data["key_to_id"]}
            self._next_id = keys_data.get("next_id", len(self._key_to_id))
            self._index = idx
            return True
        except Exception as e:
            logger.warning("Failed to load HNSW index: %s", e)
            return False

    def build(self, keys: list[ChunkKey], vectors: list[list[float]]) -> None:
        import hnswlib
        if not vectors:
            return
        actual_dim = len(vectors[0])
        self.dim = actual_dim
        max_elements = max(len(keys) * 2, 1000)
        idx = hnswlib.Index(space="cosine", dim=self.dim)
        idx.init_index(max_elements=max_elements, ef_construction=200, M=16)
        idx.set_ef(50)

        self._key_to_id = {}
        self._id_to_key = {}
        ids = []
        for i, key in enumerate(keys):
            self._key_to_id[key] = i
            self._id_to_key[i] = key
            ids.append(i)
        self._next_id = len(keys)

        idx.add_items(vectors, ids)
        self._index = idx

    def add(self, keys: list[ChunkKey], vectors: list[list[float]]) -> None:
        if not keys:
            return
        if self._index is None:
            self.build(keys, vectors)
            return

        # Dimension changed (e.g. model switched) — rebuild from scratch
        if vectors and len(vectors[0]) != self.dim:
            self.build(keys, vectors)
            return

        new_keys = []
        new_vecs = []
        for key, vec in zip(keys, vectors):
            if key not in self._key_to_id:
                new_keys.append(key)
                new_vecs.append(vec)

        if not new_keys:
            return

        current_max = self._index.get_max_elements()
        needed = self._next_id + len(new_keys)
        if needed > current_max:
            self._index.resize_index(needed * 2)

        ids = []
        for key in new_keys:
            idx_id = self._next_id
            self._key_to_id[key] = idx_id
            self._id_to_key[idx_id] = key
            ids.append(idx_id)
            self._next_id += 1

        self._index.add_items(new_vecs, ids)

    def query(self, vector: list[float], *, top_k: int = 50) -> dict[ChunkKey, float]:
        if self._index is None or self._index.get_current_count() == 0:
            return {}
        if len(vector) != self.dim:
            return {}
        k = min(top_k, self._index.get_current_count())
        labels, distances = self._index.knn_query([vector], k=k)
        results: dict[ChunkKey, float] = {}
        for label, dist in zip(labels[0], distances[0]):
            key = self._id_to_key.get(int(label))
            if key:
                results[key] = 1.0 - float(dist)  # cosine distance → similarity
        return results

    def remove_keys(self, keys: set[ChunkKey]) -> None:
        if self._index is None:
            return
        for key in keys:
            idx_id = self._key_to_id.pop(key, None)
            if idx_id is not None:
                self._id_to_key.pop(idx_id, None)
                try:
                    self._index.mark_deleted(idx_id)
                except Exception:
                    pass

    def save(self) -> None:
        if self._index is None:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index.save_index(str(self.index_path))
        keys_data = {
            "key_to_id": [[list(k), v] for k, v in self._key_to_id.items()],
            "next_id": self._next_id,
        }
        with open(self.keys_path, "w") as f:
            json.dump(keys_data, f)
