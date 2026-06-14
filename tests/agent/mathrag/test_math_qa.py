import json
import logging
import pytest

from nanobot.agent.math_qa import (
    MISTAKE_BOOK_PATH,
    MathKnowledgeBase,
    _rrf_merge,
    append_mistake_record,
    build_math_qa_prompt,
    resolve_math_qa_context,
)
from nanobot.agent.rag.utils import IndexedChunk, IndexedHit
from nanobot.session.manager import Session


def test_math_qa_prompt_mentions_empty_knowledge_base() -> None:
    prompt = build_math_qa_prompt([])

    assert "数学考研 AI 助手" in prompt
    assert "知识库中未检索到相关内容" in prompt
    assert "不能只给最终答案" in prompt


@pytest.mark.asyncio
async def test_resolve_math_qa_context_prefers_prebuilt_prompt(tmp_path) -> None:
    prompt = await resolve_math_qa_context(
        [{"role": "user", "content": "求极限"}],
        {"math_qa_prompt": "prebuilt math prompt"},
        workspace=tmp_path,
    )

    assert prompt == "prebuilt math prompt"


@pytest.mark.asyncio
async def test_resolve_math_qa_context_extracts_text_blocks(tmp_path) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    (kb_dir / "calculus.md").write_text(
        "# 洛必达法则\n\n适用于 0/0 或无穷/无穷型极限，需要先验证条件。",
        encoding="utf-8",
    )

    prompt = await resolve_math_qa_context(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这个极限能不能用洛必达法则？"},
                    {"type": "image_url", "image_url": {"url": "file://x.png"}},
                ],
            }
        ],
        {},
        workspace=tmp_path,
    )

    assert prompt is not None
    assert "洛必达法则" in prompt


def test_math_knowledge_base_searches_utf8_markdown(tmp_path) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    (kb_dir / "calculus.md").write_text(
        "# 洛必达法则\n\n适用于 0/0 或无穷/无穷型极限，需要先验证条件。",
        encoding="utf-8",
    )

    hits = MathKnowledgeBase(tmp_path).search("这个极限能不能用洛必达法则？")

    assert hits
    assert hits[0].title == "洛必达法则"
    assert "calculus.md" in hits[0].source


def test_append_mistake_record_writes_jsonl_utf8(tmp_path) -> None:
    session = Session(key="websocket:test")
    session.add_message("user", "求极限 lim x->0 sin x / x")
    session.add_message(
        "assistant",
        "## 涉及知识点\n- 重要极限\n\n## 最终答案\n1",
    )

    record = append_mistake_record(tmp_path, session, error_reason="公式不熟")
    path = tmp_path / MISTAKE_BOOK_PATH
    saved = json.loads(path.read_text(encoding="utf-8").strip())

    assert record["error_reason"] == "公式不熟"
    assert saved["question"] == "求极限 lim x->0 sin x / x"
    assert saved["ai_answer"].endswith("1")
    assert saved["mastery_status"] == "未复习"


@pytest.mark.asyncio
async def test_math_knowledge_base_async_search_uses_markdown_rag(tmp_path, caplog) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge" / "_markdown"
    kb_dir.mkdir(parents=True)
    (kb_dir / "examples.md").write_text(
        "# 极限\n\n例1 求 $\\lim_{x\\to0}\\frac{\\sin x}{x}$。\n\n解：重要极限。\n\n答案：$1$",
        encoding="utf-8",
    )

    caplog.set_level(logging.INFO, logger="nanobot.agent.math_qa")
    hits = await MathKnowledgeBase(tmp_path).async_search("sin x / x 的答案", limit=3)

    assert hits
    assert any("答案" in hit.content for hit in hits)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "🔎 MathRAG start" in log_text
    assert "✓ MathRAG index sync" in log_text
    assert "✓ MathRAG bm25 recall" in log_text
    assert "⚠ MathRAG dense fallback" in log_text
    assert "✓ MathRAG hybrid merge" in log_text
    assert "⚠ MathRAG rerank fallback" in log_text
    assert "✓ MathRAG example expand" in log_text
    assert "✓ MathRAG done" in log_text


def test_rrf_merge_combines_bm25_and_dense_reasons() -> None:
    shared = IndexedChunk(
        source_type="math",
        path="lesson.md",
        start_line=1,
        end_line=3,
        kind="note",
        text="重要极限",
    )
    dense_only = IndexedChunk(
        source_type="math",
        path="lesson.md",
        start_line=4,
        end_line=6,
        kind="formula",
        text="sin x / x",
    )

    merged = _rrf_merge(
        [IndexedHit(chunk=shared, score=9.0, reason=["bm25"])],
        [
            IndexedHit(chunk=shared, score=0.9, reason=["dense"]),
            IndexedHit(chunk=dense_only, score=0.8, reason=["dense"]),
        ],
        limit=5,
    )

    assert merged[0].chunk is shared
    assert "bm25" in merged[0].reason
    assert "dense" in merged[0].reason
    assert any(hit.chunk is dense_only for hit in merged)
