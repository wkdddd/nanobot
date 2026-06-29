"""Lightweight evidence prefetch for code review mode."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.review.input import policy_for_depth
from nanobot.review.types import (
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


@dataclass(frozen=True, slots=True)
class ReviewPrefetchResult:
    attempted: bool
    status: str
    summary: str | None = None
    reason: str = ""


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
    metadata: dict[str, Any] | None = None,
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
            **(metadata or {}),
        },
    }
    try:
        await progress_callback("", tool_events=[event])
    except Exception:
        logger.exception("review.prefetch.progress_emit_failed trace_id={}", trace_id)


def _compact_evidence(raw: str, *, budget: int = 10000) -> str:
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
) -> ReviewPrefetchResult:
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
        return ReviewPrefetchResult(False, "skip", reason="non_prefetch_action")
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
        return ReviewPrefetchResult(False, "skip", reason="evidence_service_unavailable")

    trace_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    policy = policy_for_depth(plan.depth, requested_max_subagents=plan.max_subagents)
    query = plan.user_requirements or "code review security architecture tests performance entry points config"
    logger.info(
        "review.prefetch.start trace_id={} action={} target_type={} target={} target_repo={} scope_kind={} review_root={} target_subpath={} query_chars={} max_results={}",
        trace_id,
        plan.action.value,
        plan.target_type,
        plan.target,
        plan.target_repo,
        plan.local_scope.kind if plan.local_scope else "",
        plan.local_scope.review_root if plan.local_scope else "",
        plan.target_subpath or "",
        len(query),
        policy.evidence_max_results,
    )
    await _emit_prefetch_progress(
        progress_callback,
        phase="start",
        trace_id=trace_id,
        action=plan.action,
        target_type=plan.target_type,
        status="start",
        metadata={
            "scope_kind": plan.local_scope.kind if plan.local_scope else None,
            "review_root": plan.local_scope.review_root if plan.local_scope else None,
            "scope_paths_count": len(plan.local_scope.scope_paths) if plan.local_scope else 0,
            "target_subpath": plan.target_subpath,
        },
    )
    try:
        target_type = plan.target_type if plan.target_type != "auto" else "local"
        result = await evidence_service.dispatch(
            target_type=target_type,
            action=plan.action.value,
            repo=(plan.target_repo or plan.target or "").strip(),
            ref=plan.target_ref,
            pr_number=plan.pr_number or 0,
            target_subpath=plan.target_subpath,
            target_subpath_kind=plan.target_subpath_kind,
            review_query=query,
            max_results=policy.evidence_max_results,
            include_tests=True,
            local_scope=plan.local_scope,
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
        return ReviewPrefetchResult(True, "error", reason=str(exc))
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
        return ReviewPrefetchResult(True, "no_summary")
    return ReviewPrefetchResult(True, "ok", summary=summary)
