"""Math-oriented Markdown chunking for MathQA RAG."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanobot.rag.utils import IndexedChunk

MATH_SOURCE_TYPE = "math"

_EXAMPLE_START_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?(?:【?例(?:题)?\s*[\d一二三四五六七八九十]*】?|Example\s*\d*)")
_EXERCISE_START_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?(?:习题|练习|Exercise)\s*[\d一二三四五六七八九十.]*")
_SOLUTION_RE = re.compile(r"(?m)^\s*(?:解|解析|证明|Solution)\s*[:：]")
_ANSWER_RE = re.compile(r"(?m)^\s*(?:答案|答|最终答案|故选)\s*[:：]?")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_FORMULA_BLOCK_RE = re.compile(r"(?s)\$\$.*?\$\$|\\\[.*?\\\]")
_INLINE_FORMULA_RE = re.compile(r"(?s)\$[^$\n]+\$|\\\(.*?\\\)")

_MATH_SYMBOL_EXPANSIONS = {
    r"\lim": "极限",
    r"\int": "积分",
    r"\sum": "求和 级数",
    r"\prod": "连乘",
    r"\frac": "分式 分数 比值",
    r"\sqrt": "根号 平方根",
    r"\infty": "无穷",
    r"\partial": "偏导",
    r"\nabla": "梯度",
    r"\sin": "正弦 sin",
    r"\cos": "余弦 cos",
    r"\tan": "正切 tan",
    r"\ln": "对数 ln",
    r"\log": "对数 log",
    r"\det": "行列式",
    r"\mathbb": "数集",
    "0/0": "零比零 未定式",
    "∞/∞": "无穷比无穷 未定式",
}


@dataclass(slots=True)
class MathChunk:
    id: str
    source: str
    path: str
    start_line: int
    end_line: int
    block_type: str
    title: str
    text: str
    chapter: str = ""
    section: str = ""
    page: int | None = None
    latex: list[str] = field(default_factory=list)
    search_text: str = ""
    parent_id: str = ""
    example_id: str = ""
    tags: list[str] = field(default_factory=list)

    def to_indexed_chunk(self) -> IndexedChunk:
        symbols = [
            f"block_type:{self.block_type}",
            *([f"chapter:{self.chapter}"] if self.chapter else []),
            *([f"section:{self.section}"] if self.section else []),
            *([f"parent_id:{self.parent_id}"] if self.parent_id else []),
            *([f"example_id:{self.example_id}"] if self.example_id else []),
            *(f"tag:{tag}" for tag in self.tags),
        ]
        text = _chunk_index_text(self)
        return IndexedChunk(
            source_type=MATH_SOURCE_TYPE,
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            kind=self.block_type,
            text=text,
            symbols=symbols,
            title=self.title,
        )


def chunk_math_file(path: Path, text: str, *, source_type: str = MATH_SOURCE_TYPE) -> list[IndexedChunk]:
    """Chunk one math knowledge file into RAGIndex chunks."""
    rel_path = path.as_posix()
    chunks = build_math_chunks(path, text)
    indexed = [chunk.to_indexed_chunk() for chunk in chunks]
    for chunk in indexed:
        chunk.source_type = source_type
        chunk.path = rel_path
    return indexed


def build_math_chunks(path: Path, text: str) -> list[MathChunk]:
    rel_path = path.as_posix()
    if path.suffix.lower() in {".json", ".jsonl"}:
        text = _structured_to_markdown(path, text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    sections = _markdown_sections(lines, fallback_title=path.stem)
    chunks: list[MathChunk] = []

    for section in sections:
        chunks.extend(_chunk_section(rel_path, section))

    return chunks or [
        _make_chunk(
            rel_path,
            1,
            max(1, len(lines)),
            "note",
            path.stem,
            text,
            chapter="",
            section="",
        )
    ]

@dataclass(slots=True)
class _Section:
    title: str
    heading_level: int
    start_line: int
    end_line: int
    text: str
    chapter: str = ""
    section: str = ""


def _markdown_sections(lines: list[str], *, fallback_title: str) -> list[_Section]:
    headings: list[tuple[int, int, str]] = []
    stack: dict[int, str] = {}
    for idx, line in enumerate(lines, 1):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        headings.append((idx, level, title))
        stack[level] = title
        for old in [k for k in stack if k > level]:
            stack.pop(old, None)

    if not headings:
        return [_Section(fallback_title, 1, 1, max(1, len(lines)), "\n".join(lines))]

    sections: list[_Section] = []
    chapter = ""
    current_stack: dict[int, str] = {}
    for i, (line_no, level, title) in enumerate(headings):
        end = headings[i + 1][0] - 1 if i + 1 < len(headings) else len(lines)
        current_stack[level] = title
        for old in [k for k in current_stack if k > level]:
            current_stack.pop(old, None)
        if level <= 2:
            chapter = title
        section = title if level > 2 else ""
        body = "\n".join(lines[line_no - 1:end])
        sections.append(_Section(title, level, line_no, max(line_no, end), body, chapter, section))
    return sections


def _chunk_section(path: str, section: _Section) -> list[MathChunk]:
    chunks: list[MathChunk] = []
    consumed: list[tuple[int, int]] = []
    text = section.text

    for start, end in _find_spans(text, [_EXAMPLE_START_RE, _EXERCISE_START_RE]):
        span = text[start:end].strip()
        if not span:
            continue
        start_line = section.start_line + text[:start].count("\n")
        end_line = section.start_line + text[:end].count("\n")
        if _EXAMPLE_START_RE.match(span):
            chunks.extend(_chunk_example(path, span, start_line, end_line, section))
        else:
            chunks.extend(_split_oversize(_make_chunk(
                path, start_line, end_line, "exercise", _first_line_title(span, "习题"),
                span, chapter=section.chapter, section=section.section,
            )))
        consumed.append((start, end))

    for start, end, block in _remaining_blocks(text, consumed):
        block = block.strip()
        if not block:
            continue
        if _is_heading_only(block):
            continue
        start_line = section.start_line + text[:start].count("\n")
        end_line = section.start_line + text[:end].count("\n")
        block_type = _classify_block(block)
        title = _first_line_title(block, section.title)
        chunks.extend(_split_oversize(_make_chunk(
            path,
            start_line,
            end_line,
            block_type,
            title,
            block,
            chapter=section.chapter,
            section=section.section,
        )))

    return chunks


def _find_spans(text: str, patterns: list[re.Pattern[str]]) -> list[tuple[int, int]]:
    starts = sorted(
        {match.start() for pattern in patterns for match in pattern.finditer(text)}
    )
    spans: list[tuple[int, int]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        spans.append((start, end))
    return spans


def _remaining_blocks(text: str, consumed: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    pos = 0
    for start, end in sorted(consumed):
        if pos < start:
            blocks.extend(_paragraph_blocks(text, pos, start))
        pos = max(pos, end)
    if pos < len(text):
        blocks.extend(_paragraph_blocks(text, pos, len(text)))
    return blocks


def _paragraph_blocks(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    part = text[start:end]
    blocks: list[tuple[int, int, str]] = []
    offset = start
    pieces = re.split(r"\n\s*\n", part)
    for piece in pieces:
        local = part.find(piece, offset - start)
        if local < 0:
            continue
        p_start = start + local
        p_end = p_start + len(piece)
        if piece.strip():
            blocks.append((p_start, p_end, piece))
        offset = p_end
    return blocks


def _chunk_example(
    path: str, span: str, start_line: int, end_line: int, section: _Section
) -> list[MathChunk]:
    title = _first_line_title(span, "例题")
    example_id = _stable_id(path, start_line, title)
    solution_match = _SOLUTION_RE.search(span)
    answer_match = _ANSWER_RE.search(span)

    q_end = min(
        [m.start() for m in [solution_match, answer_match] if m is not None] or [len(span)]
    )
    question = span[:q_end].strip()
    solution = ""
    answer = ""
    if solution_match:
        s_start = solution_match.start()
        s_end = answer_match.start() if answer_match and answer_match.start() > s_start else len(span)
        solution = span[s_start:s_end].strip()
    if answer_match:
        answer = span[answer_match.start():].strip()

    chunks = [
        _make_chunk(
            path, start_line, end_line, "example_question", title, question or span,
            chapter=section.chapter, section=section.section, example_id=example_id,
        )
    ]
    if solution:
        solution_text = f"题干摘要：\n{_shorten(question)}\n\n{solution}"
        chunks.extend(_split_oversize(_make_chunk(
            path, start_line, end_line, "example_solution", f"{title} 解析", solution_text,
            chapter=section.chapter, section=section.section, example_id=example_id,
            parent_id=example_id,
        )))
    if answer:
        answer_text = f"题干摘要：\n{_shorten(question)}\n\n{answer}"
        chunks.append(_make_chunk(
            path, start_line, end_line, "example_answer", f"{title} 答案", answer_text,
            chapter=section.chapter, section=section.section, example_id=example_id,
            parent_id=example_id,
        ))
    return chunks


def _split_oversize(chunk: MathChunk, *, max_chars: int = 4500) -> list[MathChunk]:
    if len(chunk.text) <= max_chars or chunk.block_type in {"formula", "example_question", "example_answer"}:
        return [chunk]
    parts = _split_with_chonkie(chunk.text, max_chars=max_chars)
    if len(parts) <= 1:
        return [chunk]
    result: list[MathChunk] = []
    for i, part in enumerate(parts, 1):
        title = f"{chunk.title} ({i}/{len(parts)})"
        text = part
        if chunk.block_type == "example_solution" and "题干摘要：" not in part:
            text = f"题干摘要：\n{_extract_question_summary(chunk.text)}\n\n步骤 {i}：\n{part}"
        result.append(_make_chunk(
            chunk.path,
            chunk.start_line,
            chunk.end_line,
            chunk.block_type,
            title,
            text,
            chapter=chunk.chapter,
            section=chunk.section,
            parent_id=chunk.parent_id,
            example_id=chunk.example_id,
            tags=chunk.tags,
        ))
    return result


def _split_with_chonkie(text: str, *, max_chars: int) -> list[str]:
    try:
        from chonkie import RecursiveChunker

        chunker = RecursiveChunker.from_recipe("markdown", chunk_size=max_chars, chunk_overlap=400)
        chunks = chunker.chunk(text)
        parts = [_chunk_text(item) for item in chunks]
        return [p for p in parts if p.strip()]
    except Exception:
        return _fallback_split(text, max_chars=max_chars, overlap=400)


def _fallback_split(text: str, *, max_chars: int, overlap: int) -> list[str]:
    protected = _protect_formulas(text)
    paragraphs = re.split(r"\n\s*\n", protected)
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        restored = _restore_formulas(paragraph)
        if len(current) + len(restored) + 2 <= max_chars:
            current = f"{current}\n\n{restored}".strip()
            continue
        if current:
            parts.append(current)
        current = restored
    if current:
        parts.append(current)
    if len(parts) <= 1:
        return parts
    with_overlap: list[str] = []
    for i, part in enumerate(parts):
        if i == 0:
            with_overlap.append(part)
        else:
            prefix = parts[i - 1][-overlap:]
            with_overlap.append(f"{prefix}\n\n{part}")
    return with_overlap


def _chunk_text(item: Any) -> str:
    for attr in ("text", "content"):
        value = getattr(item, attr, None)
        if isinstance(value, str):
            return value
    return str(item)


def _protect_formulas(text: str) -> str:
    formulas: list[str] = []

    def repl(match: re.Match[str]) -> str:
        formulas.append(match.group(0))
        return f"@@FORMULA_{len(formulas) - 1}@@"

    protected = _FORMULA_BLOCK_RE.sub(repl, text)
    protected = _INLINE_FORMULA_RE.sub(repl, protected)
    return protected + "\n<!--FORMULAS " + json.dumps(formulas, ensure_ascii=False) + "-->"


def _restore_formulas(text: str) -> str:
    marker = "<!--FORMULAS "
    if marker not in text:
        return text
    body, raw = text.split(marker, 1)
    try:
        formulas = json.loads(raw.removesuffix("-->").strip())
    except json.JSONDecodeError:
        return body
    for i, formula in enumerate(formulas):
        body = body.replace(f"@@FORMULA_{i}@@", formula)
    return body


def _classify_block(text: str) -> str:
    stripped = text.strip()
    if _FORMULA_BLOCK_RE.fullmatch(stripped) or stripped.startswith(("公式", "恒等式")):
        return "formula"
    if re.match(r"^\s*(?:定义|Definition)\s*[\d一二三四五六七八九十.]*", stripped):
        return "definition"
    if re.match(r"^\s*(?:定理|性质|推论|Theorem|Lemma)\s*[\d一二三四五六七八九十.]*", stripped):
        return "theorem"
    return "note"


def _is_heading_only(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) == 1 and bool(_HEADING_RE.match(lines[0]))


def _make_chunk(
    path: str,
    start_line: int,
    end_line: int,
    block_type: str,
    title: str,
    text: str,
    *,
    chapter: str,
    section: str,
    page: int | None = None,
    parent_id: str = "",
    example_id: str = "",
    tags: list[str] | None = None,
) -> MathChunk:
    latex = _extract_latex(text)
    search_text = _math_search_text(text, latex)
    chunk_id = _stable_id(path, start_line, block_type, title, text[:80])
    return MathChunk(
        id=chunk_id,
        source=path,
        path=path,
        start_line=start_line,
        end_line=max(start_line, end_line),
        block_type=block_type,
        title=title,
        text=text.strip(),
        chapter=chapter,
        section=section,
        page=page,
        latex=latex,
        search_text=search_text,
        parent_id=parent_id,
        example_id=example_id,
        tags=tags or _infer_tags(text),
    )


def _chunk_index_text(chunk: MathChunk) -> str:
    meta = [
        f"类型：{chunk.block_type}",
        f"章节：{chunk.chapter}" if chunk.chapter else "",
        f"小节：{chunk.section}" if chunk.section else "",
        f"例题ID：{chunk.example_id}" if chunk.example_id else "",
        f"标签：{', '.join(chunk.tags)}" if chunk.tags else "",
    ]
    latex = "\n".join(chunk.latex)
    parts = [chunk.title, "\n".join(p for p in meta if p), chunk.text]
    if latex:
        parts.append(f"公式原文：\n{latex}")
    if chunk.search_text:
        parts.append(f"检索词：{chunk.search_text}")
    return "\n\n".join(p for p in parts if p.strip())


def _extract_latex(text: str) -> list[str]:
    found = [m.group(0) for m in _FORMULA_BLOCK_RE.finditer(text)]
    found.extend(m.group(0) for m in _INLINE_FORMULA_RE.finditer(text))
    return list(dict.fromkeys(found))


def _math_search_text(text: str, formulas: list[str]) -> str:
    haystack = "\n".join([text, *formulas])
    expansions = [
        expansion for needle, expansion in _MATH_SYMBOL_EXPANSIONS.items()
        if needle in haystack
    ]
    return " ".join(dict.fromkeys(" ".join(expansions).split()))


def _infer_tags(text: str) -> list[str]:
    candidates = [
        "极限", "导数", "微分", "积分", "级数", "矩阵", "行列式", "特征值", "特征向量",
        "概率", "随机变量", "期望", "方差", "泰勒", "洛必达", "中值定理", "二重积分",
    ]
    return [word for word in candidates if word in text][:8]


def _first_line_title(text: str, fallback: str) -> str:
    first = next((line.strip("# 　\t") for line in text.splitlines() if line.strip()), "")
    return _shorten(first or fallback, limit=80)


def _shorten(text: str, *, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized if len(normalized) <= limit else normalized[:limit].rstrip() + "..."


def _extract_question_summary(text: str) -> str:
    if "题干摘要：" in text:
        return text.split("题干摘要：", 1)[1].split("\n\n", 1)[0].strip()
    return _shorten(text)


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _structured_to_markdown(path: Path, raw: str) -> str:
    rows: list[Any] = []
    if path.suffix.lower() == ".jsonl":
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        rows = data if isinstance(data, list) else [data]
    blocks = [f"# {path.stem}"]
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("question") or f"条目 {i}")
        content = row.get("content") or row.get("text") or row.get("body") or ""
        blocks.append(f"## {title}\n\n{content}")
    return "\n\n".join(blocks)
