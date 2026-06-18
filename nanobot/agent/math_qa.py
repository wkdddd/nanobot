"""Math exam question-answering helpers."""

from __future__ import annotations

import json
import re
import shutil
import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.rag.utils import ChunkKey, IndexedHit

logger = logging.getLogger(__name__)

MATH_QA_MODE_KEY = "math_qa_mode"
KNOWLEDGE_DIR = ".nanobot/math_knowledge"
MISTAKE_BOOK_PATH = ".nanobot/math_mistakes.jsonl"
SUPPORTED_KNOWLEDGE_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl"}
SUPPORTED_SOURCE_SUFFIXES = SUPPORTED_KNOWLEDGE_SUFFIXES | {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
}


@dataclass(frozen=True)
class KnowledgeHit:
    title: str
    content: str
    source: str
    subject: str = ""
    chapter: str = ""
    tags: tuple[str, ...] = ()
    problem_types: tuple[str, ...] = ()
    score: float = 0.0

    def citation(self) -> str:
        parts = [p for p in (self.source, self.chapter) if p]
        return " / ".join(parts) if parts else self.title


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return str(content or "")


def _query_terms(query: str) -> list[str]:
    query = _normalize_ws(query)
    terms: list[str] = []
    seen: set[str] = set()

    for token in re.findall(r"[A-Za-z0-9_+\-*/^=()]{2,}", query):
        token = token.lower()
        if token not in seen:
            seen.add(token)
            terms.append(token)

    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        if chunk not in seen:
            seen.add(chunk)
            terms.append(chunk)
        if len(chunk) > 8:
            for i in range(0, len(chunk) - 1):
                pair = chunk[i : i + 2]
                if pair not in seen:
                    seen.add(pair)
                    terms.append(pair)

    math_keywords = (
        "极限", "导数", "微分", "积分", "级数", "矩阵", "行列式", "特征值", "特征向量",
        "线性相关", "概率", "随机变量", "分布", "期望", "方差", "泰勒", "拉格朗日",
        "中值定理", "偏导", "二重积分", "微分方程",
    )
    for word in math_keywords:
        if word in query and word not in seen:
            seen.add(word)
            terms.append(word)
    return terms


def _split_markdown(text: str, source: str) -> list[KnowledgeHit]:
    chunks: list[KnowledgeHit] = []
    current_title = Path(source).stem
    current_lines: list[str] = []

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append(KnowledgeHit(title=current_title, content=content, source=source))

    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", line)
        if heading:
            flush()
            current_title = heading.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    flush()
    if not chunks and text.strip():
        chunks.append(KnowledgeHit(title=Path(source).stem, content=text.strip(), source=source))
    return chunks


def _hit_from_dict(raw: dict[str, Any], source: str) -> KnowledgeHit | None:
    content = raw.get("content") or raw.get("text") or raw.get("body")
    if not isinstance(content, str) or not content.strip():
        return None
    tags = raw.get("tags") or raw.get("knowledge_tags") or []
    problem_types = raw.get("problem_types") or raw.get("types") or []
    return KnowledgeHit(
        title=str(raw.get("title") or Path(source).stem),
        content=content.strip(),
        source=str(raw.get("source") or raw.get("file") or source),
        subject=str(raw.get("subject") or ""),
        chapter=str(raw.get("chapter") or ""),
        tags=tuple(str(t) for t in tags if isinstance(t, str)),
        problem_types=tuple(str(t) for t in problem_types if isinstance(t, str)),
    )


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _hit_key(hit: IndexedHit) -> ChunkKey:
    chunk = hit.chunk
    return (chunk.path, int(chunk.start_line), int(chunk.end_line), chunk.kind)


