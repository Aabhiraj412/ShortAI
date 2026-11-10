"""
Openly Licensed Media Acquisition Subsystem.

Provides automated discovery, strict license verification (CC0, CC BY, Public Domain),
safe streaming downloads, 1080x1920 portrait adaptation, SQLite asset registration,
and legal attribution manifest generation.
"""

from backend.app.media.acquisition_manager import MediaAcquisitionEngine
from backend.app.media.asset_registry import AssetRegistry
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.downloader import AssetDownloader
from backend.app.media.image_processor import AdaptationMode, ImageProcessor
from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import (
    AttributionItem,
    AttributionManifest,
    LicenseStatus,
    LicenseType,
    MediaAsset,
)
from backend.app.media.providers.base_provider import BaseMediaProvider
from backend.app.media.providers.nasa_provider import NASAProvider
from backend.app.media.providers.openverse_provider import OpenverseProvider
from backend.app.media.providers.wikimedia_provider import WikimediaProvider
from backend.app.media.relevance_scorer import RelevanceScorer

__all__ = [
    "MediaAcquisitionEngine",
    "AssetRegistry",
    "AttributionGenerator",
    "AssetDownloader",
    "ImageProcessor",
    "AdaptationMode",
    "LicenseVerifier",
    "AttributionItem",
    "AttributionManifest",
    "LicenseStatus",
    "LicenseType",
    "MediaAsset",
    "BaseMediaProvider",
    "NASAProvider",
    "WikimediaProvider",
    "OpenverseProvider",
    "RelevanceScorer",
]
