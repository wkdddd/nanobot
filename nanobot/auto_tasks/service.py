"""Service layer for GitHub PR auto-review tasks."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from nanobot.auto_tasks.github import GitHubPullRequestEvent
from nanobot.auto_tasks.store import AutoTaskStore
from nanobot.auto_tasks.types import AutoTask, AutoTaskRun, now_iso
from nanobot.config.schema import Config
from nanobot.utils.log_style import log_event

ReviewStarter = Callable[[dict[str, Any]], Awaitable[dict[str, str]]]


class AutoTaskService:
    """Coordinate auto task storage and review triggering."""

    def __init__(
        self,
        config: Config,
        store: AutoTaskStore,
        *,
        review_starter: ReviewStarter | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.review_starter = review_starter

    def list_tasks(self) -> list[AutoTask]:
        return self.store.list_tasks()

    def create_task(self, payload: dict[str, Any]) -> AutoTask:
        return self.store.create_task(payload)

    def update_task(self, task_id: str, payload: dict[str, Any]) -> AutoTask | None:
        return self.store.update_task(task_id, payload)

    def delete_task(self, task_id: str) -> bool:
        return self.store.delete_task(task_id)

    def list_runs(self, task_id: str) -> list[AutoTaskRun]:
        return self.store.list_runs(task_id)

    def get_run(self, task_id: str, run_id: str) -> AutoTaskRun | None:
        return self.store.get_run(task_id, run_id)

    def _resolve_mode(self, task: AutoTask) -> str:
        return (
            task.mode
            or self.config.review.auto_tasks.default_mode
            or self.config.review.default_mode
            or "full"
        )

    def _resolve_focus(self, task: AutoTask) -> list[str]:
        if task.focus is not None:
            return task.focus
        if self.config.review.auto_tasks.default_focus is not None:
            return list(self.config.review.auto_tasks.default_focus)
        return list(self.config.review.default_focus)

    def matching_tasks(self, event: GitHubPullRequestEvent) -> list[AutoTask]:
        return [
            task for task in self.store.list_tasks()
            if task.enabled and task.repo.lower() == event.repo.lower()
        ]

    async def trigger_for_event(self, event: GitHubPullRequestEvent) -> list[AutoTaskRun]:
        settings = self.config.review.auto_tasks
        if not settings.enabled:
            log_event(logger, "info", "auto_task.webhook.skip", status="skipped", reason="disabled")
            return []
        if event.action not in settings.allowed_actions:
            log_event(
                logger,
                "info",
                "auto_task.webhook.skip",
                status="skipped",
                repo=event.repo,
                pr=event.pr_number,
                action=event.action,
                reason="action_not_allowed",
            )
            return []
        if settings.ignore_drafts and event.draft:
            log_event(
                logger,
                "info",
                "auto_task.webhook.skip",
                status="skipped",
                repo=event.repo,
                pr=event.pr_number,
                reason="draft_pr",
            )
            return []
        tasks = self.matching_tasks(event)
        log_event(
            logger,
            "info",
            "auto_task.webhook.matched",
            status="success",
            repo=event.repo,
            pr=event.pr_number,
            tasks=len(tasks),
        )
        runs = []
        for task in tasks:
            runs.append(await self.run_task(task, event))
        return runs

    async def run_task(self, task: AutoTask, event: GitHubPullRequestEvent) -> AutoTaskRun:
        run = AutoTaskRun(
            run_id=uuid.uuid4().hex[:12],
            task_id=task.id,
            repo=task.repo,
            pr_number=event.pr_number,
            pr_title=event.pr_title,
            pr_url=event.pr_url,
            status="queued",
            report_filename=f"review-{task.repo.replace('/', '-')}-pr-{event.pr_number}-{uuid.uuid4().hex[:6]}.md",
        )
        self.store.save_run(run)
        log_event(
            logger,
            "info",
            "auto_task.run.created",
            status="success",
            task_id=task.id,
            run_id=run.run_id,
            repo=task.repo,
            pr=event.pr_number,
        )
        if self.review_starter is None:
            run.status = "failed"
            run.reason = "review starter unavailable"
            run.completed_at = now_iso()
            self.store.save_run(run)
            return run

        try:
            run.status = "running"
            self.store.save_run(run)
            result = await self.review_starter(
                {
                    "target": event.pr_url,
                    "target_type": "github",
                    "action": "diff",
                    "mode": self._resolve_mode(task),
                    "focus": self._resolve_focus(task),
                    "target_paths": list(task.target_paths),
                    "content": f"Auto review GitHub PR #{event.pr_number}: {event.pr_title}".strip(),
                    "metadata": {
                        "auto_task_id": task.id,
                        "auto_task_run_id": run.run_id,
                        "github_repo": task.repo,
                        "github_pr_number": event.pr_number,
                    },
                }
            )
            run.chat_id = result.get("chat_id")
            run.session_key = result.get("session_key")
            run.status = "running"
            self.store.save_run(run)
            log_event(
                logger,
                "info",
                "auto_task.run.started_review",
                status="success",
                task_id=task.id,
                run_id=run.run_id,
                session=run.session_key,
            )
        except Exception as exc:
            logger.exception(
                "auto_task.run.start_failed task_id={} run_id={} repo={} pr={}",
                task.id,
                run.run_id,
                task.repo,
                event.pr_number,
            )
            run.status = "failed"
            run.reason = str(exc)
            run.completed_at = now_iso()
            self.store.save_run(run)
        return run
