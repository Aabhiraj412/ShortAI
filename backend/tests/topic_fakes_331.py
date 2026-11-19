"""Offline fakes for Stage 3.31 topic-intelligence tests: no network, temporary databases only."""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from backend.app.content.creative_production_policy import CategoryDistributionTracker
from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine
from backend.app.content.topic_intelligence_flags import TopicIntelligenceFlags
from backend.app.content.topic_ledger import TopicLedger


def extract_for(title: str, kind: str = "volcano") -> str:
    lead = {
        "volcano": f"{title} is a massive stratovolcano in the Andes mountains of northern Chile.",
        "probe": f"{title} is a robotic space probe launched to study the outer solar system and interstellar space.",
        "song": f"{title} is a song recorded by an American singer for her debut studio album.",
        "politician": f"{title} is an American politician and activist known for an election campaign.",
        "short": f"{title} is short.",
    }[kind]
    if kind == "short":
        return lead
    return (lead + f" Researchers first described {title} in detail during a survey in 1913. "
            "It rises more than 6,100 meters above the surrounding plateau and dominates the landscape. "
            "Several expeditions measured its height and recorded unusual activity in 1950 and 1972. "
            "The site remains one of the most studied features of the region by visiting teams. "
            "Instruments installed in 2004 continue to monitor gas output and seismic tremors today.")


class FakeWeb:
    """Routes the engine's HTTP calls. ``search`` maps a query string to titles (offset-aware)."""

    def __init__(self, search: Optional[Dict[str, List[str]]] = None, kinds: Optional[Dict[str, str]] = None,
                 feed: Optional[Dict[str, Any]] = None, fail_queries: Optional[set] = None):
        self.search = search or {}
        self.kinds = kinds or {}
        self.feed = feed or {}
        self.fail_queries = fail_queries or set()
        self.calls: List[str] = []

    def get_json(self, url: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "list=search" in url:
            q = qs.get("srsearch", [""])[0]
            if q in self.fail_queries:
                raise TimeoutError(f"simulated timeout for {q}")
            off = int(qs.get("sroffset", ["0"])[0])
            lim = int(qs.get("srlimit", ["6"])[0])
            titles = self.search.get(q)
            if titles is None:     # news-lead lookups search with the headline's words
                titles = next((v for k, v in self.search.items() if k.lower() in q.lower()), [])
            return {"query": {"search": [{"title": t, "snippet": f"About {t}"} for t in titles[off:off + lim]]}}
        if "/page/summary/" in url:
            title = urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")
            return {"title": title, "type": "standard", "extract": extract_for(title, self.kinds.get(title, "volcano"))[:300],
                    "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}},
                    "timestamp": "2026-11-01T00:00:00Z"}
        if "/page/media-list/" in url:
            return {"items": []}
        if "prop=extracts" in url:
            title = urllib.parse.unquote(qs.get("titles", [""])[0])
            return {"query": {"pages": {"1": {"extract": extract_for(title, self.kinds.get(title, "volcano")),
                                               "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}}}}
        if "feed/featured" in url:
            return self.feed
        if "images-api.nasa.gov" in url:
            return {}
        return None

    def get_text(self, url: str, timeout: float = 12.0) -> Optional[str]:
        self.calls.append(url)
        return None                          # no RSS in these tests

    def install(self, engine: CuriosityTopicEngine) -> None:
        engine._http_get_json = self.get_json
        engine._http_get_text = self.get_text

    def count(self, needle: str) -> int:
        return sum(1 for c in self.calls if needle in c)


def make_engine(tmp_path, web: Optional[FakeWeb] = None, **flags: Any) -> CuriosityTopicEngine:
    ledger = TopicLedger(db_path=str(tmp_path / "studio.db"), json_path=str(tmp_path / "topic_history.json"),
                         include_validation_history=False)
    tracker = CategoryDistributionTracker(store_path=str(tmp_path / "category_distribution.json"))
    eng = CuriosityTopicEngine(ledger=ledger, category_tracker=tracker,
                               flags=TopicIntelligenceFlags.from_mapping(flags))
    if web is not None:
        web.install(eng)
    return eng


def all_queries(engine: CuriosityTopicEngine) -> Dict[str, List[str]]:
    return {s.key: list(s.search_queries) for s in engine.policy.list_categories()}


def unique_name(n: int) -> str:
    """Deterministic, lexically unrelated single-word names ('Bakeli', 'Dokuma', ...): titles that
    share no words cannot trip the lexical duplicate detector against each other."""
    import hashlib
    cons, vows = "bdfgklmnprstvz", "aeiou"
    h = hashlib.sha256(f"name-{n}".encode()).digest()
    out = [cons[h[i] % len(cons)] + vows[h[i + 8] % len(vows)] for i in range(5)]
    return "".join(out).capitalize()


def web_for_every_query(engine: CuriosityTopicEngine, pages: int = 2, **kw: Any) -> FakeWeb:
    """A FakeWeb that answers every category query with distinct titles (6 per page)."""
    search: Dict[str, List[str]] = {}
    n = 0
    for key, queries in all_queries(engine).items():
        for q in queries:
            titles = []
            for _ in range(6 * pages):
                n += 1
                titles.append(unique_name(n))
            search[q] = titles
    web = FakeWeb(search=search, **kw)
    web.install(engine)
    return web
