"""Build structured code review plans from metadata and user text."""
from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.review.beforeplan import (
    apply_policy_to_roles,
    extract_review_target,
    normalize_focus,
    normalize_mode,
    normalize_review_action,
    normalize_review_target_type,
    parse_repo_target,
    parse_target_paths,
    policy_for_depth,
)
from nanobot.agent.review.types import (
    LocalReviewScope,
    ReviewAction,
    ReviewMetaKey,
    ReviewPlan,
    ReviewTargetType,
)
from nanobot.agent.review.utils import (
    parse_github_scoped_target,
    parse_pr_target,
)
from nanobot.session.manager import Session


def _find_git_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _normalize_target_paths_for_scope(raw_paths: list[str], *, review_root: Path) -> tuple[list[str], str]:
    normalized: list[str] = []
    for raw in raw_paths:
        try:
            candidate = Path(raw).expanduser()
            resolved = candidate.resolve() if candidate.is_absolute() else (review_root / candidate).resolve()
            rel = _relative_posix(resolved, review_root)
        except (OSError, ValueError) as exc:
            return [], f"target_path_outside_review_root:{raw}:{exc}"
        if rel.startswith("../") or rel == "..":
            return [], f"target_path_outside_review_root:{raw}"
        normalized.append(rel)
    return list(dict.fromkeys(normalized)), ""


def _resolve_local_scope(target: str | None, target_paths: list[str]) -> tuple[LocalReviewScope | None, list[str], str]:
    if not target:
        return None, target_paths, "missing_target"
    try:
        resolved_target = Path(target).expanduser().resolve()
    except OSError as exc:
        return None, target_paths, f"target_resolve_failed:{exc}"
    if not resolved_target.exists():
        return None, target_paths, "target_not_found"

    if resolved_target.is_file():
        git_root = _find_git_root(resolved_target)
        review_root = git_root or resolved_target.parent
        inferred_paths = [_relative_posix(resolved_target, review_root)]
        if target_paths:
            scoped_paths, reason = _normalize_target_paths_for_scope(target_paths, review_root=review_root)
            if reason:
                return None, [], reason
            final_paths = scoped_paths
        else:
            final_paths = inferred_paths
        return (
            LocalReviewScope(
                kind="file",
                review_root=str(review_root),
                scope_paths=final_paths,
                target_path=_relative_posix(resolved_target, review_root),
                reason="file_target",
            ),
            final_paths,
            "file_target",
        )

    if resolved_target.is_dir():
        review_root = resolved_target
        if target_paths:
            scoped_paths, reason = _normalize_target_paths_for_scope(target_paths, review_root=review_root)
            if reason:
                return None, [], reason
            scope_paths = scoped_paths
            kind = "directory"
            reason = "directory_target_with_paths"
        else:
            scope_paths = []
            kind = "directory"
            reason = "directory_target"
        return (
            LocalReviewScope(
                kind=kind,
                review_root=str(review_root),
                scope_paths=scope_paths,
                target_path=".",
                reason=reason,
            ),
            scope_paths,
            reason,
        )
    return None, target_paths, "target_not_file_or_directory"


def _resolve_action(
    *,
    requested: ReviewAction,
    target: str | None,
    target_type: ReviewTargetType,
    target_paths: list[str],
    user_content: str,
) -> ReviewAction:
    pr_repo, _ = parse_pr_target(target)
    if pr_repo and requested == ReviewAction.REPO:
        return ReviewAction.DIFF
    return requested


