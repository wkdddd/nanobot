from __future__ import annotations

import pytest

from nanobot.rag.index import RAGIndex
from nanobot.rag.utils import IndexedChunk, IndexedHit


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
