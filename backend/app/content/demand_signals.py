"""Stage 3.31K: demand-led topic discovery signals (flag ``topic_intelligence.demand_signals``).

Free, keyless, public sources only (no scraping of pages that forbid it, no paid APIs):

* **Wikimedia Pageviews** (``wikimedia.org/api/rest_v1``): the most-read English Wikipedia articles of the
  previous UTC day, plus per-article daily views for **trend velocity** (last 2 days vs the 7 before).
  These are the public-interest and "popular question" signals, already grounded in an encyclopedic article.
* **Google Trends daily trending searches RSS** (``trends.google.com/trending/rss?geo=..``): trending search
  terms with approximate traffic and the news articles behind them (current affairs, breaking developments).

Every signal keeps its provenance (source, region, metric, value, URL, fetch time) and is persisted in
``topic_demand_signals``. Terms are filtered before use: non-article pages, adult content, deaths and crime,
rumour/leak/scandal phrasing and politics are dropped (no sensationalism; politics stays out of scope).
A trending *search term* never becomes a topic by itself: it must resolve to a Wikipedia article, which the
existing enrichment then verifies like any other candidate.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

USER_AGENT = "CrazzyDocs-TopicResearch/1.0 (local documentary research tool)"
WIKI_TOP_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{y}/{m}/{d}"
WIKI_ARTICLE_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
                    "{title}/daily/{start}/{end}")
TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo={geo}"
TRENDS_REGIONS = ("US", "IN", "GB")
MAX_RSS_BYTES = 2_000_000
WIKI_TOP_LIMIT = 60
VELOCITY_LOOKUPS = 12          # per-article velocity requests per run (bounded)

_NON_ARTICLE = re.compile(r"^(Main_Page|Special:|Wikipedia:|File:|Portal:|Help:|Talk:|User:|Template:|Category:|"
                          r"Draft:|Module:|-$)", re.I)
_SENSATIONAL = re.compile(
    r"\b(deaths?|dies|died|dead|killed|killing|murder|shooting|shot|stabbing|suicide|rape|assault|arrest(?:ed)?|"
    r"porn\w*|nude|naked|sex|onlyfans|leak(?:ed)?|scandal|rumou?r|affair|divorce|obituary|funeral|"
    r"election|elections|president|senator|minister|parliament|congress|campaign|poll|trump|biden|modi|"
    r"lottery|powerball|betting|odds|casino|stocks?|share price|shares|crypto price|nasdaq)\b", re.I)
_META_PAGE = re.compile(r"^(?:list of|lists of|deaths in|\d{4} in |timeline of|index of|\.)|\bxxx\b", re.I)
_SPORT_FIXTURE = re.compile(r"\bvs\.?\b|\bv\.?\s|\bscore\b|\blive\b|\bhighlights\b", re.I)


@dataclass
class DemandSignal:
    term: str                       # article title (wikipedia) or search term (trends)
    source: str                     # wikipedia_top | google_trends
    region: str
    metric: str                     # daily_views | approx_traffic
    value: float
    rank: int
    fetched_at_utc: str
    url: str = ""
    velocity: Optional[float] = None
    related: List[Dict[str, str]] = field(default_factory=list)   # news items behind a trend (title, url, source)
    wikipedia_title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def acceptable_term(term: str) -> bool:
    """False for non-articles, sensational/sensitive subjects, politics and live sport fixtures."""
    t = (term or "").strip()
    if len(t) < 3 or len(t) > 120:
        return False
    if _NON_ARTICLE.search(t.replace(" ", "_")) or _META_PAGE.search(t.replace("_", " ")):
        return False
    letters = [ch for ch in t if ch.isalpha()]
    if letters and sum(ch.isascii() for ch in letters) / len(letters) < 0.8:
        return False                       # the production pipeline is English-language
    text = t.replace("_", " ")
    return not (_SENSATIONAL.search(text) or _SPORT_FIXTURE.search(text))


_PERSON_SUBJECT = re.compile(
    r"\b(?:is|was) an? [^.]{0,80}?\b(?:actor|actress|singer|rapper|musician|songwriter|footballer|cricketer|player|"
    r"athlete|politician|businessman|businesswoman|entrepreneur|influencer|personality|model|wrestler|boxer|"
    r"youtuber|streamer|socialite|criminal|murderer|serial killer|journalist|comedian|presenter|celebrity)\b", re.I)


def article_unsuitable(text: str) -> Optional[str]:
    """Checks an article's opening (after enrichment). Demand signals often point at people and incidents:
    biographies (privacy, gossip risk) and crime/death subjects are skipped. Returns the reason or None."""
    lead = re.split(r"(?<=[.!?])\s", (text or "").strip(), maxsplit=2)
    head = " ".join(lead[:2])[:600]
    if _PERSON_SUBJECT.search(head[:300]):
        return "person biography"
    if _SENSATIONAL.search(head):
        return "sensitive or sensational subject"
    return None


def parse_traffic(raw: str) -> float:
    """'200K+' -> 200000, '2M+' -> 2000000, '1,000+' -> 1000."""
    s = (raw or "").strip().upper().replace(",", "").rstrip("+")
    mult = 1.0
    if s.endswith("K"):
        mult, s = 1e3, s[:-1]
    elif s.endswith("M"):
        mult, s = 1e6, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


class DemandSignalCollector:
    """Collects demand signals with injected HTTP functions (offline-testable)."""

    def __init__(self, get_json: Callable[[str, float], Optional[Dict]], get_text: Callable[[str, float], Optional[str]],
                 *, now: Callable[[], datetime] = _now, regions: Sequence[str] = TRENDS_REGIONS):
        self.now = now
        self.regions = tuple(regions)
        self.errors: Dict[str, str] = {}

        def safe(fn: Callable[[str, float], Any]) -> Callable[[str, float], Any]:
            def call(url: str, timeout: float) -> Any:
                try:
                    return fn(url, timeout)
                except Exception as exc:          # one failing provider never stops the others
                    self.errors[urllib.parse.urlsplit(url).netloc] = type(exc).__name__
                    return None
            return call

        self.get_json = safe(get_json)
        self.get_text = safe(get_text)

    def wikipedia_top(self, limit: int = WIKI_TOP_LIMIT) -> List[DemandSignal]:
        day = (self.now() - timedelta(days=1)).date()
        url = WIKI_TOP_URL.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")
        data = self.get_json(url, 12.0)
        if not data:
            self.errors["wikipedia_top"] = "no response"
            return []
        try:
            arts = data["items"][0]["articles"]
        except (KeyError, IndexError, TypeError):
            self.errors["wikipedia_top"] = "unexpected payload"
            return []
        out, fetched = [], _ts(self.now())
        for a in arts:
            title = str(a.get("article", ""))
            if not acceptable_term(title):
                continue
            out.append(DemandSignal(term=title.replace("_", " "), source="wikipedia_top", region="global-en",
                                    metric="daily_views", value=float(a.get("views") or 0), rank=int(a.get("rank") or 0),
                                    fetched_at_utc=fetched, url=f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                                    wikipedia_title=title.replace("_", " ")))
            if len(out) >= limit:
                break
        return out

    def article_velocity(self, title: str) -> Optional[float]:
        """Mean views of the last 2 days divided by the mean of the 7 days before (>1 = rising)."""
        end = (self.now() - timedelta(days=1)).date()
        start = end - timedelta(days=8)
        url = WIKI_ARTICLE_URL.format(title=urllib.parse.quote(title.replace(" ", "_"), safe=""),
                                      start=start.strftime("%Y%m%d00"), end=end.strftime("%Y%m%d00"))
        data = self.get_json(url, 10.0)
        try:
            views = [float(i["views"]) for i in (data or {}).get("items", [])]
        except (KeyError, TypeError, ValueError):
            return None
        if len(views) < 5:
            return None
        recent, before = views[-2:], views[:-2]
        base = sum(before) / len(before) if before else 0.0
        return round((sum(recent) / len(recent)) / base, 2) if base > 0 else None

    def google_trends(self) -> List[DemandSignal]:
        out: List[DemandSignal] = []
        ns = {"ht": "https://trends.google.com/trending/rss"}
        for geo in self.regions:
            raw = self.get_text(TRENDS_RSS_URL.format(geo=geo), 12.0)
            if not raw:
                self.errors[f"google_trends:{geo}"] = "no response"
                continue
            if len(raw) > MAX_RSS_BYTES or "<!DOCTYPE" in raw[:200].upper():
                self.errors[f"google_trends:{geo}"] = "unexpected payload"
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                self.errors[f"google_trends:{geo}"] = "unparseable feed"
                continue
            fetched = _ts(self.now())
            for rank, item in enumerate(root.iter("item"), start=1):
                term = (item.findtext("title") or "").strip()
                if not acceptable_term(term):
                    continue
                related = []
                for n in item.findall("ht:news_item", ns)[:3]:
                    related.append({"title": (n.findtext("ht:news_item_title", default="", namespaces=ns) or "")[:200],
                                    "url": (n.findtext("ht:news_item_url", default="", namespaces=ns) or "")[:500],
                                    "source": (n.findtext("ht:news_item_source", default="", namespaces=ns) or "")[:80]})
                if any(_SENSATIONAL.search(r["title"]) for r in related):
                    continue
                out.append(DemandSignal(term=term, source="google_trends", region=geo, metric="approx_traffic",
                                        value=parse_traffic(item.findtext("ht:approx_traffic", default="", namespaces=ns)),
                                        rank=rank, fetched_at_utc=fetched, url=TRENDS_RSS_URL.format(geo=geo),
                                        related=related))
        return out

    def collect(self, *, velocity_lookups: int = VELOCITY_LOOKUPS) -> List[DemandSignal]:
        self.errors = {}
        signals = self.wikipedia_top() + self.google_trends()
        for s in [s for s in signals if s.source == "wikipedia_top"][:velocity_lookups]:
            s.velocity = self.article_velocity(s.wikipedia_title or s.term)
        return signals


def demand_strength(sig: DemandSignal) -> float:
    """0..1 on a log scale: 10k daily views or 10k searches ~ 0.5, 1M ~ 1.0."""
    if sig.value <= 0:
        return 0.0
    return round(max(0.0, min(1.0, (math.log10(sig.value) - 3.0) / 3.0)), 3)


def rank_demand_terms(signals: Iterable[DemandSignal], limit: int = 12) -> List[Dict[str, Any]]:
    """Merges signals per normalized term: strength = max source strength, +0.1 per extra source, x velocity."""
    merged: Dict[str, Dict[str, Any]] = {}
    for s in signals:
        key = re.sub(r"[^a-z0-9]+", " ", (s.wikipedia_title or s.term).lower()).strip()
        if not key:
            continue
        m = merged.setdefault(key, {"term": s.wikipedia_title or s.term, "wikipedia_title": s.wikipedia_title,
                                    "strength": 0.0, "velocity": None, "sources": [], "signals": []})
        m["strength"] = max(m["strength"], demand_strength(s))
        if s.velocity is not None:
            m["velocity"] = max(m["velocity"] or 0.0, s.velocity)
        if s.source not in m["sources"]:
            m["sources"].append(s.source)
        m["wikipedia_title"] = m["wikipedia_title"] or s.wikipedia_title
        m["signals"].append({k: v for k, v in s.to_dict().items() if k != "term"})
    for m in merged.values():
        vel = m["velocity"]
        boost = 1.0 if vel is None else max(0.7, min(1.5, vel))
        m["demand_index"] = round(min(1.0, (m["strength"] + 0.1 * (len(m["sources"]) - 1)) * boost), 3)
    return sorted(merged.values(), key=lambda m: -m["demand_index"])[:limit]


SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_demand_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    fetched_at_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    region TEXT NOT NULL,
    term TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    rank INTEGER,
    velocity REAL,
    wikipedia_title TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_topic_demand_fetched ON topic_demand_signals (fetched_at_utc);
"""


class DemandSignalStore:
    """Persists signals (provenance) in the production database; additive table only."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ready = False
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        if not self._ready:
            with self._lock:
                conn.executescript(SCHEMA)
                self._ready = True
        return conn

    def record(self, signals: Sequence[DemandSignal], run_id: Optional[str] = None) -> int:
        if not signals:
            return 0
        conn = self._conn()
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO topic_demand_signals (run_id, fetched_at_utc, source, region, term, metric, value, rank, "
                    "velocity, wikipedia_title, evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(run_id, s.fetched_at_utc, s.source, s.region, s.term[:200], s.metric, s.value, s.rank, s.velocity,
                      s.wikipedia_title, json.dumps({"url": s.url, "related": s.related})[:4000]) for s in signals])
        finally:
            conn.close()
        return len(signals)

    def recent(self, hours: int = 48, limit: int = 200) -> List[Dict[str, Any]]:
        since = _ts(_now() - timedelta(hours=hours))
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return []
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='topic_demand_signals'").fetchone():
                return []
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM topic_demand_signals WHERE fetched_at_utc >= ? "
                                                  "ORDER BY fetched_at_utc DESC, value DESC LIMIT ?", (since, limit))]
        finally:
            conn.close()
