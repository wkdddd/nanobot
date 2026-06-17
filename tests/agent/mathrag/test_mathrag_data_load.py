import json

from nanobot.agent.tools._mathrag.math_knowledge_convert import MathKnowledgeMarkdownConverter


def test_converter_copies_utf8_markdown_to_output_dir(tmp_path) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    source = kb_dir / "limit.md"
    source.write_text("# 极限\n\n重要极限 $\\lim_{x\\to0}\\sin x/x=1$", encoding="utf-8")

    result = MathKnowledgeMarkdownConverter(tmp_path).convert_file(source)

    assert result.ok
    assert result.markdown_path == kb_dir / "_markdown" / "limit.md"
    assert "重要极限" in result.markdown
    assert result.markdown_path.read_text(encoding="utf-8").startswith("# 极限")


def test_converter_turns_jsonl_entries_into_markdown(tmp_path) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    source = kb_dir / "rules.jsonl"
    source.write_text(
        json.dumps(
            {
                "title": "洛必达法则",
                "subject": "高等数学",
                "chapter": "极限",
                "tags": ["极限", "洛必达"],
                "content": "适用于 $0/0$ 或 $\\infty/\\infty$ 型。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = MathKnowledgeMarkdownConverter(tmp_path).convert_file(source)

    assert result.ok
    assert "## 洛必达法则" in result.markdown
    assert "- 科目：高等数学" in result.markdown
    assert "- 标签：极限, 洛必达" in result.markdown
    assert "$0/0$" in result.markdown


def test_converter_reports_non_utf8_text(tmp_path) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    source = kb_dir / "bad.txt"
    source.write_bytes("极限".encode("gbk"))

    result = MathKnowledgeMarkdownConverter(tmp_path).convert_file(source)

    assert not result.ok
    assert "not UTF-8 encoded" in result.markdown
    assert result.warnings
