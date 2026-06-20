"""Local and GitHub repository review tools."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.review.evidence import ReviewEvidenceService
from nanobot.agent.review.github import GitHubRepoConfig, GitHubRepoReader
from nanobot.agent.review.local import LocalRepoReader
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
from nanobot.rag.review_service import (
    SOURCE_TYPE,
    RepositoryRAGOptions,
    RepositoryRAGService,
)
from nanobot.rag.runtime import RAGRuntime


_READER_ACTIONS = ("meta", "tree", "file")
_REVIEW_ACTIONS = tuple(action.value for action in ReviewAction)
_ALL_ACTIONS = (*_READER_ACTIONS, *_REVIEW_ACTIONS)


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


class _ReviewToolBase(Tool):
    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        rag_config = getattr(ctx, "rag_config", None) or RAGConfig()
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
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        if runtime is None:
            runtime = RAGRuntime(
                embedding_client=embedding_client,
                rerank_client=rerank_client,
                vector_store=vector_store,
            )
        self.runtime = runtime
        options = RepositoryRAGOptions.from_retrieval_config(runtime.retrieval)
        self.repository_rag = RepositoryRAGService(
            workspace,
            runtime=runtime,
            options=options,
            source_type=SOURCE_TYPE,
        )
        self.local = LocalRepoReader(self.workspace, options=options)
        self.github = GitHubRepoReader(github_config, workspace=workspace)
        self.evidence_service = ReviewEvidenceService(
            self.repository_rag,
            self.github,
            workspace=self.workspace,
        )

    @property
    def evidence_provider(self) -> ReviewEvidenceService:
        return self.evidence_service

    @property
    def read_only(self) -> bool:
        return False

    def _unknown_action(self, action: str) -> str:
        allowed = ", ".join(_ALL_ACTIONS)
        return f"Error: unknown {self.name} action '{action}'. Use {allowed}."

    def _log_finish(
        self,
        *,
        trace_id: str,
        action: str,
        target_type: str,
        result_text: str,
        status: str,
        error: str,
        started: float,
    ) -> None:
        result_kind = _review_result_kind(result_text, status=status)
        logger.info(
            "{}.finish {} trace_id={} action={} target_type={} status={} result_kind={} result_chars={} error={} elapsed_ms={:.1f}",
            self.name,
            "ok" if result_kind == "success" else "check",
            trace_id,
            action,
            target_type,
            status,
            result_kind,
            len(result_text),
            error,
            (time.perf_counter() - started) * 1000,
        )


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Local repository action: meta, tree, file, repo, or diff",
            enum=_ALL_ACTIONS,
            nullable=True,
        ),
        target=StringSchema(
            "Optional local file or directory path for meta/tree/file actions.",
            nullable=True,
        ),
        repo_path=StringSchema(
            "Local repository-relative file or directory path for action='file'.",
            nullable=True,
        ),
        target_paths=ArraySchema(
            StringSchema("Repository-relative file or directory path"),
            description="Optional files or directories that limit repo/diff review scope",
            max_items=80,
            nullable=True,
        ),
        tree_pattern=StringSchema(
            "Glob filter for local tree results (e.g. '*.py')",
            nullable=True,
        ),
        tree_limit=IntegerSchema(
            500,
            description="Maximum local tree entries to return",
            minimum=1,
            maximum=10000,
        ),
        review_query=StringSchema(
            "Question or keywords describing repository review references to retrieve",
            nullable=True,
        ),
        max_results=IntegerSchema(
            5,
            description="Maximum repository review references to return",
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
class LocalReviewTool(_ReviewToolBase):
    """Tool wrapper for local repository reading and review evidence."""

    @property
    def name(self) -> str:
        return "local_review"

    @property
    def description(self) -> str:
        return (
            "Local repository reader and CodeReview RAG tool. Use meta/tree/file for "
            "read-only local repository inspection, repo for full or scoped evidence retrieval, "
            "and diff for current local git changes."
        )

    async def execute(
        self,
        review_query: str | None = None,
        action: str | None = None,
        target: str | None = None,
        repo_path: str | None = None,
        target_paths: list[str] | None = None,
        tree_pattern: str | None = None,
        tree_limit: int = 500,
        max_results: int = 5,
        include_tests: bool | None = None,
    ) -> str:
        trace_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        action_value = (action or ReviewAction.REPO.value).strip().lower()
        result_text = ""
        status = "ok"
        error = ""
        logger.info(
            "local_review.start trace_id={} action={} target={} paths_count={} query_chars={} max_results={}",
            trace_id,
            action_value,
            target or repo_path,
            len(target_paths or []),
            len(review_query or ""),
            max_results,
        )
        try:
            if action_value not in _ALL_ACTIONS:
                result_text = self._unknown_action(action_value)
                return result_text
            if action_value in _READER_ACTIONS:
                path = repo_path or target
                result_text = await self.local.execute(
                    action=action_value,
                    path=path,
                    pattern=tree_pattern,
                    max_entries=tree_limit,
                )
                return result_text
            result_text = await self.evidence_service.dispatch(
                target_type="local",
                action=action_value,
                target_paths=target_paths or ([repo_path or target] if (repo_path or target) else []),
                tree_pattern=tree_pattern,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                trace_id=trace_id,
            )
            return result_text
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            self._log_finish(
                trace_id=trace_id,
                action=action_value,
                target_type="local",
                result_text=result_text,
                status=status,
                error=error,
                started=started,
            )


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "GitHub repository action: meta, tree, file, repo, or diff",
            enum=_ALL_ACTIONS,
            nullable=True,
        ),
        target=StringSchema(
            "Optional GitHub repo URL or GitHub PR URL. PR URLs imply action='diff'.",
            nullable=True,
        ),
        target_repo=StringSchema(
            "GitHub repo in 'owner/repo' format or a full GitHub URL.",
            nullable=True,
        ),
        pr_number=IntegerSchema(
            0,
            description="GitHub pull request number for action='diff'",
            minimum=0,
            maximum=1000000,
        ),
        repo_path=StringSchema(
            "File path within the GitHub repo for action='file'.",
            nullable=True,
        ),
        target_paths=ArraySchema(
            StringSchema("Repository-relative file or directory path"),
            description="Optional files or directories that limit repo/diff review scope",
            max_items=80,
            nullable=True,
        ),
        ref=StringSchema("GitHub branch, tag, or commit SHA", nullable=True),
        tree_pattern=StringSchema(
            "Glob filter for GitHub tree or repo snapshot results (e.g. '*.py')",
            nullable=True,
        ),
        tree_limit=IntegerSchema(
            500,
            description="Maximum GitHub tree entries to return",
            minimum=1,
            maximum=10000,
        ),
        review_query=StringSchema(
            "Question or keywords describing repository review references to retrieve",
            nullable=True,
        ),
        max_results=IntegerSchema(
            5,
            description="Maximum repository review references to return",
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
class GitHubReviewTool(_ReviewToolBase):
    """Tool wrapper for GitHub repository reading and review evidence."""

    @property
    def name(self) -> str:
        return "github_review"

    @property
    def description(self) -> str:
        return (
            "GitHub repository reader and CodeReview RAG tool. Use meta/tree/file for "
            "read-only GitHub API inspection, repo for full or scoped remote evidence retrieval, "
            "and diff for GitHub pull request review."
        )

    async def execute(
        self,
        review_query: str | None = None,
        action: str | None = None,
        target: str | None = None,
        target_repo: str | None = None,
        pr_number: int = 0,
        repo_path: str | None = None,
        target_paths: list[str] | None = None,
        ref: str | None = None,
        tree_pattern: str | None = None,
        tree_limit: int = 500,
        max_results: int = 5,
        include_tests: bool | None = None,
    ) -> str:
        trace_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        action_value = (action or ReviewAction.REPO.value).strip().lower()
        result_text = ""
        status = "ok"
        error = ""
        pr_repo, parsed_pr_number = parse_pr_target(target)
        if pr_repo:
            target_repo = target_repo or pr_repo
            pr_number = pr_number or parsed_pr_number or 0
            if action_value == ReviewAction.REPO.value:
                action_value = ReviewAction.DIFF.value
        repo = (target_repo or target or "").strip()
        logger.info(
            "github_review.start trace_id={} action={} target={} target_repo={} pr={} paths_count={} query_chars={} max_results={}",
            trace_id,
            action_value,
            target,
            target_repo,
            pr_number,
            len(target_paths or []),
            len(review_query or ""),
            max_results,
        )
        try:
            if action_value not in _ALL_ACTIONS:
                result_text = self._unknown_action(action_value)
                return result_text
            if not self.github.config.enable:
                result_text = "Error: GitHub repository access is disabled by tools.githubRepo.enable."
                return result_text
            if not repo:
                result_text = "Error: target_repo is required for github_review."
                return result_text
            if action_value == "file" and not repo_path:
                result_text = "Error: repo_path is required for github_review action='file'."
                return result_text
            if action_value == ReviewAction.DIFF.value and int(pr_number or 0) <= 0:
                result_text = "Error: pr_number is required for github_review action='diff'."
                return result_text
            if action_value in _READER_ACTIONS:
                result_text = await self.github.execute(
                    action=action_value,
                    repo=repo,
                    path=repo_path,
                    ref=ref,
                    pattern=tree_pattern,
                    max_entries=tree_limit,
                    pr_number=int(pr_number or 0),
                )
                return result_text
            result_text = await self.evidence_service.dispatch(
                target_type="github",
                action=action_value,
                repo=repo,
                ref=ref,
                pr_number=int(pr_number or 0),
                target_paths=target_paths or ([repo_path] if repo_path else []),
                tree_pattern=tree_pattern,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                trace_id=trace_id,
            )
            return result_text
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            self._log_finish(
                trace_id=trace_id,
                action=action_value,
                target_type="github",
                result_text=result_text,
                status=status,
                error=error,
                started=started,
            )


try:
    from nanobot.config import schema as _config_schema

    if not getattr(_config_schema.ToolsConfig, "__pydantic_complete__", False):
        _config_schema._resolve_tool_config_refs()
except Exception:
    pass
