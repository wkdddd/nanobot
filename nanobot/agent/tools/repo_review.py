"""Local and GitHub repository review tool."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.review.evidence import ReviewEvidenceService
from nanobot.agent.review.github import GitHubRepoConfig, GitHubRepoReader
from nanobot.agent.review.types import ReviewAction
from nanobot.agent.review.utils import parse_pr_target
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.rag import create_rag_runtime
from nanobot.rag.config import RAGConfig
from nanobot.rag.review import (
    SOURCE_TYPE,
    RepositoryRAGEvaluator,
    RepositoryRAGOptions,
    RepositoryRAGService,
)
from nanobot.rag.runtime import RAGRuntime

_GITHUB_URL_RE = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s.,;!?)#]+)")


def _repo_review_action_values() -> tuple[str, ...]:
    return tuple(action.value for action in ReviewAction)


def _review_result_kind(result: str, *, status: str) -> str:
    if status == "error":
        return "error"
    if result.startswith("Error:"):
        return "error"
    if result.startswith("No text files found"):
        return "empty_files"
    if "No relevant" in result:
        return "no_hits"
    return "success"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@tool_parameters(
    tool_parameters_schema(
        target_type=StringSchema(
            "Review target type: 'local' for current workspace search, 'github' for GitHub API reads, or 'auto'",
            enum=("auto", "local", "github"),
        ),
        action=StringSchema(
            "Code review evidence action: full_repo, pr_diff, or local_changed",
            enum=_repo_review_action_values(),
            nullable=True,
        ),
        target=StringSchema(
            "Optional local path, GitHub repo URL, or GitHub PR URL. PR URLs imply action='pr_diff'.",
            nullable=True,
        ),
        target_repo=StringSchema(
            "GitHub repo in 'owner/repo' format or a full GitHub URL when target_type='github'",
            nullable=True,
        ),
        pr_number=IntegerSchema(
            0,
            description="GitHub pull request number for action='pr_diff'",
            minimum=0,
            maximum=1000000,
        ),
        repo_path=StringSchema(
            "File path within the GitHub repo.",
            nullable=True,
        ),
        target_paths=ArraySchema(
            StringSchema("Repository-relative file or directory path"),
            description="Optional files or directories that limit the review scope",
            max_items=80,
            nullable=True,
        ),
        ref=StringSchema("GitHub branch, tag, or commit SHA", nullable=True),
        tree_pattern=StringSchema(
            "Glob filter for GitHub tree results (e.g. '*.py')",
            nullable=True,
        ),
        review_query=StringSchema(
            "Question or keywords describing the repository review references to retrieve",
            nullable=True,
        ),
        max_results=IntegerSchema(
            5,
            description="Maximum number of local repository review references to return",
            minimum=1,
            maximum=20,
        ),
        include_tests=BooleanSchema(
            description="Include likely related test file paths when available",
            default=True,
        ),
        required=[],
    )
)
class RepoReviewTool(Tool):
    """Tool wrapper for repository review references."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        rag_config = getattr(ctx, "rag_config", None)
        if rag_config is None:
            rag_config = RAGConfig()
        runtime = create_rag_runtime(rag_config)
        tools_config = ctx.config if ctx.config else None
        return cls(
            workspace=Path(ctx.workspace),
            runtime=runtime,
            github_config=getattr(tools_config, "github_repo", None),
        )

    def __init__(
        self,
        workspace: Path,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        vector_store: Any | None = None,
        runtime: RAGRuntime | None = None,
        github_config: GitHubRepoConfig | None = None,
        semantic_weight: float = 0.6,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        if runtime is None:
            runtime = RAGRuntime(
                embedding_client=embedding_client,
                rerank_client=rerank_client,
                vector_store=vector_store,
            )
        self.runtime = runtime
        self.semantic_weight = runtime.retrieval.semantic_weight
        self.report_dir = ".nanobot/review_reports"
        self.repository_rag = RepositoryRAGService(
            workspace,
            runtime=runtime,
            options=RepositoryRAGOptions.from_retrieval_config(runtime.retrieval),
            source_type=SOURCE_TYPE,
        )
        self.github = GitHubRepoReader(github_config, workspace=workspace)
        self.evidence_service = ReviewEvidenceService(
            self.repository_rag,
            self.github,
            workspace=self.workspace,
        )

    @property
    def name(self) -> str:
        return "repo_review"

    @property
    def evidence_provider(self) -> ReviewEvidenceService:
        return self.evidence_service

    @property
    def description(self) -> str:
        return (
            "CodeReview RAG tool for repository evidence retrieval, GitHub full-repo "
            "and PR diff context. Use action='full_repo' for complete repository or limited-scope retrieval, "
            "action='pr_diff' for GitHub PR review, and action='local_changed' for local git changes."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        review_query: str | None = None,
        target_type: str = "local",
        action: str | None = None,
        target: str | None = None,
        target_repo: str | None = None,
        pr_number: int = 0,
        repo_path: str | None = None,
        target_paths: list[str] | None = None,
        ref: str | None = None,
        tree_pattern: str | None = None,
        max_results: int = 5,
        include_tests: bool | None = None,
        tree_limit: int = 500,
        report_path: str | None = None,
        dataset_path: str | None = None,
        budget_chars: int = 16000,
    ) -> str:
        trace_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        action_value = (action or ReviewAction.FULL_REPO.value).strip().lower()
        result_text = ""
        status = "ok"
        error = ""
        logger.info(
            "repo_review.start trace_id={} action={} target_type={} target={} target_repo={} paths_count={} query_chars={} max_results={}",
            trace_id,
            action_value,
            target_type,
            target,
            target_repo,
            len(target_paths or []),
            len(review_query or ""),
            max_results,
        )
        target_type = (target_type or "local").strip().lower()
        if target_type == "auto":
            target_type = "github" if _GITHUB_URL_RE.search(target or target_repo or "") else "local"
        pr_repo, parsed_pr_number = parse_pr_target(target)
        if pr_repo:
            target_type = "github"
            target_repo = target_repo or pr_repo
            pr_number = pr_number or parsed_pr_number or 0
            if action_value == ReviewAction.FULL_REPO.value:
                action_value = ReviewAction.PR_DIFF.value
        try:
            action_values = _repo_review_action_values()
            if action_value not in action_values:
                allowed = ", ".join(action_values)
                result_text = (
                    f"Error: unknown repo_review action '{action_value}'. "
                    f"Use {allowed}."
                )
                return result_text
            if target_type == "github":
                if not self.github.config.enable:
                    result_text = "Error: GitHub repository access is disabled by tools.githubRepo.enable."
                    return result_text
                repo = (target_repo or target or "").strip()
                if not repo:
                    result_text = "Error: target_repo is required when target_type='github'."
                    return result_text
                if action_value == ReviewAction.PR_DIFF.value:
                    result_text = await self.evidence_service.github_diff_context(
                        repo=repo,
                        pr_number=int(pr_number or 0),
                        target_paths=target_paths or ([repo_path] if repo_path else []),
                        review_query=review_query,
                        max_results=max_results,
                        include_tests=include_tests,
                        trace_id=trace_id,
                    )
                    return result_text
                if action_value == ReviewAction.FULL_REPO.value and (target_paths or repo_path):
                    result_text = await self.evidence_service.github_targeted_context(
                        repo=repo,
                        ref=ref,
                        target_paths=target_paths or ([repo_path] if repo_path else []),
                        review_query=review_query,
                        max_results=max_results,
                        include_tests=include_tests,
                        trace_id=trace_id,
                    )
                    return result_text
                if action_value == ReviewAction.FULL_REPO.value:
                    result_text = await self.evidence_service.github_context(
                        repo=repo,
                        ref=ref,
                        tree_pattern=tree_pattern,
                        review_query=review_query,
                        max_results=max_results,
                        include_tests=include_tests,
                        trace_id=trace_id,
                    )
                    return result_text
                result_text = f"Error: unknown GitHub action '{action_value}'."
                return result_text

            if action_value == ReviewAction.PR_DIFF.value:
                result_text = "Error: action 'pr_diff' requires target_type='github'."
                return result_text
            if action_value == ReviewAction.LOCAL_CHANGED.value:
                result_text = await self.evidence_service.local_changed_context(
                    review_query=review_query,
                    target_paths=target_paths or ([repo_path] if repo_path else []),
                    max_results=max_results,
                    include_tests=include_tests,
                )
                return result_text
            if action_value == ReviewAction.FULL_REPO.value and (target_paths or repo_path):
                result_text = await self.evidence_service.local_targeted_context(
                    review_query=review_query,
                    target_paths=target_paths or ([repo_path] if repo_path else []),
                    max_results=max_results,
                    include_tests=include_tests,
                )
                return result_text
            if action_value != ReviewAction.FULL_REPO.value:
                result_text = f"Error: action '{action_value}' is not supported for local target_type."
                return result_text
            result_text = await self.evidence_service.local_context(
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
            )
            return result_text
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            result_kind = _review_result_kind(result_text, status=status)
            logger.info(
                "repo_review.finish {} trace_id={} action={} target_type={} status={} result_kind={} result_chars={} error={} elapsed_ms={:.1f}",
                "✅" if result_kind == "success" else "🔎",
                trace_id,
                action_value,
                target_type,
                status,
                result_kind,
                len(result_text),
                error,
                (time.perf_counter() - started) * 1000,
            )


    async def _report(
        self,
        *,
        review_query: str | None,
        target_type: str,
        target: str | None,
        target_repo: str | None,
        ref: str | None,
        max_results: int,
        include_tests: bool | None,
        report_path: str | None,
        budget_chars: int,
        trace_id: str,
    ) -> str:
        if target_type == "github":
            context = await self.evidence_service.github_context(
                repo=(target_repo or target or "").strip(),
                ref=ref,
                tree_pattern=None,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                trace_id=trace_id,
            )
        else:
            context = await self.evidence_service.local_context(
                review_query=review_query or "code review entry points security tests config",
                max_results=max_results,
                include_tests=include_tests,
            )
        markdown = "\n".join(
            [
                "# Code Review RAG Context Report",
                "",
                f"- Generated: {_now_iso()}",
                f"- Target type: {target_type}",
                f"- Target: {target_repo or target or 'local workspace'}",
                f"- Query: {review_query or '(default code review query)'}",
                "",
                "## Retrieved Evidence",
                "",
                context[: max(1000, int(budget_chars))],
                "",
            ]
        )
        path = self._resolve_report_path(report_path, "code-review-rag-report.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8", newline="\n")
        logger.info("repo_review report written trace_id={} path={} chars={}", trace_id, path, len(markdown))
        return f"Markdown report written: {path}\n\n{markdown[:4000]}"

    async def _evaluate(
        self,
        *,
        dataset_path: str | None,
        report_path: str | None,
        max_results: int,
        trace_id: str,
    ) -> str:
        if not dataset_path:
            return "Error: dataset_path is required for action='evaluate'."
        path = Path(dataset_path).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        try:
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            return f"Error: failed to read evaluation dataset: {exc}"

        async def retrieve_context(row: dict[str, Any], query: str, limit: int, eval_trace_id: str) -> str:
            target_type = str(row.get("target_type") or "local")
            target_repo = row.get("target_repo") or row.get("target")
            if target_type == "github" and target_repo:
                return await self.evidence_service.github_context(
                    repo=str(target_repo),
                    ref=row.get("ref"),
                    tree_pattern=row.get("tree_pattern"),
                    review_query=query,
                    max_results=limit,
                    include_tests=True,
                    trace_id=eval_trace_id,
                )
            return await self.evidence_service.local_context(
                review_query=query,
                max_results=limit,
                include_tests=True,
            )

        evaluator = RepositoryRAGEvaluator(retrieve_context)
        results = await evaluator.evaluate(rows, max_results=max_results, trace_id=trace_id)

        average = (
            sum(float(r["evidence_coverage"]) for r in results) / len(results)
            if results
            else 0.0
        )
        lines = [
            "# Code Review RAG Evaluation Report",
            "",
            f"- Generated: {_now_iso()}",
            f"- Dataset: {path}",
            f"- Samples: {len(results)}",
            f"- Average evidence coverage: {average:.3f}",
            "",
            "| ID | Coverage | File hits | Symbol hits | Query |",
            "|---|---:|---:|---:|---|",
        ]
        for result in results:
            query = str(result["query"]).replace("|", "\\|")
            lines.append(
                f"| {result['id']} | {result['evidence_coverage']:.3f} | "
                f"{result['file_hits']}/{result['expected_files']} | "
                f"{result['symbol_hits']}/{result['expected_symbols']} | {query} |"
            )
        markdown = "\n".join(lines) + "\n"
        out_path = self._resolve_report_path(report_path, "code-review-rag-eval.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8", newline="\n")
        logger.info(
            "repo_review evaluation written trace_id={} path={} samples={} average={:.3f}",
            trace_id,
            out_path,
            len(results),
            average,
        )
        return f"Evaluation report written: {out_path}\n\n{markdown}"

    def _resolve_report_path(self, raw: str | None, default_name: str) -> Path:
        if raw and raw.strip():
            path = Path(raw.strip()).expanduser()
            if not path.is_absolute():
                path = self.workspace / path
            return path
        return self.workspace / self.report_dir / default_name


try:
    from nanobot.config import schema as _config_schema

    if not getattr(_config_schema.ToolsConfig, "__pydantic_complete__", False):
        _config_schema._resolve_tool_config_refs()
except Exception:
    pass
