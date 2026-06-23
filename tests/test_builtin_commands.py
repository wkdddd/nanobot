from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_stop
from nanobot.command.router import CommandContext


@pytest.mark.asyncio
async def test_stop_uses_effective_context_key() -> None:
    cancelled: list[str] = []

    async def cancel(key: str) -> int:
        cancelled.append(key)
        return 1

    msg = InboundMessage(
        channel="websocket",
        sender_id="client",
        chat_id="chat",
        content="/stop",
    )

    result = await cmd_stop(
        CommandContext(
            msg=msg,
            session=None,
            key="__unified__",
            raw="/stop",
            loop=SimpleNamespace(_cancel_active_tasks=cancel),
        )
    )

    assert cancelled == ["__unified__"]
    assert result.content == "Stopped 1 task(s)."
