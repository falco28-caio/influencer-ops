from __future__ import annotations

from src.adapters.gmail import GmailAdapter
from src.adapters.hubspot import HubSpotAdapter
from src.adapters.notion import NotionAdapter
from src.adapters.slack import SlackAdapter

__all__ = [
    "GmailAdapter",
    "SlackAdapter",
    "HubSpotAdapter",
    "NotionAdapter",
]
