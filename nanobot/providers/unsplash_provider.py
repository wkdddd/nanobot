"""unsplash search photo client"""
from __future__ import annotations
from dataclasses import dataclass

from typing import Any
import httpx
_DEFAULT_API_BASE="https://api.unsplash.com"
_DEFAULT_TIMEOUT_S=20.0
class UnsplashError(RuntimeError):
    """Raised when Unsplash cannot return image search results."""

@dataclass(frozen=True)
class UnsplashPhoto:
    id: str
    description: str
    alt_description: str
    author_name: str
    author_url: str
    page_url: str
    small_url: str
    regular_url: str
    thumb_url: str
    download_location: str
    width: int
    height: int
    color: str

@dataclass(frozen=True)
class UnsplashSearchResponse:
    query:str
    total: int
    total_pages: int
    results: list[UnsplashPhoto]
    raw: dict[str, Any]

class UnsplashClient:
    def __init__(
            self,
            *,
            api_key:str| None,
            api_base:str| None = None,
            timeout:float=_DEFAULT_TIMEOUT_S,
            client:httpx.AsyncClient| None = None
            ):
        self.api_key=api_key
        self.api_base=(api_base or _DEFAULT_API_BASE).rstrip("/")
        self.timeout=timeout
        self._client=client
    #AI修改前
    # async def search_photo(
    #AI修改后
    async def search_photos(
            self,
            *,
            query:str,
            count:int=5,
            page:int=1,
            order_by:str="relevant",
            orientation: str | None = None,
            color: str | None = None,
            content_filter: str = "high",
    )-> UnsplashSearchResponse:
        if not self.api_key:
            raise UnsplashError("Unsplash API key is not configured. Set providers.unsplash.apiKey.")      
        params: dict[str, Any] = {
            "query": query,
            "page": page,
            "per_page": count,
            "order_by": order_by,
            "content_filter": content_filter,
        }
        if orientation:
            params["orientation"] = orientation
        if color:
            params["color"] = color
        headers = {
            "Authorization": f"Client-ID {self.api_key}",
            "Accept-Version": "v1",
        }
        url = f"{self.api_base}/search/photos"
        if self._client is not None:
            response = await self._client.get(url, headers=headers, params=params)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:500]
            raise UnsplashError(f"Unsplash search failed: {detail}") from exc
        payload=response.json()
        results = [self._photo_from_payload(item) for item in payload.get("results") or []]
        return UnsplashSearchResponse(
            query=query,
            total=int(payload.get("total") or 0),
            total_pages=int(payload.get("total_pages") or 0),
            results=results,
            raw=payload,
        )
    def _photo_from_payload(self,item: dict[str, Any])-> UnsplashPhoto:
        urls = item.get("urls") or {}
        links = item.get("links") or {}
        user = item.get("user") or {}
        user_links = user.get("links") or {}

        return UnsplashPhoto(
        id=str(item.get("id") or ""),
        description=str(item.get("description") or ""),
        alt_description=str(item.get("alt_description") or ""),
        author_name=str(user.get("name") or ""),
        author_url=str(user_links.get("html") or ""),
        page_url=str(links.get("html") or ""),
        small_url=str(urls.get("small") or ""),
        regular_url=str(urls.get("regular") or ""),
        thumb_url=str(urls.get("thumb") or ""),
        download_location=str(links.get("download_location") or ""),
        width=int(item.get("width") or 0),
        height=int(item.get("height") or 0),
        color=str(item.get("color") or ""),
    )
