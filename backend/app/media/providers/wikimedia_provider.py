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

    def _parse_commons_pages(self, pages: dict, query: str) -> List[MediaAsset]:
        assets: List[MediaAsset] = []
        allowed_mimes = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/webm"}
        for page_id, page in pages.items():
            imageinfo_list = page.get("imageinfo", [])
            if not imageinfo_list:
                continue

            info = imageinfo_list[0]
            mime = (info.get("mime") or "").lower()
            if mime not in allowed_mimes:
                continue

            orig_url = info.get("url", "")
            thumb_url = info.get("thumburl", "")
            orig_w = int(info.get("width") or 0)
            is_open_anim = mime in ("image/gif", "video/webm")
            # For animations/videos keep original stream; for oversized images prefer 1280px Wikimedia thumb
            if is_open_anim:
                download_url = orig_url or thumb_url
            else:
                download_url = thumb_url if (thumb_url and (orig_w > 1400 or not orig_url)) else orig_url
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
                thumbnail_url=thumb_url or orig_url,
                license_type=lic_type,
                license_version="",
                license_url=license_url,
                attribution_text=attr_text,
                width=int(info.get("thumbwidth") or orig_w),
                height=int(info.get("thumbheight") or int(info.get("height") or 0)),
                file_size_bytes=int(info.get("size") or 0),
                mime_type=mime or "image/jpeg",
                asset_type="existing_animation" if mime in ("image/gif", "video/webm") else "image",
                original_filename=raw_title,
                verification_status=status,
                rejection_reason=reason,
                search_query=query
            )
            assets.append(asset)
        return assets

    def fetch_files_by_titles(self, file_titles: List[str], topic_title: str = "") -> List[MediaAsset]:
        """Fetches verified image metadata from Wikimedia Commons for explicit File:... titles."""
        if not file_titles:
            return []
        normalized = [t if t.startswith("File:") else f"File:{t}" for t in file_titles[:12]]
        params = {
            "action": "query",
            "titles": "|".join(normalized),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": "1280",
            "format": "json",
        }
        url = f"{WIKIMEDIA_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "CrazzyDocsStudio/1.0 (https://github.com/CrazzyDocs; research@crazzydocs.local)"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            pages = data.get("query", {}).get("pages", {})
            return self._parse_commons_pages(pages, query=topic_title or normalized[0])
        except Exception as e:
            logger.info("Wikimedia batch file lookup failed: %s", e)
            return []

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6",  # File namespace
            "gsrlimit": min(20, max(1, limit)),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": "1280",
            "format": "json"
        }
        url = f"{WIKIMEDIA_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "CrazzyDocsStudio/1.0 (https://github.com/CrazzyDocs; research@crazzydocs.local)"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            pages = data.get("query", {}).get("pages", {})
            return self._parse_commons_pages(pages, query=query)
        except Exception as e:
            logger.info("Wikimedia search query '%s' failed or timed out: %s", query, e)
            return []

    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]:
        """Dispatches search to Wikimedia Commons without blocking event loop."""
        return await asyncio.to_thread(self._sync_fetch, query, limit)
