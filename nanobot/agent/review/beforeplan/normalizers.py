"""Normalize review inputs before building a review plan."""
from __future__ import annotations

from typing import Any

from nanobot.agent.review.beforeplan.targets import infer_review_target_type
from nanobot.agent.review.types import (
    ALL_REVIEW_ROLES,
    DEFAULT_REVIEW_ROLES,
    ReviewAction,
    ReviewDepth,
    ReviewRole,
    review_action_values,
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


def normalize_review_target_type(raw: str | None, target: str | None = None) -> str | None:
    value = (raw or "").strip().lower()
    if value in {"auto", "local", "github"}:
        return value
    return infer_review_target_type(target)


def normalize_review_action(raw: str | None) -> ReviewAction:
    value = (raw or ReviewAction.REPO.value).strip().lower()
    try:
        return ReviewAction(value)
    except ValueError:
        pass
    allowed = ", ".join(review_action_values())
    raise ValueError(f"Unknown review action '{value}'. Available action values: {allowed}")


def normalize_mode(raw: Any) -> ReviewDepth:
    value = str(raw or "full").strip().lower()
    if value in {"quick", "full", "deep"}:
        return value  # type: ignore[return-value]
    return "full"

