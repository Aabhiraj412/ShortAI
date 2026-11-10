"""
Wikimedia Commons Media Provider.
Official API: https://commons.wikimedia.org/w/api.php
"""

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import List

from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset
from backend.app.media.providers.base_provider import BaseMediaProvider

logger = logging.getLogger(__name__)

WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"


def strip_html(text: str) -> str:
    """Removes HTML markup tags and unescapes text."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    return " ".join(clean.split())


class WikimediaProvider(BaseMediaProvider):
    """Integrates Wikimedia Commons official API for peer-reviewed openly licensed media."""

    def __init__(self, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="Wikimedia Commons", timeout_sec=timeout_sec)
        self.verifier = license_verifier or LicenseVerifier()

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",  # File namespace
            "gsrlimit": min(20, max(1, limit)),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "format": "json"
        }
        url = f"{WIKIMEDIA_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AIYouTubeShortsStudio/1.0 (OpenlyLicensedMediaEngine; contact@localstudio.dev)"
            }
        )

        assets: List[MediaAsset] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            pages = data.get("query", {}).get("pages", {})
            for page_id, page in pages.items():
                imageinfo_list = page.get("imageinfo", [])
                if not imageinfo_list:
                    continue

                info = imageinfo_list[0]
                download_url = info.get("url", "")
                if not download_url:
                    continue

                source_url = info.get("descriptionurl", download_url)
                ext = info.get("extmetadata", {})

                raw_title = page.get("title", "").replace("File:", "")
                clean_title = ext.get("ObjectName", {}).get("value") or raw_title

                raw_creator = ext.get("Artist", {}).get("value") or ext.get("Credit", {}).get("value") or "Wikimedia Commons Contributor"
                creator = strip_html(raw_creator)

                license_name = ext.get("LicenseShortName", {}).get("value") or ext.get("UsageTerms", {}).get("value") or ""
                license_url = ext.get("LicenseUrl", {}).get("value")
                notes = strip_html(ext.get("Restrictions", {}).get("value", ""))

                # Strict license verification
                status, lic_type, norm_lic_name, reason = self.verifier.verify_asset_license(
                    raw_license=license_name,
                    source_url=source_url,
                    repository=self.provider_name,
                    creator=creator,
                    metadata_notes=notes
                )

                attr_text = f"\"{clean_title}\" by {creator} via Wikimedia Commons, licensed under {norm_lic_name}"
                if license_url:
                    attr_text += f" ({license_url})"

                asset = MediaAsset(
                    asset_id=f"wiki_{page_id}",
                    title=clean_title,
                    creator=creator,
                    source_repository=self.provider_name,
                    source_url=source_url,
                    download_url=download_url,
                    thumbnail_url=info.get("thumburl", download_url),
                    license_type=lic_type,
                    license_version="",
                    license_url=license_url,
                    attribution_text=attr_text,
                    width=int(info.get("width") or 0),
                    height=int(info.get("height") or 0),
                    file_size_bytes=int(info.get("size") or 0),
                    mime_type=info.get("mime", "image/jpeg"),
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query
                )
                assets.append(asset)
        except Exception as e:
            logger.info("Wikimedia search query '%s' failed or timed out: %s", query, e)

        return assets

    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]:
        """Dispatches search to Wikimedia Commons without blocking event loop."""
        return await asyncio.to_thread(self._sync_fetch, query, limit)
