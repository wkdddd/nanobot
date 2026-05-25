# ##AI修改后
"""Tests for the Unsplash image search tool."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nanobot.agent.tools.unsplash import UnsplashSearchTool


def _response(status: int = 200, payload: dict[str, Any] | None = None) -> httpx.Response:
    response = httpx.Response(status, json=payload or {})
    response._request = httpx.Request("GET", "https://api.unsplash.com/search/photos")
    return response


@pytest.mark.asyncio
async def test_unsplash_search_requires_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UNSPLASH_API_KEY", raising=False)

    tool = UnsplashSearchTool()
    result = await tool.execute(query="minimal workspace")

    assert result == "Error: UNSPLASH_ACCESS_KEY is not configured."


@pytest.mark.asyncio
async def test_unsplash_search_calls_api_and_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    async def mock_get(self, url: str, **kwargs: Any) -> httpx.Response:
        calls["url"] = url
        calls["params"] = kwargs["params"]
        calls["headers"] = kwargs["headers"]
        return _response(
            payload={
                "page": 2,
                "per_page": 3,
                "total": 1,
                "total_pages": 1,
                "results": [
                    {
                        "id": "abc123",
                        "alt_description": "a calm desk beside a window",
                        "width": 4000,
                        "height": 3000,
                        "color": "#eeeeee",
                        "urls": {
                            "raw": "https://images.unsplash.com/raw",
                            "full": "https://images.unsplash.com/full",
                            "regular": "https://images.unsplash.com/regular",
                            "small": "https://images.unsplash.com/small",
                            "thumb": "https://images.unsplash.com/thumb",
                        },
                        "links": {
                            "html": "https://unsplash.com/photos/abc123",
                            "download_location": "https://api.unsplash.com/photos/abc123/download",
                        },
                        "user": {
                            "name": "Ada Lens",
                            "username": "adalens",
                            "links": {"html": "https://unsplash.com/@adalens"},
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = UnsplashSearchTool(access_key="test-key", user_agent="nanobot-test")

    result = await tool.execute(
        query="minimal workspace",
        count=3,
        page=2,
        orientation="landscape",
        order_by="latest",
        color="white",
        content_filter="high",
        lang="en",
    )
    data = json.loads(result)

    assert calls["url"] == "https://api.unsplash.com/search/photos"
    assert calls["headers"]["Authorization"] == "Client-ID test-key"
    assert calls["headers"]["User-Agent"] == "nanobot-test"
    assert calls["params"] == {
        "query": "minimal workspace",
        "page": 2,
        "per_page": 3,
        "order_by": "latest",
        "content_filter": "high",
        "orientation": "landscape",
        "color": "white",
        "lang": "en",
    }
    assert data["results"][0]["id"] == "abc123"
    assert data["results"][0]["urls"]["regular"] == "https://images.unsplash.com/regular"
    assert data["results"][0]["photographer"]["name"] == "Ada Lens"
    assert data["results"][0]["links"]["download_location"].endswith("/download")
    assert "Photo by Ada Lens" in data["results"][0]["attribution"]


@pytest.mark.asyncio
async def test_unsplash_search_reports_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_get(self, url: str, **kwargs: Any) -> httpx.Response:
        return _response(status=401, payload={"errors": ["OAuth error"]})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = UnsplashSearchTool(access_key="bad-key")

    result = await tool.execute(query="forest")

    assert result == "Error: Unsplash authentication failed. Check UNSPLASH_ACCESS_KEY."


@pytest.mark.asyncio
async def test_unsplash_search_reports_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_get(self, url: str, **kwargs: Any) -> httpx.Response:
        return _response(status=429, payload={"errors": ["Rate limit exceeded"]})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    tool = UnsplashSearchTool(access_key="test-key")

    result = await tool.execute(query="forest")

    assert result == "Error: Unsplash API rate limit reached. Retry later or reduce requests."
# ######