def _rrf_merge(
    bm25_hits: list[IndexedHit],
    dense_hits: list[IndexedHit],
    *,
    limit: int,
    k: int = 60,
) -> list[IndexedHit]:
    scores: dict[ChunkKey, float] = {}
    chunks: dict[ChunkKey, Any] = {}
    reasons: dict[ChunkKey, list[str]] = {}

    for source_reason, hits in (("bm25", bm25_hits), ("dense", dense_hits)):
        for rank, hit in enumerate(hits, 1):
            key = _hit_key(hit)
            chunks[key] = hit.chunk
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            bucket = reasons.setdefault(key, [])
            for reason in [source_reason, *hit.reason]:
                if reason not in bucket:
                    bucket.append(reason)

    merged = [
        IndexedHit(
            chunk=chunks[key],
            score=score,
            reason=["hybrid", *reasons.get(key, [])],
        )
        for key, score in scores.items()
    ]
    merged.sort(key=lambda hit: -hit.score)
    return merged[:limit]


def _qdrant_sync_fingerprint(
    chunks: list[Any],
    *,
    collection: str,
    embedding_model: str,
    dimensions: int,
) -> str:
    rows = [
        [
            chunk.path,
            int(chunk.start_line),
            int(chunk.end_line),
            chunk.kind,
            chunk.content_hash,
        ]
        for chunk in chunks
    ]
    payload = {
        "collection": collection,
        "embedding_model": embedding_model,
        "dimensions": dimensions,
        "chunks": rows,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _index_meta_get(index: Any, key: str) -> str:
    with index._connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else ""


def _index_meta_set(index: Any, key: str, value: str) -> None:
    with index._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            (key, value),
        )


