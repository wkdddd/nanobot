"""GitHub webhook parsing and signature validation for auto review tasks."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from nanobot.auto_tasks.types import normalize_repo


@dataclass(frozen=True, slots=True)
class GitHubPullRequestEvent:
    action: str
    repo: str
    pr_number: int
    pr_title: str
    pr_url: str
    draft: bool


def verify_github_signature(*, secret: str, body: bytes, signature: str | None) -> bool:
    if not secret.strip() or not signature:
        return False
    prefix = "sha256="
    if not signature.startswith(prefix):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"{prefix}{digest}", signature)


def parse_pull_request_event(payload: dict[str, Any]) -> GitHubPullRequestEvent:
    action = str(payload.get("action") or "").strip()
    repo_obj = payload.get("repository")
    pr_obj = payload.get("pull_request")
    if not isinstance(repo_obj, dict) or not isinstance(pr_obj, dict):
        raise ValueError("payload is not a pull_request event")
    full_name = str(repo_obj.get("full_name") or "").strip()
    number = int(pr_obj.get("number") or payload.get("number") or 0)
    if not full_name or number <= 0:
        raise ValueError("payload is missing repository or pull request number")
    return GitHubPullRequestEvent(
        action=action,
        repo=normalize_repo(full_name),
        pr_number=number,
        pr_title=str(pr_obj.get("title") or ""),
        pr_url=str(pr_obj.get("html_url") or f"https://github.com/{full_name}/pull/{number}"),
        draft=bool(pr_obj.get("draft", False)),
    )
