"""
Openverse Media Provider.
Official API: https://api.openverse.org/v1/images/
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

OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"


class OpenverseProvider(BaseMediaProvider):
    """Integrates Openverse official catalog for CC0, Public Domain, and CC BY media."""

    def __init__(self, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="Openverse", timeout_sec=timeout_sec)
        self.verifier = license_verifier or LicenseVerifier()

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        params = {
            "q": query,
            "license": "cc0,pdm,by",
            "page_size": min(20, max(1, limit)),
            "mature": "false"
        }
        url = f"{OPENVERSE_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AIYouTubeShortsStudio/1.0 (OpenlyLicensedMediaEngine; contact@localstudio.dev)"
            }
        )

        assets: List[MediaAsset] = []
        try:
            data = fetch_json(req.full_url, provider=self.provider_name, retries=1,
                              headers=dict(req.header_items()))

            results = data.get("results", [])
            for item in results:
                raw_lic = item.get("license", "")
                lic_ver = item.get("license_version", "")
                full_lic_str = f"{raw_lic} {lic_ver}".strip()

                source_url = item.get("foreign_landing_url") or item.get("url") or ""
                download_url = item.get("url") or ""
                if not download_url:
                    continue

                creator = item.get("creator") or "Unknown Creator"
                title = item.get("title") or query.capitalize()

                # License verification
                status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
                    raw_license=full_lic_str,
                    source_url=source_url,
                    repository=self.provider_name,
                    creator=creator
                )

                attr_text = item.get("attribution") or f"\"{title}\" by {creator} via Openverse ({lic_name})"

                asset = MediaAsset(
                    asset_id=f"openverse_{item.get('id', hash(download_url))}",
                    title=title,
                    creator=creator,
                    source_repository=self.provider_name,
                    source_url=source_url,
                    download_url=download_url,
                    thumbnail_url=item.get("thumbnail"),
                    license_type=lic_type,
                    license_version=lic_ver,
                    license_url=item.get("license_url"),
                    attribution_text=attr_text,
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    file_size_bytes=int(item.get("filesize") or 0),
                    mime_type=f"image/{(item.get('filetype') or 'jpeg').lower().replace('jpg', 'jpeg')}",
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query,
                    tags=[str(t.get("name")) for t in (item.get("tags") or []) if isinstance(t, dict) and t.get("name")][:30],
                )
                assets.append(asset)
        except Exception as e:
            logger.warning("Openverse search query '%s' failed or timed out: %s", query, e)

        return assets

    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]:
        """Dispatches search to Openverse without blocking the event loop."""
        return await asyncio.to_thread(self._sync_fetch, query, limit)
