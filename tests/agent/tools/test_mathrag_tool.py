from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.mathrag import MathRAGTool
from nanobot.agent.tools.registry import ToolRegistry


def test_tool_loader_registers_mathrag() -> None:
    tools = ToolLoader().discover()
    assert any(tool.__name__ == "MathRAGTool" for tool in tools)


@pytest.mark.asyncio
async def test_mathrag_search_returns_json_hits(tmp_path, monkeypatch) -> None:
    kb_dir = tmp_path / ".nanobot" / "math_knowledge"
    kb_dir.mkdir(parents=True)
    (kb_dir / "calculus.md").write_text("# 洛必达法则\n\n适用于极限。", encoding="utf-8")

    tool = MathRAGTool(workspace=tmp_path)
    result = await tool.execute(action="search", query="洛必达法则", top_k=3)
    payload = json.loads(result)

    assert payload["action"] == "search"
    assert payload["hits"]
    assert any("洛必达法则" in hit["content"] for hit in payload["hits"])


@pytest.mark.asyncio
async def test_mathrag_convert_refreshes_index(tmp_path, monkeypatch) -> None:
    source = tmp_path / ".nanobot" / "math_knowledge" / "sample.md"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")

    tool = MathRAGTool(workspace=tmp_path)
    result = await tool.execute(action="convert", source_path=str(source))
    payload = json.loads(result)

    assert payload["action"] == "convert"
    assert payload["converted"][0]["ok"] is True
    assert payload["converted"][0]["markdown"].endswith("sample.md")


def test_tool_context_accepts_model_and_provider() -> None:
    ctx = ToolContext(config=SimpleNamespace(), workspace=".", provider=object(), model="m")
    assert ctx.provider is not None
    assert ctx.model == "m"
