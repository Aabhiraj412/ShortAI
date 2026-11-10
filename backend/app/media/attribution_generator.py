"""
Attribution Generator and Manifest Exporter.

Constructs legally compliant per-video attribution manifests for all visual media
incorporated into production Shorts, exporting structured JSON records and
ready-to-publish YouTube description credit blocks.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from backend.app.media.models import AttributionItem, AttributionManifest, LicenseType, MediaAsset


class AttributionGenerator:
    """Manages legal attribution compilation and manifest export."""

    def __init__(self, manifests_dir: str = r"D:\Agents\CrazzyDocs\assets\media_library\manifests"):
        self.manifests_dir = manifests_dir
        os.makedirs(self.manifests_dir, exist_ok=True)

    def create_attribution_item(self, asset: MediaAsset, modifications: str = "Adapted and cropped for 9:16 vertical video") -> AttributionItem:
        """Constructs an AttributionItem from a verified MediaAsset."""
        creator_name = asset.creator.strip() if asset.creator else "Public Domain Contributor"
        lic_name = asset.license_type.value
        lic_url = asset.license_url or "https://creativecommons.org/"

        return AttributionItem(
            title=asset.title,
            creator=creator_name,
            source_repository=asset.source_repository,
            source_url=asset.source_url,
            license=lic_name,
            license_url=lic_url,
            modifications=modifications,
            verified=(asset.verification_status.value == "APPROVED")
        )

    def generate_manifest(
        self,
        video_id: str,
        assets: List[MediaAsset],
        custom_modifications: Optional[str] = None
    ) -> AttributionManifest:
        """
        Builds a comprehensive AttributionManifest for all visual assets in a video.
        """
        items: List[AttributionItem] = []
        for a in assets:
            mod = custom_modifications or "Adapted and cropped for 9:16 vertical video"
            items.append(self.create_attribution_item(a, mod))

        # Format YouTube description text block
        desc_lines = [
            "---",
            "VISUAL MEDIA CREDITS & LICENSING:",
        ]
        if not items:
            desc_lines.append("All visuals rendered autonomously using original procedurally generated canvas graphics.")
        else:
            for it in items:
                desc_lines.append(f"• {it.to_citation_line()}")

        desc_block = "\n".join(desc_lines)

        manifest = AttributionManifest(
            video_id=video_id,
            created_at=datetime.utcnow().isoformat(),
            total_assets=len(items),
            items=items,
            youtube_description_block=desc_block
        )

        return manifest

    def save_manifest(self, manifest: AttributionManifest) -> str:
        """Saves manifest to a JSON file in the manifests directory."""
        filename = f"manifest_{manifest.video_id}.json"
        path = os.path.join(self.manifests_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)
        return path
