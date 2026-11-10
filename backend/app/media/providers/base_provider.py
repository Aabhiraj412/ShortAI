"""
Abstract Base Provider Interface for Media Acquisition.
"""

from abc import ABC, abstractmethod
from typing import List
from backend.app.media.models import MediaAsset


class BaseMediaProvider(ABC):
    """Abstract base class for all openly licensed media repositories."""

    def __init__(self, provider_name: str, timeout_sec: float = 6.0):
        self.provider_name = provider_name
        self.timeout_sec = timeout_sec

    @abstractmethod
    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]:
        """
        Discovers openly licensed media candidates matching the query.
        Returns a list of MediaAsset objects with metadata and parsed licenses.
        """
        pass
