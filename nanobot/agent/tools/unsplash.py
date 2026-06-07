"""Unsplash image search tool."""
from pydantic import Field
from nanobot.config.schema import Base
from typing import Any
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.providers.unsplash_provider import UnsplashClient, UnsplashError, UnsplashSearchResponse
class UnsplashSearchToolConfig(Base):
    """Unsplash search tool configuration.
    Args:
        enabled: bool = False
        default_count: int = Field(default=5, ge=1, le=10)
        max_results: int = Field(default=10, ge=1, le=30)
        content_filter: str = "high"
    """

    enabled: bool = True
    default_count: int = Field(default=5, ge=1, le=10)
    max_results: int = Field(default=10, ge=1, le=30)
    content_filter: str = "high"

@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Image search query.", min_length=1),
        count=IntegerSchema(
            description="Number of image results to return.",
            minimum=1,
            maximum=10,
        ),
        orientation=StringSchema(
            "Optional orientation filter.",
            enum=("landscape", "portrait", "squarish"),
            nullable=True,
        ),
        color=StringSchema(
            "Optional color filter.",
            enum=(
                "black_and_white",
                "black",
                "white",
                "yellow",
                "orange",
                "red",
                "purple",
                "magenta",
                "green",
                "teal",
                "blue",
            ),
            nullable=True,
        ),
        order_by=StringSchema(
            "Sort order.",
            enum=("relevant", "latest"),
            nullable=True,
        ),
        page=IntegerSchema(
            description="Page number to retrieve.",
            minimum=1,
            maximum=100,
        ),
        required=["query"],
    )
)
class UnsplashSearchTool(Tool):
    """Search Unsplash for real photo assets."""
    config_key = "unsplash"
    @classmethod
    def config_cls(cls):
        return UnsplashSearchToolConfig
    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.config.unsplash_search.enabled
    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            config=ctx.config.unsplash_search,
            provider_config=ctx.unsplash_provider_config,
        )
    def __init__(self,
                 *,
                 config:UnsplashSearchToolConfig,
                 provider_config:Any
                 ):
        super().__init__()
        self.config=config
        self.provider_config=provider_config
    @property
    def name(self):
        #AI修改前
        # return "unsplash"
        #AI修改后
        return "search_unsplash_images"
    @property
    def description(self) -> str:
        return (
            "Search Unsplash for real photo assets. Returns image URLs, "
            "photographer attribution, Unsplash page links, and metadata."
        )
    @property
    def read_only(self) -> bool:
        return True
    
    async def execute(
        self,
        query: str,
        count: int | None = None,
        orientation: str | None = None,
        color: str | None = None,
        order_by: str | None = None,
        page: int | None = None,
        **kwargs: Any,
    ) -> str:
        api_key = getattr(self.provider_config, "api_key", None)
        if not api_key:
            return "Error: Unsplash API key is not configured. Set providers.unsplash.apiKey."
        requested = count or self.config.default_count
        if requested > self.config.max_results:
            return f"Error: count exceeds tools.unsplashSearch.maxResults ({self.config.max_results})"
        client = UnsplashClient(
            api_key=api_key,
            api_base=getattr(self.provider_config, "api_base", None),
        )
        try:
            #AI修改前
            # response=await client.search_photo(
            #AI修改后
            response=await client.search_photos(
                query=query,
                count=requested,
                page=page or 1,
                order_by=order_by or "relevant",
                orientation=orientation,
                color=color,
                content_filter=self.config.content_filter,
            )
        except UnsplashError as exc:
            return f"Error: {exc}"
        
        return self._format_unsplash_results(response)
    
    def _format_unsplash_results(self,response: UnsplashSearchResponse) -> str:
        if not response.results:
            return f"No Unsplash results for: {response.query}"
        lines = [
        f"Unsplash results for: {response.query}",
        f"Total: {response.total} results across {response.total_pages} pages",
        "",
    ]
        for index,data in enumerate(response.results,1):
            description=data.alt_description or data.description or "no description"
            lines.extend(
            [
                f"{index}. {description}",
                f"   Author: {data.author_name}",
                f"   Author page: {data.author_url}",
                f"   Unsplash page: {data.page_url}",
                f"   Image: {data.regular_url or data.small_url}",
                f"   Thumbnail: {data.thumb_url}",
                f"   Size: {data.width}x{data.height}",
                f"   Download tracking URL: {data.download_location}",
                "",
            ]
        )
        return "\n".join(lines).strip()
