"""Lightweight evidence prefetch for code review mode."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.review.types import (
    ReviewAction,
    ReviewEvidenceProvider,
    ReviewMetaKey,
    ReviewPlan,
)

_PREFETCH_ACTIONS = {
    ReviewAction.REPO,
    ReviewAction.DIFF,
}

ReviewProgressCallback = Callable[..., Awaitable[None]]


async def _emit_prefetch_progress(
    progress_callback: ReviewProgressCallback | None,
    *,
    phase: str,
    trace_id: str | None,
    action: ReviewAction,
    target_type: str,
    status: str,
    reason: str = "",
    elapsed_ms: float | None = None,
    raw_chars: int | None = None,
    summary_chars: int | None = None,
) -> None:
    if progress_callback is None:
        return
    event: dict[str, Any] = {
        "version": 1,
        "phase": phase,
        "name": "review_prefetch",
        "arguments": {
            "action": action.value,
            "target_type": target_type,
        },
        "result": status,
        "error": reason or None,
        "files": [],
        "embeds": [],
        "metadata": {
            "trace_id": trace_id,
            "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
            "raw_chars": raw_chars,
            "summary_chars": summary_chars,
        },
    }
    try:
        await progress_callback("", tool_events=[event])
    except Exception:
        logger.exception("review.prefetch.progress_emit_failed trace_id={}", trace_id)


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


async def maybe_prefetch_review_context(
    plan: ReviewPlan,
    session_meta: dict[str, Any],
    progress_callback: ReviewProgressCallback | None = None,
) -> str | None:
    if plan.action not in _PREFETCH_ACTIONS:
        logger.info("review.prefetch.skip action={} reason=non_prefetch_action", plan.action.value)
        await _emit_prefetch_progress(
            progress_callback,
            phase="skip",
            trace_id=None,
            action=plan.action,
            target_type=plan.target_type,
            status="skip",
            reason="non_prefetch_action",
        )
        return None
    evidence_service: ReviewEvidenceProvider | None = session_meta.get(ReviewMetaKey.EVIDENCE_PROVIDER)
    if evidence_service is None:
        logger.info("review.prefetch.skip action={} reason=evidence_service_unavailable", plan.action.value)
        await _emit_prefetch_progress(
            progress_callback,
            phase="skip",
            trace_id=None,
            action=plan.action,
            target_type=plan.target_type,
            status="skip",
            reason="evidence_service_unavailable",
        )
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
    await _emit_prefetch_progress(
        progress_callback,
        phase="start",
        trace_id=trace_id,
        action=plan.action,
        target_type=plan.target_type,
        status="start",
    )
    try:
        target_type = plan.target_type if plan.target_type != "auto" else "local"
        result = await evidence_service.dispatch(
            target_type=target_type,
            action=plan.action.value,
            repo=(plan.target_repo or plan.target or "").strip(),
            pr_number=plan.pr_number or 0,
            target_paths=plan.target_paths or None,
            review_query=query,
            max_results=5,
            include_tests=True,
            trace_id=trace_id,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning(
            "review.prefetch.done trace_id={} status=error action={} target_type={} reason={} elapsed_ms={:.1f}",
            trace_id,
            plan.action.value,
            plan.target_type,
            exc,
            elapsed_ms,
        )
        await _emit_prefetch_progress(
            progress_callback,
            phase="end",
            trace_id=trace_id,
            action=plan.action,
            target_type=plan.target_type,
            status="error",
            reason=str(exc),
            elapsed_ms=elapsed_ms,
        )
        return None
    raw = str(result)
    summary = _compact_evidence(raw)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "review.prefetch.done trace_id={} status=ok action={} raw_chars={} summary_chars={} elapsed_ms={:.1f}",
        trace_id,
        plan.action.value,
        len(raw),
        len(summary),
        elapsed_ms,
    )
    await _emit_prefetch_progress(
        progress_callback,
        phase="end",
        trace_id=trace_id,
        action=plan.action,
        target_type=plan.target_type,
        status="ok" if summary else "no_summary",
        elapsed_ms=elapsed_ms,
        raw_chars=len(raw),
        summary_chars=len(summary),
    )
    if not summary:
        return None
    return summary
