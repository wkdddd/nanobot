"""Persistent storage for auto review tasks and run records."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.auto_tasks.types import AutoTask, AutoTaskRun, normalize_repo, now_iso
from nanobot.config.paths import get_runtime_subdir
from nanobot.utils.log_style import log_event


class AutoTaskStore:
    """UTF-8 JSON store for GitHub PR auto-review tasks."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_runtime_subdir("auto_tasks") / "tasks.json")

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {"tasks": [], "runs": []}

    @classmethod
    def _normalize_data(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return cls._empty_data()
        normalized = dict(data)
        if not isinstance(normalized.get("tasks"), list):
            normalized["tasks"] = []
        if not isinstance(normalized.get("runs"), list):
            normalized["runs"] = []
        return normalized

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty_data()
        try:
            return self._normalize_data(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception as exc:
            log_event(
                logger,
                "warning",
                "auto_task.store.read_failed",
                status="failed",
                path=self.path,
                reason=exc,
            )
            return self._empty_data()

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def list_tasks(self) -> list[AutoTask]:
        data = self._read()
        tasks = []
        for item in data.get("tasks", []):
            if isinstance(item, dict):
                try:
                    tasks.append(AutoTask.from_dict(item))
                except Exception as exc:
                    log_event(
                        logger,
                        "warning",
                        "auto_task.store.bad_task",
                        status="failed",
                        reason=exc,
                    )
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def get_task(self, task_id: str) -> AutoTask | None:
        return next((task for task in self.list_tasks() if task.id == task_id), None)

    def create_task(self, payload: dict[str, Any]) -> AutoTask:
        now = now_iso()
        task = AutoTask(
            id=uuid.uuid4().hex[:12],
            name=str(payload.get("name") or payload.get("repo") or "GitHub PR review").strip(),
            repo=normalize_repo(str(payload.get("repo") or "")),
            enabled=bool(payload.get("enabled", True)),
            mode=str(payload["mode"]) if payload.get("mode") else None,
            focus=[str(item).strip() for item in payload.get("focus", []) if str(item).strip()]
            if isinstance(payload.get("focus"), list)
            else None,
            target_paths=[str(item).strip() for item in payload.get("target_paths", []) if str(item).strip()]
            if isinstance(payload.get("target_paths"), list)
            else [],
            max_subagents=int(payload["max_subagents"]) if payload.get("max_subagents") is not None else None,
            created_at=now,
            updated_at=now,
        )
        data = self._read()
        data.setdefault("tasks", []).append(task.to_dict())
        data.setdefault("runs", data.get("runs", []))
        self._write(data)
        log_event(
            logger,
            "info",
            "auto_task.created",
            status="success",
            task_id=task.id,
            repo=task.repo,
        )
        return task

    def update_task(self, task_id: str, payload: dict[str, Any]) -> AutoTask | None:
        data = self._read()
        updated: AutoTask | None = None
        rows = []
        for item in data.get("tasks", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("id")) != task_id:
                rows.append(item)
                continue
            next_item = dict(item)
            for key in ("name", "enabled", "mode", "focus", "target_paths", "max_subagents"):
                if key in payload:
                    next_item[key] = payload[key]
            if "repo" in payload:
                next_item["repo"] = normalize_repo(str(payload["repo"]))
            next_item["updated_at"] = now_iso()
            updated = AutoTask.from_dict(next_item)
            rows.append(updated.to_dict())
        if updated is None:
            return None
        data["tasks"] = rows
        self._write(data)
        log_event(
            logger,
            "info",
            "auto_task.updated",
            status="success",
            task_id=updated.id,
            repo=updated.repo,
        )
        return updated

    def delete_task(self, task_id: str) -> bool:
        data = self._read()
        before = len(data.get("tasks", []))
        data["tasks"] = [
            item for item in data.get("tasks", [])
            if not (isinstance(item, dict) and str(item.get("id")) == task_id)
        ]
        deleted = len(data["tasks"]) != before
        if deleted:
            self._write(data)
            log_event(logger, "info", "auto_task.deleted", status="success", task_id=task_id)
        return deleted

    def list_runs(self, task_id: str | None = None) -> list[AutoTaskRun]:
        data = self._read()
        runs = []
        for item in data.get("runs", []):
            if not isinstance(item, dict):
                continue
            if task_id is not None and str(item.get("task_id")) != task_id:
                continue
            try:
                runs.append(AutoTaskRun.from_dict(item))
            except Exception as exc:
                log_event(
                    logger,
                    "warning",
                    "auto_task.store.bad_run",
                    status="failed",
                    reason=exc,
                )
        return sorted(runs, key=lambda item: item.started_at, reverse=True)

    def get_run(self, task_id: str, run_id: str) -> AutoTaskRun | None:
        return next(
            (run for run in self.list_runs(task_id) if run.run_id == run_id),
            None,
        )

    def save_run(self, run: AutoTaskRun) -> None:
        data = self._read()
        rows = []
        replaced = False
        for item in data.get("runs", []):
            if isinstance(item, dict) and str(item.get("run_id")) == run.run_id:
                rows.append(run.to_storage_dict())
                replaced = True
            else:
                rows.append(item)
        if not replaced:
            rows.append(run.to_storage_dict())
        data["runs"] = rows
        tasks = []
        for item in data.get("tasks", []):
            if isinstance(item, dict) and str(item.get("id")) == run.task_id:
                item = dict(item)
                item["last_run_at"] = run.started_at
                item["last_status"] = run.status
                item["updated_at"] = now_iso()
            tasks.append(item)
        data["tasks"] = tasks
        self._write(data)
