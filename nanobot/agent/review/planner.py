"""Build structured code review plans from metadata and user text."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import replace
from typing import Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.review.types import (
    ALL_REVIEW_ROLES,
    DEFAULT_REVIEW_ROLES,
    ReviewAction,
    ReviewMode,
    ReviewPlan,
    ReviewRole,
    ReviewTargetType,
    review_action_values,
)
from nanobot.session.manager import Session

_GITHUB_RE = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s.,;!?)#]+)", re.I)
_GITHUB_PR_RE = re.compile(
    r"(?:https?://)?github\.com/([^/\s]+)/([^/\s.,;!?)#]+)/pull/(\d+)",
    re.I,
)
_LOCAL_CHANGED_HINT_RE = re.compile(
    r"(?i)\b(changed|changes|diff|local\s+diff|working\s+tree|unstaged|staged|untracked|当前改动|本地改动|变更)\b"
)


def normalize_focus(raw: str | list[str] | None) -> tuple[list[ReviewRole], bool]:
    forced = True
    if not raw:
        forced = False
        return list(DEFAULT_REVIEW_ROLES.values()), forced

    selected: list[ReviewRole] = []
    items = raw if isinstance(raw, list) else raw.split(",")
    for item in items:
        key = item.strip().lower()
        if not key:
            continue
        role = ALL_REVIEW_ROLES.get(key)
        if role is None:
            allowed = ", ".join(sorted(ALL_REVIEW_ROLES))
            raise ValueError(f"Unknown review focus '{key}'. Available focus values: {allowed}")
        if role not in selected:
            selected.append(role)
    return selected or list(DEFAULT_REVIEW_ROLES.values()), forced


def infer_review_target_type(target: str | None) -> str | None:
    if not target:
        return None
    if _GITHUB_RE.search(target):
        return "github"
    return "local"


def normalize_review_target_type(raw: str | None, target: str | None = None) -> str | None:
    value = (raw or "").strip().lower()
    if value in {"auto", "local", "github"}:
        return value
    return infer_review_target_type(target)


def normalize_review_action(raw: str | None) -> ReviewAction:
    value = (raw or ReviewAction.FULL_REPO.value).strip().lower()
    try:
        return ReviewAction(value)
    except ValueError:
        pass
    allowed = ", ".join(review_action_values())
    raise ValueError(f"Unknown review action '{value}'. Available action values: {allowed}")


def normalize_mode(raw: Any) -> ReviewMode:
    value = str(raw or "full").strip().lower()
    if value in {"quick", "full", "deep"}:
        return value  # type: ignore[return-value]
    return "full"


def parse_target_paths(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    else:
        values = re.split(r"[\n,]+", str(raw))
        values = [item.strip() for item in values]
    return [item for item in dict.fromkeys(values) if item]


def extract_review_target(text: str) -> tuple[str, str] | None:
    github_match = _GITHUB_RE.search(text)
    if github_match:
        owner, repo = github_match.group(1), github_match.group(2).removesuffix(".git")
        target = f"https://github.com/{owner}/{repo}"
        if pr_match := _GITHUB_PR_RE.search(text):
            target = f"https://github.com/{pr_match.group(1)}/{pr_match.group(2)}/pull/{pr_match.group(3)}"
        return target, f"{owner}/{repo}"

    local_match = re.search(r"(?i)(?:review|code\s*review|审查|评审)\s+([^\s，。；;\r\n]+)", text)
    if local_match:
        target = local_match.group(1).strip(" `\"'")
        if target:
            return target, target
    path_match = re.search(
        r"(?P<path>(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|~[\\/]|/)[^\s`\"'，。；;]+)",
        text,
    )
    if path_match:
        target = path_match.group("path").rstrip(".,;:!?)）】]")
        if target:
            return target, target
    stripped = text.strip().strip(" `\"'")
    if stripped and not re.search(r"\s", stripped):
        if re.match(r"^[A-Za-z]:[\\/]", stripped) or stripped.startswith(("/", "./", "../", "~")):
            return stripped, stripped
    return None


def parse_pr_target(target: str | None) -> tuple[str | None, int | None]:
    if not target:
        return None, None
    match = _GITHUB_PR_RE.search(target.strip())
    if not match:
        return None, None
    owner, repo, pr_number = match.group(1), match.group(2).removesuffix(".git"), int(match.group(3))
    return f"{owner}/{repo}", pr_number


def parse_repo_target(target: str | None) -> str | None:
    if not target:
        return None
    match = _GITHUB_RE.search(target.strip())
    if match:
        return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
    parts = target.strip().rstrip("/").split("/")
    if len(parts) == 2 and all(parts):
        return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return None


def _resolve_action(
    *,
    requested: ReviewAction,
    target: str | None,
    target_type: ReviewTargetType,
    target_paths: list[str],
    user_content: str,
) -> ReviewAction:
    pr_repo, _ = parse_pr_target(target)
    if pr_repo and requested == ReviewAction.FULL_REPO:
        return ReviewAction.PR_DIFF
    return requested


def build_review_plan(
    *,
    target: str | None = None,
    user_content: str = "",
    focus: str | list[str] | None = None,
    mode: Any = "full",
    output_format: str = "markdown",
    max_subagents: Any = 4,
    target_type: str | None = None,
    action: str | None = None,
    target_paths: Any = None,
    prefetch_summary: str | None = None,
) -> ReviewPlan | None:
    trace_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    logger.info("review.plan.start trace_id={} target={} action={}", trace_id, target, action or "auto")
    target_name = target
    if not target:
        extracted = extract_review_target(user_content)
        if extracted:
            target, target_name = extracted

    paths = parse_target_paths(target_paths)
    if not target and paths:
        target = paths[0]
        target_name = target

    if not target:
        logger.info(
            "review.plan.done trace_id={} status=fallback elapsed_ms={:.1f}",
            trace_id,
            (time.perf_counter() - started) * 1000,
        )
        return None

    roles, forced = normalize_focus(focus)
    normalized_type = normalize_review_target_type(target_type, target)
    if normalized_type not in {"github", "local"}:
        normalized_type = normalize_review_target_type(None, target) or "auto"
    target_type_value: ReviewTargetType = normalized_type  # type: ignore[assignment]
    requested_action = normalize_review_action(action)
    resolved_action = _resolve_action(
        requested=requested_action,
        target=target,
        target_type=target_type_value,
        target_paths=paths,
        user_content=user_content,
    )
    target_repo, pr_number = parse_pr_target(target)
    if target_repo is None and target_type_value == "github":
        target_repo = parse_repo_target(target)

    if output_format not in {"markdown", "json"}:
        output_format = "markdown"
    try:
        max_subagents_int = int(max_subagents)
    except (TypeError, ValueError):
        max_subagents_int = 4
    max_subagents_int = min(max(max_subagents_int, 1), 10)

    plan = ReviewPlan(
        target=target,
        target_name=target_name or target,
        target_type=target_type_value,
        action=resolved_action,
        mode=normalize_mode(mode),
        roles=roles,
        forced_focus=forced,
        output_format=output_format,
        max_subagents=max_subagents_int,
        user_requirements=user_content.strip(),
        target_repo=target_repo,
        pr_number=pr_number,
        target_paths=paths,
        prefetch_summary=prefetch_summary,
    )
    logger.info(
        "review.plan.done trace_id={} action={} target_type={} forced_focus={} paths_count={} elapsed_ms={:.1f}",
        trace_id,
        plan.action.value,
        plan.target_type,
        plan.forced_focus,
        len(plan.target_paths),
        (time.perf_counter() - started) * 1000,
    )
    return plan


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    """Return the latest user text, without the appended runtime metadata."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts)
        else:
            text = ""
        if ContextBuilder._RUNTIME_CONTEXT_TAG in text:
            text = text.split(ContextBuilder._RUNTIME_CONTEXT_TAG, 1)[0]
        return text.strip()
    return ""


