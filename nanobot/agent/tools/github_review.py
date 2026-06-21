"""GitHub repository review tool."""

from __future__ import annotations

import time
import uuid

from loguru import logger

from nanobot.agent.review.types import ReviewAction
from nanobot.agent.review.utils import parse_pr_target
from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.review_base import (
    ALL_REVIEW_TOOL_ACTIONS,
    READER_ACTIONS,
    ReviewToolBase,
)
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "GitHub repository action: meta, tree, file, repo, or diff",
            enum=ALL_REVIEW_TOOL_ACTIONS,
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
class GitHubReviewTool(ReviewToolBase):
    """Tool wrapper for GitHub repository reading and review evidence."""

    @property
    def name(self) -> str:
        return "github_review"

    @property
    def description(self) -> str:
        return (
            "GitHub repository reader and CodeReview RAG tool. Use meta/tree/file for "
            "read-only GitHub API inspection, repo for full or scoped remote evidence retrieval, "
            "and diff for GitHub pull request review. Do not clone repositories; repo/diff "
            "actions use fixed snapshots under workspace/.nanobot/review_github."
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
            if action_value not in ALL_REVIEW_TOOL_ACTIONS:
                result_text = self._unknown_action(action_value)
                logger.warning(
                    "github_review.invalid_action trace_id={} action={} repo={}",
                    trace_id,
                    action_value,
                    repo,
                )
                return result_text
            if not self.github.config.enable:
                result_text = "Error: GitHub repository access is disabled by tools.githubRepo.enable."
                logger.warning("github_review.disabled trace_id={} repo={}", trace_id, repo)
                return result_text
            if not repo:
                result_text = "Error: target_repo is required for github_review."
                logger.warning("github_review.missing_repo trace_id={} action={}", trace_id, action_value)
                return result_text
            if action_value == "file" and not repo_path:
                result_text = "Error: repo_path is required for github_review action='file'."
                logger.warning("github_review.missing_path trace_id={} repo={}", trace_id, repo)
                return result_text
            if action_value == ReviewAction.DIFF.value and int(pr_number or 0) <= 0:
                result_text = "Error: pr_number is required for github_review action='diff'."
                logger.warning("github_review.missing_pr trace_id={} repo={}", trace_id, repo)
                return result_text
            if action_value in READER_ACTIONS:
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
            logger.exception(
                "github_review.failed trace_id={} action={} repo={} pr={} paths_count={}",
                trace_id,
                action_value,
                repo,
                pr_number,
                len(target_paths or []),
            )
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
