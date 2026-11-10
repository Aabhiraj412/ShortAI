"""
Topic Discovery and Evaluation Subsystem for Content Intelligence.

Identifies, scores, and ranks video topic candidates across 10 educational categories.
Evaluates audience interest, freshness, storytelling potential, 50-60 second feasibility,
and enforces duplicate prevention against historical production records.
"""

import json
import logging
import random
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import feedparser
from google import genai

from backend.app.core.config import settings
from backend.app.content.duplicate_detector import DuplicateDetector

logger = logging.getLogger(__name__)

# The 10 Target Educational Categories
TOPIC_CATEGORIES = [
    "Space & Astrophysics",
    "Cutting-Edge Technology & AI",
    "Bizarre Science & Physics",
    "Computer Science & Algorithms",
    "Ancient History & Mysteries",
    "Human Psychology & Paradoxes",
    "Business & Global Economics",
    "Engineering Marvels",
    "Mindblowing Earth Facts",
    "General Educational Curiosities"
]

# Free and accessible RSS news feeds
RSS_FEEDS = [
    ("https://phys.org/rss-feed/space-news/", "Space & Astrophysics"),
    ("https://phys.org/rss-feed/technology-news/", "Cutting-Edge Technology & AI"),
    ("https://www.sciencedaily.com/rss/space_time.xml", "Space & Astrophysics"),
    ("https://www.sciencedaily.com/rss/computers_math/computer_science.xml", "Computer Science & Algorithms"),
]

# Curated Evergreen Topic Knowledge Pool (Guaranteed Zero-Cost Offline Resilience)
CURATED_EVERGREEN_TOPICS: List[Dict] = [
    {
        "title": "What Happens If You Fall Into a Black Hole?",
        "category": "Space & Astrophysics",
        "summary": "The bizarre physics of spaghettification and time dilation near the event horizon.",
        "freshness": 8.5, "search_interest": 9.7, "virality": 9.8, "feasibility_50s": 9.5, "storytelling": 9.6, "uniqueness": 9.0
    },
    {
        "title": "Why Does the Moon Not Fall to Earth?",
        "category": "Space & Astrophysics",
        "summary": "Orbital velocity: the Moon is in perpetual free fall at 1.02 km/s, constantly missing Earth as the ground curves away.",
        "freshness": 8.0, "search_interest": 9.2, "virality": 9.4, "feasibility_50s": 9.8, "storytelling": 9.5, "uniqueness": 8.8
    },
    {
        "title": "Why Solar Energy Became the Cheapest Power in History",
        "category": "Business & Global Economics",
        "summary": "Swanson's Law and the 89% cost plunge of utility solar photovoltaics from 2010 to 2024.",
        "freshness": 9.2, "search_interest": 9.0, "virality": 9.1, "feasibility_50s": 9.2, "storytelling": 9.4, "uniqueness": 9.3
    },
    {
        "title": "Why You Can't Remember Being a Baby",
        "category": "Human Psychology & Paradoxes",
        "summary": "Infantile amnesia: how explosive neurogenesis in early childhood replaces and overwrites memory circuits.",
        "freshness": 8.5, "search_interest": 9.3, "virality": 9.6, "feasibility_50s": 9.5, "storytelling": 9.4, "uniqueness": 9.2
    },
    {
        "title": "The Ocean Trapped Inside Earth's Mantle",
        "category": "Mindblowing Earth Facts",
        "summary": "Ringwoodite mineral crystals 400 miles beneath the crust hold more water than all surface oceans combined.",
        "freshness": 8.7, "search_interest": 9.1, "virality": 9.5, "feasibility_50s": 9.2, "storytelling": 9.5, "uniqueness": 9.7
    },
    {
        "title": "The Antikythera: A 2,000-Year-Old Analog Computer",
        "category": "Ancient History & Mysteries",
        "summary": "A complex 37-gear bronze mechanism used by ancient Greeks to predict eclipses and planetary positions.",
        "freshness": 8.2, "search_interest": 8.8, "virality": 9.2, "feasibility_50s": 9.0, "storytelling": 9.7, "uniqueness": 9.6
    },
    {
        "title": "How Hash Tables Work in O(1) Constant Time",
        "category": "Computer Science & Algorithms",
        "summary": "How mathematical hash functions map arbitrary data keys into instant array memory lookups.",
        "freshness": 8.0, "search_interest": 8.5, "virality": 8.8, "feasibility_50s": 9.4, "storytelling": 9.0, "uniqueness": 8.9
    },
    {
        "title": "Why Aerogels Are Called Frozen Smoke",
        "category": "Engineering Marvels",
        "summary": "The world's lightest solid material: 99.8% air, capable of insulating a flower directly above a blowtorch.",
        "freshness": 8.6, "search_interest": 9.0, "virality": 9.3, "feasibility_50s": 9.3, "storytelling": 9.2, "uniqueness": 9.4
    },
    {
        "title": "The Quantum Immortality Paradox",
        "category": "Bizarre Science & Physics",
        "summary": "According to the Many-Worlds interpretation of quantum mechanics, a conscious observer never experiences non-existence.",
        "freshness": 8.8, "search_interest": 9.4, "virality": 9.7, "feasibility_50s": 8.8, "storytelling": 9.6, "uniqueness": 9.5
    },
    {
        "title": "Why Milk Is Always at the Back of the Grocery Store",
        "category": "General Educational Curiosities",
        "summary": "Retail psychology and supply chain logistics engineered to force shoppers past high-margin impulse items.",
        "freshness": 8.4, "search_interest": 9.1, "virality": 9.5, "feasibility_50s": 9.6, "storytelling": 9.4, "uniqueness": 9.1
    }
]


