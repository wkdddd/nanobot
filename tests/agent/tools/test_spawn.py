from __future__ import annotations

import pytest

from nanobot.agent.review.types import ReviewMetaKey
from nanobot.agent.tools.context import RequestContext
from nanobot.agent.tools.spawn import SpawnTool


class FakeSubagentManager:
    max_concurrent_subagents = 4

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_running_count(self) -> int:
        return 0

    async def spawn(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "started"


@pytest.mark.asyncio
async def test_spawn_allows_selected_review_dimension_label() -> None:
    manager = FakeSubagentManager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    tool.set_context(RequestContext(
        channel="websocket",
        chat_id="chat",
        metadata={ReviewMetaKey.ALLOWED_DIMENSIONS: ["dependency"]},
    ))

    result = await tool.execute(task="review dependencies", label="Dependency Reviewer")

    assert result == "started"
    assert manager.calls[0]["label"] == "dependency"


@pytest.mark.asyncio
async def test_spawn_rejects_unselected_review_dimension() -> None:
    manager = FakeSubagentManager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    tool.set_context(RequestContext(
        channel="websocket",
        chat_id="chat",
        metadata={ReviewMetaKey.ALLOWED_DIMENSIONS: ["dependency"]},
    ))

    result = await tool.execute(task="review security", label="Security Reviewer")

    assert "not allowed" in result
    assert "dependency" in result
    assert manager.calls == []


@pytest.mark.asyncio
async def test_spawn_rejects_missing_label_when_review_dimensions_are_forced() -> None:
    manager = FakeSubagentManager()
    tool = SpawnTool(manager)  # type: ignore[arg-type]
    tool.set_context(RequestContext(
        channel="websocket",
        chat_id="chat",
        metadata={ReviewMetaKey.ALLOWED_DIMENSIONS: ["dependency"]},
    ))

    result = await tool.execute(task="review dependencies")

    assert "requires label" in result
    assert manager.calls == []
