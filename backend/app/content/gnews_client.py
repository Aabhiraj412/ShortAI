"""
GNews (gnews.io v4) current-topic discovery (Stage 3.30). Supplements Wikipedia / Phys.org / Gemini research; never
required: a missing key, exhausted daily quota or any API error yields no candidates and discovery continues.

* Credential: GNEWS_API_KEY (exact name). Never logged; never part of a cache key or a stored URL.
* Free tier: 100 requests/day. A persisted per-UTC-day budget keeps CrazzyDocs at <= 90; responses are cached 6 h.
* Freshness: articles older than MAX_AGE_DAYS are dropped. Duplicates (same URL) are removed, and articles about
  the same event (similar titles) are clustered.
* Headlines are NOT facts: a candidate is produced only for an event reported by >= MIN_SOURCES distinct outlets,
  labelled `multi_source_corroborated_headline`, with every source URL and publication date kept for research.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

API = "https://gnews.io/api/v4/"
DAILY_LIMIT = 100
DAILY_BUDGET = 90
CACHE_SECONDS = 6 * 3600
MAX_AGE_DAYS = 3
MIN_SOURCES = 2
CATEGORIES = {"technology": "Technology", "science": "Science", "business": "Business"}
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "with", "by", "at", "as", "from",
         "after", "new", "says", "say", "its", "it", "be", "will", "this", "that", "has", "have", "how", "why", "what"}


def api_key() -> str:
    key = os.environ.get("GNEWS_API_KEY", "").strip()
    if key:
        return key
    try:
        from dotenv import dotenv_values
        from backend.app.core.paths import ENV_LOCAL_PATH
        return (dotenv_values(ENV_LOCAL_PATH).get("GNEWS_API_KEY") or "").strip()
    except Exception:
        return ""


def _data_dir() -> Path:
    from backend.app.core.paths import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "backend" / "data" / "cache" / "gnews"


def _tokens(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if w not in _STOP and len(w) > 2}


def _norm_url(url: str) -> str:
    p = urllib.parse.urlsplit(url or "")
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"


def similar(a: str, b: str, threshold: float = 0.5) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def dedupe_and_cluster(articles: List[Dict[str, Any]], *, now: datetime,
                       max_age_days: int = MAX_AGE_DAYS) -> List[Dict[str, Any]]:
    """Drops stale and duplicate articles, then groups articles describing the same event."""
    seen, fresh = set(), []
    for a in articles:
        url = _norm_url(a.get("url", ""))
        if not url or url in seen:
            continue
        try:
            pub = datetime.fromisoformat(str(a.get("publishedAt")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - pub > timedelta(days=max_age_days) or pub > now + timedelta(hours=1):
            continue
        seen.add(url)
        fresh.append({**a, "_pub": pub})
    clusters: List[Dict[str, Any]] = []
    for a in sorted(fresh, key=lambda x: x["_pub"], reverse=True):
        for c in clusters:
            if similar(a.get("title", ""), c["title"]):
                c["articles"].append(a)
                break
        else:
            clusters.append({"title": a.get("title", ""), "articles": [a]})
    for c in clusters:
        c["sources"] = sorted({(x.get("source") or {}).get("name") or _norm_url(x.get("url", "")).split("/")[0]
                               for x in c["articles"]})
        c["latest_published_at"] = max(x["_pub"] for x in c["articles"]).isoformat()
    return clusters


class GNewsClient:
    def __init__(self, key: Optional[str] = None, fetch: Optional[Callable[[str], Any]] = None,
                 data_dir: Optional[Path] = None, now: Optional[Callable[[], datetime]] = None):
        self.key = api_key() if key is None else key
        self._fetch = fetch
        self.data_dir = data_dir
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_status: str = "configured" if self.key else "key_missing"
        self.last_error_category: Optional[str] = None

    def configured(self) -> bool:
        return bool(self.key)

    # ---- budget and cache --------------------------------------------------------------------------
    def _dir(self) -> Path:
        d = self.data_dir or _data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def usage_today(self) -> int:
        try:
            u = json.loads((self._dir() / "usage.json").read_text(encoding="utf-8"))
            return int(u.get(self.now().strftime("%Y-%m-%d"), 0))
        except (OSError, ValueError):
            return 0

    def _spend(self) -> None:
        path = self._dir() / "usage.json"
        try:
            u = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            u = {}
        day = self.now().strftime("%Y-%m-%d")
        u = {day: int(u.get(day, 0)) + 1}
        path.write_text(json.dumps(u), encoding="utf-8")

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.key:
            self.last_status, self.last_error_category = "key_missing", "key_missing"
            return None
        ck = hashlib.sha256(json.dumps([endpoint, sorted(params.items())]).encode()).hexdigest()[:32]
        cache = self._dir() / f"{endpoint.replace('-', '_')}_{ck}.json"
        try:
            if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SECONDS:
                self.last_status = "ok (cached)"
                return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if self.usage_today() >= DAILY_BUDGET:
            self.last_status, self.last_error_category = "daily_budget_reached", "rate_limited"
            return None
        url = API + endpoint + "?" + urllib.parse.urlencode({**params, "apikey": self.key})
        try:
            self._spend()
            if self._fetch:
                data = self._fetch(url)
            else:
                from backend.app.media.net import fetch_json
                data = fetch_json(url, provider="GNews", retries=1, timeout=8.0)
        except Exception as exc:
            status = getattr(exc, "status", None)
            kind = getattr(exc, "kind", "")
            cat = ("invalid_key" if status in (401, 403) else "rate_limited" if status == 429
                   else "timeout" if kind == "timeout" else "error")
            self.last_status, self.last_error_category = f"error: {cat}", cat
            logger.warning("GNews %s request failed: %s", endpoint, cat)   # never the URL (it carries the key)
            return None
        try:
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        self.last_status, self.last_error_category = "ok", None
        return data

    # ---- public ------------------------------------------------------------------------------------
    def top_headlines(self, category: str, max_results: int = 10) -> List[Dict[str, Any]]:
        data = self._get("top-headlines", {"category": category, "lang": "en", "max": max_results})
        arts = list((data or {}).get("articles") or [])
        if data is not None and not arts:
            self.last_status, self.last_error_category = "empty", "empty"
        return arts

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        data = self._get("search", {"q": query, "lang": "en", "max": max_results})
        return list((data or {}).get("articles") or [])

    def corroborated_events(self, categories=tuple(CATEGORIES)) -> List[Dict[str, Any]]:
        """Fresh, deduplicated events reported by >= MIN_SOURCES outlets, per category."""
        out = []
        for cat in categories:
            clusters = dedupe_and_cluster(self.top_headlines(cat), now=self.now())
            for c in clusters:
                if len(c["sources"]) >= MIN_SOURCES:
                    out.append({**c, "category": CATEGORIES.get(cat, cat.title()), "gnews_category": cat})
        return out
