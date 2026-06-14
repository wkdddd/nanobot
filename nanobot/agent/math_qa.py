"""Math exam question-answering helpers."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

MATH_QA_MODE_KEY = "math_qa_mode"
KNOWLEDGE_DIR = ".nanobot/math_knowledge"
MISTAKE_BOOK_PATH = ".nanobot/math_mistakes.jsonl"
SUPPORTED_KNOWLEDGE_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl"}


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


class MathKnowledgeBase:
    """Small file-backed knowledge base for the MVP math QA mode."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()
        self.base_dir = self.workspace / KNOWLEDGE_DIR

    def ensure_dir(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def add_file(self, source_path: Path) -> Path:
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        if source_path.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
            raise ValueError(
                "Only Markdown, TXT, JSON and JSONL files are supported for the MVP knowledge base."
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
            if p.is_file() and p.suffix.lower() in SUPPORTED_KNOWLEDGE_SUFFIXES
        )

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
        for path in self.list_files():
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
