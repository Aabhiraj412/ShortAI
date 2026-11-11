"""
Internet Archive Public Domain & Creative Commons Media Provider.
Official API: https://archive.org/advancedsearch.php
"""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import List

from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import MediaAsset
from backend.app.media.providers.base_provider import BaseMediaProvider

logger = logging.getLogger(__name__)

IA_SEARCH_URL = "https://archive.org/advancedsearch.php"


class InternetArchiveProvider(BaseMediaProvider):
    """Discovers verified Public Domain / CC0 / CC BY media from Internet Archive."""

    def __init__(self, timeout_sec: float = 6.0, license_verifier: LicenseVerifier = None):
        super().__init__(provider_name="Internet Archive", timeout_sec=timeout_sec)
        self.verifier = license_verifier or LicenseVerifier()

    @staticmethod
    def _normalize_ia_license(raw_lic_url: str) -> str:
        low = (raw_lic_url or "").lower()
        if "publicdomain/mark" in low or "publicdomain" in low:
            return "Public Domain"
        if "publicdomain/zero" in low or "cc0" in low:
            return "CC0"
        if "/licenses/by/" in low:
            return "CC BY 4.0"
        if "/licenses/by-sa/" in low:
            return "CC BY-SA 4.0"
        if "/licenses/by-nc" in low:
            return "CC BY-NC"
        if "/licenses/by-nd" in low:
            return "CC BY-ND"
        return raw_lic_url or ""

    def _sync_fetch(self, query: str, limit: int) -> List[MediaAsset]:
        safe_q = query.replace('"', "").strip()
        ia_q = f"({safe_q}) AND mediatype:(image OR movies) AND (licenseurl:*publicdomain* OR licenseurl:*licenses/by/* OR licenseurl:*zero*)"
        params = {
            "q": ia_q,
            "fl[]": ["identifier", "title", "creator", "licenseurl", "mediatype"],
            "rows": min(10, max(1, limit)),
            "page": 1,
            "output": "json",
        }
        url = f"{IA_SEARCH_URL}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "CrazzyDocsStudio/1.0 (Autonomous Documentary Pipeline)"},
        )
        assets: List[MediaAsset] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            docs = data.get("response", {}).get("docs", [])
            for doc in docs:
                ident = doc.get("identifier")
                if not ident:
                    continue
                title = doc.get("title") or ident
                if isinstance(title, list):
                    title = title[0]
                creator_val = doc.get("creator") or "Internet Archive Contributor"
                if isinstance(creator_val, list):
                    creator_val = ", ".join(str(x) for x in creator_val[:2])
                lic_url = doc.get("licenseurl") or ""
                if isinstance(lic_url, list):
                    lic_url = lic_url[0]
                norm_raw_lic = self._normalize_ia_license(str(lic_url))
                source_url = f"https://archive.org/details/{ident}"
                download_url = f"https://archive.org/services/img/{ident}"
                status, lic_type, norm_lic_name, reason = self.verifier.verify_asset_license(
                    raw_license=norm_raw_lic,
                    source_url=source_url,
                    repository=self.provider_name,
                    creator=str(creator_val),
                )
                assets.append(MediaAsset(
                    asset_id=f"ia_{ident}",
                    title=str(title),
                    creator=str(creator_val),
                    source_repository=self.provider_name,
                    source_url=source_url,
                    download_url=download_url,
                    thumbnail_url=download_url,
                    license_type=lic_type,
                    license_url=str(lic_url) or None,
                    attribution_text=f"\"{title}\" by {creator_val} via Internet Archive ({norm_lic_name})",
                    width=1024,
                    height=1024,
                    mime_type="image/jpeg",
                    asset_type="image",
                    original_filename=f"{ident}.jpg",
                    verification_status=status,
                    rejection_reason=reason,
                    search_query=query,
                ))
        except Exception as e:
            logger.info("Internet Archive search '%s' failed or timed out: %s", query, e)
        return assets

    async def search_media(self, query: str, limit: int = 6) -> List[MediaAsset]:
        return await asyncio.to_thread(self._sync_fetch, query, limit)
