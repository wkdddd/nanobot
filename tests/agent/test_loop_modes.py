from __future__ import annotations

from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunResult, AgentRunSpec
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.session.manager import Session


class DummyProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy"


class CapturingRunner:
    def __init__(self) -> None:
        self.initial_messages: list[dict[str, Any]] | None = None

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        self.initial_messages = list(spec.initial_messages)
        return AgentRunResult(final_content="ok", messages=spec.initial_messages)


@pytest.mark.asyncio
async def test_agent_loop_leaves_context_plain_when_specialist_modes_disabled(
    tmp_path,
    monkeypatch,
) -> None:
    async def fail_review(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("review context should not resolve")

    async def fail_math(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("math QA context should not resolve")

    monkeypatch.setattr("nanobot.agent.loop.resolve_review_context", fail_review)
    monkeypatch.setattr("nanobot.agent.loop.resolve_math_qa_context", fail_math)

    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    session = Session(key="test:plain")

    await loop._run_agent_loop(
        [{"role": "user", "content": "hello"}],
        session=session,
        session_key=session.key,
    )

    assert runner.initial_messages == [{"role": "user", "content": "hello"}]
