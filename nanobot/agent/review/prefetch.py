"""Lightweight evidence prefetch for code review mode."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from loguru import logger

from nanobot.agent.review.types import (
    ReviewAction,
    ReviewEvidenceProvider,
    ReviewMetaKey,
    ReviewPlan,
)

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
    evidence_service: ReviewEvidenceProvider | None = session_meta.get(ReviewMetaKey.EVIDENCE_PROVIDER)
    if evidence_service is None:
        logger.info("review.prefetch.skip action={} reason=evidence_service_unavailable", plan.action.value)
        return None

    trace_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    query = plan.user_requirements or "code review security architecture tests performance entry points config"
    logger.info(
        "review.prefetch.start trace_id={} action={} target_type={} target={} target_repo={} paths_count={} query_chars={} max_results={}",
        trace_id,
        plan.action.value,
        plan.target_type,
        plan.target,
        plan.target_repo,
        len(plan.target_paths),
        len(query),
        5,
    )
    try:
        target_type = plan.target_type if plan.target_type != "auto" else "local"
        if target_type == "github":
            repo = (plan.target_repo or plan.target or "").strip()
            if plan.action == ReviewAction.PR_DIFF:
                result = await evidence_service.github_diff_context(
                    repo=repo,
                    pr_number=plan.pr_number or 0,
                    target_paths=plan.target_paths,
                    review_query=query,
                    max_results=5,
                    include_tests=True,
                    trace_id=trace_id,
                )
            else:
                result = await evidence_service.github_context(
                    repo=repo,
                    ref=None,
                    tree_pattern=None,
                    review_query=query,
                    max_results=5,
                    include_tests=True,
                    trace_id=trace_id,
                )
        elif plan.action == ReviewAction.LOCAL_CHANGED:
            result = await evidence_service.local_changed_context(
                review_query=query,
                target_paths=plan.target_paths,
                max_results=5,
                include_tests=True,
            )
        elif plan.target_paths:
            result = await evidence_service.local_targeted_context(
                review_query=query,
                target_paths=plan.target_paths,
                max_results=5,
                include_tests=True,
            )
        else:
            result = await evidence_service.local_context(
                review_query=query,
                max_results=5,
                include_tests=True,
            )
    except Exception as exc:
        logger.warning(
            "review.prefetch.done trace_id={} status=error action={} target_type={} reason={} elapsed_ms={:.1f}",
            trace_id,
            plan.action.value,
            plan.target_type,
            exc,
            (time.perf_counter() - started) * 1000,
        )
        return None
    raw = str(result)
    summary = _compact_evidence(raw)
    logger.info(
        "review.prefetch.done trace_id={} status=ok action={} raw_chars={} summary_chars={} elapsed_ms={:.1f}",
        trace_id,
        plan.action.value,
        len(raw),
        len(summary),
        (time.perf_counter() - started) * 1000,
    )
    if not summary:
        return None
    return summary
