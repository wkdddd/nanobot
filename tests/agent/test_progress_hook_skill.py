"""Tests for skill usage detection in AgentProgressHook.after_iteration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.agent.progress_hook import AgentProgressHook, _SKILL_PATH_RE


@dataclass
class FakeToolCall:
    id: str = "call_1"
    name: str = "read_file"
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeContext:
    iteration: int = 1
    messages: list = field(default_factory=list)
    response: Any = None
    usage: dict = field(default_factory=dict)
    tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    tool_events: list = field(default_factory=list)
    streamed_content: bool = False
    streamed_reasoning: bool = False
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None


@pytest.fixture
def hook():
    return AgentProgressHook(on_progress=AsyncMock())


class TestSkillPathRegex:
    def test_posix_path(self):
        m = _SKILL_PATH_RE.search("/workspace/skills/rag/SKILL.md")
        assert m and m.group(1) == "rag"

    def test_windows_path(self):
        m = _SKILL_PATH_RE.search("C:\\Users\\x\\skills\\demo\\SKILL.md")
        assert m and m.group(1) == "demo"

    def test_builtin_path(self):
        m = _SKILL_PATH_RE.search("/app/nanobot/skills/github/SKILL.md")
        assert m and m.group(1) == "github"

    def test_no_match_normal_file(self):
        assert _SKILL_PATH_RE.search("/workspace/src/main.py") is None

    def test_no_match_partial(self):
        assert _SKILL_PATH_RE.search("/workspace/skills/rag/README.md") is None


class TestAfterIterationSkillLog:
    @pytest.mark.asyncio
    async def test_skill_log_on_successful_read(self, hook):
        ctx = FakeContext(
            tool_calls=[FakeToolCall(arguments={"path": "/workspace/skills/rag/SKILL.md"})],
            tool_results=["1|---\n2|name: rag\n3|..."],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            mock_logger.info.assert_called_with("using the skill {}", "rag")

    @pytest.mark.asyncio
    async def test_no_log_on_read_error(self, hook):
        ctx = FakeContext(
            tool_calls=[FakeToolCall(arguments={"path": "/workspace/skills/rag/SKILL.md"})],
            tool_results=["Error: File not found: skills/rag/SKILL.md"],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_log_on_dedup(self, hook):
        ctx = FakeContext(
            tool_calls=[FakeToolCall(arguments={"path": "/workspace/skills/rag/SKILL.md"})],
            tool_results=["[File unchanged since last read: /workspace/skills/rag/SKILL.md]"],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_log_on_normal_file(self, hook):
        ctx = FakeContext(
            tool_calls=[FakeToolCall(arguments={"path": "/workspace/src/main.py"})],
            tool_results=["1|import os\n2|..."],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_skills_one_iteration(self, hook):
        ctx = FakeContext(
            tool_calls=[
                FakeToolCall(id="c1", arguments={"path": "/skills/rag/SKILL.md"}),
                FakeToolCall(id="c2", arguments={"path": "/skills/github/SKILL.md"}),
            ],
            tool_results=["1|rag content", "1|github content"],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            assert mock_logger.info.call_count == 2

    @pytest.mark.asyncio
    async def test_windows_path_detection(self, hook):
        ctx = FakeContext(
            tool_calls=[
                FakeToolCall(arguments={"path": "C:\\nanobot\\skills\\demo\\SKILL.md"})
            ],
            tool_results=["1|---\n2|name: demo"],
        )
        with patch("nanobot.agent.progress_hook.logger") as mock_logger:
            await hook.after_iteration(ctx)
            mock_logger.info.assert_called_with("using the skill {}", "demo")
