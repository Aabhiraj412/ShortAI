"""
Commercial Stock Media Providers (Pexels & Pixabay).
Gracefully reports unconfigured state when API keys are absent without fabricating results.
"""

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from typing import List, Optional

from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import MediaAsset
from backend.app.media.providers.base_provider import BaseMediaProvider

logger = logging.getLogger(__name__)


class PexelsProvider(BaseMediaProvider):
    """Commercial-safe Pexels photos provider (active when PEXELS_API_KEY is configured)."""

    def __init__(self, api_key: Optional[str] = None, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="Pexels", timeout_sec=timeout_sec)
        self.api_key = api_key if api_key is not None else os.getenv("PEXELS_API_KEY", "")
        self.verifier = license_verifier or LicenseVerifier()
        self.last_status = "configured" if self.api_key else "unconfigured"

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        if not self.api_key:
            self.last_status = "unconfigured"
            return []
        url = f"https://api.pexels.com/v1/search?{urllib.parse.urlencode({'query': query, 'per_page': min(10, max(1, limit))})}"
        req = urllib.request.Request(url, headers={"Authorization": self.api_key, "User-Agent": "CrazzyDocsStudio/1.0"})
        assets: List[MediaAsset] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            self.last_status = "ok"
            for ph in data.get("photos", []):
                src = ph.get("src", {})
                dl = src.get("large2x") or src.get("original") or src.get("large") or ""
                if not dl:
                    continue
                landing = ph.get("url") or dl
                creator = ph.get("photographer") or "Pexels Contributor"
                title = ph.get("alt") or f"{query.title()} photograph"
                status, lic_type, norm_lic, reason = self.verifier.verify_asset_license(
                    raw_license="Pexels License",
                    source_url=landing,
                    repository=self.provider_name,
                    creator=creator,
                )
                assets.append(MediaAsset(
                    asset_id=f"pexels_{ph.get('id', hash(dl))}",
                    title=title,
                    creator=creator,
                    source_repository=self.provider_name,
                    source_url=landing,
                    download_url=dl,
                    thumbnail_url=src.get("medium"),
                    license_type=lic_type,
                    license_url="https://www.pexels.com/license/",
                    attribution_text=f"\"{title}\" by {creator} via Pexels (Pexels License)",
                    width=int(ph.get("width") or 0),
                    height=int(ph.get("height") or 0),
                    mime_type="image/jpeg",
                    asset_type="image",
                    usage_restrictions="No standalone redistribution; commercial video editing permitted",
                    attribution_requirements="Creator credit included in manifest",
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query,
                ))
        except Exception as e:
            self.last_status = f"error: {e}"
            logger.info("Pexels query '%s' failed: %s", query, e)
        return assets

    async def search_media(self, query: str, limit: int = 6) -> List[MediaAsset]:
        return await asyncio.to_thread(self._sync_fetch, query, limit)


class PixabayProvider(BaseMediaProvider):
    """Commercial-safe Pixabay photos provider (active when PIXABAY_API_KEY is configured)."""

    def __init__(self, api_key: Optional[str] = None, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="Pixabay", timeout_sec=timeout_sec)
        self.api_key = api_key if api_key is not None else os.getenv("PIXABAY_API_KEY", "")
        self.verifier = license_verifier or LicenseVerifier()
        self.last_status = "configured" if self.api_key else "unconfigured"

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        if not self.api_key:
            self.last_status = "unconfigured"
            return []
        url = f"https://pixabay.com/api/?{urllib.parse.urlencode({'key': self.api_key, 'q': query, 'per_page': min(10, max(3, limit)), 'safesearch': 'true'})}"
        req = urllib.request.Request(url, headers={"User-Agent": "CrazzyDocsStudio/1.0"})
        assets: List[MediaAsset] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            self.last_status = "ok"
            for hit in data.get("hits", []):
                dl = hit.get("largeImageURL") or hit.get("webformatURL") or ""
                if not dl:
                    continue
                landing = hit.get("pageURL") or dl
                creator = hit.get("user") or "Pixabay Contributor"
                title = hit.get("tags") or query.title()
                status, lic_type, _, reason = self.verifier.verify_asset_license(
                    raw_license="Pixabay License",
                    source_url=landing,
                    repository=self.provider_name,
                    creator=creator,
                )
                assets.append(MediaAsset(
                    asset_id=f"pixabay_{hit.get('id', hash(dl))}",
                    title=str(title),
                    creator=str(creator),
                    source_repository=self.provider_name,
                    source_url=landing,
                    download_url=dl,
                    thumbnail_url=hit.get("previewURL"),
                    license_type=lic_type,
                    license_url="https://pixabay.com/service/license-summary/",
                    attribution_text=f"\"{title}\" by {creator} via Pixabay (Pixabay Content License)",
                    width=int(hit.get("imageWidth") or 0),
                    height=int(hit.get("imageHeight") or 0),
                    mime_type="image/jpeg",
                    asset_type="image",
                    usage_restrictions="No standalone sale; commercial YouTube adaptation permitted",
                    attribution_requirements="Creator credit included in manifest",
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query,
                ))
        except Exception as e:
            self.last_status = f"error: {e}"
            logger.info("Pixabay query '%s' failed: %s", query, e)
        return assets

    async def search_media(self, query: str, limit: int = 6) -> List[MediaAsset]:
        return await asyncio.to_thread(self._sync_fetch, query, limit)
