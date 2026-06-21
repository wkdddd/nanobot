"""Local repository review tool."""

from __future__ import annotations

import time
import uuid

from loguru import logger

from nanobot.agent.review.types import ReviewAction
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
            "Local repository action: meta, tree, file, repo, or diff",
            enum=ALL_REVIEW_TOOL_ACTIONS,
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
class LocalReviewTool(ReviewToolBase):
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
            if action_value not in ALL_REVIEW_TOOL_ACTIONS:
                result_text = self._unknown_action(action_value)
                logger.warning(
                    "local_review.invalid_action trace_id={} action={}",
                    trace_id,
                    action_value,
                )
                return result_text
            if action_value in READER_ACTIONS:
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
            logger.exception(
                "local_review.failed trace_id={} action={} target={} paths_count={}",
                trace_id,
                action_value,
                target or repo_path,
                len(target_paths or []),
            )
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
