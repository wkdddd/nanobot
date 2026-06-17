"""MathRAG tool: search, convert, and evaluate the local math knowledge base."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools._mathrag.math_knowledge_convert import MathKnowledgeMarkdownConverter
from nanobot.agent.tools._mathrag.math_knowledge_eval import (
    evaluate_samples,
    load_eval_dataset,
    render_markdown_report,
    write_markdown_report,
)
from nanobot.agent.math_qa import MathKnowledgeBase
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.path_utils import resolve_workspace_path
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema


def _clamp_int(value: int | None, default: int, low: int, high: int) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(low, min(high, n))


def _rel(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "MathRAG action: search local math knowledge, convert knowledge files to Markdown, or evaluate a JSONL dataset.",
            enum=("search", "convert", "evaluate"),
        ),
        query=StringSchema(
            "Math question or keywords to search for. Required for action='search'.",
            nullable=True,
        ),
        top_k=IntegerSchema(
            4,
            description="Maximum search hits to return.",
            minimum=1,
            maximum=10,
        ),
        source_path=StringSchema(
            "Optional file path to convert for action='convert'. If omitted, converts all supported math knowledge files.",
            nullable=True,
        ),
        dataset_path=StringSchema(
            "JSONL evaluation dataset path for action='evaluate'.",
            nullable=True,
        ),
        max_samples=IntegerSchema(
            0,
            description="Maximum evaluation samples. Use 0 or omit for all samples.",
            minimum=0,
            maximum=1000,
        ),
        required=["action"],
    )
)
class MathRAGTool(Tool):
    """Tool wrapper for local MathRAG retrieval and maintenance."""

    _scopes = {"core"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=Path(ctx.workspace),
            embedding_config=getattr(ctx, "embedding_config", None),
            rerank_config=getattr(ctx, "rerank_config", None),
            qdrant_config=getattr(ctx, "qdrant_config", None),
            provider=getattr(ctx, "provider", None),
            model=getattr(ctx, "model", None),
        )

    def __init__(
        self,
        *,
        workspace: Path,
        embedding_config: Any | None = None,
        rerank_config: Any | None = None,
        qdrant_config: Any | None = None,
        provider: Any | None = None,
        model: str | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.embedding_config = embedding_config
        self.rerank_config = rerank_config
        self.qdrant_config = qdrant_config
        self.provider = provider
        self.model = model

    @property
    def name(self) -> str:
        return "mathrag"

    @property
    def description(self) -> str:
        return (
            "Search and maintain the local MathRAG knowledge base. "
            "Use action='search' when a math answer needs local textbook/example context; "
            "use action='convert' to convert PDF/image/structured math files into Markdown; "
            "use action='evaluate' to run the MathRAG JSONL evaluation harness."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        action: str,
        query: str | None = None,
        top_k: int = 4,
        source_path: str | None = None,
        dataset_path: str | None = None,
        max_samples: int = 0,
    ) -> str:
        action = (action or "").strip().lower()
        if action == "search":
            return await self._search(query=query or "", top_k=top_k)
        if action == "convert":
            return await self._convert(source_path=source_path)
        if action == "evaluate":
            return await self._evaluate(
                dataset_path=dataset_path or "",
                top_k=top_k,
                max_samples=max_samples,
            )
        return "Error: action must be one of search, convert, evaluate."

    def _kb(self) -> MathKnowledgeBase:
        return MathKnowledgeBase(
            self.workspace,
            embedding_config=self.embedding_config,
            rerank_config=self.rerank_config,
            qdrant_config=self.qdrant_config,
        )

    async def _search(self, *, query: str, top_k: int) -> str:
        query = query.strip()
        if not query:
            return "Error: query is required for action='search'."
        limit = _clamp_int(top_k, 4, 1, 10)
        hits = await self._kb().async_search(query, limit=limit)
        payload = {
            "action": "search",
            "query": query,
            "hits": [
                {
                    "rank": i,
                    "title": hit.title,
                    "content": hit.content,
                    "source": hit.source,
                    "chapter": hit.chapter,
                    "tags": list(hit.tags),
                    "score": hit.score,
                }
                for i, hit in enumerate(hits, 1)
            ],
            "message": "" if hits else "知识库中未检索到相关内容。",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _convert(self, *, source_path: str | None) -> str:
        converter = MathKnowledgeMarkdownConverter(self.workspace)
        if source_path and source_path.strip():
            path = resolve_workspace_path(source_path.strip(), self.workspace)
            results = [converter.convert_file(path, write=True)]
        else:
            results = converter.convert_all(write=True)

        try:
            await self._kb().async_sync_index()
            index_refreshed = True
            index_error = ""
        except Exception as exc:
            index_refreshed = False
            index_error = str(exc)

        payload = {
            "action": "convert",
            "converted": [
                {
                    "source": _rel(result.source_path, self.workspace),
                    "markdown": _rel(result.markdown_path, self.workspace) if result.markdown_path else None,
                    "ok": result.ok,
                    "warnings": result.warnings,
                }
                for result in results
            ],
            "index_refreshed": index_refreshed,
            "index_error": index_error,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _evaluate(self, *, dataset_path: str, top_k: int, max_samples: int) -> str:
        if self.provider is None or not self.model:
            return "Error: action='evaluate' requires the active LLM provider and model."
        if not dataset_path.strip():
            return "Error: dataset_path is required for action='evaluate'."
        dataset = resolve_workspace_path(dataset_path.strip(), self.workspace)
        samples = load_eval_dataset(dataset)
        limit = _clamp_int(max_samples, 0, 0, 1000)
        if limit > 0:
            samples = samples[:limit]
        if not samples:
            return "Error: dataset has no samples."

        results = await evaluate_samples(
            samples,
            workspace=self.workspace,
            provider=self.provider,
            model=self.model,
            embedding_config=self.embedding_config,
            rerank_config=self.rerank_config,
            qdrant_config=self.qdrant_config,
            top_k=_clamp_int(top_k, 4, 1, 10),
        )
        report_path = write_markdown_report(results, self.workspace / ".nanobot" / "math_eval")
        report = render_markdown_report(results)
        summary = report.split("## Details", 1)[0].strip()
        payload = {
            "action": "evaluate",
            "dataset": _rel(dataset, self.workspace),
            "report": _rel(report_path, self.workspace),
            "summary": summary,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
