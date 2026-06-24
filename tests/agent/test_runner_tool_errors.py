from __future__ import annotations

from typing import Any

import pytest

from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.agent.hooks.lifecycle import AgentHook, AgentHookContext
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


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


class ReplacingHook(AgentHook):
    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        context.content_replaced = True
        return "REPORT"


class FailingTool(Tool):
    @property
    def name(self) -> str:
        return "fail_tool"

    @property
    def description(self) -> str:
        return "Always fails."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        raise ValueError("boom")


class SystemExitTool(Tool):
    @property
    def name(self) -> str:
        return "system_exit_tool"

    @property
    def description(self) -> str:
        return "Raises SystemExit."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        raise SystemExit("stop")


class ErrorTextTool(Tool):
    @property
    def name(self) -> str:
        return "error_text_tool"

    @property
    def description(self) -> str:
        return "Returns a legitimate result that begins with Error."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return "Error handling best practices: keep the real result intact."


def make_spec(tools: ToolRegistry | None = None, **overrides: Any) -> AgentRunSpec:
    values: dict[str, Any] = {
        "initial_messages": [],
        "tools": tools or ToolRegistry(),
        "model": "dummy",
        "max_iterations": 1,
        "max_tool_result_chars": 1000,
    }
    values.update(overrides)
    return AgentRunSpec(**values)


@pytest.mark.asyncio
async def test_run_tool_logs_exception_and_preserves_model_error_payload(monkeypatch) -> None:
    log_calls: list[tuple[str, str]] = []

    def capture_exception(message: str, tool_name: str, call_id: str) -> None:
        log_calls.append((message.format(tool_name, call_id), call_id))

    monkeypatch.setattr("nanobot.agent.runner.logger.exception", capture_exception)

    tools = ToolRegistry()
    tools.register(FailingTool())
    runner = AgentRunner(DummyProvider())
    spec = make_spec(tools)

    result, event, error = await runner._run_tool(
        spec,
        ToolCallRequest(id="call_1", name="fail_tool", arguments={}),
        external_lookup_counts={},
        workspace_violation_counts={},
    )

    assert result == "Error: ValueError: boom"
    assert event == {
        "name": "fail_tool",
        "status": "error",
        "detail": "ValueError: boom",
    }
    assert error is None
    assert log_calls == [("Tool 'fail_tool' execution failed for call_id=call_1", "call_1")]


@pytest.mark.asyncio
async def test_replaced_final_content_does_not_drain_injections() -> None:
    calls = 0

    async def injection_callback(**kwargs: Any) -> list[dict[str, str]]:
        nonlocal calls
        calls += 1
        return [{"role": "user", "content": "late system summary request"}]

    runner = AgentRunner(DummyProvider())
    result = await runner.run(make_spec(
        hook=ReplacingHook(),
        injection_callback=injection_callback,
        max_iterations=2,
    ))

    assert result.final_content == "REPORT"
    assert result.content_replaced is True
    assert calls == 0


@pytest.mark.asyncio
async def test_run_tool_does_not_catch_system_exit() -> None:
    tools = ToolRegistry()
    tools.register(SystemExitTool())
    runner = AgentRunner(DummyProvider())

    with pytest.raises(SystemExit):
        await runner._run_tool(
            make_spec(tools),
            ToolCallRequest(id="call_1", name="system_exit_tool", arguments={}),
            external_lookup_counts={},
            workspace_violation_counts={},
        )


@pytest.mark.asyncio
async def test_run_tool_does_not_treat_error_word_as_error_status() -> None:
    tools = ToolRegistry()
    tools.register(ErrorTextTool())
    runner = AgentRunner(DummyProvider())

    result, event, error = await runner._run_tool(
        make_spec(tools),
        ToolCallRequest(id="call_1", name="error_text_tool", arguments={}),
        external_lookup_counts={},
        workspace_violation_counts={},
    )

    assert result == "Error handling best practices: keep the real result intact."
    assert event["status"] == "ok"
    assert error is None


@pytest.mark.asyncio
async def test_drain_injections_falls_back_when_signature_is_unavailable(monkeypatch) -> None:
    class OpaqueInjectionCallback:
        async def __call__(self, *, limit: int) -> list[dict[str, str]]:
            return [{"role": "user", "content": f"limit={limit}"}]

    monkeypatch.setattr(
        "nanobot.agent.runner.inspect.signature",
        lambda _callback: (_ for _ in ()).throw(ValueError("opaque")),
    )

    runner = AgentRunner(DummyProvider())
    injected = await runner._drain_injections(
        make_spec(injection_callback=OpaqueInjectionCallback())
    )

    assert injected == [{"role": "user", "content": "limit=3"}]