async def resolve_code_review_context(
    initial_messages: list[dict[str, Any]],
    session_meta: dict[str, Any],
) -> str:
    """Build the Review-mode system prompt from metadata or the user prompt."""
    from nanobot.agent.review.prefetch import maybe_prefetch_review_context
    from nanobot.agent.review.prompt import build_review_fallback_prompt, render_review_prompt

    user_content = latest_user_text(initial_messages)
    plan = build_review_plan(
        target=session_meta.get("review_target") if isinstance(session_meta.get("review_target"), str) else None,
        user_content=user_content,
        focus=session_meta.get("review_focus"),
        mode=session_meta.get("review_mode_variant") or session_meta.get("review_mode_name") or "full",
        output_format=session_meta.get("review_output_format") or "markdown",
        max_subagents=session_meta.get("review_max_subagents") or 4,
        target_type=session_meta.get("review_target_type") if isinstance(session_meta.get("review_target_type"), str) else None,
        action=session_meta.get("review_action") if isinstance(session_meta.get("review_action"), str) else None,
        target_paths=session_meta.get("review_target_paths"),
    )
    if plan is None:
        return build_review_fallback_prompt()
    prefetch_summary = await maybe_prefetch_review_context(plan, session_meta)
    if prefetch_summary:
        plan = replace(plan, prefetch_summary=prefetch_summary)
    return render_review_prompt(plan)