def build_review_plan(
    *,
    target: str | None = None,
    user_content: str = "",
    focus: str | list[str] | None = None,
    depth: Any = "full",
    max_subagents: Any = 4,
    target_type: str | None = None,
    action: str | None = None,
    target_paths: Any = None,
    target_ref: str | None = None,
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
    normalized_ref: str | None = target_ref.strip() if isinstance(target_ref, str) and target_ref.strip() else None
    scoped_target = parse_github_scoped_target(target)
    if scoped_target is not None and target_type_value == "github":
        target_repo = scoped_target.repo
        normalized_ref = scoped_target.ref
        if scoped_target.path and scoped_target.path not in paths:
            paths.append(scoped_target.path)
    if target_repo is None and target_type_value == "github":
        target_repo = parse_repo_target(target)

    local_scope: LocalReviewScope | None = None
    scope_reason = ""
    if target_type_value == "local":
        local_scope, paths, scope_reason = _resolve_local_scope(target, paths)

    try:
        max_subagents_int = int(max_subagents)
    except (TypeError, ValueError):
        max_subagents_int = 4
    max_subagents_int = min(max(max_subagents_int, 1), 10)

    depth_value = normalize_mode(depth)
    policy = policy_for_depth(depth_value, requested_max_subagents=max_subagents_int)
    roles = apply_policy_to_roles(roles=roles, forced_focus=forced, policy=policy)
    max_subagents_int = policy.max_subagents

    plan = ReviewPlan(
        target=target,
        target_name=target_name or target,
        target_type=target_type_value,
        action=resolved_action,
        depth=depth_value,
        roles=roles,
        forced_focus=forced,
        max_subagents=max_subagents_int,
        user_requirements=user_content.strip(),
        target_repo=target_repo,
        pr_number=pr_number,
        target_paths=paths,
        target_ref=normalized_ref,
        local_scope=local_scope,
        prefetch_summary=prefetch_summary,
    )
    logger.info(
        "review.plan.done trace_id={} action={} target_type={} scope_kind={} scope_reason={} review_root={} forced_focus={} requested_focus={} roles={} allowed_dimensions={} user_requirements={} paths_count={} elapsed_ms={:.1f}",
        trace_id,
        plan.action.value,
        plan.target_type,
        plan.local_scope.kind if plan.local_scope else "",
        scope_reason,
        plan.local_scope.review_root if plan.local_scope else "",
        plan.forced_focus,
        focus,
        [role.name for role in plan.roles],
        [role.name for role in plan.roles],
        plan.user_requirements,
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
    progress_callback: Any | None = None,
) -> str:
    """Build the Review-mode system prompt from metadata or the user prompt."""
    from nanobot.agent.review.prefetch import maybe_prefetch_review_context
    from nanobot.agent.review.prompt import build_review_fallback_prompt, render_review_prompt

    user_content = latest_user_text(initial_messages)
    plan = build_review_plan(
        target=session_meta.get(ReviewMetaKey.TARGET) if isinstance(session_meta.get(ReviewMetaKey.TARGET), str) else None,
        user_content=user_content,
        focus=session_meta.get(ReviewMetaKey.FOCUS),
        depth=session_meta.get(ReviewMetaKey.MODE_VARIANT) or session_meta.get("review_mode_name") or "full",
        max_subagents=session_meta.get(ReviewMetaKey.MAX_SUBAGENTS) or 4,
        target_type=session_meta.get(ReviewMetaKey.TARGET_TYPE) if isinstance(session_meta.get(ReviewMetaKey.TARGET_TYPE), str) else None,
        action=session_meta.get(ReviewMetaKey.ACTION) if isinstance(session_meta.get(ReviewMetaKey.ACTION), str) else None,
        target_paths=session_meta.get(ReviewMetaKey.TARGET_PATHS),
        target_ref=session_meta.get(ReviewMetaKey.TARGET_REF) if isinstance(session_meta.get(ReviewMetaKey.TARGET_REF), str) else None,
    )
    if plan is None:
        return build_review_fallback_prompt()
    session_meta[ReviewMetaKey.TARGET_PATHS] = list(plan.target_paths)
    if plan.local_scope:
        session_meta[ReviewMetaKey.LOCAL_ROOT] = plan.local_scope.review_root
        local_root = Path(plan.local_scope.review_root)
        local_target = (
            local_root / plan.local_scope.target_path
            if plan.local_scope.target_path and plan.local_scope.target_path != "."
            else local_root
        )
        session_meta[ReviewMetaKey.LOCAL_TARGET] = str(local_target.resolve())
        session_meta[ReviewMetaKey.LOCAL_SCOPE_KIND] = plan.local_scope.kind
    else:
        session_meta.pop(ReviewMetaKey.LOCAL_ROOT, None)
        session_meta.pop(ReviewMetaKey.LOCAL_TARGET, None)
        session_meta.pop(ReviewMetaKey.LOCAL_SCOPE_KIND, None)
    prefetch_summary = await maybe_prefetch_review_context(
        plan,
        session_meta,
        progress_callback=progress_callback,
    )
    if prefetch_summary.summary:
        plan = replace(plan, prefetch_summary=prefetch_summary.summary)
        if plan.target_type == "github":
            session_meta[ReviewMetaKey.GITHUB_PREFETCH_READY] = True
    elif prefetch_summary.attempted:
        target_label = "GitHub" if plan.target_type == "github" else "repository"
        if plan.target_type == "github":
            session_meta[ReviewMetaKey.GITHUB_PREFETCH_READY] = True
        detail = f": {prefetch_summary.reason}" if prefetch_summary.reason else ""
        plan = replace(
            plan,
            prefetch_summary=(
                f"{target_label} evidence prefetch was already attempted for this review "
                f"and returned {prefetch_summary.status}{detail}. Do not call "
                f"{'github_review' if plan.target_type == 'github' else 'local_review'} again "
                "for the same target in this turn; continue with the available context and "
                "state any evidence limitations in the review."
            ),
        )
    session_meta[ReviewMetaKey.ALLOWED_DIMENSIONS] = [role.name for role in plan.roles]
    return render_review_prompt(plan)


def build_code_review_context(
    *,
    target: str | None = None,
    user_content: str = "",
    focus: str | None = None,
    mode: str = "full",
    max_subagents: int = 4,
    target_type: str | None = None,
    action: str | None = None,
    target_paths: Any = None,
    target_ref: str | None = None,
) -> str:
    from nanobot.agent.review.prompt import build_review_fallback_prompt, render_review_prompt

    plan = build_review_plan(
        target=target,
        user_content=user_content,
        focus=focus,
        depth=mode,
        max_subagents=max_subagents,
        target_type=target_type,
        action=action,
        target_paths=target_paths,
        target_ref=target_ref,
    )
    if plan is None:
        return build_review_fallback_prompt()
    return render_review_prompt(plan)


def apply_review_metadata_from_message(
    session: Session,
    metadata: dict[str, Any] | None,
) -> bool:
    """Apply structured Review metadata before this turn runs."""
    if not isinstance(metadata, dict):
        return False
    keys = (
        ReviewMetaKey.TARGET,
        ReviewMetaKey.TARGET_TYPE,
        ReviewMetaKey.MODE_VARIANT,
        ReviewMetaKey.ACTION,
        ReviewMetaKey.FOCUS,
        ReviewMetaKey.TARGET_PATHS,
        ReviewMetaKey.TARGET_REF,
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

    _set_meta(ReviewMetaKey.MODE, True)

    raw_mode = metadata.get(ReviewMetaKey.MODE_VARIANT)
    if isinstance(raw_mode, str):
        mode = raw_mode.strip().lower()
        if mode in {"quick", "full", "deep"}:
            _set_meta(ReviewMetaKey.MODE_VARIANT, mode)
        else:
            _pop_meta(ReviewMetaKey.MODE_VARIANT)

    raw_target = metadata.get(ReviewMetaKey.TARGET)
    if isinstance(raw_target, str):
        target = raw_target.strip()
        if target:
            _set_meta(ReviewMetaKey.TARGET, target)
        else:
            _pop_meta(ReviewMetaKey.TARGET)

    raw_action = metadata.get(ReviewMetaKey.ACTION)
    if isinstance(raw_action, str):
        try:
            _set_meta(ReviewMetaKey.ACTION, normalize_review_action(raw_action).value)
        except ValueError:
            _pop_meta(ReviewMetaKey.ACTION)

    raw_focus = metadata.get(ReviewMetaKey.FOCUS)
    if isinstance(raw_focus, (str, list)):
        _set_meta(ReviewMetaKey.FOCUS, raw_focus)

    paths = parse_target_paths(metadata.get(ReviewMetaKey.TARGET_PATHS))
    if paths:
        _set_meta(ReviewMetaKey.TARGET_PATHS, paths)
    elif ReviewMetaKey.TARGET_PATHS in metadata:
        _pop_meta(ReviewMetaKey.TARGET_PATHS)

    raw_ref = metadata.get(ReviewMetaKey.TARGET_REF)
    if isinstance(raw_ref, str) and raw_ref.strip():
        _set_meta(ReviewMetaKey.TARGET_REF, raw_ref.strip())
    elif ReviewMetaKey.TARGET_REF in metadata:
        _pop_meta(ReviewMetaKey.TARGET_REF)

    target_type = normalize_review_target_type(
        metadata.get(ReviewMetaKey.TARGET_TYPE) if isinstance(metadata.get(ReviewMetaKey.TARGET_TYPE), str) else None,
        session.metadata.get(ReviewMetaKey.TARGET),
    )
    if target_type:
        _set_meta(ReviewMetaKey.TARGET_TYPE, target_type)
    else:
        _pop_meta(ReviewMetaKey.TARGET_TYPE)

    return changed
