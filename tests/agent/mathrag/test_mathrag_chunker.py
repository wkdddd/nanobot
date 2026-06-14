import pytest

from nanobot.agent.mathrag.chunker import build_math_chunks


def test_math_chunker_keeps_example_answer_linked(tmp_path) -> None:
    source = tmp_path / "lesson.md"
    text = """# 极限

## 重要极限

例1 求 $\\lim_{x\\to0}\\frac{\\sin x}{x}$。

解：由重要极限可知，原式等于 1。

答案：$1$
"""

    chunks = build_math_chunks(source, text)

    question = next(c for c in chunks if c.block_type == "example_question")
    solution = next(c for c in chunks if c.block_type == "example_solution")
    answer = next(c for c in chunks if c.block_type == "example_answer")
    assert question.example_id
    assert solution.example_id == question.example_id
    assert answer.example_id == question.example_id
    assert "题干摘要" in solution.text
    assert "答案" in answer.text


def test_math_chunker_preserves_formula_and_search_text(tmp_path) -> None:
    source = tmp_path / "formula.md"
    text = """# 微积分

公式：
$$
\\int_a^b f(x) dx
$$
"""

    chunks = build_math_chunks(source, text)

    formula_chunk = next(c for c in chunks if c.latex)
    assert "$$" in formula_chunk.latex[0]
    assert "积分" in formula_chunk.search_text



@pytest.mark.parametrize("heading", ["定义1", "定理 2", "性质"])
def test_math_chunker_classifies_common_blocks(tmp_path, heading: str) -> None:
    source = tmp_path / "blocks.md"
    text = f"# 线代\n\n{heading} 若矩阵 A 可逆，则 $|A|\\ne0$。"

    chunks = build_math_chunks(source, text)

    assert chunks[0].block_type in {"definition", "theorem"}
