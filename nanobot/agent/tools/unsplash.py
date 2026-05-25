"""Unsplash image search tool."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

_DEFAULT_USER_AGENT = "nanobot-unsplash-tool/1.0"
_UNSPLASH_SEARCH_ENDPOINT = "https://api.unsplash.com/search/photos"


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _photo_to_result(photo: dict[str, Any]) -> dict[str, Any]:
    user = photo.get("user") or {}
    links = photo.get("links") or {}
    user_links = user.get("links") or {}
    urls = photo.get("urls") or {}
    description = photo.get("alt_description") or photo.get("description") or ""
    photographer = user.get("name") or user.get("username") or ""
    unsplash_url = links.get("html") or ""
    photographer_url = user_links.get("html") or ""

    return {
        "id": photo.get("id", ""),
        "description": description,
        "width": photo.get("width"),
        "height": photo.get("height"),
        "color": photo.get("color"),
        "urls": {
            "raw": urls.get("raw", ""),
            "full": urls.get("full", ""),
            "regular": urls.get("regular", ""),
            "small": urls.get("small", ""),
            "thumb": urls.get("thumb", ""),
        },
        "links": {
            "html": unsplash_url,
            "download_location": links.get("download_location", ""),
        },
        "photographer": {
            "name": photographer,
            "username": user.get("username", ""),
            "url": photographer_url,
        },
        "attribution": (
            f"Photo by {photographer} on Unsplash: {unsplash_url}"
            if photographer and unsplash_url
            else ""
        ),
    }


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "Image search text, e.g. 'minimal workspace', 'Tokyo night street', or 'forest trail'.",
            min_length=1,
        ),
        count=IntegerSchema(
            10,
            description="Number of photos to return (1-30).",
            minimum=1,
            maximum=30,
        ),
        page=IntegerSchema(
            1,
            description="Unsplash results page number.",
            minimum=1,
            maximum=100,
        ),
        orientation={
            "type": "string",
            "enum": ["landscape", "portrait", "squarish"],
            "description": "Optional image orientation filter.",
        },
        order_by={
            "type": "string",
            "enum": ["relevant", "latest"],
            "description": "Result ordering. Defaults to relevant.",
        },
        color=StringSchema(
            "Optional Unsplash color filter, e.g. black_and_white, black, white, yellow, orange, red, purple, magenta, green, teal, or blue.",
        ),
        content_filter={
            "type": "string",
            "enum": ["low", "high"],
            "description": "Unsplash content safety filter. Defaults to low.",
        },
        lang=StringSchema(
            "Optional ISO language code for the search query, e.g. en, zh, ja.",
            max_length=8,
        ),
        required=["query"],
    )
)
class UnsplashSearchTool(Tool):
    """Search Unsplash photos with official API credentials."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "unsplash_search"

    @property
    def description(self) -> str:
        return (
            "Search Unsplash for photos. Requires UNSPLASH_ACCESS_KEY. "
            "Returns image URLs, dimensions, photographer attribution, Unsplash page links, "
            "and download_location values for required download tracking."
        )

    @property
    def read_only(self) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        web_cfg = getattr(getattr(ctx, "config", None), "web", None)
        return cls(
            proxy=getattr(web_cfg, "proxy", None),
            user_agent=getattr(web_cfg, "user_agent", None),
        )

    def __init__(
        self,
        *,
        access_key: str | None = None,
        proxy: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.access_key = access_key
        self.proxy = proxy
        self.user_agent = user_agent or _DEFAULT_USER_AGENT

    def _access_key(self) -> str:
        return (
            self.access_key
            or os.environ.get("UNSPLASH_ACCESS_KEY", "")
            or os.environ.get("UNSPLASH_API_KEY", "")
        ).strip()

    async def execute(
        self,
        query: str,
        count: int = 10,
        page: int = 1,
        orientation: str | None = None,
        order_by: str | None = "relevant",
        color: str | None = None,
        content_filter: str | None = "low",
        lang: str | None = None,
        **kwargs: Any,
    ) -> str:
        access_key = self._access_key()
        if not access_key:
            return "Error: UNSPLASH_ACCESS_KEY is not configured."

        params: dict[str, Any] = {
            "query": query,
            "page": page,
            "per_page": count,
            "order_by": _clean_optional(order_by) or "relevant",
            "content_filter": _clean_optional(content_filter) or "low",
        }
        for key, value in {
            "orientation": orientation,
            "color": color,
            "lang": lang,
        }.items():
            cleaned = _clean_optional(value)
            if cleaned:
                params[key] = cleaned

        headers = {
            "Accept": "application/json",
            "Authorization": f"Client-ID {access_key}",
            "User-Agent": self.user_agent,
        }

        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=15.0) as client:
                response = await client.get(
                    _UNSPLASH_SEARCH_ENDPOINT,
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                return "Error: Unsplash authentication failed. Check UNSPLASH_ACCESS_KEY."
            if status == 429:
                return "Error: Unsplash API rate limit reached. Retry later or reduce requests."
            return f"Error: Unsplash search failed with HTTP {status}: {exc.response.text[:500]}"
        except httpx.RequestError as exc:
            return f"Error: Unsplash request failed: {exc}"
        except Exception as exc:
            return f"Error: Unsplash search failed: {exc}"

        data = response.json()
        payload = {
            "query": query,
            "page": data.get("page", page),
            "per_page": data.get("per_page", count),
            "total": data.get("total", 0),
            "total_pages": data.get("total_pages", 0),
            "results": [_photo_to_result(item) for item in data.get("results", [])],
            "note": (
                "If a photo is selected for project use or download, call its "
                "links.download_location endpoint once with the same Unsplash Authorization header."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

