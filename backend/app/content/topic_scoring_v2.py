"""Stage 3.31K: multi-signal topic scoring v2 (flag ``topic_intelligence.scoring_v2``).

Eight dimensions, each 0..1 with its evidence and whether it was actually measured:

=========================  ======  =========================================================================
dimension                  weight  source
=========================  ======  =========================================================================
demand                     0.20    demand index of the candidate's trending/public-interest signals
trend_velocity             0.12    article pageview velocity (last 2 days vs the 7 before)
novelty                    0.15    1 - highest semantic similarity to produced/validated topics
freshness                  0.10    age of the publication/event date (half-life 7 days); evergreen = 0.5
curiosity                  0.15    v1 curiosity and short-form suitability
source_credibility         0.12    v1 factual reliability + number of independent sources
production_feasibility     0.11    v1 visual availability, image count, research depth
historical_performance     0.05    category audience performance (Stage 3.30 analytics) - ONE signal only
=========================  ======  =========================================================================

Unmeasured signals use a stated neutral value; nothing is invented. Historical performance can never veto a
topic: its weight is 5 % and it is neutral (0.5) until real analytics exist for the category.

``shadow``: v2 is computed and recorded but the v1 ranking decides. ``on``: final = 0.5 x v1 + 0.5 x v2 (both on
0..10), so demand can lift a topic without discarding the v1 safety and suitability gates (a v1 score of 0
still rejects).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

VERSION = "3.31K-v2"
WEIGHTS: Dict[str, float] = {
    "demand": 0.20, "trend_velocity": 0.12, "novelty": 0.15, "freshness": 0.10, "curiosity": 0.15,
    "source_credibility": 0.12, "production_feasibility": 0.11, "historical_performance": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
NEUTRAL = {"demand": 0.25, "trend_velocity": 0.4, "freshness": 0.5, "historical_performance": 0.5}
FRESHNESS_HALF_LIFE_DAYS = 7.0
DATED_ORIGINS = ("gnews", "physorg", "wikipedia_itn", "nasa")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    for cand in (s, s[:19], s[:10]):
        try:
            dt = datetime.fromisoformat(cand)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def velocity_score(v: Optional[float]) -> float:
    if v is None:
        return NEUTRAL["trend_velocity"]
    if v >= 1.0:
        return round(min(1.0, 0.5 + 0.3 * math.log2(v)), 3)          # 1x -> 0.5, 2x -> 0.8, 3.2x+ -> 1.0
    return round(max(0.0, 0.5 * v), 3)


def score_v2(cand: Dict[str, Any], v1: Any, *, now: Optional[datetime] = None,
             historical: Optional[Callable[[str], Tuple[Optional[float], str]]] = None) -> Dict[str, Any]:
    """``cand`` is the discovery candidate dict, ``v1`` its ScoredTopic. Returns the v2 breakdown."""
    now = now or datetime.now(timezone.utc)
    bd = dict(getattr(v1, "score_breakdown", {}) or {})
    meta = dict(getattr(v1, "discovery_metadata", {}) or {})
    dims: Dict[str, Dict[str, Any]] = {}

    def put(name: str, value: float, measured: bool, evidence: str) -> None:
        dims[name] = {"value": round(max(0.0, min(1.0, value)), 3), "measured": measured, "evidence": evidence}

    demand = cand.get("demand") or {}
    if demand.get("demand_index") is not None:
        put("demand", float(demand["demand_index"]), True,
            f"demand index {demand['demand_index']} from {', '.join(demand.get('sources') or [])}")
    else:
        put("demand", NEUTRAL["demand"], False, "no trending or public-interest signal for this candidate")
    vel = demand.get("velocity")
    put("trend_velocity", velocity_score(vel), vel is not None,
        f"pageview velocity {vel}x" if vel is not None else "velocity not measured")
    sim = meta.get("semantic_similarity")
    if sim is not None:
        put("novelty", 1.0 - float(sim), True, f"highest similarity to past topics {sim}")
    else:
        put("novelty", float(bd.get("novelty", 5.0)) / 10.0, "novelty" in bd, "v1 novelty dimension")
    # Only news/feed items carry a real publication date; Wikipedia's timestamp is the last EDIT (trending
    # articles are edited constantly), so for encyclopedic subjects freshness is not measured.
    dated = str(cand.get("discovery_source") or "").lower().startswith(DATED_ORIGINS)
    pub = _parse_dt(cand.get("publication_timestamp")) if dated else None
    if pub:
        age = max(0.0, (now - pub).total_seconds() / 86400.0)
        put("freshness", 0.5 ** (age / FRESHNESS_HALF_LIFE_DAYS), True, f"published {age:.1f} days ago")
    else:
        put("freshness", NEUTRAL["freshness"], False, "evergreen subject (no publication date)")
    put("curiosity", (float(bd.get("curiosity", 5.0)) + float(bd.get("short_form_suitability", 5.0))) / 20.0,
        "curiosity" in bd, "v1 curiosity + short-form suitability")
    n_src = len(cand.get("sources") or [])
    put("source_credibility", 0.8 * float(bd.get("factual_reliability", 5.0)) / 10.0 + 0.2 * min(1.0, n_src / 3.0),
        "factual_reliability" in bd, f"v1 factual reliability, {n_src} source(s)")
    n_img = len(cand.get("image_urls") or [])
    depth = len(cand.get("extended_extract") or cand.get("summary") or "")
    put("production_feasibility", (0.5 * float(bd.get("visual_availability", 5.0)) / 10.0 + 0.25 * min(1.0, n_img / 4.0)
                                   + 0.25 * min(1.0, depth / 1500.0)), "visual_availability" in bd,
        f"{n_img} image(s), {depth} characters of research")
    hv, why = (historical(str(getattr(v1, "category", "") or "")) if historical else (None, "no analytics provider"))
    if hv is None:
        put("historical_performance", NEUTRAL["historical_performance"], False, f"neutral: {why}")
    else:
        put("historical_performance", hv, True, why)
    total = round(10.0 * sum(WEIGHTS[k] * dims[k]["value"] for k in WEIGHTS), 2)
    return {"version": VERSION, "score": total, "weights": dict(WEIGHTS), "dimensions": dims,
            "measured": sorted(k for k, d in dims.items() if d["measured"])}


def blended(v1_score: float, v2: Dict[str, Any]) -> float:
    """``on`` mode: a v1 rejection (score <= 0) always stays a rejection."""
    if v1_score <= 0:
        return v1_score
    return round(0.5 * float(v1_score) + 0.5 * float(v2["score"]), 2)


def category_performance_provider(db_path: Optional[str]) -> Callable[[str], Tuple[Optional[float], str]]:
    """Category-level audience signal from Stage 3.30 ``video_performance`` (views relative to the channel
    median). Returns (None, reason) until at least 3 videos of the category have metrics."""
    cache: Dict[str, Tuple[Optional[float], str]] = {}

    def lookup(category: str) -> Tuple[Optional[float], str]:
        if category in cache:
            return cache[category]
        res: Tuple[Optional[float], str] = (None, "no video analytics collected yet")
        if db_path:
            try:
                import json
                import sqlite3
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
                try:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='video_performance'").fetchone():
                        rows = conn.execute("SELECT topic_category, metrics_json FROM video_performance").fetchall()
                        views = [(c, (json.loads(m or "{}") or {}).get("views")) for c, m in rows]
                        views = [(c, float(v)) for c, v in views if isinstance(v, (int, float))]
                        if views:
                            allv = sorted(v for _, v in views)
                            med = allv[len(allv) // 2] or 1.0
                            mine = [v for c, v in views if (c or "").lower() == category.lower()]
                            if len(mine) >= 3:
                                rel = (sum(mine) / len(mine)) / med
                                res = (round(min(1.0, 0.5 * rel), 3), f"{len(mine)} videos, {rel:.2f}x channel median views")
                            else:
                                res = (None, f"only {len(mine)} analysed video(s) in this category")
                finally:
                    conn.close()
            except Exception as exc:  # analytics are optional
                res = (None, f"analytics unavailable ({type(exc).__name__})")
        cache[category] = res
        return res

    return lookup
