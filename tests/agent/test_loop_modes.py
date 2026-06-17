from __future__ import annotations

from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop, TurnContext, TurnState
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
        raise AssertionError("code review context should not resolve")

    monkeypatch.setattr("nanobot.agent.loop.resolve_code_review_context", fail_review)

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


@pytest.mark.asyncio
async def test_agent_loop_ignores_legacy_math_qa_mode_metadata(tmp_path) -> None:
    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    session = Session(key="test:math")
    session.metadata["math_qa_mode"] = True

    await loop._run_agent_loop(
        [{"role": "user", "content": "求极限"}],
        session=session,
        session_key=session.key,
    )

    assert runner.initial_messages == [{"role": "user", "content": "求极限"}]


@pytest.mark.asyncio
async def test_agent_loop_review_mode_injects_code_review_context(tmp_path) -> None:
    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    session = Session(key="test:review")
    session.metadata["review_mode"] = True

    await loop._run_agent_loop(
        [{"role": "user", "content": "please review https://github.com/test/repo"}],
        session=session,
        session_key=session.key,
    )

    assert runner.initial_messages is not None
    assert runner.initial_messages[0]["role"] == "system"
    assert "You are CodeReviewAgent" in runner.initial_messages[0]["content"]
    assert "- Name: test/repo" in runner.initial_messages[0]["content"]


@pytest.mark.asyncio
async def test_agent_loop_review_message_metadata_is_visible_same_turn(tmp_path) -> None:
    from nanobot.bus.events import InboundMessage

    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    session = Session(key="websocket:review")
    msg = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="review",
        content="请审查登录逻辑",
        metadata={
            "review_target": "https://github.com/test/repo",
            "review_target_type": "github",
        },
    )
    ctx = TurnContext(
        msg=msg,
        session_key=session.key,
        state=TurnState.RESTORE,
        turn_id="turn",
        session=session,
    )

    await loop._state_restore(ctx)
    await loop._state_compact(ctx)
    await loop._state_build(ctx)
    await loop._state_run(ctx)

    assert runner.initial_messages is not None
    assert runner.initial_messages[0]["role"] == "system"
    assert "- Name: https://github.com/test/repo" in runner.initial_messages[0]["content"]
    assert "- Type: github" in runner.initial_messages[0]["content"]
