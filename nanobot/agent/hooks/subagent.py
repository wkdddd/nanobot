"""Subagent execution hooks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from loguru import logger

from nanobot.agent.hooks.lifecycle import AgentHook, AgentHookContext


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float
    phase: str = "initializing"
    iteration: int = 0
    tool_events: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None


class SubagentHook(AgentHook):
    """Hook for subagent execution: log tool calls and update status."""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        # Force review subagents onto the provider streaming path so they avoid
        # long non-stream request timeouts while keeping the execution flow local.
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        if not delta:
            return
        self._stream_buf += delta

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info(
                "Subagent [{}] tool call: {}({})",
                self._task_id,
                tool_call.name,
                args_str[:200],
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)
