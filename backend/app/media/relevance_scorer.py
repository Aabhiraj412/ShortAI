"""
Relevance Scoring and Query Generation Engine for Media Assets.

Generates targeted search queries from video scripts and evaluates discovered
assets for semantic relevance, image resolution, source trust, and license confidence.
"""

import re
from typing import Dict, List, Optional, Set

from backend.app.media.models import LicenseStatus, LicenseType, MediaAsset


def clean_words(text: str) -> Set[str]:
    """Extracts non-stopword alphanumeric tokens."""
    stop_words = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with",
        "by", "from", "of", "about", "into", "through", "over", "after", "is",
        "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "can", "could", "should", "would", "will", "shall",
        "what", "why", "how", "when", "where", "this", "that", "these", "those"
    }
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return {t for t in tokens if t not in stop_words}


class RelevanceScorer:
    """Scores candidate media assets and generates scene-specific queries."""

    def generate_search_queries(
        self,
        topic_title: str,
        category: str,
        scene_description: Optional[str] = None,
        key_concept: Optional[str] = None
    ) -> List[str]:
        """
        Produces targeted, distinct search queries for openly licensed media repositories.
        """
        queries = []

        # 1. Cleaned core topic query
        clean_topic = re.sub(r"[^\w\s]", "", topic_title).strip()
        queries.append(clean_topic)

        # 2. Specific scene concept query
        if key_concept:
            queries.append(f"{clean_topic} {key_concept}".strip())
        elif scene_description:
            words = list(clean_words(scene_description))[:3]
            if words:
                queries.append(f"{clean_topic} {' '.join(words)}".strip())

        # 3. Category domain queries
        topic_lower = topic_title.lower()
        if "black hole" in topic_lower or "singularity" in topic_lower:
            queries.extend(["black hole accretion disk", "gravitational lensing", "event horizon telescope"])
        elif "moon" in topic_lower and ("orbit" in topic_lower or "fall" in topic_lower):
            queries.extend(["moon orbiting earth", "lunar orbit", "earth moon space"])
        elif "solar" in topic_lower or "photovoltaic" in topic_lower:
            queries.extend(["solar farm utility photovoltaic", "solar panels field", "silicon solar cell"])
        elif "baby" in topic_lower or "amnesia" in topic_lower:
            queries.extend(["human brain hippocampus neurons", "synapse brain neural network"])
        elif "mantle" in topic_lower or "ringwoodite" in topic_lower:
            queries.extend(["earth mantle cross section", "subterranean geology ringwoodite"])
        elif "hash" in topic_lower or "algorithm" in topic_lower:
            queries.extend(["hash table computer science diagram", "binary search tree data structure"])

        # Deduplicate preserving order
        seen = set()
        final_queries = []
        for q in queries:
            q_norm = q.lower().strip()
            if q_norm and q_norm not in seen:
                seen.add(q_norm)
                final_queries.append(q)

        return final_queries[:4]

    def score_asset(self, asset: MediaAsset, target_concept: str) -> float:
        """
        Calculates a holistic relevance score (0.0 to 1.0) based on:
        1. Semantic keyword match (40%)
        2. Resolution quality (25%)
        3. License confidence (20%)
        4. Repository authority (15%)
        """
        # If asset is legally rejected, score is 0.0
        if asset.verification_status == LicenseStatus.REJECTED:
            return 0.0

        # 1. Semantic match against ACTUAL asset title/source_url (never inflate via search_query)
        target_tokens = clean_words(target_concept)
        asset_text = f"{asset.title} {asset.source_url or ''}"
        asset_tokens = clean_words(asset_text)

        overlap = len(target_tokens.intersection(asset_tokens))
        if overlap == 0:
            return 0.05
        semantic_score = min(1.0, overlap / max(1, len(target_tokens)))

        # 2. Resolution quality
        w, h = asset.width, asset.height
        if w >= 1920 or h >= 1920:
            res_score = 1.0
        elif w >= 1080 or h >= 1080:
            res_score = 0.85
        elif w >= 720 or h >= 720:
            res_score = 0.65
        else:
            res_score = 0.40

        # 3. License confidence
        if asset.license_type in (LicenseType.CC0, LicenseType.PUBLIC_DOMAIN, LicenseType.US_GOV_PD):
            lic_score = 1.0
        elif asset.license_type == LicenseType.CC_BY:
            lic_score = 0.90
        else:
            lic_score = 0.50

        # 4. Repository authority
        if asset.source_repository == "NASA Image Library":
            repo_score = 1.0
        elif asset.source_repository == "Wikimedia Commons":
            repo_score = 0.90
        else:
            repo_score = 0.85

        composite = (
            (0.40 * semantic_score) +
            (0.25 * res_score) +
            (0.20 * lic_score) +
            (0.15 * repo_score)
        )
        return round(composite, 3)