class TopicEngine:
    """Discovers, scores, and selects high-potential educational topics for YouTube Shorts."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.duplicate_detector = DuplicateDetector(similarity_threshold=0.60)

    def calculate_score(
        self,
        freshness: float,
        search_interest: float,
        virality: float,
        feasibility_50s: float,
        storytelling: float,
        uniqueness: float
    ) -> float:
        """
        Calculates composite interestingness score (0.0 to 10.0).
        Evaluates virality, 50-60s narrative feasibility, and audience curiosity.
        """
        score = (
            (freshness * 0.20) +
            (search_interest * 0.20) +
            (virality * 0.20) +
            (feasibility_50s * 0.20) +
            (storytelling * 0.10) +
            (uniqueness * 0.10)
        )
        return round(score, 2)

    def fetch_rss_candidates(self, timeout_sec: float = 1.0) -> List[Dict]:
        """Fetches live candidates from accessible scientific RSS feeds with fast failover."""
        candidates = []
        for url, default_cat in RSS_FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    raw_xml = resp.read()
                feed = feedparser.parse(raw_xml)
                for entry in feed.entries[:2]:
                    title = getattr(entry, "title", "").strip()
                    summary = getattr(entry, "summary", "").strip()
                    link = getattr(entry, "link", "")
                    if title and len(title) > 15:
                        candidates.append({
                            "title": title,
                            "category": default_cat,
                            "summary": summary[:200] if summary else f"Latest development in {default_cat}",
                            "freshness": 9.8,
                            "search_interest": 8.2,
                            "virality": 8.5,
                            "feasibility_50s": 8.5,
                            "storytelling": 8.5,
                            "uniqueness": 8.8,
                            "sources": [link] if link else []
                        })
            except Exception as e:
                logger.debug("RSS fetch failed for %s: %s", url, e)
        return candidates

    async def discover_topics(
        self,
        requested_category: Optional[str] = None,
        manual_override_title: Optional[str] = None,
        enable_rss: bool = True
    ) -> List[Dict]:
        """
        Discovers and ranks candidate topics, filtering duplicates against database history.
        """
        # Fetch previous topics to enforce duplicate prevention
        historical_titles = self.duplicate_detector.fetch_historical_titles(limit=100)

        # 1. Handle manual dashboard override
        if manual_override_title:
            is_dup, sim, match = self.duplicate_detector.is_duplicate(manual_override_title, historical_titles)
            return [{
                "title": manual_override_title,
                "category": requested_category or "Curated Selection",
                "summary": "Manually selected topic from dashboard.",
                "score": 10.0,
                "freshness": 10.0,
                "search_interest": 10.0,
                "virality": 10.0,
                "feasibility_50s": 10.0,
                "storytelling": 10.0,
                "uniqueness": 10.0,
                "is_duplicate": is_dup,
                "duplicate_similarity": sim,
                "matched_historical": match,
                "sources": []
            }]

        candidates: List[Dict] = []

        # 2. Add accessible RSS candidates
        if enable_rss:
            candidates.extend(self.fetch_rss_candidates())

        # 3. Gemini Flash Trend Generation (when API is configured)
        if self.client:
            cat_filter = f"Specifically in '{requested_category}'" if requested_category else f"Across categories: {', '.join(TOPIC_CATEGORIES)}"
            prompt = f"""
            You are an elite YouTube Shorts educational researcher.
            Suggest 8 viral, scientifically accurate, and fascinating topics for 50-60 second YouTube Shorts.
            {cat_filter}.
            Avoid these previously covered topics: {historical_titles[-15:] if historical_titles else 'None'}.
            
            Return strictly a JSON array of objects with the following schema:
            [
              {{
                "title": "Short, punchy topic title (under 60 chars)",
                "category": "Category name",
                "summary": "1-2 sentence compelling factual summary",
                "freshness": float (1-10),
                "search_interest": float (1-10),
                "virality": float (1-10),
                "feasibility_50s": float (1-10),
                "storytelling": float (1-10),
                "uniqueness": float (1-10)
              }}
            ]
            """
            try:
                response = self.client.models.generate_content(
                    model=settings.DEFAULT_GEMINI_MODEL,
                    contents=prompt
                )
                text = response.text.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                gemini_data = json.loads(text)
                if isinstance(gemini_data, list):
                    candidates.extend(gemini_data)
            except Exception as e:
                logger.info("Gemini topic discovery fallback to curated knowledge: %s", e)

        # 4. Add Curated Evergreen Topics
        if requested_category:
            filtered_curated = [t for t in CURATED_EVERGREEN_TOPICS if t["category"] == requested_category]
            candidates.extend(filtered_curated or CURATED_EVERGREEN_TOPICS)
        else:
            candidates.extend(CURATED_EVERGREEN_TOPICS)

        # 5. Score, Filter Duplicates, and Rank
        scored_candidates = []
        seen_in_batch = []

        for c in candidates:
            title = c.get("title", "").strip()
            if not title:
                continue

            # Check duplicate against batch
            is_batch_dup, _, _ = self.duplicate_detector.is_duplicate(title, seen_in_batch, custom_threshold=0.65)
            if is_batch_dup:
                continue

            # Check duplicate against historical database
            is_hist_dup, sim, match = self.duplicate_detector.is_duplicate(title, historical_titles, custom_threshold=0.60)
            if is_hist_dup:
                logger.debug("Filtered duplicate topic: '%s' matches '%s' (sim: %.2f)", title, match, sim)
                continue

            score = self.calculate_score(
                freshness=float(c.get("freshness", 8.0)),
                search_interest=float(c.get("search_interest", 8.0)),
                virality=float(c.get("virality", 8.0)),
                feasibility_50s=float(c.get("feasibility_50s", 8.5)),
                storytelling=float(c.get("storytelling", 8.0)),
                uniqueness=float(c.get("uniqueness", 8.0))
            )
            c["score"] = score
            seen_in_batch.append(title)
            scored_candidates.append(c)

        # Sort descending by composite score
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        return scored_candidates[:10]
