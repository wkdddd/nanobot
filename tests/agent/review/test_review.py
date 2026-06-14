from __future__ import annotations

import pytest

from nanobot.agent.review.prompts import build_review_prompt, resolve_review_context
from nanobot.agent.review.roles import (
    ALL_REVIEW_ROLES,
    DEFAULT_REVIEW_ROLES,
    OPTIONAL_REVIEW_ROLES,
    normalize_focus,
)


def test_normalize_focus_defaults_to_four_roles_without_forcing() -> None:
    roles, forced = normalize_focus(None)

    assert [role.name for role in roles] == ["security", "tests", "architecture", "performance"]
    assert forced is False


def test_normalize_focus_selects_requested_roles_and_deduplicates() -> None:
    roles, forced = normalize_focus("security, tests, security")

    assert [role.name for role in roles] == ["security", "tests"]
    assert forced is True


def test_normalize_focus_rejects_unknown_focus() -> None:
    with pytest.raises(ValueError, match="Unknown review focus 'bogus'"):
        normalize_focus("security,bogus")


def test_normalize_focus_accepts_optional_roles() -> None:
    roles, forced = normalize_focus("bug-risk,maintainability,dependency")

    assert [role.name for role in roles] == ["bug-risk", "maintainability", "dependency"]
    assert forced is True


def test_normalize_focus_mixes_default_and_optional() -> None:
    roles, forced = normalize_focus("security,bug-risk")

    assert [role.name for role in roles] == ["security", "bug-risk"]
    assert forced is True


def test_default_roles_has_four_entries() -> None:
    assert len(DEFAULT_REVIEW_ROLES) == 4


def test_optional_roles_has_three_entries() -> None:
    assert len(OPTIONAL_REVIEW_ROLES) == 3


def test_all_review_roles_has_seven_entries() -> None:
    assert len(ALL_REVIEW_ROLES) == 7


# --- Prompt generation tests ---


def test_build_review_prompt_quick_mode_mentions_speed() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        mode="quick",
    )
    assert "QUICK review" in prompt
    assert "critical and high" in prompt


def test_build_review_prompt_deep_mode_mentions_thorough() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        mode="deep",
    )
    assert "DEEP review" in prompt
    assert "thorough" in prompt.lower()


def test_build_review_prompt_json_format_contains_schema() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        output_format="json",
    )
    assert "JSON object" in prompt
    assert '"findings"' in prompt


def test_build_review_prompt_markdown_format_contains_template() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        output_format="markdown",
    )
    assert "## Code Review Report" in prompt


def test_build_review_prompt_quick_caps_subagents() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=6,
        forced=True,
        mode="quick",
    )
    assert "up to 2 total" in prompt


@pytest.mark.asyncio
async def test_resolve_review_context_prefers_prebuilt_prompt() -> None:
    prompt = await resolve_review_context(
        [{"role": "user", "content": "review https://github.com/test/repo"}],
        {"review_prompt": "prebuilt"},
    )

    assert prompt == "prebuilt"


@pytest.mark.asyncio
async def test_resolve_review_context_detects_github_target() -> None:
    prompt = await resolve_review_context(
        [{"role": "user", "content": "please review https://github.com/test/repo."}],
        {},
    )

    assert prompt is not None
    assert "- Name: test/repo" in prompt
    assert "- URL: https://github.com/test/repo" in prompt


@pytest.mark.asyncio
async def test_resolve_review_context_returns_fallback_without_target() -> None:
    prompt = await resolve_review_context(
        [{"role": "user", "content": "how should I do a review?"}],
        {},
    )

    assert prompt is not None
    assert "Review mode is active" in prompt
