from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop, TurnContext, TurnState
from nanobot.agent.runner import AgentRunResult, AgentRunSpec
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config, _resolve_tool_config_refs
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


class InjectionRunner:
    def __init__(self) -> None:
        self.injected: list[dict[str, Any]] | None = None

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        assert spec.injection_callback is not None
        self.injected = await spec.injection_callback(limit=3)
        return AgentRunResult(
            final_content="ok",
            messages=list(spec.initial_messages) + list(self.injected),
            had_injections=bool(self.injected),
        )


class RunningSubagents:
    def get_running_count_by_session(self, session_key: str) -> int:
        return 1


def _config(data: dict[str, Any]) -> Config:
    _resolve_tool_config_refs()
    return Config.model_validate(data)


def test_agent_loop_applies_configured_subagent_concurrency(tmp_path) -> None:
    config = _config(
        {
            "agents": {
                "defaults": {
                    "workspace": str(tmp_path),
                    "maxConcurrentSubagents": 5,
                }
            }
        }
    )
    loop = AgentLoop.from_config(config, bus=MessageBus(), provider=DummyProvider())

    assert loop.subagents.max_concurrent_subagents == 5


def test_config_accepts_subagent_concurrency_below_five() -> None:
    config = _config(
        {
            "agents": {
                "defaults": {
                    "maxConcurrentSubagents": 2,
                }
            }
        }
    )

    assert config.agents.defaults.max_concurrent_subagents == 2


def test_agent_loop_state_machine_has_no_review_finalize_state() -> None:
    assert "FINALIZE" not in TurnState.__members__
    assert all(
        state.name != "FINALIZE"
        for transition in AgentLoop._TRANSITIONS.values()
        for state in [transition]
    )


@pytest.mark.asyncio
async def test_agent_loop_always_injects_review_context(
    tmp_path,
    monkeypatch,
) -> None:
    """Review context is always resolved regardless of session metadata."""
    async def mock_review(*args: Any, **kwargs: Any) -> str:
        return "review system prompt"

    monkeypatch.setattr("nanobot.agent.loop.resolve_code_review_context", mock_review)

    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    session = Session(key="test:plain")

    await loop._run_agent_loop(
        [{"role": "user", "content": "hello"}],
        session=session,
        session_key=session.key,
    )

    assert runner.initial_messages[0] == {"role": "system", "content": "review system prompt"}
    assert runner.initial_messages[1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_agent_loop_ignores_legacy_math_qa_mode_metadata(tmp_path) -> None:
    """Legacy math_qa_mode metadata does not alter behavior — review context is still injected."""
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

    # Review context is always injected as the first system message
    assert runner.initial_messages[0]["role"] == "system"
    assert runner.initial_messages[1] == {"role": "user", "content": "求极限"}


@pytest.mark.asyncio
async def test_agent_loop_pending_drain_does_not_wait_for_running_subagents(tmp_path) -> None:
    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = InjectionRunner()
    loop.runner = runner
    loop.subagents = RunningSubagents()
    session = Session(key="test:pending")
    pending: asyncio.Queue = asyncio.Queue()

    await asyncio.wait_for(
        loop._run_agent_loop(
            [{"role": "user", "content": "hello"}],
            session=session,
            session_key=session.key,
            pending_queue=pending,
        ),
        timeout=0.1,
    )

    assert runner.injected == []


def test_invalid_max_concurrent_requests_falls_back_to_default(monkeypatch) -> None:
    warnings: list[str] = []

    def capture_warning(message: str, raw: str) -> None:
        warnings.append(message.format(raw))

    monkeypatch.setenv("NANOBOT_MAX_CONCURRENT_REQUESTS", "not-an-int")
    monkeypatch.setattr("nanobot.agent.loop.logger.warning", capture_warning)

    assert AgentLoop._parse_max_concurrent_requests() == 3
    assert warnings == ["Invalid NANOBOT_MAX_CONCURRENT_REQUESTS='not-an-int'; using default 3"]


def test_cleanup_session_lock_removes_idle_lock() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    lock = asyncio.Lock()
    loop._session_locks = {"session": lock}
    loop._pending_queues = {}
    loop._active_tasks = {}

    loop._cleanup_session_lock("session", lock)

    assert loop._session_locks == {}


def test_sanitize_persisted_blocks_converts_non_dict_blocks() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop.max_tool_result_chars = 20

    result = loop._sanitize_persisted_blocks(["hello", b"raw", 123])

    assert result == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "[binary content omit\n... (truncated)"},
        {"type": "text", "text": "123"},
    ]


def test_session_history_preserves_subagent_result_metadata() -> None:
    session = Session(key="test:review")
    session.add_message(
        "assistant",
        "wrapped result",
        injected_event="subagent_result",
        subagent_task_id="task",
        subagent_label="security",
        subagent_status="ok",
        subagent_result="[]",
    )

    history = session.get_history()

    assert history == [{
        "role": "assistant",
        "content": "wrapped result",
        "_metadata": {
            "injected_event": "subagent_result",
            "subagent_task_id": "task",
            "subagent_label": "security",
            "subagent_status": "ok",
            "subagent_result": "[]",
        },
    }]


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
            "review_focus": ["dependency"],
            "review_mode_variant": "quick",
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
    assert session.metadata["allowed_review_dimensions"] == ["dependency"]
    assert "Dependency Reviewer" in runner.initial_messages[0]["content"]
    assert "Security Reviewer" not in runner.initial_messages[0]["content"]


@pytest.mark.asyncio
async def test_process_system_message_accepts_agent_loop_return_shape(tmp_path) -> None:
    from nanobot.bus.events import InboundMessage

    loop = AgentLoop(MessageBus(), DummyProvider(), tmp_path)
    runner = CapturingRunner()
    loop.runner = runner
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="websocket:parent",
        content="subagent result",
    )

    response = await loop._process_system_message(msg)

    assert response is not None
    assert response.content == "ok"
