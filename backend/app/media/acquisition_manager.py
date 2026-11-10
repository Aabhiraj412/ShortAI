"""
Unified Media Acquisition Manager.

Coordinates discovery across Openverse, Wikimedia Commons, and NASA Media Library,
strictly verifying licenses, scoring relevance, safely downloading assets,
adapting them to 1080x1920 portrait format, and cataloging attribution manifests.
"""

import asyncio
import logging
import os
from typing import List, Optional, Tuple

from backend.app.media.asset_registry import AssetRegistry
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.downloader import AssetDownloader
from backend.app.media.image_processor import AdaptationMode, ImageProcessor
from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import AttributionManifest, LicenseStatus, MediaAsset
from backend.app.media.providers.base_provider import BaseMediaProvider
from backend.app.media.providers.nasa_provider import NASAProvider
from backend.app.media.providers.openverse_provider import OpenverseProvider
from backend.app.media.providers.wikimedia_provider import WikimediaProvider
from backend.app.media.relevance_scorer import RelevanceScorer

logger = logging.getLogger(__name__)


class MediaAcquisitionEngine:
    """Master coordinator for automated openly licensed media discovery and processing."""

    def __init__(
        self,
        providers: Optional[List[BaseMediaProvider]] = None,
        downloader: Optional[AssetDownloader] = None,
        processor: Optional[ImageProcessor] = None,
        verifier: Optional[LicenseVerifier] = None,
        scorer: Optional[RelevanceScorer] = None,
        registry: Optional[AssetRegistry] = None,
        attribution_gen: Optional[AttributionGenerator] = None
    ):
        self.verifier = verifier or LicenseVerifier()
        self.providers = providers or [
            NASAProvider(license_verifier=self.verifier),
            WikimediaProvider(license_verifier=self.verifier),
            OpenverseProvider(license_verifier=self.verifier)
        ]
        self.downloader = downloader or AssetDownloader()
        self.processor = processor or ImageProcessor()
        self.scorer = scorer or RelevanceScorer()
        self.registry = registry or AssetRegistry()
        self.attribution_gen = attribution_gen or AttributionGenerator()

    async def search_all_providers(self, query: str, limit_per_provider: int = 5) -> List[MediaAsset]:
        """Dispatches query across all configured providers concurrently."""
        tasks = [p.search_media(query, limit=limit_per_provider) for p in self.providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_assets: List[MediaAsset] = []
        for res in results:
            if isinstance(res, list):
                all_assets.extend(res)
            elif isinstance(res, Exception):
                logger.debug("Provider error during search: %s", res)

        return all_assets

    async def acquire_scene_assets(
        self,
        topic_title: str,
        category: str,
        scene_queries: Optional[List[str]] = None,
        job_id: Optional[str] = None,
        max_assets: int = 3,
        adaptation_mode: AdaptationMode = AdaptationMode.FIT_BLURRED_BACKGROUND
    ) -> Tuple[List[MediaAsset], AttributionManifest]:
        """
        Executes end-to-end acquisition for a video:
        1. Formulates search queries
        2. Discovers media across Openverse, Wikimedia Commons, NASA
        3. Filters for strictly APPROVED licenses
        4. Scores candidates for relevance and resolution
        5. Downloads top assets with integrity and hash deduplication
        6. Adapts images to 1080x1920 portrait format
        7. Persists in SQLite registry
        8. Compiles and saves legal AttributionManifest
        """
        queries = scene_queries or self.scorer.generate_search_queries(topic_title, category)
        candidate_pool: List[MediaAsset] = []

        # 1. Search providers for each query
        for q in queries:
            assets = await self.search_all_providers(q, limit_per_provider=4)
            candidate_pool.extend(assets)

        # 2. Score and filter candidates
        scored_candidates: List[Tuple[float, MediaAsset]] = []
        seen_urls = set()

        for a in candidate_pool:
            # Skip rejected licenses immediately
            if a.verification_status != LicenseStatus.APPROVED:
                continue

            if a.download_url in seen_urls:
                continue
            seen_urls.add(a.download_url)

            score = self.scorer.score_asset(a, topic_title)
            a.relevance_score = score
            scored_candidates.append((score, a))

        # Sort descending by relevance score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        top_candidates = [item[1] for item in scored_candidates[:max_assets]]

        # 3. Download and process top candidates
        acquired_assets: List[MediaAsset] = []
        video_id = job_id or f"short_{int(asyncio.get_event_loop().time() * 1000)}"

        for asset in top_candidates:
            # Check if hash already exists in registry
            ok, local_path, sha_hash, err = self.downloader.download_asset(asset)
            if not ok or not local_path or not os.path.exists(local_path):
                logger.info("Asset download skipped or failed: %s (%s)", asset.title, err)
                continue

            # Adapt image to 1080x1920 portrait
            try:
                portrait_path = self.processor.adapt_image_to_portrait(
                    input_path=local_path,
                    mode=adaptation_mode
                )
                asset.portrait_path = portrait_path
            except Exception as e:
                logger.error("Failed to adapt image to portrait: %s", e)
                continue

            # Register in SQLite database
            self.registry.register_asset(asset, job_id=video_id)
            acquired_assets.append(asset)

        # 4. Generate and save legal AttributionManifest
        manifest = self.attribution_gen.generate_manifest(
            video_id=video_id,
            assets=acquired_assets
        )
        self.attribution_gen.save_manifest(manifest)

        logger.info(
            "Media acquisition complete for '%s': %d/%d assets acquired and verified.",
            topic_title, len(acquired_assets), max_assets
        )
        return acquired_assets, manifest
