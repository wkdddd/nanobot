"""Data types for GitHub PR auto review tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

AutoTaskStatus = Literal["queued", "running", "completed", "failed", "skipped"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_repo(value: str) -> str:
    repo = value.strip()
    if repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/")
    if repo.startswith("http://github.com/"):
        repo = repo.removeprefix("http://github.com/")
    if repo.startswith("github.com/"):
        repo = repo.removeprefix("github.com/")
    repo = repo.removesuffix(".git").strip("/")
    parts = [part for part in repo.split("/") if part]
    if len(parts) < 2:
        raise ValueError("repo must be in owner/name format")
    return f"{parts[0]}/{parts[1]}"


@dataclass(slots=True)
class AutoTask:
    id: str
    name: str
    repo: str
    enabled: bool = True
    mode: str | None = None
    focus: list[str] | None = None
    target_paths: list[str] = field(default_factory=list)
    max_subagents: int | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_run_at: str | None = None
    last_status: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "repo": self.repo,
            "enabled": self.enabled,
            "mode": self.mode,
            "focus": self.focus,
            "target_paths": list(self.target_paths),
            "max_subagents": self.max_subagents,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutoTask":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data.get("repo") or "GitHub PR review"),
            repo=normalize_repo(str(data["repo"])),
            enabled=bool(data.get("enabled", True)),
            mode=str(data["mode"]) if data.get("mode") else None,
            focus=[str(item) for item in data["focus"]] if isinstance(data.get("focus"), list) else None,
            target_paths=[str(item) for item in data.get("target_paths", []) if str(item).strip()],
            max_subagents=int(data["max_subagents"]) if data.get("max_subagents") is not None else None,
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
            last_run_at=str(data["last_run_at"]) if data.get("last_run_at") else None,
            last_status=str(data["last_status"]) if data.get("last_status") else None,
        )


@dataclass(slots=True)
class AutoTaskRun:
    run_id: str
    task_id: str
    repo: str
    pr_number: int
    pr_title: str
    pr_url: str
    status: AutoTaskStatus
    session_key: str | None = None
    chat_id: str | None = None
    reason: str = ""
    report_markdown: str = ""
    report_filename: str = ""
    started_at: str = field(default_factory=now_iso)
    completed_at: str | None = None

    @property
    def report_available(self) -> bool:
        return bool(self.report_markdown.strip())

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "pr_url": self.pr_url,
            "status": self.status,
            "session_key": self.session_key,
            "chat_id": self.chat_id,
            "reason": self.reason,
            "report_available": self.report_available,
            "report_filename": self.report_filename,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def to_storage_dict(self) -> dict:
        data = self.to_dict()
        data["report_markdown"] = self.report_markdown
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AutoTaskRun":
        return cls(
            run_id=str(data["run_id"]),
            task_id=str(data["task_id"]),
            repo=normalize_repo(str(data["repo"])),
            pr_number=int(data["pr_number"]),
            pr_title=str(data.get("pr_title") or ""),
            pr_url=str(data.get("pr_url") or ""),
            status=str(data.get("status") or "queued"),  # type: ignore[arg-type]
            session_key=str(data["session_key"]) if data.get("session_key") else None,
            chat_id=str(data["chat_id"]) if data.get("chat_id") else None,
            reason=str(data.get("reason") or ""),
            report_markdown=str(data.get("report_markdown") or ""),
            report_filename=str(data.get("report_filename") or ""),
            started_at=str(data.get("started_at") or now_iso()),
            completed_at=str(data["completed_at"]) if data.get("completed_at") else None,
        )
