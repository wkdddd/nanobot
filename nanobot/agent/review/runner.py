"""Programmatic runner for CodeReviewAgent mode."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from nanobot.agent.loop import AgentLoop
from nanobot.agent.review.prompts import build_review_prompt
from nanobot.agent.review.render import render_json, render_markdown
from nanobot.agent.review.roles import normalize_focus
from nanobot.agent.review.target import ReviewTarget, resolve_review_target
from nanobot.agent.review.tools import (
    ReportFindingTool,
    ReportSummaryTool,
    ReviewReport,
    clear_active_report,
    set_active_report,
)
from nanobot.config.schema import Config


@dataclass(frozen=True, slots=True)
class ReviewRunSpec:
    target: str
    focus: str | None = None
    session_key: str = "review:default"


@dataclass
class ReviewResult:
    """Structured review output."""
    report: ReviewReport
    target_name: str
    raw_content: str

    def to_markdown(self) -> str:
        if self.report.findings or self.report.executive_summary:
            return render_markdown(self.report, self.target_name)
        return self.raw_content

    def to_json(self) -> str:
        if self.report.findings or self.report.executive_summary:
            return render_json(self.report, self.target_name)
        return json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target": self.target_name,
            "content": self.raw_content,
        }, ensure_ascii=False, indent=2)

    def has_critical(self) -> bool:
        return self.report.has_critical()


async def run_review(
    *,
    config: Config,
    agent_loop: AgentLoop,
    spec: ReviewRunSpec,
    on_stream: Callable[[str], Awaitable[None]] | None = None,
    on_stream_end: Callable[..., Awaitable[None]] | None = None,
) -> ReviewResult:
    target = await resolve_review_target(spec.target, workspace=config.workspace_path)

    roles, isforced = normalize_focus(spec.focus)

    prompt = build_review_prompt(
        target_path=str(target.path),
        target_name=target.display_name,
        target_kind=target.kind,
        roles=roles,
        max_subagents=config.agents.defaults.max_concurrent_subagents,
        forced=isforced,
    )

    report = ReviewReport()
    set_active_report(report)
    agent_loop.tools.register(ReportFindingTool())
    agent_loop.tools.register(ReportSummaryTool())

    try:
        response = await agent_loop.process_direct(
            prompt,
            session_key=_session_key_for_target(spec.session_key, target),
            channel="cli",
            chat_id="review",
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
    finally:
        agent_loop.tools.unregister("report_finding")
        agent_loop.tools.unregister("report_summary")
        clear_active_report()

    raw = response.content if response else ""
    return ReviewResult(report=report, target_name=target.display_name, raw_content=raw)


def _session_key_for_target(base: str, target: ReviewTarget) -> str:
    safe = target.display_name.replace("/", "-").replace("\\", "-")
    return f"{base}:{safe}"
