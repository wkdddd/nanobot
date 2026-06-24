"""Review finalization hook."""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from loguru import logger

from nanobot.agent.hooks.lifecycle import AgentHook, AgentHookContext
from nanobot.agent.review.beforeplan import policy_for_depth
from nanobot.agent.review.finalizer import ReviewFinalizer
from nanobot.agent.review.judge import ReviewJudge
from nanobot.agent.review.types import ReviewDepth


class ReviewFinalizerHook(AgentHook):
    """Runner hook that renders a fixed review report from subagent outputs."""

    def __init__(
        self,
        *,
        workspace: str,
        target_name: str,
        changed_files: list[str] | None = None,
        depth: ReviewDepth = "full",
        judge: ReviewJudge | None = None,
        allowed_dimensions: list[str] | set[str] | None = None,
        can_finalize: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self._target_name = target_name
        self._policy = policy_for_depth(depth)
        self._finalizer = ReviewFinalizer(
            workspace,
            changed_files,
            policy=self._policy,
            allowed_dimensions=allowed_dimensions,
        )
        self._rendered = False
        self._rendered_report: str | None = None
        self._seen_subagent_results: set[str] = set()
        self._judged = False
        self._judge = judge
        self._can_finalize = can_finalize or (lambda: True)

    def set_allowed_dimensions(self, allowed_dimensions: list[str] | set[str] | None) -> None:
        self._finalizer.set_allowed_dimensions(allowed_dimensions)

    def set_validation_context(
        self,
        *,
        workspace: str,
        changed_files: list[str] | None = None,
        local_target: str | None = None,
    ) -> None:
        self._finalizer.set_validation_context(
            workspace=workspace,
            changed_files=changed_files,
            local_target=local_target,
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._rendered:
            return
        ingested = self._ensure_ingested(context)
        if ingested <= 0:
            return
        self._judged = False
        await self._finalizer.apply_judge(self._judge)
        self._judged = True

    def _ensure_ingested(self, context: AgentHookContext) -> int:
        messages: list[dict[str, Any]] = []
        for message in context.messages:
            meta = ReviewFinalizer._subagent_metadata(message)
            if not meta:
                continue
            raw = ReviewFinalizer._subagent_raw_output(message, meta)
            key = self._subagent_result_key(meta, raw)
            if key in self._seen_subagent_results:
                continue
            self._seen_subagent_results.add(key)
            messages.append(message)
        if not messages:
            return 0
        return self._finalizer.ingest_messages(messages)

    @staticmethod
    def _subagent_result_key(meta: dict[str, Any], raw: str) -> str:
        task_id = meta.get("subagent_task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
        label = str(meta.get("subagent_label") or meta.get("label") or "unknown")
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{label}:{digest}"

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        if self._rendered:
            context.content_replaced = True
            return self._rendered_report or content
        ingested = self._ensure_ingested(context)
        if not self._can_finalize():
            if ingested:
                logger.info(
                    "review.finalizer.defer target={} ingested={} waiting_for_subagents=true",
                    self._target_name,
                    ingested,
                )
            return content
        if ingested == 0 and not self._finalizer.dimensions:
            logger.warning("review.finalizer.no_subagent_results target={}", self._target_name)
            result = self._finalizer.finalize(self._target_name)
            self._rendered = True
            self._rendered_report = result.report_markdown
            context.content_replaced = True
            logger.info(
                "review.finalizer.rendered target={} dimensions={} needs_confirmation={} errors={}",
                self._target_name,
                len(result.dimensions),
                len(result.needs_confirmation),
                len(result.errors),
            )
            return result.report_markdown
        result = self._finalizer.finalize(self._target_name)
        self._rendered = True
        self._rendered_report = result.report_markdown
        context.content_replaced = True
        logger.info(
            "review.finalizer.rendered target={} dimensions={} needs_confirmation={} errors={}",
            self._target_name,
            len(result.dimensions),
            len(result.needs_confirmation),
            len(result.errors),
        )
        return result.report_markdown
