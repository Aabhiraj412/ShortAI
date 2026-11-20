"""Stage 3.31K: additional in-scope topic categories (flag ``topic_intelligence.expanded_categories``).

The original 20 categories excluded film/TV/music, sport and internet culture. These specs make them
in-scope, but only when the flag is ``on``: :func:`register_expanded_categories` adds them to the
creative-production policy the topic engine uses, and the v2 classifier then maps "film/series/album"
and "athlete/tournament" subjects to them instead of rejecting them. Politics stays out of scope.
"""

from __future__ import annotations

from typing import List

from backend.app.content.creative_production_policy import CategorySpec

EXPANDED_CATEGORY_SPECS: List[CategorySpec] = [
    CategorySpec(
        key="cinema_and_entertainment",
        display_name="Cinema & Entertainment",
        category_code="CINE",
        search_queries=["film production history behind the scenes", "special effects technique cinema history"],
        keywords=["film", "cinema", "movie", "director", "box office", "animation studio", "special effects",
                  "screenplay", "soundtrack", "album", "television series", "video game"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["hook", "behind_the_scenes", "turning_point", "legacy"],
        visual_affinities=["diagram", "authentic_photo", "typography", "animated_authentic"],
        accent_rgb=(244, 114, 182),
    ),
    CategorySpec(
        key="media_and_internet_culture",
        display_name="Media & Internet Culture",
        category_code="NETC",
        search_queries=["internet phenomenon origin history", "social media platform history"],
        keywords=["internet meme", "viral", "social media", "streaming service", "podcast", "website",
                  "online community", "influencer", "search engine", "video-sharing", "subreddit"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["hook", "origin", "spread", "impact"],
        visual_affinities=["typography", "diagram", "data_visualization"],
        accent_rgb=(129, 140, 248),
    ),
    CategorySpec(
        key="sports_and_major_events",
        display_name="Sports & Major Events",
        category_code="SPRT",
        search_queries=["sports science record breaking performance", "history of the olympic games event"],
        keywords=["tournament", "championship", "world cup", "olympic", "athlete", "world record", "stadium",
                  "grand prix", "marathon", "league"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["hook", "stakes", "decisive_moment", "aftermath"],
        visual_affinities=["authentic_photo", "data_visualization", "map"],
        accent_rgb=(250, 204, 21),
    ),
]

EXPANDED_KEYS = tuple(s.key for s in EXPANDED_CATEGORY_SPECS)


def register_expanded_categories(policy) -> List[str]:
    """Adds the expanded specs to ``policy`` (idempotent). Returns the keys that were added."""
    added = []
    for spec in EXPANDED_CATEGORY_SPECS:
        if spec.key not in policy.categories:
            policy.register_category(spec)
            added.append(spec.key)
    return added
