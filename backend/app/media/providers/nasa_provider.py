"""
NASA Image and Video Library Media Provider.
Official API: https://images-api.nasa.gov/search
"""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import List

from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.net import fetch_json
from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset
from backend.app.media.providers.base_provider import BaseMediaProvider

logger = logging.getLogger(__name__)

NASA_API_URL = "https://images-api.nasa.gov/search"


class NASAProvider(BaseMediaProvider):
    """Integrates official NASA Media Library for public domain space and astrophysics assets."""

    def __init__(self, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="NASA Image Library", timeout_sec=timeout_sec)
        self.verifier = license_verifier or LicenseVerifier()

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        params = {
            "q": query,
            "media_type": "image",
            "page_size": min(20, max(1, limit))
        }
        url = f"{NASA_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AIYouTubeShortsStudio/1.0 (NASAOpenMediaEngine; contact@localstudio.dev)"
            }
        )

        assets: List[MediaAsset] = []
        try:
            data = fetch_json(req.full_url, provider=self.provider_name, retries=1,
                              headers=dict(req.header_items()))

            items = data.get("collection", {}).get("items", [])
            for item in items:
                data_list = item.get("data", [])
                if not data_list:
                    continue

                d = data_list[0]
                nasa_id = d.get("nasa_id", "")
                if not nasa_id:
                    continue

                title = d.get("title", query.capitalize())
                center = d.get("center", "NASA")
                creator = d.get("photographer") or d.get("secondary_creator") or f"NASA/{center}"

                source_url = f"https://images.nasa.gov/details-{nasa_id}"

                # Preview thumbnail
                links = item.get("links", [])
                thumb_url = links[0].get("href") if links else None

                # Derive direct high-res / large download URL
                if thumb_url and "~thumb." in thumb_url:
                    download_url = thumb_url.replace("~thumb.", "~orig.")
                    preview_fallback = thumb_url.replace("~thumb.", "~large.")
                else:
                    download_url = thumb_url or f"https://images-assets.nasa.gov/image/{nasa_id}/{nasa_id}~orig.jpg"
                    preview_fallback = download_url

                # NASA media is U.S. Federal Government Public Domain
                raw_lic = "Public Domain (U.S. Government Work)"
                status, lic_type, norm_lic, reason = self.verifier.verify_asset_license(
                    raw_license=raw_lic,
                    source_url=source_url,
                    repository=self.provider_name,
                    creator=creator
                )

                attr_text = f"\"{title}\" courtesy of NASA/{center} (Public Domain)"

                asset = MediaAsset(
                    asset_id=f"nasa_{nasa_id}",
                    title=title,
                    creator=creator,
                    source_repository=self.provider_name,
                    source_url=source_url,
                    download_url=download_url,
                    thumbnail_url=thumb_url or preview_fallback,
                    license_type=lic_type,
                    license_version="Public Domain",
                    license_url="https://www.nasa.gov/multimedia/guidelines/index.html",
                    attribution_text=attr_text,
                    width=1920,
                    height=1080,
                    description=str(d.get("description") or "")[:800],
                    tags=[str(k) for k in (d.get("keywords") or [])][:30],
                    mime_type="image/jpeg",
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query
                )
                assets.append(asset)
        except Exception as e:
            logger.warning("NASA Media Library search query '%s' failed or timed out: %s", query, e)

        return assets

    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]:
        """Dispatches search to NASA Image & Video Library without blocking event loop."""
        return await asyncio.to_thread(self._sync_fetch, query, limit)
