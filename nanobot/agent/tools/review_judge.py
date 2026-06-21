"""Internal code-review AI judge tool."""
from __future__ import annotations

from typing import Any

from nanobot.agent.review.judge import ReviewJudge, ReviewJudgeConfig
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        candidates_json=StringSchema("JSON array of review finding candidates to judge."),
        required=["candidates_json"],
    )
)
class ReviewJudgeTool(Tool):
    """Expose code review judge capability as an internal tool."""

    _scopes = {"core"}

    def __init__(self, judge: ReviewJudge | None = None) -> None:
        self._judge = judge

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        if ctx.provider is None or not ctx.model:
            return cls(None)
        config = ReviewJudgeConfig()
        return cls(ReviewJudge(provider=ctx.provider, model=ctx.model, config=config))

    @property
    def name(self) -> str:
        return "review_judge"

    @property
    def description(self) -> str:
        return "Internal AI judge for code-review finding candidates."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, candidates_json: str) -> str:
        if self._judge is None:
            return "Error: review judge is not configured."
        return (
            "review_judge is managed by the code review finalizer. "
            "Use CodeReview mode to run judge validation."
        )
