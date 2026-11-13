"""
Data Models and Enums for Openly Licensed Media Acquisition Subsystem.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class LicenseType(str, Enum):
    CC0 = "CC0"
    PUBLIC_DOMAIN = "Public Domain"
    US_GOV_PD = "US Government Work"
    CC_BY = "CC BY"
    PEXELS = "Pexels License"
    PIXABAY = "Pixabay License"
    CC_BY_NC = "CC BY-NC"
    CC_BY_ND = "CC BY-ND"
    CC_BY_SA = "CC BY-SA"
    UNKNOWN = "Unknown"


class LicenseStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


@dataclass
class MediaAsset:
    """Represents a discovered or downloaded visual media asset."""
    asset_id: str
    title: str
    creator: Optional[str]
    source_repository: str          # "Openverse", "Wikimedia Commons", "NASA Image Library"
    source_url: str                 # Original landing / context page URL
    download_url: str               # High-res / original image direct download URL
    license_type: LicenseType
    attribution_text: str
    thumbnail_url: Optional[str] = None
    license_version: str = ""       # e.g. "4.0", "3.0", "1.0"
    license_url: Optional[str] = None
    width: int = 0
    height: int = 0
    mime_type: str = "image/jpeg"
    file_size_bytes: int = 0
    local_path: Optional[str] = None
    portrait_path: Optional[str] = None
    asset_hash: Optional[str] = None
    relevance_score: float = 0.0
    verification_status: LicenseStatus = LicenseStatus.PENDING
    rejection_reason: Optional[str] = None
    search_query: Optional[str] = None
    asset_type: str = "image"         # "image", "existing_animation", "animated_gif", "video", "generated_illustration"
    download_timestamp: str = ""
    original_filename: str = ""
    usage_restrictions: str = "None (Commercial Use Permitted)"
    attribution_requirements: str = "Credit creator/source in manifest and video description"
    modification_permitted: bool = True
    share_alike_compatible: bool = True
    description: str = ""            # provider caption/description (plain text), used for relevance ranking
    tags: List[str] = field(default_factory=list)   # provider categories / keywords
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["license_type"] = self.license_type.value
        d["verification_status"] = self.verification_status.value
        d["sha256"] = self.asset_hash or ""
        return d


@dataclass
class AttributionItem:
    """Structured attribution for a single openly licensed asset."""
    title: str
    creator: str
    source_repository: str
    source_url: str
    license: str
    license_url: str
    modifications: str = "Adapted and cropped to 9:16 portrait video"
    verified: bool = True
    asset_id: str = ""
    asset_type: str = "image"
    download_timestamp: str = ""
    original_filename: str = ""
    sha256: str = ""
    usage_restrictions: str = "None (Commercial Use Permitted)"
    attribution_requirements: str = "Credit creator and source"
    modification_permitted: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_citation_line(self) -> str:
        """Generates a standard compliant attribution line."""
        c = self.creator if self.creator else "Public Domain"
        lic = self.license
        url = f" ({self.license_url})" if self.license_url else ""
        return f"\"{self.title}\" by {c} via {self.source_repository}, licensed under {lic}{url}."


@dataclass
class AttributionManifest:
    """Per-video attribution manifest cataloging all external visual media."""
    video_id: str
    created_at: str
    total_assets: int
    items: List[AttributionItem] = field(default_factory=list)
    youtube_description_block: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "created_at": self.created_at,
            "total_assets": self.total_assets,
            "items": [it.to_dict() for it in self.items],
            "youtube_description_block": self.youtube_description_block
        }
