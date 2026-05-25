
from __future__ import annotations

from typing import Any
from types import SimpleNamespace

import pytest

from nanobot.agent.tools.unsplash import UnsplashSearchTool, UnsplashSearchToolConfig
from nanobot.config.schema import Config, ProviderConfig
from nanobot.providers.unsplash_provider import UnsplashPhoto, UnsplashSearchResponse


class FakeUnsplashClient:
    instances: list["FakeUnsplashClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.instances.append(self)

    async def search_photos(self, **kwargs: Any) -> UnsplashSearchResponse:
        self.calls.append(kwargs)
        return UnsplashSearchResponse(
            query=kwargs["query"],
            total=1,
            total_pages=1,
            results=[
                UnsplashPhoto(
                    id="abc123",
                    description="A quiet beach",
                    alt_description="beach at sunset",
                    author_name="Jane Doe",
                    author_url="https://unsplash.com/@jane",
                    page_url="https://unsplash.com/photos/abc123",
                    small_url="https://images.unsplash.com/small.jpg",
                    regular_url="https://images.unsplash.com/regular.jpg",
                    thumb_url="https://images.unsplash.com/thumb.jpg",
                    download_location="https://api.unsplash.com/photos/abc123/download",
                    width=4000,
                    height=3000,
                    color="#ffffff",
                )
            ],
            raw={},
        )


@pytest.mark.asyncio
async def test_unsplash_tool_reports_missing_key() -> None:
    tool = UnsplashSearchTool(
        config=UnsplashSearchToolConfig(enabled=True),
        provider_config=SimpleNamespace(api_key=None, api_base=None),
    )

    result = await tool.execute(query="beach")

    assert result.startswith("Error: Unsplash API key is not configured")


@pytest.mark.asyncio
async def test_unsplash_tool_rejects_too_many_results() -> None:
    tool = UnsplashSearchTool(
        config=UnsplashSearchToolConfig(enabled=True, max_results=3),
        provider_config=SimpleNamespace(api_key="test-key", api_base=None),
    )

    result = await tool.execute(query="beach", count=5)

    assert "count exceeds" in result


@pytest.mark.asyncio
async def test_unsplash_tool_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeUnsplashClient.instances = []
    monkeypatch.setattr(
        "nanobot.agent.tools.unsplash.UnsplashClient",
        FakeUnsplashClient,
    )

    tool = UnsplashSearchTool(
        config=UnsplashSearchToolConfig(enabled=True),
        provider_config=SimpleNamespace(api_key="test-key", api_base=None),
    )

    result = await tool.execute(query="beach sunset", count=1, orientation="landscape")

    assert "Unsplash results for: beach sunset" in result
    assert "Jane Doe" in result
    assert "https://unsplash.com/photos/abc123" in result
    assert "https://images.unsplash.com/regular.jpg" in result

    fake = FakeUnsplashClient.instances[0]
    assert fake.kwargs["api_key"] == "test-key"
    assert fake.calls[0]["query"] == "beach sunset"
    assert fake.calls[0]["count"] == 1
    assert fake.calls[0]["orientation"] == "landscape"


#AI修改前
# 之前没有测试 from_config 是否把 providers.unsplash 注入到 Unsplash 工具。
#AI修改后
def test_unsplash_provider_config_flows_from_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.agent.conftest import make_provider
    from nanobot.agent.loop import AgentLoop

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.providers.unsplash = ProviderConfig(api_key="unsplash-key", api_base="https://example.test")

    monkeypatch.setattr(
        "nanobot.agent.loop.preset_helpers.make_preset_snapshot_loader",
        lambda *args, **kwargs: None,
    )

    #AI修改前
    # loop = AgentLoop.from_config(config, provider=make_provider())
    #AI修改后
    loop = AgentLoop.from_config(config, provider=make_provider(spec=False))
    tool = loop.tools.get("search_unsplash_images")

    assert isinstance(tool, UnsplashSearchTool)
    assert tool.provider_config.api_key == "unsplash-key"
    assert tool.provider_config.api_base == "https://example.test"
######
