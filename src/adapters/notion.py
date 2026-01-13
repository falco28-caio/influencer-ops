from dataclasses import dataclass
from datetime import datetime
from typing import Any

from notion_client import AsyncClient
from tenacity import retry, stop_after_attempt, wait_exponential

from src.adapters.base import BaseAdapter
from src.core.config import settings


@dataclass
class NotionPage:
    """Represents a Notion page (SOP or Template)."""

    page_id: str
    title: str
    content: str
    category: str | None
    tags: list[str]
    last_edited: datetime
    properties: dict[str, Any]


class NotionAdapter(BaseAdapter):
    """Adapter for Notion API operations (read-only for SOPs)."""

    def __init__(self) -> None:
        super().__init__()
        self._client: AsyncClient | None = None

    async def initialize(self) -> None:
        """Initialize Notion client."""
        api_key = settings.notion_api_key.get_secret_value()
        if not api_key:
            raise ValueError("Notion API key not configured")

        self._client = AsyncClient(auth=api_key)
        self._initialized = True
        self.logger.info("Notion adapter initialized")

    async def health_check(self) -> bool:
        """Check Notion connection."""
        if not self._client:
            return False
        try:
            await self._client.users.me()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close Notion adapter."""
        if self._client:
            await self._client.aclose()
        self._client = None
        self._initialized = False
        self.logger.info("Notion adapter closed")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def get_page(self, page_id: str) -> NotionPage | None:
        """Get a page by ID."""
        if not self._client:
            raise RuntimeError("Notion adapter not initialized")

        try:
            page = await self._client.pages.retrieve(page_id=page_id)
            content = await self._get_page_content(page_id)

            return NotionPage(
                page_id=page["id"],
                title=self._extract_title(page),
                content=content,
                category=self._extract_property(page, "Category", "select"),
                tags=self._extract_property(page, "Tags", "multi_select") or [],
                last_edited=datetime.fromisoformat(
                    page["last_edited_time"].replace("Z", "+00:00")
                ),
                properties=page.get("properties", {}),
            )
        except Exception as e:
            self.logger.error("Failed to get page", page_id=page_id, error=str(e))
            return None

    async def _get_page_content(self, page_id: str) -> str:
        """Get the content of a page as plain text."""
        if not self._client:
            raise RuntimeError("Notion adapter not initialized")

        blocks = []
        cursor = None

        while True:
            response = await self._client.blocks.children.list(
                block_id=page_id,
                start_cursor=cursor,
            )
            blocks.extend(response["results"])

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        return self._blocks_to_text(blocks)

    def _blocks_to_text(self, blocks: list[dict[str, Any]]) -> str:
        """Convert Notion blocks to plain text."""
        lines = []

        for block in blocks:
            block_type = block.get("type")
            if not block_type:
                continue

            block_content = block.get(block_type, {})

            if block_type in ["paragraph", "heading_1", "heading_2", "heading_3"]:
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                if block_type.startswith("heading"):
                    level = int(block_type[-1])
                    text = f"{'#' * level} {text}"
                lines.append(text)

            elif block_type == "bulleted_list_item":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                lines.append(f"• {text}")

            elif block_type == "numbered_list_item":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                lines.append(f"1. {text}")

            elif block_type == "to_do":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                checked = "✓" if block_content.get("checked") else "☐"
                lines.append(f"{checked} {text}")

            elif block_type == "code":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                language = block_content.get("language", "")
                lines.append(f"```{language}\n{text}\n```")

            elif block_type == "quote":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                lines.append(f"> {text}")

            elif block_type == "divider":
                lines.append("---")

            elif block_type == "callout":
                rich_text = block_content.get("rich_text", [])
                text = self._rich_text_to_plain(rich_text)
                icon = block_content.get("icon", {}).get("emoji", "📝")
                lines.append(f"{icon} {text}")

        return "\n\n".join(lines)

    def _rich_text_to_plain(self, rich_text: list[dict[str, Any]]) -> str:
        """Convert rich text array to plain string."""
        return "".join(item.get("plain_text", "") for item in rich_text)

    def _extract_title(self, page: dict[str, Any]) -> str:
        """Extract title from page properties."""
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                title_content = prop.get("title", [])
                return self._rich_text_to_plain(title_content)
        return "Untitled"

    def _extract_property(
        self, page: dict[str, Any], name: str, prop_type: str
    ) -> Any:
        """Extract a property value from a page."""
        props = page.get("properties", {})
        prop = props.get(name, {})

        if prop.get("type") != prop_type:
            return None

        if prop_type == "select":
            select = prop.get("select")
            return select.get("name") if select else None

        elif prop_type == "multi_select":
            return [item.get("name") for item in prop.get("multi_select", [])]

        elif prop_type == "rich_text":
            return self._rich_text_to_plain(prop.get("rich_text", []))

        elif prop_type == "checkbox":
            return prop.get("checkbox", False)

        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def query_database(
        self,
        database_id: str | None = None,
        filter_params: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> list[NotionPage]:
        """Query a Notion database."""
        if not self._client:
            raise RuntimeError("Notion adapter not initialized")

        db_id = database_id or settings.notion_sop_database_id
        if not db_id:
            raise ValueError("No database ID provided")

        try:
            query_params: dict[str, Any] = {"database_id": db_id, "page_size": page_size}
            if filter_params:
                query_params["filter"] = filter_params
            if sorts:
                query_params["sorts"] = sorts

            response = await self._client.databases.query(**query_params)

            pages = []
            for result in response["results"]:
                page = await self.get_page(result["id"])
                if page:
                    pages.append(page)

            return pages
        except Exception as e:
            self.logger.error("Failed to query database", database_id=db_id, error=str(e))
            raise

    async def get_sop_by_category(self, category: str) -> list[NotionPage]:
        """Get SOPs by category."""
        filter_params = {
            "property": "Category",
            "select": {"equals": category},
        }
        return await self.query_database(filter_params=filter_params)

    async def get_sop_by_tags(self, tags: list[str]) -> list[NotionPage]:
        """Get SOPs that contain any of the specified tags."""
        filter_params = {
            "or": [
                {"property": "Tags", "multi_select": {"contains": tag}}
                for tag in tags
            ]
        }
        return await self.query_database(filter_params=filter_params)

    async def search_sops(self, query: str) -> list[NotionPage]:
        """Search SOPs by title or content."""
        if not self._client:
            raise RuntimeError("Notion adapter not initialized")

        try:
            response = await self._client.search(
                query=query,
                filter={"property": "object", "value": "page"},
                page_size=10,
            )

            pages = []
            for result in response["results"]:
                if result.get("parent", {}).get("database_id") == settings.notion_sop_database_id:
                    page = await self.get_page(result["id"])
                    if page:
                        pages.append(page)

            return pages
        except Exception as e:
            self.logger.error("Failed to search SOPs", query=query, error=str(e))
            raise
