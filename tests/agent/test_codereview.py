from __future__ import annotations

import json

import pytest

from nanobot.agent.codereview import (
    ALL_REVIEW_ROLES,
    DEFAULT_REVIEW_ROLES,
    OPTIONAL_REVIEW_ROLES,
    SEVERITY_ORDER,
    Finding,
    ReviewReport,
    build_code_review_context,
    build_review_prompt,
    normalize_focus,
)
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse


class DummyProvider(LLMProvider):
    async def chat(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy"


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


def test_review_role_sets() -> None:
    assert len(DEFAULT_REVIEW_ROLES) == 4
    assert len(OPTIONAL_REVIEW_ROLES) == 3
    assert len(ALL_REVIEW_ROLES) == 7


def test_build_review_prompt_quick_mode_caps_subagents() -> None:
    roles, _ = normalize_focus(None)
    prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=6,
        forced=True,
        mode="quick",
    )

    assert "QUICK review" in prompt
    assert "critical and high" in prompt
    assert "up to 2 total" in prompt


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


def test_build_review_prompt_json_and_markdown_formats() -> None:
    roles, _ = normalize_focus(None)
    json_prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        output_format="json",
    )
    markdown_prompt = build_review_prompt(
        target_url="https://github.com/test/repo",
        target_name="test/repo",
        roles=roles,
        max_subagents=4,
        forced=False,
        output_format="markdown",
    )

    assert "JSON object" in json_prompt
    assert '"findings"' in json_prompt
    assert "## Code Review Report" in markdown_prompt


def test_build_code_review_context_extracts_github_target() -> None:
    prompt = build_code_review_context(
        user_content="please review https://github.com/test/repo.",
    )

    assert "- Name: test/repo" in prompt
    assert "- URL: https://github.com/test/repo" in prompt
    assert "- Type: github" in prompt


def test_build_code_review_context_uses_local_target_type() -> None:
    prompt = build_code_review_context(
        target=r"C:\work\repo\src\app.py",
        target_type="local",
    )

    assert "- Type: local" in prompt
    assert "Access `C:\\work\\repo\\src\\app.py` directly" in prompt
    assert "do not clone" in prompt


def test_build_code_review_context_extracts_local_path_inside_prompt() -> None:
    prompt = build_code_review_context(
        user_content=r"请审查 C:\work\repo\src\app.py 的登录逻辑",
    )

    assert "- URL: C:\\work\\repo\\src\\app.py" in prompt
    assert "- Type: local" in prompt


def test_build_code_review_context_uses_github_target_type() -> None:
    prompt = build_code_review_context(
        target="https://github.com/test/repo",
        target_type="github",
    )

    assert "- Type: github" in prompt
    assert "repo_review(target_type='github'" in prompt
    assert "github_repo_read" not in prompt


def test_build_code_review_context_returns_fallback_without_target() -> None:
    prompt = build_code_review_context(user_content="how should I do a review?")

    assert "Code review workflow is active" in prompt


def test_report_to_json_has_correct_structure() -> None:
    report = ReviewReport(
        target="https://github.com/test/repo",
        mode="full",
        dimensions=["security", "tests"],
        summary="No critical issues found.",
        findings=[
            Finding("high", "src/auth.py", 10, "Weak hash", "Brute force", "Use bcrypt"),
            Finding("low", "src/utils.py", None, "Unused import", "Dead code", "Remove it"),
        ],
        checks_performed=["security", "tests"],
        checks_skipped=[],
        recommendations=["Fix the weak hash"],
    )
    data = json.loads(report.to_json())

    assert data["target"] == "https://github.com/test/repo"
    assert data["mode"] == "full"
    assert data["statistics"]["high"] == 1
    assert data["statistics"]["low"] == 1
    assert data["statistics"]["critical"] == 0
    assert len(data["findings"]) == 2


def test_max_severity_and_order() -> None:
    report = ReviewReport(
        target="repo",
        mode="full",
        dimensions=[],
        summary="",
        findings=[
            Finding("low", "a.py", None, "t", "i", "r"),
            Finding("high", "b.py", None, "t", "i", "r"),
            Finding("medium", "c.py", None, "t", "i", "r"),
        ],
    )

    assert report.max_severity() == "high"
    assert SEVERITY_ORDER == ("critical", "high", "medium", "low")


def test_code_review_is_not_registered_as_a_tool(tmp_path) -> None:
    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)

    assert "code_review" not in loop.tool_names
    assert "github_repo_read" not in loop.tool_names
    assert "repo_review" in loop.tool_names
