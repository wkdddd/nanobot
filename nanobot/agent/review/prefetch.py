"""Lightweight evidence prefetch for code review mode."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from loguru import logger

from nanobot.agent.review.types import ReviewAction, ReviewPlan

_PREFETCH_ACTIONS = {
    ReviewAction.FULL_REPO,
    ReviewAction.PR_DIFF,
    ReviewAction.LOCAL_CHANGED,
}


def _compact_evidence(raw: str, *, budget: int = 2400) -> str:
    if not raw.strip():
        return ""
    lines = raw.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.startswith("[")
            or stripped.startswith("- ")
            or stripped.startswith("## ")
            or stripped.startswith("File:")
            or stripped.startswith("Tree for")
            or re.match(r"^[A-Za-z0-9_./\\-]+:\d+", stripped)
        ):
            kept.append(stripped)
        if len("\n".join(kept)) >= budget:
            break
    compact = "\n".join(kept) or raw[:budget]
    if len(compact) > budget:
        compact = compact[:budget].rstrip() + "\n... (prefetch summary truncated)"
    return compact


async def maybe_prefetch_review_context(plan: ReviewPlan, session_meta: dict[str, Any]) -> str | None:
    if plan.action not in _PREFETCH_ACTIONS:
        logger.info("review.prefetch.skip action={} reason=non_prefetch_action", plan.action.value)
        return None
    tool = session_meta.get("_repo_review_tool")
    if tool is None:
        logger.info("review.prefetch.skip action={} reason=tool_unavailable", plan.action.value)
        return None

    trace_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    logger.info(
        "review.prefetch.start trace_id={} action={} target_type={} target={} paths_count={}",
        trace_id,
        plan.action.value,
        plan.target_type,
        plan.target,
        len(plan.target_paths),
    )
    query = plan.user_requirements or "code review security architecture tests performance entry points config"
    try:
        result = await tool.execute(
            action=plan.action.value,
            target_type=plan.target_type if plan.target_type != "auto" else "local",
            target=plan.target,
            target_repo=plan.target_repo,
            pr_number=plan.pr_number or 0,
            target_paths=plan.target_paths,
            review_query=query,
            max_results=5,
            include_tests=True,
        )
    except Exception as exc:
        logger.warning("review.prefetch.done trace_id={} status=error reason={}", trace_id, exc)
        return None
    summary = _compact_evidence(str(result))
    logger.info(
        "review.prefetch.done trace_id={} action={} chars={} elapsed_ms={:.1f}",
        trace_id,
        plan.action.value,
        len(summary),
        (time.perf_counter() - started) * 1000,
    )
    if not summary:
        return None
    return summary
