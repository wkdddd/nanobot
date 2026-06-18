from __future__ import annotations

from typing import Any

import pytest

from nanobot.agent.runner import AgentRunSpec, AgentRunner
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


@pytest.mark.asyncio
async def test_run_tool_logs_exception_and_preserves_model_error_payload(monkeypatch) -> None:
    log_calls: list[tuple[str, str]] = []

    def capture_exception(message: str, tool_name: str, call_id: str) -> None:
        log_calls.append((message.format(tool_name, call_id), call_id))

    monkeypatch.setattr("nanobot.agent.runner.logger.exception", capture_exception)

    tools = ToolRegistry()
    tools.register(FailingTool())
    runner = AgentRunner(DummyProvider())
    spec = AgentRunSpec(
        initial_messages=[],
        tools=tools,
        model="dummy",
        max_iterations=1,
        max_tool_result_chars=1000,
    )

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
