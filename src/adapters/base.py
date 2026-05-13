from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.core.logging import get_logger


class BaseAdapter(ABC):
    """Base class for all external service adapters."""

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the adapter connection."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the adapter is healthy and connected."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the adapter connection."""
        pass

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def __aenter__(self) -> BaseAdapter:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
