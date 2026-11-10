"""
Agent 1: Trend Researcher.
Discovers and ranks trending topics across science, technology, space, history,
mysteries, psychology, and mind-bending facts.
"""
import json
import os
import random
from datetime import datetime
from typing import Dict, List, Optional
import feedparser
from google import genai
from app.core.config import settings

CATEGORIES = [
    "Space & Astrophysics",
    "Cutting-Edge Technology & AI",
    "Bizarre Science & Physics",
    "Ancient History & Mysteries",
    "Human Psychology & Paradoxes",
    "Mindblowing Earth Facts"
]

RSS_FEEDS = [
    "https://phys.org/rss-feed/space-news/",
    "https://phys.org/rss-feed/technology-news/",
    "https://www.sciencedaily.com/rss/space_time.xml"
]

class TrendResearcher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    def calculate_score(self, freshness: float, search_interest: float, engagement: float, storytelling: float, uniqueness: float) -> float:
        return round(
            (freshness * 0.25) + 
            (search_interest * 0.25) + 
            (engagement * 0.20) + 
            (storytelling * 0.20) + 
            (uniqueness * 0.10),
            2
        )

    def fetch_rss_candidates(self) -> List[Dict]:
        candidates = []
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    candidates.append({
                        "title": entry.title,
                        "category": "Science & Tech",
                        "sources": [entry.link],
                        "freshness": 9.5,
                        "search_interest": 8.0,
                        "engagement": 8.5,
                        "storytelling": 8.0,
                        "uniqueness": 8.5
                    })
            except Exception as e:
                pass
        return candidates

    async def discover_trends(self, previous_titles: List[str] = None) -> List[Dict]:
        """Discovers top 10 ranked topic candidates using Gemini + RSS feeds."""
        previous_titles = previous_titles or []
        candidates = []
        
        # Try RSS first for live signals
        candidates.extend(self.fetch_rss_candidates())
        
        # Use Gemini to generate high-performing, curated YouTube Shorts topics
        if self.client:
            prompt = f"""
            You are an elite YouTube Shorts trend researcher.
            Suggest 10 viral, scientifically accurate, and fascinating topics for 50-60 second YouTube Shorts.
            Categories: {', '.join(CATEGORIES)}.
            Avoid these previously covered topics: {previous_titles[-20:] if previous_titles else 'None'}.
            
            Return strictly a JSON array of objects with the following schema:
            [
              {{
                "title": "Short, catchy topic title (under 60 chars)",
                "category": "Category name",
                "summary": "1-2 sentence compelling summary of the facts",
                "sources": ["credible reference link or journal name"],
                "freshness": float (1-10),
                "search_interest": float (1-10),
                "engagement": float (1-10),
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
                gemini_candidates = json.loads(text)
                candidates.extend(gemini_candidates)
            except Exception as e:
                print(f"Gemini trend research fallback: {e}")
                
        # Curated fallback pool if offline or API quota unavailable
        if not candidates:
            curated_pool = [
                {
                    "title": "What Happens If You Fall Into a Black Hole?",
                    "category": "Space & Astrophysics",
                    "summary": "The bizarre physics of spaghettification and time dilation near the event horizon.",
                    "sources": ["NASA Astrophysics Archive", "Einstein General Relativity"],
                    "freshness": 8.8, "search_interest": 9.5, "engagement": 9.8, "storytelling": 9.5, "uniqueness": 8.9
                },
                {
                    "title": "Why You Can't Remember Being a Baby",
                    "category": "Human Psychology & Paradoxes",
                    "summary": "Infantile amnesia: how the rapid birth of new neurons in infancy wipes childhood memories.",
                    "sources": ["Nature Neuroscience", "Science Journal"],
                    "freshness": 8.5, "search_interest": 9.0, "engagement": 9.2, "storytelling": 9.0, "uniqueness": 9.1
                },
                {
                    "title": "The Ocean Inside the Earth's Mantle",
                    "category": "Bizarre Science & Physics",
                    "summary": "Scientists discovered a subterranean reservoir of water trapped in ringwoodite 400 miles down.",
                    "sources": ["Northwestern University Geology", "Science 2014"],
                    "freshness": 8.7, "search_interest": 8.9, "engagement": 9.4, "storytelling": 9.3, "uniqueness": 9.6
                },
                {
                    "title": "The Quantum Immortality Paradox",
                    "category": "Bizarre Science & Physics",
                    "summary": "According to the Many-Worlds interpretation, your consciousness may never experience its own end.",
                    "sources": ["Max Tegmark Many Worlds", "MIT Physics"],
                    "freshness": 8.9, "search_interest": 9.3, "engagement": 9.7, "storytelling": 9.6, "uniqueness": 9.5
                },
                {
                    "title": "The Antikythera: The First Analog Computer",
                    "category": "Ancient History & Mysteries",
                    "summary": "A 2,000-year-old Greek mechanical device that accurately predicted eclipses and planetary motions.",
                    "sources": ["Nature Scientific Reports", "Athens Archaeological Museum"],
                    "freshness": 8.2, "search_interest": 8.6, "engagement": 9.1, "storytelling": 9.4, "uniqueness": 9.3
                }
            ]
            candidates.extend(curated_pool)
            
        # Calculate final weighted scores and rank
        scored_candidates = []
        for c in candidates:
            score = self.calculate_score(
                freshness=c.get("freshness", 8.0),
                search_interest=c.get("search_interest", 8.0),
                engagement=c.get("engagement", 8.0),
                storytelling=c.get("storytelling", 8.0),
                uniqueness=c.get("uniqueness", 8.0)
            )
            c["score"] = score
            scored_candidates.append(c)
            
        # Filter out duplicates and sort descending
        seen_titles = set(previous_titles)
        unique_candidates = []
        for c in sorted(scored_candidates, key=lambda x: x["score"], reverse=True):
            if c["title"].lower() not in seen_titles:
                seen_titles.add(c["title"].lower())
                unique_candidates.append(c)
                
        return unique_candidates[:10]