class MathKnowledgeBase:
    """File-backed math knowledge base with RAG indexing and lexical fallback."""

    def __init__(
        self,
        workspace: Path,
        *,
        embedding_config: Any | None = None,
        rerank_config: Any | None = None,
        qdrant_config: Any | None = None,
    ):
        self.workspace = workspace.expanduser().resolve()
        self.base_dir = self.workspace / KNOWLEDGE_DIR
        self.embedding_config = embedding_config
        self.rerank_config = rerank_config
        self.qdrant_config = qdrant_config

    def ensure_dir(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def add_file(self, source_path: Path) -> Path:
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        if source_path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            raise ValueError(
                "Only Markdown, TXT, JSON, JSONL, PDF and image files are supported."
            )
        target_dir = self.ensure_dir()
        target = target_dir / source_path.name
        if target.exists():
            stem = source_path.stem
            suffix = source_path.suffix
            i = 2
            while True:
                candidate = target_dir / f"{stem}-{i}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                i += 1
        shutil.copy2(source_path, target)
        return target

    def list_files(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        return sorted(
            p for p in self.base_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
        )

    def list_text_files(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        return sorted(
            p for p in self.base_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_KNOWLEDGE_SUFFIXES
        )

    def list_index_files(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        markdown_dir = self.base_dir / "_markdown"
        preferred = (
            sorted(
                p for p in markdown_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED_KNOWLEDGE_SUFFIXES
            )
            if markdown_dir.exists() else []
        )
        direct = sorted(
            p for p in self.base_dir.rglob("*")
            if (
                p.is_file()
                and p.suffix.lower() in SUPPORTED_KNOWLEDGE_SUFFIXES
                and "_markdown" not in p.relative_to(self.base_dir).parts
            )
        )
        return preferred + direct

    def _load_hits_from_file(self, path: Path) -> list[KnowledgeHit]:
        rel = path.relative_to(self.workspace).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return []
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return []
            rows = data if isinstance(data, list) else [data]
            return [
                hit for row in rows
                if isinstance(row, dict)
                if (hit := _hit_from_dict(row, rel)) is not None
            ]
        if suffix == ".jsonl":
            hits: list[KnowledgeHit] = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and (hit := _hit_from_dict(row, rel)) is not None:
                    hits.append(hit)
            return hits
        return _split_markdown(text, rel)

    def search(self, query: str, *, limit: int = 4) -> list[KnowledgeHit]:
        terms = _query_terms(query)
        if not terms:
            return []

        scored: list[KnowledgeHit] = []
        for path in self.list_text_files():
            for hit in self._load_hits_from_file(path):
                haystack = "\n".join([
                    hit.title,
                    hit.subject,
                    hit.chapter,
                    " ".join(hit.tags),
                    " ".join(hit.problem_types),
                    hit.content,
                ]).lower()
                score = 0.0
                for term in terms:
                    t = term.lower()
                    if t in haystack:
                        score += 3.0 if t in hit.title.lower() else 1.0
                if score > 0:
                    scored.append(KnowledgeHit(**{**hit.__dict__, "score": score}))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def sync_index(self, *, trace_id: str | None = None) -> None:
        from nanobot.agent.tools._mathrag.math_knowledge_chunker import MATH_SOURCE_TYPE, chunk_math_file
        from nanobot.rag import RAGIndex, create_embedding_client_from_config
        from nanobot.rag.rerank import create_rerank_client_from_config

        t0 = time.perf_counter()
        files = self.list_index_files()
        embedding_client = create_embedding_client_from_config(self.embedding_config)
        rerank_client = create_rerank_client_from_config(self.rerank_config)
        index = RAGIndex(
            self.workspace,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )

        def chunker(path: Path, text: str):
            rel = path.relative_to(self.workspace)
            return chunk_math_file(rel, text)

        index.sync_files(
            source_type=MATH_SOURCE_TYPE,
            files=files,
            chunker=chunker,
            max_file_chars=2_000_000,
        )
        logger.info(
            "✓ MathRAG index sync rag=math trace=%s files=%s chunks=%s elapsed_ms=%s",
            trace_id or "-",
            len(files),
            index.count(MATH_SOURCE_TYPE),
            _elapsed_ms(t0),
        )

    async def async_sync_index(self, *, trace_id: str | None = None) -> None:
        from nanobot.agent.tools._mathrag.math_knowledge_chunker import MATH_SOURCE_TYPE
        from nanobot.rag import RAGIndex, create_embedding_client_from_config
        from nanobot.rag.qdrant_store import (
            QdrantVectorStore,
            chunk_key,
            stable_point_id,
        )

        await asyncio.to_thread(self.sync_index, trace_id=trace_id)
        embedding_client = create_embedding_client_from_config(self.embedding_config)
        if embedding_client is None:
            logger.info(
                "⚠ MathRAG dense fallback rag=math trace=%s reason=embedding_disabled",
                trace_id or "-",
            )
            return

        vector_store = QdrantVectorStore.from_config(
            self.qdrant_config,
            dimensions=getattr(embedding_client, "dimensions", 1024),
        )
        if vector_store is None:
            logger.info(
                "⚠ MathRAG dense fallback rag=math trace=%s reason=qdrant_disabled",
                trace_id or "-",
            )
            return

        t0 = time.perf_counter()
        try:
            index = RAGIndex(self.workspace)
            chunks = index.list_chunks(MATH_SOURCE_TYPE)
            dimensions = getattr(embedding_client, "dimensions", 1024)
            fingerprint = _qdrant_sync_fingerprint(
                chunks,
                collection=vector_store.collection,
                embedding_model=getattr(embedding_client, "model", ""),
                dimensions=dimensions,
            )
            meta_key = f"math_qdrant_sync:{vector_store.collection}:{dimensions}"
            if _index_meta_get(index, meta_key) == fingerprint:
                logger.info(
                    "✓ MathRAG dense sync rag=math trace=%s collection=%s chunks=%s status=current elapsed_ms=%s",
                    trace_id or "-",
                    vector_store.collection,
                    len(chunks),
                    _elapsed_ms(t0),
                )
                return
            vectors = await embedding_client.embed_texts([chunk.text for chunk in chunks])
            upserted = await asyncio.to_thread(
                vector_store.upsert_chunks,
                source_type=MATH_SOURCE_TYPE,
                chunks=chunks,
                vectors=vectors,
            )
            keep_ids = {
                stable_point_id(MATH_SOURCE_TYPE, chunk_key(chunk))
                for chunk in chunks
            }
            pruned = await asyncio.to_thread(
                vector_store.prune_missing,
                source_type=MATH_SOURCE_TYPE,
                keep_point_ids=keep_ids,
            )
            _index_meta_set(index, meta_key, fingerprint)
            logger.info(
                "✓ MathRAG dense sync rag=math trace=%s collection=%s chunks=%s upserted=%s pruned=%s elapsed_ms=%s",
                trace_id or "-",
                vector_store.collection,
                len(chunks),
                upserted,
                pruned,
                _elapsed_ms(t0),
            )
        except Exception as exc:
            logger.warning(
                "⚠ MathRAG dense sync fallback rag=math trace=%s reason=%s elapsed_ms=%s",
                trace_id or "-",
                exc,
                _elapsed_ms(t0),
            )

    async def async_search(self, query: str, *, limit: int = 4) -> list[KnowledgeHit]:
        from nanobot.agent.tools._mathrag.math_knowledge_chunker import MATH_SOURCE_TYPE
        from nanobot.rag import RAGIndex, create_embedding_client_from_config
        from nanobot.rag.qdrant_store import QdrantVectorStore
        from nanobot.rag.rerank import create_rerank_client_from_config

        trace_id = uuid.uuid4().hex[:8]
        total_t0 = time.perf_counter()
        logger.info(
            "🔎 MathRAG start rag=math trace=%s query_chars=%s limit=%s",
            trace_id,
            len(query),
            limit,
        )
        embedding_client = create_embedding_client_from_config(self.embedding_config)
        rerank_client = create_rerank_client_from_config(self.rerank_config)
        index = RAGIndex(
            self.workspace,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )
        try:
            await self.async_sync_index(trace_id=trace_id)
        except Exception as exc:
            logger.warning(
                "⚠ MathRAG index sync skipped rag=math trace=%s reason=%s",
                trace_id,
                exc,
            )

        bm25_t0 = time.perf_counter()
        bm25_hits = index.lexical_search(MATH_SOURCE_TYPE, query, limit=80)
        logger.info(
            "✓ MathRAG bm25 recall rag=math trace=%s hits=%s elapsed_ms=%s",
            trace_id,
            len(bm25_hits),
            _elapsed_ms(bm25_t0),
        )

        dense_hits: list[IndexedHit] = []
        vector_store = QdrantVectorStore.from_config(
            self.qdrant_config,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )
        if vector_store is None:
            logger.info(
                "⚠ MathRAG dense fallback rag=math trace=%s reason=qdrant_disabled",
                trace_id,
            )
        elif embedding_client is None:
            logger.info(
                "⚠ MathRAG dense fallback rag=math trace=%s reason=embedding_disabled",
                trace_id,
            )
        else:
            dense_t0 = time.perf_counter()
            try:
                query_vec = await embedding_client.embed_query(query)
                if query_vec is None:
                    logger.info(
                        "⚠ MathRAG dense fallback rag=math trace=%s reason=query_embedding_failed",
                        trace_id,
                    )
                else:
                    qdrant_hits = await asyncio.to_thread(
                        vector_store.search,
                        source_type=MATH_SOURCE_TYPE,
                        query_vector=query_vec,
                        top_k=80,
                    )
                    chunks_by_key = index._load_chunks_by_keys(
                        {hit.key for hit in qdrant_hits},
                        source_type=MATH_SOURCE_TYPE,
                    )
                    dense_hits = [
                        IndexedHit(
                            chunk=chunks_by_key[hit.key],
                            score=hit.score,
                            reason=["dense"],
                        )
                        for hit in qdrant_hits
                        if hit.key in chunks_by_key
                    ]
                    logger.info(
                        "✓ MathRAG dense recall rag=math trace=%s collection=%s hits=%s elapsed_ms=%s",
                        trace_id,
                        vector_store.collection,
                        len(dense_hits),
                        _elapsed_ms(dense_t0),
                    )
            except Exception as exc:
                logger.warning(
                    "⚠ MathRAG dense fallback rag=math trace=%s reason=%s elapsed_ms=%s",
                    trace_id,
                    exc,
                    _elapsed_ms(dense_t0),
                )

        hybrid_hits = _rrf_merge(bm25_hits, dense_hits, limit=30)
        logger.info(
            "✓ MathRAG hybrid merge rag=math trace=%s bm25=%s dense=%s merged=%s",
            trace_id,
            len(bm25_hits),
            len(dense_hits),
            len(hybrid_hits),
        )

        if rerank_client and hybrid_hits:
            rerank_t0 = time.perf_counter()
            try:
                reranked = await index.rerank(query, hybrid_hits, max(limit * 3, 8))
                hits = [
                    IndexedHit(
                        chunk=hit.chunk,
                        score=hit.score,
                        reason=list(dict.fromkeys([*hit.reason, "rerank"])),
                    )
                    for hit in reranked
                ]
                logger.info(
                    "✓ MathRAG rerank rag=math trace=%s model=%s input=%s output=%s elapsed_ms=%s",
                    trace_id,
                    getattr(rerank_client, "model", "unknown"),
                    len(hybrid_hits),
                    len(hits),
                    _elapsed_ms(rerank_t0),
                )
            except Exception as exc:
                hits = hybrid_hits[: max(limit * 3, 8)]
                logger.warning(
                    "⚠ MathRAG rerank fallback rag=math trace=%s reason=%s elapsed_ms=%s",
                    trace_id,
                    exc,
                    _elapsed_ms(rerank_t0),
                )
        else:
            hits = hybrid_hits[: max(limit * 3, 8)]
            logger.info(
                "⚠ MathRAG rerank fallback rag=math trace=%s reason=rerank_disabled input=%s",
                trace_id,
                len(hybrid_hits),
            )

        if not hits:
            logger.info(
                "⚠ MathRAG lexical file fallback rag=math trace=%s reason=no_index_hits",
                trace_id,
            )
            fallback_hits = self.search(query, limit=limit)
            logger.info(
                "✓ MathRAG done rag=math trace=%s final_hits=%s elapsed_ms=%s",
                trace_id,
                len(fallback_hits),
                _elapsed_ms(total_t0),
            )
            return fallback_hits

        expand_t0 = time.perf_counter()
        expanded = self._expand_example_hits(hits)
        final_hits = expanded[:limit]
        logger.info(
            "✓ MathRAG example expand rag=math trace=%s input=%s output=%s elapsed_ms=%s",
            trace_id,
            len(hits),
            len(expanded),
            _elapsed_ms(expand_t0),
        )
        logger.info(
            "✓ MathRAG done rag=math trace=%s final_hits=%s elapsed_ms=%s",
            trace_id,
            len(final_hits),
            _elapsed_ms(total_t0),
        )
        for i, hit in enumerate(final_hits, 1):
            logger.debug(
                "MathRAG hit rag=math trace=%s rank=%s score=%.4f source=%s title=%s",
                trace_id,
                i,
                hit.score,
                hit.source,
                hit.title,
            )
        return final_hits

    def _expand_example_hits(self, hits: list[Any]) -> list[KnowledgeHit]:
        converted: list[KnowledgeHit] = []
        seen: set[tuple[str, int, int, str]] = set()
        for hit in hits:
            chunk = hit.chunk
            key = (chunk.path, chunk.start_line, chunk.end_line, chunk.kind)
            if key in seen:
                continue
            seen.add(key)
            converted.append(_knowledge_hit_from_indexed_hit(hit))

            example_id = _symbol_value(chunk.symbols, "example_id:")
            if example_id:
                for sibling in self._load_example_siblings(example_id, exclude=key):
                    sibling_key = (
                        sibling.chunk.path,
                        sibling.chunk.start_line,
                        sibling.chunk.end_line,
                        sibling.chunk.kind,
                    )
                    if sibling_key in seen:
                        continue
                    seen.add(sibling_key)
                    converted.append(_knowledge_hit_from_indexed_hit(sibling, score=hit.score * 0.92))
        converted.sort(key=lambda item: item.score, reverse=True)
        return converted

    def _load_example_siblings(self, example_id: str, *, exclude: tuple[str, int, int, str]) -> list[Any]:
        from nanobot.agent.tools._mathrag.math_knowledge_chunker import MATH_SOURCE_TYPE
        from nanobot.rag import RAGIndex
        from nanobot.rag.utils import IndexedHit, chunk_from_row

        index = RAGIndex(self.workspace)
        siblings: list[IndexedHit] = []
        with index._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_type, path, start_line, end_line, kind, text, symbols,
                       title, url, query, fetched_at, mtime, content_hash
                FROM chunks
                WHERE source_type = ? AND symbols LIKE ?
                ORDER BY
                  CASE kind
                    WHEN 'example_question' THEN 1
                    WHEN 'example_solution' THEN 2
                    WHEN 'example_answer' THEN 3
                    ELSE 4
                  END
                """,
                (MATH_SOURCE_TYPE, f"%example_id:{example_id}%"),
            ).fetchall()
        for row in rows:
            chunk = chunk_from_row(row)
            key = (chunk.path, chunk.start_line, chunk.end_line, chunk.kind)
            if key == exclude:
                continue
            siblings.append(IndexedHit(chunk=chunk, score=0.0, reason=["example_sibling"]))
        return siblings


def format_knowledge_context(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return "知识库中未检索到相关内容。"
    blocks: list[str] = []
    for i, hit in enumerate(hits, 1):
        tags = f"\n- 标签：{', '.join(hit.tags)}" if hit.tags else ""
        problem_types = f"\n- 适用题型：{', '.join(hit.problem_types)}" if hit.problem_types else ""
        content = hit.content.strip()
        if len(content) > 1200:
            content = content[:1200].rstrip() + "..."
        blocks.append(
            f"[{i}] {hit.title}\n"
            f"- 来源：{hit.citation()}\n"
            f"- 科目：{hit.subject or '未标注'}"
            f"{tags}{problem_types}\n"
            f"- 内容：{content}"
        )
    return "\n\n".join(blocks)


def _symbol_value(symbols: list[str], prefix: str) -> str:
    for symbol in symbols:
        if symbol.startswith(prefix):
            return symbol[len(prefix):]
    return ""


def _knowledge_hit_from_indexed_hit(hit: Any, *, score: float | None = None) -> KnowledgeHit:
    chunk = hit.chunk
    chapter = _symbol_value(chunk.symbols, "chapter:")
    block_type = _symbol_value(chunk.symbols, "block_type:") or chunk.kind
    tags = tuple(
        symbol.removeprefix("tag:")
        for symbol in chunk.symbols
        if symbol.startswith("tag:")
    )
    problem_types = (block_type,)
    return KnowledgeHit(
        title=chunk.title or Path(chunk.path).stem,
        content=chunk.text,
        source=f"{chunk.path}:{chunk.start_line}",
        subject="数学",
        chapter=chapter,
        tags=tags,
        problem_types=problem_types,
        score=float(hit.score if score is None else score),
    )


def build_math_qa_prompt(knowledge_hits: list[KnowledgeHit]) -> str:
    knowledge_context = format_knowledge_context(knowledge_hits)
    return f"""你正在以“数学考研 AI 助手”的答疑模式工作，面向考研数学一、数学二、数学三。

回答要求：
- 围绕题目给出清晰、可靠的分步骤解析，不能只给最终答案。
- 必须说明关键步骤为什么成立，尤其是变形、定理使用、公式代入和条件检查。
- 需要标注涉及的知识点、公式或题型，并给出明确最终答案。
- 追问时要结合当前会话上下文回答，不要把追问当作全新题目。
- 如果题目不完整、图片不清晰或条件缺失，先指出缺失信息，并引导用户补充。
- 如果用户要求“直接给答案”，仍然至少给出必要推导过程。
- 对复杂题目在最终答案前做一次自检；不确定时明确说明不确定点。
- 绝对不能编造知识库引用来源。只有下面“本地知识库检索结果”中出现的来源才可以作为知识库引用。
- 如果本地知识库没有可靠内容，回答中必须明确写出：“知识库中未检索到相关内容”。
- 可以在必要时使用网页检索工具辅助，但必须把网页来源和本地知识库来源分开说明。

推荐回答结构：
1. 题目识别
2. 解题思路
3. 分步骤推导
4. 最终答案
5. 涉及知识点
6. 易错提醒
7. 知识库引用

本地知识库检索结果：
{knowledge_context}
"""


async def resolve_math_qa_context(
    initial_messages: list[dict[str, Any]],
    session_meta: dict[str, Any],
    *,
    workspace: Path,
    embedding_config: Any | None = None,
    rerank_config: Any | None = None,
    qdrant_config: Any | None = None,
) -> str | None:
    """Build the math QA system prompt with best-effort local KB retrieval."""
    if session_meta.get("math_qa_prompt"):
        return session_meta["math_qa_prompt"]

    user_content = ""
    for message in reversed(initial_messages):
        if message.get("role") == "user":
            user_content = _text_from_message_content(message.get("content"))
            break

    kb = MathKnowledgeBase(
        workspace,
        embedding_config=embedding_config,
        rerank_config=rerank_config,
        qdrant_config=qdrant_config,
    )
    hits = await kb.async_search(user_content, limit=4)
    return build_math_qa_prompt(hits)


def extract_last_user_and_answer(session: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    messages = list(getattr(session, "messages", []) or [])
    assistant: dict[str, Any] | None = None
    user: dict[str, Any] | None = None
    for message in reversed(messages):
        role = message.get("role")
        if assistant is None and role == "assistant" and _text_from_message_content(message.get("content")).strip():
            assistant = message
            continue
        if assistant is not None and role == "user":
            user = message
            break
    return user, assistant


def _extract_knowledge_tags(answer: str) -> list[str]:
    marker = "涉及知识点"
    idx = answer.find(marker)
    if idx < 0:
        marker = "知识点"
        idx = answer.find(marker)
    if idx < 0:
        return []
    snippet = answer[idx : idx + 500]
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_（）()·]{2,20}", snippet)
    stop = {"涉及知识点", "知识点", "最终答案", "易错提醒", "知识库引用"}
    tags: list[str] = []
    for item in candidates:
        item = item.strip("：:，,。.；;、- ")
        if item and item not in stop and item not in tags:
            tags.append(item)
        if len(tags) >= 8:
            break
    return tags


def append_mistake_record(
    workspace: Path,
    session: Any,
    *,
    error_reason: str = "",
    mastery_status: str = "未复习",
) -> dict[str, Any]:
    user, assistant = extract_last_user_and_answer(session)
    if not user or not assistant:
        raise ValueError("No completed question-answer turn found in this session.")

    question = _text_from_message_content(user.get("content")).strip()
    answer = _text_from_message_content(assistant.get("content")).strip()
    if not question or not answer:
        raise ValueError("The latest question or answer is empty.")

    now = datetime.now().isoformat()
    record = {
        "question": question,
        "original_prompt": question,
        "ai_answer": answer,
        "knowledge_tags": _extract_knowledge_tags(answer),
        "error_reason": error_reason,
        "mastery_status": mastery_status,
        "created_at": now,
        "last_reviewed_at": None,
        "session_key": getattr(session, "key", ""),
    }
    path = workspace / MISTAKE_BOOK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
