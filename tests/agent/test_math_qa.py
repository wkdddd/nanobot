import json

from nanobot.agent.math_qa import (
    MISTAKE_BOOK_PATH,
    MathKnowledgeBase,
    append_mistake_record,
    build_math_qa_prompt,
)
from nanobot.session.manager import Session


def test_math_qa_prompt_mentions_empty_knowledge_base() -> None:
    prompt = build_math_qa_prompt([])

    assert "数学考研 AI 助手" in prompt
    assert "知识库中未检索到相关内容" in prompt
    assert "不能只给最终答案" in prompt


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