def build_code_review_context(
    *,
    target: str | None = None,
    user_content: str = "",
    focus: str | None = None,
    mode: str = "full",
    output_format: str = "markdown",
    max_subagents: int = 4,
    target_type: str | None = None,
    action: str | None = None,
    target_paths: Any = None,
) -> str:
    from nanobot.agent.review.prompt import build_review_fallback_prompt, render_review_prompt

    plan = build_review_plan(
        target=target,
        user_content=user_content,
        focus=focus,
        mode=mode,
        output_format=output_format,
        max_subagents=max_subagents,
        target_type=target_type,
        action=action,
        target_paths=target_paths,
    )
    if plan is None:
        return build_review_fallback_prompt()
    return render_review_prompt(plan)


def build_review_prompt(
    *,
    target_url: str,
    target_name: str,
    roles: list[ReviewRole],
    max_subagents: int,
    forced: bool,
    mode: str = "full",
    output_format: str = "markdown",
    target_type: str | None = None,
    action: str | None = None,
) -> str:
    from nanobot.agent.review.prompt import render_review_prompt

    plan = ReviewPlan(
        target=target_url,
        target_name=target_name,
        target_type=(normalize_review_target_type(target_type, target_url) or "auto"),  # type: ignore[arg-type]
        action=normalize_review_action(action or ReviewAction.FULL_REPO.value),
        mode=normalize_mode(mode),
        roles=roles,
        forced_focus=forced,
        output_format=output_format if output_format in {"markdown", "json"} else "markdown",
        max_subagents=min(max(int(max_subagents), 1), 10),
        target_repo=parse_repo_target(target_url),
        pr_number=parse_pr_target(target_url)[1],
    )
    return render_review_prompt(plan)


def apply_review_metadata_from_message(
    session: Session,
    metadata: dict[str, Any] | None,
) -> bool:
    """Apply structured Review metadata before this turn runs."""
    if not isinstance(metadata, dict):
        return False
    keys = (
        "review_target",
        "review_target_type",
        "review_mode_variant",
        "review_action",
        "review_focus",
        "review_target_paths",
    )
    if not any(key in metadata for key in keys):
        return False

    changed = False

    def _set_meta(key: str, value: Any) -> None:
        nonlocal changed
        if session.metadata.get(key) != value:
            session.metadata[key] = value
            changed = True

    def _pop_meta(key: str) -> None:
        nonlocal changed
        if key in session.metadata:
            session.metadata.pop(key, None)
            changed = True

    _set_meta("review_mode", True)

    raw_mode = metadata.get("review_mode_variant")
    if isinstance(raw_mode, str):
        mode = raw_mode.strip().lower()
        if mode in {"quick", "full", "deep"}:
            _set_meta("review_mode_variant", mode)
        else:
            _pop_meta("review_mode_variant")

    raw_target = metadata.get("review_target")
    if isinstance(raw_target, str):
        target = raw_target.strip()
        if target:
            _set_meta("review_target", target)
        else:
            _pop_meta("review_target")

    raw_action = metadata.get("review_action")
    if isinstance(raw_action, str):
        try:
            _set_meta("review_action", normalize_review_action(raw_action).value)
        except ValueError:
            _pop_meta("review_action")

    raw_focus = metadata.get("review_focus")
    if isinstance(raw_focus, (str, list)):
        _set_meta("review_focus", raw_focus)

    paths = parse_target_paths(metadata.get("review_target_paths"))
    if paths:
        _set_meta("review_target_paths", paths)
    elif "review_target_paths" in metadata:
        _pop_meta("review_target_paths")

    target_type = normalize_review_target_type(
        metadata.get("review_target_type") if isinstance(metadata.get("review_target_type"), str) else None,
        session.metadata.get("review_target"),
    )
    if target_type:
        _set_meta("review_target_type", target_type)
    else:
        _pop_meta("review_target_type")

    return changed
