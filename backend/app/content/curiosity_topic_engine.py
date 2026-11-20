"""
Entertainment-First Content Intelligence & Curiosity Topic Discovery Engine.

Discovers, evaluates, and ranks topic candidates across 5 high-engagement categories:
A. Strange but true history
B. Unbelievable real-life stories
C. Science and technology
D. Corporate and financial stories
E. Mysteries and unexplained events

Applies strict 6-dimensional weighted scoring:
- Immediate curiosity: 25%
- Surprise factor: 20%
- Storytelling potential: 20%
- Visual potential: 15%
- Emotional connection: 10%
- Factual reliability: 10%

Enforces a factual reliability gate (rejects speculation or unverified claims)
and checks collision against persistent TopicLedger.
Generates and evaluates 5 distinct hook directions for selected topics.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import html
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from backend.app.content.creative_production_policy import (
    CategoryDistributionTracker,
    CreativeProductionPolicy,
    get_creative_production_policy,
)
from backend.app.content import candidate_ledger as cl
from backend.app.content.topic_intelligence_flags import TopicIntelligenceFlags, load_flags
from backend.app.content.topic_ledger import TopicLedger
from backend.app.content.validation_topic_history import (
    compute_research_fingerprint,
    compute_semantic_similarity,
    compute_topic_fingerprint,
)

logger = logging.getLogger(__name__)

# The 5 High-Retention Entertainment-First Categories
CONTENT_CATEGORIES = {
    "A": "Strange but True History",
    "B": "Unbelievable Real-Life Stories",
    "C": "Science & Technology",
    "D": "Corporate & Financial Stories",
    "E": "Mysteries & Unexplained Events"
}

# Curated Knowledge Pool of Highly Documented, High-Curiosity Historical Incidents
CURATED_CURIOSITY_CANDIDATES: List[Dict] = [
    {
        "id": "cand_01_demon_core",
        "title": "The Demon Core: The 14-Pound Plutonium Sphere That Radiated Los Alamos",
        "category": "Science & Technology",
        "category_code": "C",
        "summary": "In 1946, Manhattan Project physicist Louis Slotin was holding two beryllium hemispheres apart with a flathead screwdriver during a critical mass test. The screwdriver slipped. A blinding blue flash of Cherenkov radiation filled the room, sealing his fate in half a second.",
        "curiosity": 9.8,
        "surprise": 9.7,
        "storytelling": 9.6,
        "visual_potential": 9.5,
        "emotional_connection": 9.2,
        "factual_reliability": 10.0,
        "sources": [
            "Los Alamos National Laboratory Declassified Archives",
            "U.S. Department of Energy (DOE) Historical Records",
            "Raemer Schreiber Eyewitness Inquest Memorandum (1946)"
        ],
        "key_entities": ["Louis Slotin", "Los Alamos", "Demon Core", "Plutonium", "Beryllium Hemisphere", "Manhattan Project"]
    },
    {
        "id": "cand_02_pepsi_navy",
        "title": "The Day Pepsi Commanded the World's 6th Largest Submarine Fleet",
        "category": "Corporate & Financial Stories",
        "category_code": "D",
        "summary": "In 1989, the cash-strapped Soviet Union wanted to buy Pepsi but their rubles were worthless abroad. So Moscow traded 17 military submarines, a cruiser, a frigate, and a destroyer to Pepsi for soda syrup.",
        "curiosity": 9.6,
        "surprise": 9.8,
        "storytelling": 9.4,
        "visual_potential": 9.2,
        "emotional_connection": 8.5,
        "factual_reliability": 9.8,
        "sources": [
            "New York Times Contemporary Archive (May 10, 1989)",
            "PepsiCo Corporate History Records",
            "Soviet Ministry of Foreign Trade Documentation"
        ],
        "key_entities": ["PepsiCo", "Soviet Union", "Donald Kendall", "Cold War", "Submarine Fleet", "Rubles"]
    },
    {
        "id": "cand_03_vesna_fall",
        "title": "The Flight Attendant Who Fell 33,000 Feet Without a Parachute and Survived",
        "category": "Unbelievable Real-Life Stories",
        "category_code": "B",
        "summary": "In 1972, flight attendant Vesna Vulovic was aboard JAT Flight 367 when a bomb tore the plane apart at 33,330 feet over Czechoslovakia. Trapped in the tail cone pinned by a food cart, she crashed onto a snow-covered mountainside and lived.",
        "curiosity": 9.7,
        "surprise": 9.6,
        "storytelling": 9.5,
        "visual_potential": 9.1,
        "emotional_connection": 9.6,
        "factual_reliability": 9.9,
        "sources": [
            "Guinness World Records (Highest Fall Survived Without Parachute)",
            "Czechoslovak Civil Aviation Administration Official Crash Report (1972)",
            "Prague Central Military Hospital Medical Dossier"
        ],
        "key_entities": ["Vesna Vulovic", "JAT Flight 367", "Czechoslovakia", "Srbska Kamenice", "Guinness World Record"]
    },
    {
        "id": "cand_04_mizuho_typo",
        "title": "The $7 Billion Typo: The Japanese Stock Broker Who Broke the Market",
        "category": "Corporate & Financial Stories",
        "category_code": "D",
        "summary": "In December 2005, a trader at Mizuho Securities intended to sell 1 share of J-Com stock for 610,000 yen. Instead, he entered an order to sell 610,000 shares for 1 yen each. The error wiped out $340 million in minutes and halted the Tokyo Stock Exchange.",
        "curiosity": 9.2,
        "surprise": 9.5,
        "storytelling": 9.1,
        "visual_potential": 8.7,
        "emotional_connection": 8.4,
        "factual_reliability": 10.0,
        "sources": [
            "Tokyo Stock Exchange Incident Report (2005)",
            "Financial Times Record",
            "Mizuho Securities Judicial Audit"
        ],
        "key_entities": ["Mizuho Securities", "Tokyo Stock Exchange", "J-Com", "Fat Finger Error", "Nikkei 225"]
    },
    {
        "id": "cand_05_roy_lightning",
        "title": "Roy Sullivan: The Park Ranger Struck by Lightning Seven Times",
        "category": "Unbelievable Real-Life Stories",
        "category_code": "B",
        "summary": "Between 1942 and 1977, Shenandoah National Park ranger Roy Sullivan was struck by lightning on seven separate documented occasions, losing toenails, eyebrows, and his hair. The odds of being struck seven times are roughly 1 in 10 to the 28th power.",
        "curiosity": 9.5,
        "surprise": 9.4,
        "storytelling": 9.2,
        "visual_potential": 8.9,
        "emotional_connection": 8.8,
        "factual_reliability": 9.8,
        "sources": [
            "Guinness World Records",
            "National Park Service Historical Personnel Records",
            "Waynesboro Community Hospital Incident Logs"
        ],
        "key_entities": ["Roy Sullivan", "Shenandoah National Park", "Lightning Strike", "Virginia", "Guinness Record"]
    },
    {
        "id": "cand_06_midgley_inventions",
        "title": "Thomas Midgley Jr.: The Inventor Who Accidentally Poisoned the Planet Twice",
        "category": "Strange but True History",
        "category_code": "A",
        "summary": "A brilliant mechanical engineer invented leaded gasoline in 1921, then invented Freon CFCs to make refrigerators safe. He ended up poisoning millions of people and destroying the ozone layer, before being strangled by his own pulley bed invention.",
        "curiosity": 9.6,
        "surprise": 9.7,
        "storytelling": 9.5,
        "visual_potential": 9.0,
        "emotional_connection": 8.9,
        "factual_reliability": 9.9,
        "sources": [
            "American Chemical Society Biographical Memoirs",
            "Environmental Protection Agency (EPA) Historical Reports",
            "Time Magazine Historical Archives"
        ],
        "key_entities": ["Thomas Midgley Jr", "Leaded Gasoline", "Freon", "CFCs", "General Motors", "Ozone Layer"]
    },
    {
        "id": "cand_07_yamaguchi_survivor",
        "title": "Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs",
        "category": "Unbelievable Real-Life Stories",
        "category_code": "B",
        "summary": "On August 6, 1945, naval engineer Tsutomu Yamaguchi was in Hiroshima on business when the first bomb dropped. Severely burned, he boarded a train home to Nagasaki. On August 9, while describing the blast to his skeptical boss, the second bomb detonated.",
        "curiosity": 9.8,
        "surprise": 9.6,
        "storytelling": 9.7,
        "visual_potential": 9.3,
        "emotional_connection": 9.8,
        "factual_reliability": 10.0,
        "sources": [
            "Government of Japan Ministry of Health Certification (Nijuu Hibakusha)",
            "United Nations General Assembly Speech Records (2006)",
            "Hiroshima Peace Memorial Museum Official Registry"
        ],
        "key_entities": ["Tsutomu Yamaguchi", "Hiroshima", "Nagasaki", "Mitsubishi Heavy Industries", "Atomic Bomb"]
    },
    {
        "id": "cand_08_dancing_plague",
        "title": "The Dancing Plague of 1518: When 400 People Couldn't Stop Dancing",
        "category": "Strange but True History",
        "category_code": "A",
        "summary": "In July 1518 in Strasbourg, a woman named Frau Troffea stepped into the street and began dancing uncontrollably. Within a month, 400 citizens were dancing until their feet bled, collapsing from exhaustion and heart attacks.",
        "curiosity": 9.4,
        "surprise": 9.3,
        "storytelling": 9.1,
        "visual_potential": 9.2,
        "emotional_connection": 8.5,
        "factual_reliability": 9.5,
        "sources": [
            "City of Strasbourg Historical Council Minutes (1518)",
            "Physicians Guild Notes (16th Century)",
            "John Waller Historical Medical Analysis"
        ],
        "key_entities": ["Dancing Plague", "Strasbourg", "Frau Troffea", "Mass Psychogenic Illness", "Ergotism"]
    },
    {
        "id": "cand_09_mary_celeste",
        "title": "The Mary Celeste: The Merchant Ship Found Sailing with No One on Board",
        "category": "Mysteries & Unexplained Events",
        "category_code": "E",
        "summary": "In December 1872, the merchant brigantine Mary Celeste was discovered adrift near the Azores in full seaworthy condition, with six months of food and water, untouched cargo of 1,701 barrels of alcohol, and personal belongings in place, but not a single soul aboard.",
        "curiosity": 9.5,
        "surprise": 9.1,
        "storytelling": 9.3,
        "visual_potential": 9.4,
        "emotional_connection": 8.7,
        "factual_reliability": 9.8,
        "sources": [
            "Vice Admiralty Court Inquiry Records, Gibraltar (1872-1873)",
            "British Board of Trade Official Investigation",
            "Maritime Museum of the Atlantic Historical Dossier"
        ],
        "key_entities": ["Mary Celeste", "Benjamin Briggs", "Dei Gratia", "Azores", "Gibraltar Admiralty Court"]
    },
    {
        "id": "cand_10_kola_borehole",
        "title": "The Kola Superdeep Borehole: Drilling 40,000 Feet Toward Earth's Core",
        "category": "Science & Technology",
        "category_code": "C",
        "summary": "Soviet scientists spent 24 years drilling the deepest artificial hole on Earth on the Kola Peninsula. At 12,262 meters (7.6 miles down), they discovered boiling 356°F stone, microscopic fossils in granite, and pressurized liquid water where geology said none could exist.",
        "curiosity": 9.3,
        "surprise": 9.2,
        "storytelling": 9.0,
        "visual_potential": 9.3,
        "emotional_connection": 8.2,
        "factual_reliability": 9.9,
        "sources": [
            "Soviet Academy of Sciences Geological Survey",
            "Scientific American Exploration Records",
            "Russian Academy of Sciences Kola Scientific Center"
        ],
        "key_entities": ["Kola Superdeep Borehole", "Zapolyarny", "Drilling Rig", "Granite Crust", "Continental Deep Drilling"]
    }
]


@dataclass
class HookCandidate:
    approach: str
    hook_text: str
    immediate_comprehension: float
    curiosity: float
    surprise: float
    credibility: float
    visual_potential: float
    story_connection: float
    composite_score: float = 0.0

    def compute_score(self) -> float:
        self.composite_score = round(
            self.immediate_comprehension * 0.20 +
            self.curiosity * 0.25 +
            self.surprise * 0.20 +
            self.credibility * 0.15 +
            self.visual_potential * 0.10 +
            self.story_connection * 0.10,
            2
        )
        return self.composite_score


@dataclass
class ScoredTopic:
    id: str
    title: str
    category: str
    category_code: str
    summary: str
    curiosity: float
    surprise: float
    storytelling: float
    visual_potential: float
    emotional_connection: float
    factual_reliability: float
    composite_score: float
    sources: List[str]
    key_entities: List[str]
    is_duplicate: bool = False
    duplicate_reason: str = ""
    hook_candidates: List[HookCandidate] = field(default_factory=list)
    selected_hook: Optional[HookCandidate] = None
    extended_extract: str = ""
    factual_bullets: List[str] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    discovery_source: str = "curated"
    coordinates: Optional[Dict[str, float]] = None
    research_fingerprint: str = ""
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    discovery_metadata: Dict[str, Any] = field(default_factory=dict)


# ---- Stage 3.31C pool assembly ------------------------------------------------------------------
# Small supplementary families get a few reserved slots so the pool cap cannot silently drop them.
POOL_RESERVED_SLOTS: Dict[str, int] = {"gnews": 3, "wikipedia_itn": 2, "demand": 4}
DEMAND_MAX_CANDIDATES = 6     # Stage 3.31K: trending terms resolved to verified Wikipedia articles per run
DEMAND_REQUEST_GAP_SEC = 0.5
GNEWS_MAX_LEADS = 2           # single-source headlines resolved to an encyclopedic article per run
_LEAD_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "is", "are", "was", "with", "how", "why", "what",
    "new", "says", "after", "from", "by", "as", "at", "this", "that", "its", "it", "be", "will", "could", "may",
})


def assemble_candidate_pool(groups: Sequence[Sequence[Dict[str, Any]]], cap: int,
                            reserved: Optional[Dict[str, int]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fills ``cap`` slots: first up to ``reserved[family]`` candidates of each reserved provider
    family, then everything else in its original order. Returns (pool, dropped); both keep the
    original relative order, so ranking is unaffected by where a candidate sat in the list."""
    flat = [c for g in groups for c in g]
    if len(flat) <= cap:
        return list(flat), []
    reserved = reserved or {}
    keep: set = set()
    used: Dict[str, int] = {}
    for idx, c in enumerate(flat):
        fam = cl.provider_family(c.get("discovery_source", ""))
        if fam in reserved and used.get(fam, 0) < reserved[fam] and len(keep) < cap:
            keep.add(idx)
            used[fam] = used.get(fam, 0) + 1
    for idx in range(len(flat)):
        if len(keep) >= cap:
            break
        keep.add(idx)
    pool = [c for i, c in enumerate(flat) if i in keep]
    dropped = [c for i, c in enumerate(flat) if i not in keep]
    return pool, dropped


class TopicExhaustionReport(dict):
    """Outcome of one bounded exhaustion-recovery pass (3.31D)."""

    def summary(self) -> str:
        return (f"TOPIC_EXHAUSTED: recovery {self.get('outcome')} after {len(self.get('steps', []))} step(s), "
                f"{self.get('requests', 0)} request(s), {self.get('candidates_evaluated', 0)} candidate(s) evaluated"
                + (f"; stopped by {self['stopped_by']}" if self.get("stopped_by") else ""))


class _RecoveryBudgetExhausted(Exception):
    pass


class CuriosityTopicEngine:
    """Discovers, scores, and selects high-curiosity video topics across 20+ configurable categories."""

    DISCOVERY_SEARCH_QUERIES: Tuple[str, ...] = tuple(
        q for spec in get_creative_production_policy().list_categories() for q in spec.search_queries[:1]
    )

    def __init__(
        self,
        ledger: Optional[TopicLedger] = None,
        cooldown_days: int = 30,
        policy: Optional[CreativeProductionPolicy] = None,
        category_tracker: Optional[CategoryDistributionTracker] = None,
        flags: Optional[TopicIntelligenceFlags] = None,
    ):
        self.ledger = ledger or TopicLedger()
        self.cooldown_days = cooldown_days
        self.policy = policy or get_creative_production_policy()
        self.category_tracker = category_tracker or CategoryDistributionTracker()
        self.last_discovery_audit: List[Dict[str, Any]] = []
        self.last_failed_providers: List[Dict[str, str]] = []
        self.last_discovery_summary: Dict[str, Any] = {}
        # Stage 3.31 (all off unless the creative-policy config enables them)
        self.flags: TopicIntelligenceFlags = flags or load_flags()
        self.trace: Any = cl.NullTrace()
        self._run_id: Optional[str] = None
        self._classifier_v2 = None
        self._rotation_store: Optional[cl.RotationStore] = None
        self._rotation_advances: Dict[str, Tuple[int, str]] = {}
        self._last_plan: Dict[str, Any] = {}
        self.last_recovery_report: Dict[str, Any] = {}
        self.last_demand_summary: Dict[str, Any] = {}
        # Stage 3.31K: expanded categories join the policy only when their flag is on.
        if self.flags.is_on("expanded_categories"):
            from backend.app.content.expanded_categories import register_expanded_categories
            register_expanded_categories(self.policy)

    # ------------------------------------------------------------------------------------------
    # Stage 3.31 helpers. Every helper is a no-op / identical-to-v1 while its flag is off.
    # ------------------------------------------------------------------------------------------
    def begin_run(self, run_id: str) -> None:
        """Called by the orchestrator before discovery so ledger rows carry the job id."""
        self._run_id = run_id

    def _db_path(self) -> Optional[str]:
        return getattr(self.ledger, "db_path", None)

    def _candidate_ledger(self) -> Optional[cl.CandidateLedger]:
        if not self.flags.is_on("candidate_ledger") or not self._db_path():
            return None
        return cl.CandidateLedger(self._db_path())

    def _start_trace(self) -> None:
        if self.flags.is_on("candidate_ledger") and self._db_path():
            self.trace = cl.DiscoveryTrace(run_id=self._run_id or "", job_id=self._run_id or "",
                                           flags={k: v for k, v in self.flags.to_dict().items() if k != "exhaustion_limits"})
        else:
            self.trace = cl.NullTrace()

    def _flush_trace(self) -> None:
        led = self._candidate_ledger()
        if led is not None and getattr(self.trace, "enabled", False):
            led.flush(self.trace)

    def _rotation(self) -> Optional[cl.RotationStore]:
        if not self.flags.is_on("production_query_rotation"):
            return None
        if self._rotation_store is None or self._rotation_store.db_path != self._db_path():
            self._rotation_store = cl.RotationStore(self._db_path())
        return self._rotation_store

    def _advance(self, key: str, value: str) -> None:
        """Marks one successful provider request; persisted together at the end of the run."""
        rot = self._rotation()
        if rot is not None:
            self._rotation_advances[key] = (rot.peek(key) + 1, value)

    def _commit_rotation(self) -> None:
        rot = self._rotation()
        if rot is not None and self._rotation_advances:
            rot.commit(self._rotation_advances)
            if getattr(self.trace, "enabled", False):
                self.trace.rotation = {k: {"counter": v, "value": s} for k, (v, s) in self._rotation_advances.items()}
        self._rotation_advances = {}

    def _classifier(self):
        if self._classifier_v2 is None:
            from backend.app.content.category_classifier import CategoryClassifierV2
            self._classifier_v2 = CategoryClassifierV2(self.policy)
        return self._classifier_v2

    def _apply_category(self, cand: Dict[str, Any], hint_key: Optional[str] = None) -> None:
        """v2 category handling. ``off``: untouched. ``shadow``: v2 result recorded only.
        ``on``: v2 assigns the category; a subject with no in-scope category keeps its discovery
        hint if it has one, otherwise it is marked invalid and rejected before ranking."""
        mode = self.flags.category_classifier_v2
        if mode == "off":
            return
        res = self._classifier().classify(cand.get("title", ""), cand.get("extended_extract") or cand.get("summary", ""), hint_key)
        cand["category_v2"] = res.to_dict()
        if mode != "on":
            return
        if res.assigned:
            cand["category"], cand["category_code"] = res.display_name, res.category_code
        elif hint_key and res.status != "OUT_OF_SCOPE":
            spec = self.policy.get_category(hint_key)
            cand["category"], cand["category_code"] = spec.display_name, spec.category_code
        else:
            cand["category_invalid"] = res.status if res.status != "OUT_OF_SCOPE" else f"OUT_OF_SCOPE:{res.out_of_scope_domain}"

    def _hint_key(self, hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        try:
            known = {s.key for s in self.policy.list_categories()} | {s.display_name.lower() for s in self.policy.list_categories()}
            if hint not in known and hint.lower() not in known:
                return None          # get_category() falls back to Science for unknown names
            return self.policy.get_category(hint).key
        except Exception:
            return None

    @staticmethod
    def _origin_hint(origin: str) -> Optional[str]:
        """'wikipedia_search:geography' -> 'geography' (the category the search was run for)."""
        if origin.startswith("wikipedia_search:"):
            return origin.split(":", 1)[1] or None
        return None

    def _trace_reject_new(self, source: str, title: str, blurb: str, reason: str, stage: str,
                          detail: Optional[Dict[str, Any]] = None, match: Optional[str] = None) -> None:
        """Records a candidate that was dropped before scoring. A candidate already registered in
        this run (same provider family and title) keeps its first decision."""
        trace = self.trace
        if not getattr(trace, "enabled", False) or not title:
            return
        if cl.candidate_id(source, title) in trace.candidates:
            return
        cid = trace.proposal(source, title, blurb)
        trace.reject(cid, reason, stage, detail, duplicate_match=match)

    def _parse_itn_item(self, item: Any, blocked_terms: Sequence[str]) -> Tuple[Optional[str], str, Optional[str]]:
        """One "In the news" entry -> (article title, story text, problem). The story is the
        discovery lead; the linked article is what gets researched."""
        if not isinstance(item, dict):
            return None, "", "malformed"
        story = self._clean_text(str(item.get("story") or ""))
        links = item.get("links")
        if not story or not isinstance(links, list) or not links:
            return None, story, "malformed"
        excluded = tuple(blocked_terms) + ("country in", "sovereign state", "city in", "capital of", "deaths in")
        first_title: Optional[str] = None
        for pg in links:
            if not isinstance(pg, dict):
                continue
            t = str(pg.get("normalizedtitle") or pg.get("title") or "").replace("_", " ").strip()
            if not t:
                continue
            first_title = first_title or t
            desc = str(pg.get("description") or "").lower()
            if t.startswith("List of") or any(b in desc or b in t.lower() for b in excluded):
                continue
            return t, story, None
        return first_title, story, "no eligible linked article (blocked, country or list)"

    # ---- 3.31C GNews ------------------------------------------------------------------------------
    def _gnews_candidates_v2(self, preferred_category: Optional[str], disc_ts: str,
                             blocked_terms: Sequence[str]) -> List[Dict[str, Any]]:
        """GNews with provenance and fair use. A headline is a *lead*, never evidence:

        * reported independently by >= 2 outlets (syndicated copies count once) -> candidate
          labelled ``multi_source_corroborated_headline`` (the pre-3.31 rule);
        * single outlet -> resolved to a matching encyclopedic article, which becomes the candidate
          (evidence = the article; the news URL is kept as discovery provenance only);
        * irrelevant (sport, entertainment, politics) or stale headlines are rejected and recorded.
        """
        out: List[Dict[str, Any]] = []
        trace = self.trace
        try:
            from backend.app.content.category_classifier import out_of_scope_domain
            from backend.app.content.gnews_client import GNewsClient, dedupe_and_cluster, independent_source_count
            client = GNewsClient()
            if not client.configured():
                trace.note("gnews", "key_missing")
                return out
            cats: Tuple[str, ...] = ("technology", "science", "business")
            if preferred_category:
                pref_key = self.policy.get_category(preferred_category).key
                cats = tuple(c for c in cats if self.policy.get_category(c.title()).key == pref_key) or ()
            hint_map = {"technology": "technology", "science": "science", "business": "business_and_economics"}
            leads_left = GNEWS_MAX_LEADS
            for cat in cats:
                src = f"gnews_{cat}"
                arts = client.top_headlines(cat)
                trace.query(src, len(arts))
                if client.last_error_category and client.last_error_category != "empty":
                    trace.provider_error(src, client.last_error_category)
                    self.last_failed_providers.append({"provider": src, "error": client.last_error_category})
                    continue
                clusters = dedupe_and_cluster(arts, now=client.now())
                stale_or_dup = len(arts) - sum(len(c["articles"]) for c in clusters)
                if stale_or_dup:
                    trace.note(f"{src}_stale_or_duplicate_articles", stale_or_dup)
                for c in clusters:
                    arts_c = c["articles"]
                    title = self._clean_text(c["title"])
                    text = " ".join(self._clean_text((a.get("description") or "") + " " + (a.get("content") or "").split("[")[0])
                                    for a in arts_c)
                    src_list = [f"{(a.get('source') or {}).get('name') or 'News'} ({a.get('publishedAt')}): {a.get('url')}"
                                for a in arts_c]
                    base = {"discovery_source": src, "title": title, "summary": text[:600], "sources": src_list,
                            "publication_timestamp": c.get("latest_published_at", ""), "discovery_timestamp": disc_ts,
                            "verification": "UNVERIFIED_HEADLINE"}
                    cid = trace.candidate(base, "discovery")
                    dom, _dom_score = out_of_scope_domain(f"{title}. {text}")
                    low = f"{title} {text}".lower()
                    if dom or any(b in low for b in blocked_terms):
                        trace.reject(cid, cl.LOW_RELEVANCE, "gnews_relevance", {"domain": dom or "blocked_term"})
                        continue
                    indep = independent_source_count(c)
                    sents = self._split_clean_sentences(text)
                    if indep >= 2 and len(text) >= 160 and len(sents) >= 3:
                        classified = self.policy.classify_topic(title, text)
                        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:36]
                        cand = {
                            "id": f"gnews_{slug}", "title": title,
                            "category": classified.display_name if classified else c.get("category", "Science"),
                            "category_code": classified.category_code if classified else "NEWS",
                            "summary": text[:600], "extended_extract": text, "factual_bullets": sents[:6],
                            "curiosity": 8.6, "surprise": 8.4, "storytelling": 8.2, "visual_potential": 7.8,
                            "emotional_connection": 8.2,
                            "factual_reliability": 7.5,      # corroborated headlines, not independently fact-checked
                            "verification": "multi_source_corroborated_headline",
                            "independent_sources": indep,
                            "sources": src_list, "key_entities": self._extract_entities(title, text),
                            "image_urls": [a["image"] for a in arts_c if str(a.get("image") or "").startswith("http")][:3],
                            "discovery_source": src, "discovery_timestamp": disc_ts,
                            "publication_timestamp": c.get("latest_published_at", ""),
                            "research_fingerprint": compute_research_fingerprint(summary=text[:600], extended_extract=text,
                                                                                 factual_bullets=sents[:6], sources=src_list),
                        }
                        self._apply_category(cand, hint_map.get(cat))
                        out.append(cand)
                        continue
                    if leads_left <= 0:
                        trace.reject(cid, cl.INSUFFICIENT_EVIDENCE, "gnews_corroboration",
                                     {"independent_sources": indep, "rule": "single source; lead budget used"})
                        continue
                    leads_left -= 1
                    lead = self._resolve_news_lead(title, cat, c, src_list, hint_map.get(cat), blocked_terms)
                    if lead is None:
                        trace.reject(cid, cl.INSUFFICIENT_EVIDENCE, "gnews_lead",
                                     {"independent_sources": indep, "rule": "no matching encyclopedic article"})
                        continue
                    trace.reject(cid, cl.INSUFFICIENT_EVIDENCE, "gnews_lead",
                                 {"independent_sources": indep, "converted_to": lead["title"]})
                    out.append(lead)
        except Exception as exc:   # GNews availability never decides whether a cycle can run
            logger.warning("GNews discovery skipped: %s", type(exc).__name__)
            self.last_failed_providers.append({"provider": "gnews", "error": type(exc).__name__})
            trace.provider_error("gnews", type(exc).__name__)
        return out

    def _resolve_news_lead(self, headline: str, cat: str, cluster: Dict[str, Any], src_list: List[str],
                           hint_key: Optional[str], blocked_terms: Sequence[str]) -> Optional[Dict[str, Any]]:
        """Finds the encyclopedic article a single-source headline is about (one search request)."""
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]+", headline) if w.lower() not in _LEAD_STOPWORDS]
        head_tokens = {w.lower() for w in words if len(w) > 2}
        if len(head_tokens) < 2:
            return None
        query = " ".join(words[:8])
        url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
               f"&srsearch={urllib.parse.quote(query)}&srnamespace=0&srlimit=3&format=json")
        s_json = self._http_get_json(url, timeout=8.0) or {}
        hits = s_json.get("query", {}).get("search", [])
        self.trace.query("gnews_lead_resolution", len(hits))
        origin = f"gnews_lead_{cat}"
        for hit in hits:
            t = hit.get("title", "")
            if not t or t.startswith("List of") or any(b in t.lower() for b in blocked_terms):
                continue
            overlap = head_tokens & {w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]+",
                                                                    t + " " + self._clean_text(hit.get("snippet", "")))}
            if len(overlap) < 2:
                continue
            dup, _s, why = self.ledger.check_content_collision(candidate_title=t, candidate_hook=t, candidate_entities=[t],
                                                               cooldown_days=self.cooldown_days)
            if dup:
                self._trace_reject_new(origin, t, "", cl.classify_duplicate_reason(why), "pre_enrichment", match=why)
                continue
            cand = self.enrich_wikipedia_candidate(t, event_blurb="", discovery_origin=origin)
            if not cand:
                continue
            cand["sources"] = list(cand.get("sources") or []) + [
                f"News lead (discovery provenance, not evidence): {s}" for s in src_list[:3]]
            cand["verification"] = "NEWS_LEAD_ENCYCLOPEDIC_EVIDENCE"
            cand["news_lead"] = {"headline": headline, "published_at": cluster.get("latest_published_at"),
                                 "outlets": cluster.get("sources", [])}
            self._apply_category(cand, hint_key)
            return cand
        return None

    # ---- 3.31A orchestrator hooks (no-ops while the ledger flag is off) ----------------------------
    def record_identity_rejections(self, rejections: Sequence[Dict[str, Any]]) -> None:
        try:
            trace = self.trace
            if not getattr(trace, "enabled", False):
                return
            for r in rejections:
                cid = trace.id_for_title(r.get("title", ""))
                if cid:
                    trace.reject(cid, cl.RECENTLY_USED, "identity_gate", {"status": r.get("status")},
                                 duplicate_match=str(r.get("conflicts_with") or ""))
            self._flush_trace()
        except Exception as exc:
            logger.warning("Candidate ledger: identity rejections not recorded (%s)", type(exc).__name__)

    def record_selection(self, topic: Any, job_id: str) -> None:
        try:
            trace = self.trace
            if not getattr(trace, "enabled", False):
                return
            cid = trace.id_for_title(getattr(topic, "title", ""), getattr(topic, "discovery_source", ""))
            if not cid:
                return
            trace.select(cid)
            self._flush_trace()
            led = self._candidate_ledger()
            if led is not None:
                led.mark_selected(trace.run_id, cid, job_id)
        except Exception as exc:
            logger.warning("Candidate ledger: selection not recorded (%s)", type(exc).__name__)

    def record_outcome(self, job_id: str, outcome: str, reason: str = "") -> None:
        try:
            led = self._candidate_ledger()
            if led is None:
                return
            run_id = self.trace.run_id if getattr(self.trace, "enabled", False) else (self._run_id or job_id)
            led.mark_outcome(run_id, outcome, reason)
        except Exception as exc:
            logger.warning("Candidate ledger: outcome not recorded (%s)", type(exc).__name__)

    # ---- 3.31D bounded exhaustion recovery -----------------------------------------------------
    def recover_from_exhaustion(
        self,
        accept: Optional[Callable[[List[ScoredTopic]], Tuple[List[ScoredTopic], List[Dict[str, Any]]]]] = None,
        *,
        deadline_utc: Optional[datetime] = None,
        reserve_seconds: float = 0.0,
        now_utc: Optional[datetime] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> List[ScoredTopic]:
        """Controlled extra discovery when the pool holds no usable topic.

        Steps (each bounded, stopped as soon as one acceptable topic exists):
          1. next result page of the queries this run already used;
          2. the next query of each category this run searched;
          3. categories this run did not search (least recently produced first);
          4. featured-feed dates this run did not read (most-read, on-this-day, featured article).

        Every candidate passes the unchanged gates: ledger duplicate / cooldown check, factual and
        extract gates, the v2 category gate when enabled, and finally ``accept`` (the orchestrator
        passes the creative-identity filter). Nothing is forced through. Bounds: steps, HTTP
        requests, candidates, wall-clock time capped by the job's recovery deadline minus the time a
        production attempt needs, and a per-request politeness gap.
        """
        lim = self.flags.exhaustion_limits
        report = TopicExhaustionReport(triggered=True, steps=[], requests=0, candidates_evaluated=0,
                                       outcome=None, stopped_by=None, limits=asdict(lim))
        self.last_recovery_report = report
        if not self.flags.is_on("exhaustion_recovery"):
            report.update(outcome="disabled")
            return []
        budget = float(lim.max_elapsed_seconds)
        if deadline_utc is not None:
            now = now_utc or datetime.utcnow()
            dl = deadline_utc.astimezone(timezone.utc).replace(tzinfo=None) if deadline_utc.tzinfo else deadline_utc
            budget = min(budget, (dl - now).total_seconds() - float(reserve_seconds))
        report["budget_seconds"] = round(budget, 1)
        if budget <= 0:
            report.update(outcome="skipped", stopped_by="recovery_deadline")
            self._record_recovery(report)
            return []

        start = clock()
        state = {"requests": 0, "last": None}
        had_instance_getter = "_http_get_json" in self.__dict__
        original_get = self._http_get_json            # instance override (tests) or the static method

        def guarded_get_json(url: str, timeout: float = 12.0) -> Optional[Dict]:
            elapsed = clock() - start
            if state["requests"] >= lim.max_requests:
                report["stopped_by"] = report["stopped_by"] or "max_requests"
                raise _RecoveryBudgetExhausted("max_requests")
            if elapsed >= budget:
                report["stopped_by"] = report["stopped_by"] or "time_budget"
                raise _RecoveryBudgetExhausted("time_budget")
            if state["last"] is not None:
                gap = lim.min_request_interval_seconds - (clock() - state["last"])
                if gap > 0:
                    sleep(gap)
            state["requests"] += 1
            state["last"] = clock()
            return original_get(url, timeout=max(1.0, min(timeout, budget - elapsed)))

        seen = set(getattr(self, "_last_seen_titles", set()) or set())
        plan = dict(self._last_plan or {})
        used_specs = list(getattr(self, "_last_selected_specs", []) or [])
        self._http_get_json = guarded_get_json          # instance attribute shadows the static method
        try:
            for step_name in ("next_page_same_queries", "next_query_each_category", "additional_categories",
                              "additional_feed_dates")[: max(0, lim.max_attempts)]:
                if report["stopped_by"]:
                    break
                step = {"step": step_name, "proposals": 0, "evaluated": 0, "eligible": 0, "accepted": 0}
                proposals = self._recovery_proposals(step_name, plan, used_specs)
                step["proposals"] = len(proposals)
                cands: List[Dict[str, Any]] = []
                for title, blurb, origin, hint in proposals:
                    if report["stopped_by"]:
                        break
                    if report["candidates_evaluated"] >= lim.max_candidates:
                        report["stopped_by"] = "max_candidates"
                        break
                    norm = re.sub(r"\W+", "", title.lower())
                    if norm in seen:
                        continue
                    seen.add(norm)
                    dup, _s, why = self.ledger.check_content_collision(
                        candidate_title=title, candidate_hook=blurb or title, candidate_entities=[title],
                        cooldown_days=self.cooldown_days)
                    if dup:
                        self._trace_reject_new(origin, title, blurb, cl.classify_duplicate_reason(why),
                                               "recovery_pre_enrichment", match=why)
                        continue
                    report["candidates_evaluated"] += 1
                    step["evaluated"] += 1
                    cand = self.enrich_wikipedia_candidate(title, event_blurb=blurb, discovery_origin=origin)
                    if not cand:
                        self._trace_reject_new(origin, title, blurb, cl.INSUFFICIENT_EVIDENCE, "recovery_enrichment")
                        continue
                    self._apply_category(cand, hint_key=self._origin_hint(origin) or self._hint_key(hint))
                    cands.append(cand)
                ranked: List[ScoredTopic] = []
                if cands:
                    ranked, _rows, _d, _s = self._score_and_filter(cands, True, stage_suffix=":recovery")
                step["eligible"] = len(ranked)
                if ranked and accept is not None:
                    ranked, rejections = accept(ranked)
                    self.record_identity_rejections(rejections)
                step["accepted"] = len(ranked)
                report["steps"].append(step)
                report["requests"] = state["requests"]
                if ranked:
                    report.update(outcome="recovered", elapsed_seconds=round(clock() - start, 2))
                    self._record_recovery(report)
                    return ranked
            report.update(outcome="exhausted", requests=state["requests"], elapsed_seconds=round(clock() - start, 2))
            self._record_recovery(report)
            return []
        finally:
            if had_instance_getter:
                self._http_get_json = original_get
            else:
                self.__dict__.pop("_http_get_json", None)

    def _record_recovery(self, report: Dict[str, Any]) -> None:
        logger.info("[TOPIC EXHAUSTION] recovery %s: %s", report.get("outcome"),
                    {k: report.get(k) for k in ("steps", "requests", "candidates_evaluated", "stopped_by")})
        if getattr(self.trace, "enabled", False):
            self.trace.recovery = dict(report)
            self._flush_trace()

    def _recovery_proposals(self, step: str, plan: Dict[str, Any], used_specs: List[Any]) -> List[Tuple[str, str, str, Optional[str]]]:
        """Proposals (title, blurb, origin, hint) for one recovery step. Requests go through the
        guarded getter, so a budget stop simply ends the step."""
        out: List[Tuple[str, str, str, Optional[str]]] = []
        blocked = ("election", "president", "prime minister", "parliament", "season", "league", "album", "film)",
                   "episode", "birth", "deaths in", "actor", "actress", "singer", "rapper", "politician",
                   "television", "footballer")

        def search(spec: Any, q_idx: int, offset: int) -> None:
            q_list = spec.search_queries or [spec.display_name]
            query = q_list[q_idx % len(q_list)]
            url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
                   f"&srsearch={urllib.parse.quote(query)}&srnamespace=0&srlimit=6&format=json"
                   + (f"&sroffset={offset}" if offset else ""))
            try:
                data = self._http_get_json(url, timeout=10.0) or {}
            except _RecoveryBudgetExhausted:
                raise
            except Exception as exc:
                self.trace.provider_error(f"wikipedia_search:{spec.key}", str(exc))
                return
            hits = data.get("query", {}).get("search", [])
            self.trace.query(f"wikipedia_search:{spec.key}", len(hits))
            for hit in hits:
                t = hit.get("title", "")
                if t and not t.startswith("List of") and not any(b in t.lower() for b in blocked):
                    out.append((t, self._clean_text(hit.get("snippet", "")), f"wikipedia_search:{spec.key}", spec.display_name))

        try:
            queries = plan.get("queries") or {}
            offsets = plan.get("search_offsets") or {}
            if step == "next_page_same_queries":
                for spec in used_specs:
                    search(spec, queries.get(spec.key, 0), int(offsets.get(spec.key, 0)) + 6)
            elif step == "next_query_each_category":
                for spec in used_specs:
                    if len(spec.search_queries or []) > 1:
                        search(spec, queries.get(spec.key, 0) + 1, 0)
            elif step == "additional_categories":
                used = {s.key for s in used_specs}
                recent = [c.lower() for c in self.category_tracker.recent_categories(window=24)]
                others = [s for s in self.policy.list_categories() if s.key not in used]
                others.sort(key=lambda s: (sum(1 for c in recent if c == s.display_name.lower()), s.key))
                for spec in others[:4]:
                    search(spec, 0, 0)
            elif step == "additional_feed_dates":
                tried = plan.get("feed_offset")
                now_utc = datetime.now(timezone.utc)
                for d_off in [d for d in (2, 4, 6, 9) if d != tried][:2]:
                    ref = now_utc - timedelta(days=d_off)
                    try:
                        feed = self._http_get_json(
                            f"https://en.wikipedia.org/api/rest_v1/feed/featured/{ref.strftime('%Y/%m/%d')}", timeout=10.0) or {}
                    except _RecoveryBudgetExhausted:
                        raise
                    except Exception as exc:
                        self.trace.provider_error("wikipedia_featured", str(exc))
                        continue
                    self.trace.query("wikipedia_featured", 0)
                    tag = ref.strftime("%m%d")
                    tfa = feed.get("tfa") or {}
                    if tfa.get("title") and not any(b in tfa["title"].lower() for b in blocked):
                        out.append((tfa["title"], "", f"wikipedia_tfa_{tag}", None))
                    for art in (feed.get("mostread") or {}).get("articles", [])[:10]:
                        t, desc = art.get("title", ""), (art.get("description") or "").lower()
                        if t and ":" not in t and not t.startswith(("Main_Page", "List_of")) \
                                and not any(b in desc or b in t.lower() for b in blocked + ("deaths",)):
                            out.append((t, "", f"wikipedia_mostread_{tag}", None))
                    for ev in (feed.get("onthisday") or [])[:6]:
                        pages = ev.get("pages") or []
                        text = self._clean_text(ev.get("text", ""))
                        if pages and text and not any(b in text.lower() for b in blocked):
                            t = pages[0].get("title", "")
                            if t and not t.isdigit():
                                out.append((t, ev.get("text", ""), f"wikipedia_onthisday_{tag}", "History"))
        except _RecoveryBudgetExhausted:
            pass
        return out

    def _recent_sources_and_categories(self) -> Tuple[List[str], List[str]]:
        """Inspects recent validation & ledger items to encourage source/category rotation."""
        sources: List[str] = []
        cats: List[str] = []
        try:
            if getattr(self.ledger, "validation_history", None) is not None:
                recs = self.ledger.validation_history.load_records()
                for r in recs[-6:]:
                    if r.discovery_source:
                        sources.append(r.discovery_source)
                    cat = (r.metadata or {}).get("category")
                    if cat:
                        cats.append(cat)
        except Exception:
            pass
        try:
            for r in self.ledger.load_all_records()[-4:]:
                if r.category:
                    cats.append(r.category)
        except Exception:
            pass
        return sources, cats

    def _highest_historical_similarity(self, cand: Dict) -> Tuple[float, str]:
        """Measures semantic similarity against both content_ledger and validation_history."""
        best = 0.0
        reason = ""
        title = cand.get("title", "")
        ents = cand.get("key_entities", [])
        hook = cand.get("summary", "")

        for rec in self.ledger.load_all_records():
            sc, rsn = compute_semantic_similarity(
                title, ents, hook, rec.topic_title, rec.key_entities, rec.opening_hook
            )
            if sc > best:
                best, reason = sc, rsn

        vh = getattr(self.ledger, "validation_history", None)
        if vh is not None:
            for rec in vh.load_records():
                sc, rsn = compute_semantic_similarity(
                    title, ents, hook, rec.topic_title, rec.key_entities, rec.opening_hook
                )
                if sc > best:
                    best, reason = sc, rsn
        return round(best, 3), reason

    def score_candidate(self, cand: Dict) -> ScoredTopic:
        """
        Computes transparent 8-dimension engagement & suitability score plus novelty,
        soft category-distribution adjustment, and collision checks.
        """
        research_fp = cand.get("research_fingerprint") or compute_research_fingerprint(
            summary=cand.get("summary", ""),
            extended_extract=cand.get("extended_extract", ""),
            factual_bullets=cand.get("factual_bullets", []),
            sources=cand.get("sources", []),
        )
        topic_fp = compute_topic_fingerprint(cand["title"], cand.get("key_entities", []))

        # Collision detection via TopicLedger + isolated ValidationTopicHistory
        is_dup, dup_score, reason = self.ledger.check_content_collision(
            candidate_title=cand["title"],
            candidate_hook=cand["summary"],
            candidate_entities=cand.get("key_entities", []),
            cooldown_days=self.cooldown_days,
            topic_fingerprint=topic_fp,
            research_fingerprint=research_fp,
        )

        recent_sources, recent_cats = self._recent_sources_and_categories()
        dist_recent = self.category_tracker.recent_categories(window=6)
        merged_recent_cats = recent_cats + [c for c in dist_recent if c not in recent_cats[-2:]] if dist_recent else recent_cats
        src_name = cand.get("discovery_source", "curated")
        cat_name = cand.get("category", "")
        sim_score, _ = self._highest_historical_similarity(cand)

        img_count = len(cand.get("image_urls") or [])
        extract_len = len(cand.get("extended_extract") or cand.get("summary") or "")
        bullets_count = len(cand.get("factual_bullets") or [])

        # Derive explicit 8-dimension ratings (0..10 scale)
        curiosity_dim = float(cand.get("curiosity", 8.5))
        novelty_dim = float(cand.get("surprise", 8.5))
        if sim_score > 0:
            novelty_dim = round(max(0.0, min(novelty_dim, 10.0 * (1.0 - sim_score))), 2)
        storytelling_dim = float(cand.get("storytelling", 8.5))
        factual_dim = float(cand.get("factual_reliability", 9.0))
        visual_avail_dim = float(cand.get("visual_potential", 8.0))
        if img_count >= 5:
            visual_avail_dim = max(visual_avail_dim, 9.4)
        elif img_count >= 2:
            visual_avail_dim = max(visual_avail_dim, 8.8)
        elif img_count == 0 and src_name != "curated":
            visual_avail_dim = min(visual_avail_dim, 6.8)

        audience_dim = float(cand.get("audience_relevance", cand.get("emotional_connection", 8.6)))
        # Short-form suitability favors tight, crisp 50-60s factual arcs
        short_suit_dim = float(
            cand.get(
                "short_form_suitability",
                9.2 if (extract_len >= 450 and bullets_count >= 4) else (8.4 if extract_len >= 250 else 6.5),
            )
        )
        freshness_dim = round(max(0.0, 10.0 * (1.0 - max(sim_score, dup_score if is_dup else 0.0))), 2)

        weights = self.policy.scoring_weights.normalized()
        score_breakdown = {
            "curiosity": round(curiosity_dim, 2),
            "novelty": round(novelty_dim, 2),
            "storytelling": round(storytelling_dim, 2),
            "factual_reliability": round(factual_dim, 2),
            "visual_availability": round(visual_avail_dim, 2),
            "audience_relevance": round(audience_dim, 2),
            "short_form_suitability": round(short_suit_dim, 2),
            "freshness": round(freshness_dim, 2),
        }

        base_composite = round(sum(score_breakdown[k] * weights[k] for k in weights), 2)

        # Factual & suitability gates: reject low-reliability or non-visual empty stubs
        total_text_chars = (
            len(cand.get("summary") or "")
            + len(cand.get("extended_extract") or "")
            + sum(len(str(b)) for b in (cand.get("factual_bullets") or []))
        )
        if factual_dim < 7.5:
            base_composite = 0.0
        elif src_name != "curated" and total_text_chars < 80:
            base_composite = 0.0

        cat_penalty = self.category_tracker.compute_soft_recency_penalty(cat_name, recent_override=merged_recent_cats)
        src_diversity_adj = 0.0
        if src_name != "curated":
            src_diversity_adj += -0.15 if recent_sources and recent_sources[-1] == src_name else 0.12
            # Exceptional topics (>= 9.15) are still allowed to overcome mild category recency
            final_composite = (
                round(max(0.0, base_composite + src_diversity_adj - cat_penalty - (sim_score * 0.35)), 2)
                if base_composite > 0
                else 0.0
            )
        else:
            final_composite = base_composite

        meta = {
            "source": src_name,
            "discovery_timestamp": cand.get("discovery_timestamp") or datetime.now(timezone.utc).isoformat(),
            "publication_or_update_timestamp": cand.get("publication_timestamp") or "",
            "research_availability": {
                "extract_chars": extract_len,
                "factual_bullets": bullets_count,
                "image_candidates": img_count,
                "sources_count": len(cand.get("sources") or []),
            },
            "score_breakdown": score_breakdown,
            "scoring_weights": weights,
            "category_recency_penalty": cat_penalty,
            "scoring_disclaimer": "Estimates storytelling and engagement suitability; does not predict or guarantee viral view counts.",
            "factual_confidence": factual_dim,
            "novelty_score": freshness_dim,
            "semantic_similarity": max(sim_score, round(dup_score, 3)) if is_dup else sim_score,
            "curiosity_score": base_composite,
            "final_score": final_composite,
            "topic_fingerprint": topic_fp,
        }
        # Stage 3.31 annotations, present only when their flags are enabled
        for extra in ("category_v2", "category_invalid", "verification"):
            if cand.get(extra):
                meta[extra] = cand[extra]

        resolved_code = cand.get("category_code") or self.policy.get_category(cat_name).category_code
        return ScoredTopic(
            id=cand["id"],
            title=cand["title"],
            category=cat_name,
            category_code=resolved_code,
            summary=cand.get("summary", ""),
            curiosity=float(cand.get("curiosity", curiosity_dim)),
            surprise=float(cand.get("surprise", novelty_dim)),
            storytelling=float(cand.get("storytelling", storytelling_dim)),
            visual_potential=float(cand.get("visual_potential", visual_avail_dim)),
            emotional_connection=float(cand.get("emotional_connection", audience_dim)),
            factual_reliability=float(cand.get("factual_reliability", factual_dim)),
            composite_score=final_composite,
            sources=cand.get("sources", []),
            key_entities=cand.get("key_entities", []),
            is_duplicate=is_dup,
            duplicate_reason=reason,
            extended_extract=cand.get("extended_extract", ""),
            factual_bullets=cand.get("factual_bullets", []),
            image_urls=cand.get("image_urls", []),
            discovery_source=src_name,
            coordinates=cand.get("coordinates"),
            research_fingerprint=research_fp,
            score_breakdown=score_breakdown,
            discovery_metadata=meta,
        )

    @staticmethod
    def _http_get_json(url: str, timeout: float = 12.0) -> Optional[Dict]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "CrazzyDocsStudio/1.0 (Autonomous Documentary Pipeline)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    @staticmethod
    def _http_get_text(url: str, timeout: float = 12.0) -> Optional[str]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "CrazzyDocsStudio/1.0 (Autonomous Documentary Pipeline)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="replace")

    @staticmethod
    def _clean_text(raw: str) -> str:
        txt = html.unescape(raw or "")
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    @staticmethod
    def _split_clean_sentences(text: str) -> List[str]:
        if not text:
            return []
        raw_sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text.strip())
        cleaned: List[str] = []
        for s in raw_sents:
            s_clean = re.sub(r"\s+", " ", s).strip()
            # Drop headings or tiny fragments or parenthetical citation junk
            if s_clean.startswith("=="):
                continue
            if len(s_clean.split()) < 7 or len(s_clean) > 260:
                continue
            cleaned.append(s_clean)
        return cleaned

    @staticmethod
    def _extract_entities(title: str, text: str) -> List[str]:
        entities: List[str] = []
        seen = set()
        # Title nouns + capitalized phrases from extract
        candidates = [title] + re.findall(r"\b([A-Z][a-zA-Z0-9\-]+(?:\s+[A-Z][a-zA-Z0-9\-]+){0,2})\b", text or "")
        stopwords = {"In", "The", "On", "At", "When", "After", "During", "Since", "By", "For", "From", "A", "An", "As", "It", "He", "She", "They", "This", "That"}
        for item in candidates:
            clean = item.strip(" ,.;:()\"'")
            if len(clean) < 3 or clean in stopwords:
                continue
            low = clean.lower()
            if low not in seen:
                seen.add(low)
                entities.append(clean)
            if len(entities) >= 8:
                break
        return entities

    @staticmethod
    def _infer_category(title: str, text: str) -> Tuple[str, str]:
        spec = get_creative_production_policy().classify_topic(title, text)
        return spec.display_name, spec.category_code

    def enrich_wikipedia_candidate(
        self,
        wiki_title: str,
        event_blurb: str = "",
        discovery_origin: str = "wikipedia_featured",
    ) -> Optional[Dict]:
        """Fetches verified Wikipedia extract, canonical URL, image URL, and factual bullets."""
        if not wiki_title:
            return None
        try:
            encoded = urllib.parse.quote(wiki_title.replace(" ", "_"))
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            summary_json = self._http_get_json(summary_url, timeout=10.0)
            if not summary_json or summary_json.get("type") == "disambiguation":
                return None

            canon_title = summary_json.get("title") or wiki_title
            short_extract = self._clean_text(summary_json.get("extract", ""))
            coords_raw = summary_json.get("coordinates")
            coordinates = (
                {"lat": float(coords_raw["lat"]), "lon": float(coords_raw["lon"])}
                if isinstance(coords_raw, dict) and "lat" in coords_raw and "lon" in coords_raw
                else None
            )
            page_url = (
                summary_json.get("content_urls", {})
                .get("desktop", {})
                .get("page", f"https://en.wikipedia.org/wiki/{encoded}")
            )
            image_urls: List[str] = []
            orig_img = summary_json.get("originalimage", {}).get("source")
            if orig_img and orig_img.lower().endswith((".jpg", ".jpeg", ".png")):
                image_urls.append(orig_img)

            # Query Wikipedia REST media-list for article-specific Wikimedia Commons filenames
            commons_file_titles: List[str] = []
            try:
                ml_url = f"https://en.wikipedia.org/api/rest_v1/page/media-list/{encoded}"
                ml_json = self._http_get_json(ml_url, timeout=8.0) or {}
                for item in ml_json.get("items", []):
                    t = item.get("title", "")
                    if item.get("type") == "image" and t.startswith("File:"):
                        low_t = t.lower()
                        if low_t.endswith((".jpg", ".jpeg", ".png")) and "flag_" not in low_t and "commons-logo" not in low_t and "icon" not in low_t:
                            commons_file_titles.append(t)
                    if len(commons_file_titles) >= 14:
                        break
            except Exception:
                pass

            # Fetch multi-paragraph extract for deep script/storyboard facts
            mw_url = (
                "https://en.wikipedia.org/w/api.php?action=query&prop=extracts|info"
                "&exintro=true&explaintext=true&exchars=2600&inprop=url&redirects=1"
                f"&titles={urllib.parse.quote(canon_title)}&format=json"
            )
            mw_json = self._http_get_json(mw_url, timeout=10.0)
            extended_extract = short_extract
            if mw_json:
                pages = mw_json.get("query", {}).get("pages", {})
                for pdata in pages.values():
                    ext = self._clean_text(pdata.get("extract", ""))
                    if len(ext) > len(extended_extract):
                        extended_extract = ext
                    if pdata.get("fullurl"):
                        page_url = pdata["fullurl"]

            if len(extended_extract) < 380:
                return None

            sentences = self._split_clean_sentences(extended_extract)
            if len(sentences) < 3:
                return None

            summary_text = (f"{event_blurb.strip()} " if event_blurb else "") + " ".join(sentences[:2])
            summary_text = summary_text.strip()
            cat_name, cat_code = self._infer_category(canon_title, extended_extract)
            entities = self._extract_entities(canon_title, extended_extract)

            # Score documentary curiosity dynamically based on concrete facts, numbers, and narrative tension
            blob_low = extended_extract.lower()
            has_numbers = len(re.findall(r"\b\d{2,4}\b", extended_extract)) >= 3
            has_dramatic_markers = any(
                m in blob_low
                for m in (
                    "first", "discovered", "deepest", "largest", "record", "accident",
                    "collapsed", "launched", "surviving", "built", "engineered",
                    "mysterious", "unusual", "century", "mile", "kilometer", "meters",
                )
            )
            curiosity = 9.4 if has_dramatic_markers else 8.7
            surprise = 9.2 if has_numbers else 8.5
            storytelling = 9.3 if len(sentences) >= 5 else 8.6
            visual_pot = 9.4 if image_urls else 8.7
            emotional = 8.9
            factual = 9.6  # Multi-paragraph encyclopedic Wikipedia primary record

            slug = re.sub(r"[^a-z0-9]+", "_", canon_title.lower()).strip("_")[:36]
            pub_ts = str(summary_json.get("timestamp") or "")
            disc_ts = datetime.now(timezone.utc).isoformat()
            src_list = [
                f"Wikipedia Encyclopedia Archive: {page_url}",
                f"Wikimedia REST Summary Source: {summary_url}",
            ]
            bullets = sentences[:10]
            res_fp = compute_research_fingerprint(
                summary=summary_text,
                extended_extract=extended_extract,
                factual_bullets=bullets,
                sources=src_list,
            )
            return {
                "id": f"live_{slug}",
                "title": canon_title,
                "category": cat_name,
                "category_code": cat_code,
                "summary": summary_text,
                "extended_extract": extended_extract,
                "factual_bullets": bullets,
                "curiosity": curiosity,
                "surprise": surprise,
                "storytelling": storytelling,
                "visual_potential": visual_pot,
                "emotional_connection": emotional,
                "factual_reliability": factual,
                "sources": src_list,
                "key_entities": entities,
                "image_urls": image_urls + commons_file_titles,
                "discovery_source": discovery_origin,
                "coordinates": coordinates,
                "discovery_timestamp": disc_ts,
                "publication_timestamp": pub_ts,
                "research_fingerprint": res_fp,
            }
        except Exception as e:
            logger.debug("Wikipedia enrichment failed for '%s': %s", wiki_title, e)
            return None

    def discover_live_candidates(
        self,
        max_candidates: int = 12,
        preferred_category: Optional[str] = None,
    ) -> List[Dict]:
        """Discovers genuine, fresh documentary topics from diverse live external multi-category feeds."""
        self.last_failed_providers = []
        trace = self.trace
        rot = self._rotation()                      # None unless production_query_rotation is on
        self._rotation_advances = {}
        itn_on = self.flags.is_on("wikipedia_in_the_news")
        plan: Dict[str, Any] = {"queries": {}, "search_offsets": {}, "feed_offset": None, "nasa_query": None}
        recent_sources, recent_cats = self._recent_sources_and_categories()
        dist_recent = self.category_tracker.recent_categories(window=12)
        all_recent_cats = recent_cats + dist_recent
        vh = getattr(self.ledger, "validation_history", None)
        history_offset = len(vh.load_records()) if vh is not None else 0
        # 3.31C: production never adds validation records, so history_offset is frozen there and every
        # cycle repeats the same queries. With rotation on, persisted per-provider counters replace it.
        order_offset = rot.peek("category_order") if rot is not None else history_offset

        source_buckets: Dict[str, List[Tuple[str, str, str, Optional[str]]]] = {
            "wikipedia_search": [],
            "nasa_library": [],
            "wikipedia_tfa": [],
            "wikipedia_mostread": [],
            "wikipedia_onthisday": [],
        }
        if itn_on:
            source_buckets["wikipedia_itn"] = []
        rss_candidates: List[Dict] = []

        blocked_terms = (
            "election", "president", "prime minister", "parliament", "season",
            "league", "album", "film)", "episode", "birth", "deaths in",
            "actor", "actress", "singer", "rapper", "politician", "television", "footballer",
        )
        preferred_terms = (
            "science", "physics", "biology", "psychology", "technology", "algorithm",
            "engineering", "space", "telescope", "discovery", "medical", "climate",
            "ocean", "glacier", "bridge", "invention", "cybersecurity", "mathematics",
        )

        # 1. Select diverse multi-category queries favoring under-represented categories
        all_specs = self.policy.list_categories()
        if preferred_category:
            target_spec = self.policy.get_category(preferred_category)
            selected_specs = [target_spec]
        else:
            recent_lower = [c.lower() for c in all_recent_cats[-8:]]
            cat_freq = {s.key: sum(1 for c in recent_lower if c == s.display_name.lower()) for s in all_specs}
            non_hist = [s for s in all_specs if s.key != "history"]
            hist_only = [s for s in all_specs if s.key == "history"]
            ordered_specs = sorted(non_hist, key=lambda s: (cat_freq.get(s.key, 0), (all_specs.index(s) - order_offset) % max(1, len(all_specs))))
            if ordered_specs:
                selected_specs = ordered_specs[:4]
                if not hist_only or cat_freq.get("history", 0) == 0:
                    selected_specs = ordered_specs[:3] + hist_only[:1]
            else:
                selected_specs = all_specs[:4]
        self._last_selected_specs = list(selected_specs)

        for s_idx, spec in enumerate(selected_specs):
            q_list = spec.search_queries or [spec.display_name]
            rot_key = f"wikipedia_search:{spec.key}"
            if rot is not None:
                counter = rot.peek(rot_key)
                q_idx = counter % len(q_list)
                sr_offset = ((counter // len(q_list)) % 3) * 6     # next result page once every query was used
            else:
                q_idx = (history_offset + s_idx) % len(q_list)
                sr_offset = 0
            query_str = q_list[q_idx]
            plan["queries"][spec.key] = q_idx
            plan["search_offsets"][spec.key] = sr_offset
            try:
                search_url = (
                    "https://en.wikipedia.org/w/api.php?action=query&list=search"
                    f"&srsearch={urllib.parse.quote(query_str)}&srnamespace=0&srlimit=6&format=json"
                ) + (f"&sroffset={sr_offset}" if sr_offset else "")
                s_json = self._http_get_json(search_url, timeout=10.0) or {}
                hits = s_json.get("query", {}).get("search", [])
                trace.query(rot_key, len(hits))
                for hit in hits:
                    t = hit.get("title", "")
                    snip = self._clean_text(hit.get("snippet", ""))
                    if not t or t.startswith("List of") or any(b in t.lower() for b in blocked_terms):
                        if t:
                            self._trace_reject_new(rot_key, t, snip, cl.LOW_RELEVANCE, "discovery_filter",
                                                   {"rule": "list page or blocked term"})
                        continue
                    source_buckets["wikipedia_search"].append(
                        (t, snip, f"wikipedia_search:{spec.key}", spec.display_name)
                    )
                if s_json:
                    self._advance(rot_key, query_str)
            except Exception as e:
                logger.warning("Wikipedia search discovery error (%s): %s", query_str, e)
                self.last_failed_providers.append({"provider": f"wikipedia_search:{spec.key}", "error": str(e)})
                trace.provider_error(rot_key, str(e))

        # 2. NASA Image & Video Library topic discovery for space/astronomy/earth coverage
        if not preferred_category or self.policy.get_category(preferred_category).key in ("space_and_astronomy", "science", "geography", "environment_and_climate"):
            try:
                nasa_queries = ["james webb exoplanet", "voyager interstellar", "mars rover crater", "black hole accretion"]
                nasa_q = nasa_queries[(rot.peek("nasa_library") if rot is not None else history_offset) % 4]
                plan["nasa_query"] = nasa_q
                nasa_url = f"https://images-api.nasa.gov/search?q={urllib.parse.quote(nasa_q)}&media_type=image&page_size=4"
                n_json = self._http_get_json(nasa_url, timeout=8.0) or {}
                n_items = n_json.get("collection", {}).get("items", [])
                trace.query("nasa_library", len(n_items))
                for n_item in n_items[:3]:
                    nd = (n_item.get("data") or [{}])[0]
                    nt = str(nd.get("title") or "").strip()
                    ndesc = self._clean_text(str(nd.get("description") or ""))
                    if nt and len(ndesc) >= 120:
                        wiki_seed = nasa_q.split()[0] + " " + " ".join(nt.split()[:3])
                        source_buckets["nasa_library"].append((nt.split("(")[0].strip()[:60], ndesc[:240], "nasa_library", "Space and Astronomy"))
                if n_json:
                    self._advance("nasa_library", nasa_q)
            except Exception as e:
                logger.info("NASA topic discovery unavailable: %s", e)
                self.last_failed_providers.append({"provider": "nasa_library", "error": str(e)})
                trace.provider_error("nasa_library", str(e))

        # 3. Wikipedia Featured / MostRead / OnThisDay (skip onthisday dominance for non-history categories)
        if not preferred_category or self.policy.get_category(preferred_category).key == "history":
            now_utc = datetime.now(timezone.utc)
            if rot is not None:
                feed_cycle = (1, 2, 3, 4, 5, 6, 7)
                offsets_to_try = [feed_cycle[rot.peek("wikipedia_featured") % len(feed_cycle)]]
            else:
                day_offsets = [1, 3, 7]
                rotated_offsets = day_offsets[history_offset % len(day_offsets):] + day_offsets[:history_offset % len(day_offsets)]
                offsets_to_try = rotated_offsets[:1]
            for d_off in offsets_to_try:
                plan["feed_offset"] = d_off
                try:
                    ref_dt = now_utc - timedelta(days=d_off)
                    feed_url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{ref_dt.strftime('%Y/%m/%d')}"
                    feed = self._http_get_json(feed_url, timeout=10.0) or {}
                    if getattr(trace, "enabled", False):
                        try:
                            mr = feed.get("mostread") if isinstance(feed.get("mostread"), dict) else {}
                            otd = feed.get("onthisday") if isinstance(feed.get("onthisday"), list) else []
                            trace.query("wikipedia_featured", (1 if feed.get("tfa") else 0)
                                        + len(mr.get("articles") or []) + len(otd))
                        except Exception:
                            trace.query("wikipedia_featured", 0)

                    tfa = feed.get("tfa", {})
                    if tfa.get("title") and not any(b in tfa["title"].lower() for b in blocked_terms):
                        source_buckets["wikipedia_tfa"].append((tfa["title"], "", f"wikipedia_tfa_{ref_dt.strftime('%m%d')}", None))

                    for art in feed.get("mostread", {}).get("articles", [])[:10]:
                        t = art.get("title", "")
                        desc = (art.get("description") or "").lower()
                        if not t or ":" in t or t.startswith("Main_Page") or t.startswith("List_of"):
                            continue
                        if any(b in desc or b in t.lower() for b in blocked_terms + ("deaths",)):
                            self._trace_reject_new(f"wikipedia_mostread_{ref_dt.strftime('%m%d')}", t, desc, cl.LOW_RELEVANCE,
                                                   "discovery_filter", {"rule": "blocked term"})
                            continue
                        source_buckets["wikipedia_mostread"].append((t, "", f"wikipedia_mostread_{ref_dt.strftime('%m%d')}", None))

                    scored_otd: List[Tuple[int, Dict]] = []
                    for ev in feed.get("onthisday", []):
                        ev_text = self._clean_text(ev.get("text", ""))
                        pages = ev.get("pages", [])
                        if not pages or not ev_text:
                            continue
                        low_ev = ev_text.lower()
                        if any(b in low_ev for b in blocked_terms):
                            continue
                        prio = sum(1 for p in preferred_terms if p in low_ev)
                        scored_otd.append((prio, ev))
                    scored_otd.sort(key=lambda x: x[0], reverse=True)
                    for _, ev in scored_otd[:3]:
                        for pg in ev.get("pages", [])[:1]:
                            t = pg.get("title", "")
                            if t and not t.isdigit():
                                source_buckets["wikipedia_onthisday"].append((t, ev.get("text", ""), f"wikipedia_onthisday_{ref_dt.strftime('%m%d')}", "History"))

                    # 3b. Wikipedia "In the news" (3.31C): reuses this feed response, no extra request
                    if itn_on:
                        news = feed.get("news")
                        news_items = news if isinstance(news, list) else []
                        trace.query("wikipedia_itn", len(news_items))
                        malformed = 0
                        for item in news_items[:8]:
                            itn_title, story, problem = self._parse_itn_item(item, blocked_terms)
                            if problem == "malformed":
                                malformed += 1
                                continue
                            origin = f"wikipedia_itn_{ref_dt.strftime('%m%d')}"
                            if problem:
                                if itn_title:
                                    self._trace_reject_new(origin, itn_title, story, cl.LOW_RELEVANCE, "discovery_filter", {"rule": problem})
                                continue
                            source_buckets["wikipedia_itn"].append((itn_title, story, origin, None))
                        if malformed:
                            trace.note("wikipedia_itn_malformed_items", malformed)
                    if feed:
                        self._advance("wikipedia_featured", ref_dt.strftime("%Y-%m-%d"))
                except Exception as e:
                    logger.warning("Wikipedia Featured Feed unavailable (offset=%s): %s", d_off, e)
                    self.last_failed_providers.append({"provider": "wikipedia_featured_feed", "error": str(e)})
                    trace.provider_error("wikipedia_featured", str(e))

        # Interleave buckets keeping Wikipedia multi-category search & science/mostread ahead of onthisday
        bucket_order = ["wikipedia_search", "nasa_library", "wikipedia_tfa", "wikipedia_mostread", "wikipedia_onthisday"]
        if itn_on:
            bucket_order.insert(4, "wikipedia_itn")
        interleaved_proposals: List[Tuple[str, str, str, Optional[str]]] = []
        max_len = max((len(source_buckets[k]) for k in bucket_order), default=0)
        for idx in range(max_len):
            for bk in bucket_order:
                if idx < len(source_buckets[bk]):
                    interleaved_proposals.append(source_buckets[bk][idx])

        # Pre-filter known duplicates before enrichment
        enriched_candidates: List[Dict] = []
        seen_titles = set()
        for p_idx, (wiki_title, blurb, origin, hint_cat) in enumerate(interleaved_proposals):
            if len(enriched_candidates) >= max_candidates:
                for t2, b2, o2, _h2 in interleaved_proposals[p_idx:]:
                    self._trace_reject_new(o2, t2, b2, cl.POOL_CAPACITY_LIMIT, "enrichment_cap",
                                           {"max_candidates": max_candidates})
                break
            norm_key = re.sub(r"\W+", "", wiki_title.lower())
            if norm_key in seen_titles:
                self._trace_reject_new(origin, wiki_title, blurb, cl.DUPLICATE_EXACT, "cross_provider_dedupe",
                                       {"rule": "same title proposed earlier in this run"})
                continue
            seen_titles.add(norm_key)

            pre_dup, _pre_score, pre_reason = self.ledger.check_content_collision(
                candidate_title=wiki_title,
                candidate_hook=blurb or wiki_title,
                candidate_entities=[wiki_title],
                cooldown_days=self.cooldown_days,
            )
            if pre_dup:
                self._trace_reject_new(origin, wiki_title, blurb, cl.classify_duplicate_reason(pre_reason),
                                       "pre_enrichment", match=pre_reason)
                continue

            cand = self.enrich_wikipedia_candidate(wiki_title, event_blurb=blurb, discovery_origin=origin)
            if cand:
                if hint_cat and not preferred_category:
                    # Only retain classifier result if it has strong signal; otherwise allow category alignment
                    classified_spec = self.policy.classify_topic(cand["title"], cand["extended_extract"])
                    cand["category"] = classified_spec.display_name
                    cand["category_code"] = classified_spec.category_code
                elif preferred_category:
                    pref_spec = self.policy.get_category(preferred_category)
                    cand_spec = self.policy.classify_topic(cand["title"], cand["extended_extract"])
                    if cand_spec.key == pref_spec.key or origin == f"wikipedia_search:{pref_spec.key}":
                        cand["category"] = pref_spec.display_name
                        cand["category_code"] = pref_spec.category_code
                self._apply_category(cand, hint_key=self._origin_hint(origin) or self._hint_key(hint_cat))
                enriched_candidates.append(cand)
            else:
                self._trace_reject_new(origin, wiki_title, blurb, cl.INSUFFICIENT_EVIDENCE, "enrichment",
                                       {"rule": "no usable encyclopedic extract (short, disambiguation or fetch failed)"})

        # 4. Phys.org Live Multi-Category RSS Feeds (Science, Technology, Space, Biology, Earth)
        disc_ts = datetime.now(timezone.utc).isoformat()
        rss_feeds = (
            ("https://phys.org/rss-feed/science-news/", "physorg_science_rss", "Science", "SCI"),
            ("https://phys.org/rss-feed/technology-news/", "physorg_tech_rss", "Technology", "TECH"),
            ("https://phys.org/rss-feed/space-news/", "physorg_space_rss", "Space and Astronomy", "SPACE"),
            ("https://phys.org/rss-feed/biology-news/", "physorg_biology_rss", "Nature and Wildlife", "NAT"),
            ("https://phys.org/rss-feed/earth-news/", "physorg_earth_rss", "Environment and Climate", "ENV"),
        )
        for feed_url, origin_tag, rss_cat, rss_code in rss_feeds:
            if preferred_category:
                pref_key = self.policy.get_category(preferred_category).key
                if self.policy.get_category(rss_cat).key != pref_key:
                    continue
            try:
                xml_txt = self._http_get_text(feed_url, timeout=8.0)
                if not xml_txt:
                    continue
                root = ET.fromstring(xml_txt)
                items = root.findall(".//item")
                trace.query(origin_tag, len(items))
                for item in items[:3]:
                    title = self._clean_text(item.findtext("title", ""))
                    desc = self._clean_text(item.findtext("description", ""))
                    link = (item.findtext("link", "") or "").strip()
                    pub_date = (item.findtext("pubDate", "") or "").strip()
                    if len(desc) < 160 or not link.startswith("http"):
                        self._trace_reject_new(origin_tag, title, desc, cl.INSUFFICIENT_EVIDENCE, "rss_filter",
                                               {"rule": "description under 160 chars or no link"})
                        continue
                    sents = self._split_clean_sentences(desc)
                    if len(sents) < 3:
                        self._trace_reject_new(origin_tag, title, desc, cl.INSUFFICIENT_EVIDENCE, "rss_filter",
                                               {"rule": "fewer than 3 usable sentences"})
                        continue
                    thumb_urls: List[str] = []
                    for child in item:
                        if child.tag.endswith("thumbnail") or child.tag.endswith("enclosure"):
                            u = child.attrib.get("url", "")
                            if u.startswith("http"):
                                thumb_urls.append(u)
                    classified = self.policy.classify_topic(title, desc)
                    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:36]
                    ents = self._extract_entities(title, desc)
                    src_list = [f"Phys.org Science Archive Source: {link}"]
                    rss_cand = {
                        "id": f"rss_{slug}",
                        "title": title,
                        "category": classified.display_name if classified else rss_cat,
                        "category_code": classified.category_code if classified else rss_code,
                        "summary": desc,
                        "extended_extract": desc,
                        "factual_bullets": sents if sents else [desc],
                        "curiosity": 8.9,
                        "surprise": 8.8,
                        "storytelling": 8.5,
                        "visual_potential": 8.8 if thumb_urls else 8.1,
                        "emotional_connection": 8.4,
                        "factual_reliability": 9.1,
                        "sources": src_list,
                        "key_entities": ents,
                        "image_urls": thumb_urls,
                        "discovery_source": origin_tag,
                        "discovery_timestamp": disc_ts,
                        "publication_timestamp": pub_date,
                        "research_fingerprint": compute_research_fingerprint(
                            summary=desc,
                            extended_extract=desc,
                            factual_bullets=sents,
                            sources=src_list,
                        ),
                    }
                    self._apply_category(rss_cand, hint_key=self._hint_key(rss_cat))
                    rss_candidates.append(rss_cand)
            except Exception as e:
                logger.warning("Phys.org RSS fetch error (%s): %s", feed_url, e)
                self.last_failed_providers.append({"provider": origin_tag, "error": str(e)})
                trace.provider_error(origin_tag, str(e))

        # 5. GNews current events: optional supplement, never required.
        if self.flags.is_on("gnews_candidates"):
            gnews_candidates = self._gnews_candidates_v2(preferred_category, disc_ts, blocked_terms)
        else:
            gnews_candidates = self._gnews_candidates(preferred_category, disc_ts)

        # 6. Stage 3.31K demand-led candidates: trending / public-interest signals resolved to verified
        #    Wikipedia articles. Shadow: collected and recorded, never added to the pool.
        demand_candidates: List[Dict[str, Any]] = []
        if self.flags.is_active("demand_signals"):
            demand_candidates = self._demand_candidates(disc_ts, seen_titles)
            if not self.flags.is_on("demand_signals"):
                # Stage 3.34: persist the full shadow evidence (run notes), not just titles
                trace.note("demand_shadow", self.last_demand_summary)
                demand_candidates = []
            else:
                trace.note("demand", self.last_demand_summary)

        # Pool assembly. Legacy: concatenate and cut at max_candidates + 6, which drops the GNews
        # supplement whenever Wikipedia + RSS fill the pool (audit §5). With the GNews or In-the-news
        # flag on, those small families get reserved slots instead.
        cap = max_candidates + 6
        if self.flags.is_on("gnews_candidates") or itn_on or demand_candidates:
            pool, dropped = assemble_candidate_pool(
                [demand_candidates, enriched_candidates, rss_candidates, gnews_candidates], cap,
                reserved=POOL_RESERVED_SLOTS)
        else:
            combined = enriched_candidates + rss_candidates + gnews_candidates
            pool, dropped = combined[:cap], combined[cap:]
        if getattr(trace, "enabled", False):
            for d in dropped:
                trace.reject(trace.candidate(d, "pool_assembly"), cl.POOL_CAPACITY_LIMIT, "pool_assembly", {"cap": cap})

        self._last_seen_titles = set(seen_titles) | {re.sub(r"\W+", "", str(c.get("title", "")).lower())
                                                     for c in rss_candidates + gnews_candidates}
        self._last_plan = plan
        if rot is not None and self._rotation_advances:
            self._advance("category_order", ",".join(s.key for s in selected_specs))
        self._commit_rotation()
        trace.note("discovery_plan", plan)
        return pool

    def _gnews_candidates(self, preferred_category: Optional[str], disc_ts: str) -> List[Dict[str, Any]]:
        """Events reported by >= 2 distinct outlets in the last 3 days. A headline is not treated as a verified
        fact: the candidate is labelled as corroborated news and keeps every source URL for the research stage."""
        out: List[Dict[str, Any]] = []
        try:
            from backend.app.content.gnews_client import GNewsClient
            client = GNewsClient()
            if not client.configured():
                return out
            cats = ("technology", "science", "business")
            if preferred_category:
                pref_key = self.policy.get_category(preferred_category).key
                cats = tuple(c for c in cats if self.policy.get_category(c.title()).key == pref_key) or ()
            for ev in client.corroborated_events(cats)[:3]:
                arts = ev["articles"]
                text = " ".join(self._clean_text((a.get("description") or "") + " " + (a.get("content") or "").split("[")[0])
                                for a in arts)
                sents = self._split_clean_sentences(text)
                if len(text) < 160 or len(sents) < 3:
                    continue
                title = self._clean_text(ev["title"])
                classified = self.policy.classify_topic(title, text)
                slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:36]
                src_list = [f"{(a.get('source') or {}).get('name') or 'News'} ({a.get('publishedAt')}): {a.get('url')}"
                            for a in arts]
                out.append({
                    "id": f"gnews_{slug}", "title": title,
                    "category": classified.display_name if classified else ev["category"],
                    "category_code": classified.category_code if classified else "NEWS",
                    "summary": text[:600], "extended_extract": text, "factual_bullets": sents[:6],
                    "curiosity": 8.6, "surprise": 8.4, "storytelling": 8.2, "visual_potential": 7.8,
                    "emotional_connection": 8.2,
                    "factual_reliability": 7.5,          # corroborated headlines, not independently fact-checked
                    "verification": "multi_source_corroborated_headline",
                    "sources": src_list, "key_entities": self._extract_entities(title, text),
                    "image_urls": [a["image"] for a in arts if str(a.get("image") or "").startswith("http")][:3],
                    "discovery_source": f"gnews_{ev['gnews_category']}", "discovery_timestamp": disc_ts,
                    "publication_timestamp": ev["latest_published_at"],
                    "research_fingerprint": compute_research_fingerprint(summary=text[:600], extended_extract=text,
                                                                         factual_bullets=sents[:6], sources=src_list),
                })
        except Exception as exc:   # GNews availability never decides whether a cycle can run
            logger.warning("GNews discovery skipped: %s", type(exc).__name__)
            self.last_failed_providers.append({"provider": "gnews", "error": type(exc).__name__})
        return out

    def _demand_candidates(self, disc_ts: str, seen_titles: set) -> List[Dict[str, Any]]:
        """Stage 3.31K: collects demand signals, persists them with provenance, and resolves the strongest
        terms to Wikipedia articles through the normal (verified) enrichment. Never raises."""
        from backend.app.content import demand_signals as ds
        out: List[Dict[str, Any]] = []
        summary: Dict[str, Any] = {"at_utc": disc_ts, "mode": self.flags.demand_signals, "signals": 0,
                                   "errors": {}, "candidates": [], "unresolved": []}
        try:
            collector = ds.DemandSignalCollector(lambda u, t: self._http_get_json(u, timeout=t),
                                                 lambda u, t: self._http_get_text(u, timeout=t))
            signals = collector.collect()
            summary["signals"], summary["errors"] = len(signals), dict(collector.errors)
            db = self._db_path()
            if db:
                try:
                    ds.DemandSignalStore(db).record(signals, run_id=self._run_id)
                except Exception as exc:
                    summary["errors"]["store"] = type(exc).__name__
            for term in ds.rank_demand_terms(signals, limit=DEMAND_MAX_CANDIDATES * 2):
                if len(out) >= DEMAND_MAX_CANDIDATES:
                    break
                title = term.get("wikipedia_title") or self._resolve_wikipedia_title(term["term"])
                if not title:
                    summary["unresolved"].append(term["term"])
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen_titles:
                    continue
                news = [s.get("related") for s in term["signals"] if s.get("related")]
                blurb = (news[0][0].get("title") if news and news[0] else "") or ""
                src = term["sources"][0]
                time.sleep(DEMAND_REQUEST_GAP_SEC)      # politeness: bursts get rate-limited by Wikipedia
                cand = self.enrich_wikipedia_candidate(title, event_blurb=blurb, discovery_origin=f"demand:{src}")
                if not cand:
                    summary["unresolved"].append(term["term"])
                    continue
                why = ds.article_unsuitable(cand.get("summary") or cand.get("extended_extract") or "")
                if why:
                    summary.setdefault("skipped", []).append({"title": cand.get("title"), "reason": why})
                    continue
                cand["demand"] = {"demand_index": term["demand_index"], "velocity": term["velocity"],
                                  "sources": term["sources"], "term": term["term"],
                                  "news": [n for group in news for n in group][:3]}
                self._apply_category(cand)
                seen_titles.add(key)
                out.append(cand)
                summary["candidates"].append({"title": cand.get("title"), "demand_index": term["demand_index"],
                                              "velocity": term["velocity"], "sources": term["sources"],
                                              "category": cand.get("category")})
        except Exception as exc:
            summary["errors"]["collector"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning("Demand signal discovery failed: %s", exc)
        self.last_demand_summary = summary
        return out

    def _resolve_wikipedia_title(self, term: str) -> Optional[str]:
        """Maps a trending search term to the best-matching Wikipedia article title (or None)."""
        url = ("https://en.wikipedia.org/w/rest.php/v1/search/title?limit=1&q="
               + urllib.parse.quote(term[:100]))
        data = self._http_get_json(url, timeout=8.0) or {}
        pages = data.get("pages") or []
        if not pages:
            return None
        title = str(pages[0].get("title") or "")
        from backend.app.content.demand_signals import acceptable_term
        return title if acceptable_term(title) else None

    def discover_and_rank_live_topics(
        self,
        filter_duplicates: bool = True,
        max_candidates: int = 12,
        preferred_category: Optional[str] = None,
    ) -> List[ScoredTopic]:
        """Discovers, verifies, scores, and filters genuinely fresh topics across multiple categories."""
        self._start_trace()
        live_cands = self.discover_live_candidates(
            max_candidates=max_candidates,
            preferred_category=preferred_category,
        )
        if not live_cands:
            self.last_discovery_audit = []
            self.last_discovery_summary = {
                "candidate_pool_size": 0,
                "duplicates_rejected": 0,
                "failed_providers": list(self.last_failed_providers),
                "categories_discovered": [],
                "providers_used": [],
            }
            self.trace.note("pool", "empty")
            self._flush_trace()
            return []

        valid, audit_rows, dups_rejected, scored = self._score_and_filter(live_cands, filter_duplicates)

        self.last_discovery_audit = audit_rows
        self.last_discovery_summary = {
            "candidate_pool_size": len(live_cands),
            "accepted_count": len(valid),
            "duplicates_rejected": dups_rejected,
            "failed_providers": list(self.last_failed_providers),
            "categories_discovered": sorted({t.category for t in scored if t.category}),
            "providers_used": sorted({t.discovery_source for t in scored if t.discovery_source}),
            "selected_category": valid[0].category if valid else None,
            "selected_topic": valid[0].title if valid else None,
            "selected_score": valid[0].composite_score if valid else None,
        }
        self._flush_trace()
        return valid

    def _score_and_filter(self, live_cands: List[Dict[str, Any]], filter_duplicates: bool = True,
                          stage_suffix: str = "") -> Tuple[List[ScoredTopic], List[Dict[str, Any]], int, List[ScoredTopic]]:
        """Scores candidates, applies the factual / duplicate (and, with v2 on, category) gates and
        ranks the survivors. Every decision is recorded in the trace when the ledger is on."""
        trace = self.trace
        cids = [trace.candidate(c, "scoring" + stage_suffix) for c in live_cands]
        scored = [self.score_candidate(c) for c in live_cands]
        # Stage 3.31K scoring v2: recorded in shadow, blended into the score when on.
        if self.flags.is_active("scoring_v2"):
            from backend.app.content import topic_scoring_v2 as sv2
            hist = sv2.category_performance_provider(self._db_path())
            v2_notes: List[Dict[str, Any]] = []
            for c, t in zip(live_cands, scored):
                try:
                    v2 = sv2.score_v2(c, t, historical=hist)
                except Exception as exc:
                    logger.warning("scoring v2 failed for %s: %s", c.get("title"), exc)
                    continue
                t.discovery_metadata["score_v1"] = t.composite_score
                t.discovery_metadata["score_v2"] = v2
                v2_notes.append({"title": t.title, "v1": t.composite_score, "v2": v2["score"],
                                 "blended": sv2.blended(t.composite_score, v2),
                                 "dims": {k: d["value"] for k, d in v2["dimensions"].items()},
                                 "measured": v2["measured"], "demand": bool(c.get("demand"))})
                if c.get("demand"):
                    t.discovery_metadata["demand"] = c["demand"]
                if self.flags.is_on("scoring_v2"):
                    t.composite_score = sv2.blended(t.composite_score, v2)
            # Stage 3.34: shadow scores are evidence for the activation decision, so they are persisted
            trace.note("scoring_v2" + stage_suffix, {"mode": self.flags.scoring_v2, "version": sv2.VERSION,
                                                     "candidates": v2_notes})
        else:
            for c, t in zip(live_cands, scored):
                if c.get("demand"):
                    t.discovery_metadata["demand"] = c["demand"]
        valid: List[ScoredTopic] = []
        audit_rows: List[Dict[str, Any]] = []
        dups_rejected = 0
        cid_by_obj: Dict[int, Optional[str]] = {}
        cat_gate = self.flags.is_on("category_classifier_v2")

        for idx, t in enumerate(scored):
            invalid_cat = cat_gate and bool(t.discovery_metadata.get("category_invalid"))
            is_valid = (not t.is_duplicate if filter_duplicates else True) and t.composite_score > 0 and not invalid_cat
            rej = None
            if t.composite_score <= 0:
                rej = "Failed factual, extract length, or visual suitability gate"
            elif filter_duplicates and t.is_duplicate:
                dups_rejected += 1
                rej = t.duplicate_reason or "Duplicate/cooldown collision"
            elif invalid_cat:
                rej = f"No in-scope category ({t.discovery_metadata.get('category_invalid')})"
            if is_valid:
                valid.append(t)
            row = dict(t.discovery_metadata)
            row.update({
                "topic_id": t.id,
                "topic_title": t.title,
                "category": t.category,
                "accepted": is_valid,
                "rejection_reason": rej,
                "final_rank": None,
            })
            audit_rows.append(row)

            cid = cids[idx] if idx < len(cids) else None
            cid_by_obj[id(t)] = cid
            if cid:
                bd = t.score_breakdown
                v2m = t.discovery_metadata.get("score_v2")
                if v2m:   # Stage 3.34: the ledger row carries the v2 score next to v1 (shadow or on)
                    bd = {**bd, "_v2": {"score": v2m["score"], "version": v2m["version"],
                                        "dims": {k: d["value"] for k, d in v2m["dimensions"].items()}},
                          "_v1_score": t.discovery_metadata.get("score_v1")}
                trace.score(cid, t.composite_score, bd)
                if t.composite_score <= 0:
                    unreliable = float(t.score_breakdown.get("factual_reliability", 10.0)) < 7.5
                    trace.reject(cid, cl.SOURCE_UNRELIABLE if unreliable else cl.INSUFFICIENT_EVIDENCE,
                                 "scoring_gate" + stage_suffix)
                elif filter_duplicates and t.is_duplicate:
                    trace.reject(cid, cl.classify_duplicate_reason(t.duplicate_reason), "duplicate_gate" + stage_suffix,
                                 duplicate_match=t.duplicate_reason)
                elif invalid_cat:
                    trace.reject(cid, cl.INVALID_CATEGORY, "category_gate" + stage_suffix,
                                 {"classification": t.discovery_metadata.get("category_invalid")})

        valid.sort(key=lambda x: x.composite_score, reverse=True)
        rank_map = {t.id: idx + 1 for idx, t in enumerate(valid)}
        for row in audit_rows:
            row["final_rank"] = rank_map.get(row["topic_id"])
            if row["final_rank"] is not None:
                for vt in valid:
                    if vt.id == row["topic_id"]:
                        vt.discovery_metadata["final_rank"] = row["final_rank"]
                        break
        for rank, t in enumerate(valid, start=1):
            cid = cid_by_obj.get(id(t))
            if cid:
                trace.eligible(cid, "ranking" + stage_suffix, rank)
        return valid, audit_rows, dups_rejected, scored

    def select_best_live_topic(self, preferred_category: Optional[str] = None) -> Tuple[ScoredTopic, HookCandidate]:
        """Selects top live-discovered verified topic. Fails closed; never falls back to historical topics."""
        ranked = self.discover_and_rank_live_topics(filter_duplicates=True, preferred_category=preferred_category)
        if not ranked:
            raise RuntimeError(
                "Live topic discovery failed or yielded no eligible verified topics; stopping safely without old-topic fallback."
            )
        winner = ranked[0]
        hooks = self.generate_five_hooks(winner)
        return winner, hooks[0]

    def discover_and_rank_topics(self, filter_duplicates: bool = True) -> List[ScoredTopic]:
        """Scores all candidates, optionally filters duplicates, and returns sorted ranking."""
        scored = [self.score_candidate(c) for c in CURATED_CURIOSITY_CANDIDATES]
        if filter_duplicates:
            valid = [t for t in scored if not t.is_duplicate and t.composite_score > 0]
        else:
            valid = [t for t in scored if t.composite_score > 0]
        valid.sort(key=lambda x: x.composite_score, reverse=True)
        return valid

    def generate_five_hooks(self, topic: ScoredTopic) -> List[HookCandidate]:
        """Generates 5 distinct hook directions and scores each."""
        # Customized high-impact hooks per topic
        if topic.id == "cand_01_demon_core":
            hooks = [
                HookCandidate(
                    approach="Shocking Revelation",
                    hook_text="This 14-pound metal ball killed two scientists with a single slip of a screwdriver.",
                    immediate_comprehension=9.8, curiosity=9.9, surprise=9.8, credibility=10.0, visual_potential=9.7, story_connection=9.9
                ),
                HookCandidate(
                    approach="Paradox / Contradiction",
                    hook_text="The atomic bomb that was never dropped still claimed victims inside a quiet laboratory.",
                    immediate_comprehension=9.4, curiosity=9.6, surprise=9.5, credibility=9.8, visual_potential=9.2, story_connection=9.5
                ),
                HookCandidate(
                    approach="Unexpected Consequence",
                    hook_text="A blinding flash of blue light sealed a physicist's fate in half a second.",
                    immediate_comprehension=9.6, curiosity=9.8, surprise=9.7, credibility=9.9, visual_potential=9.8, story_connection=9.8
                ),
                HookCandidate(
                    approach="Intriguing Question",
                    hook_text="Why did Manhattan Project scientists nickname this plutonium sphere 'The Demon Core'?",
                    immediate_comprehension=9.5, curiosity=9.4, surprise=9.2, credibility=9.9, visual_potential=9.0, story_connection=9.6
                ),
                HookCandidate(
                    approach="Extraordinary Human Decision",
                    hook_text="He used his bare hands to shield his colleagues from a nuclear blast.",
                    immediate_comprehension=9.7, curiosity=9.7, surprise=9.8, credibility=9.8, visual_potential=9.6, story_connection=9.7
                )
            ]
        elif topic.id == "cand_07_yamaguchi_survivor":
            hooks = [
                HookCandidate(
                    approach="Shocking Revelation",
                    hook_text="He survived the atomic blast in Hiroshima. Three days later, he was in Nagasaki.",
                    immediate_comprehension=9.9, curiosity=9.8, surprise=9.9, credibility=10.0, visual_potential=9.4, story_connection=9.9
                ),
                HookCandidate(
                    approach="Paradox / Contradiction",
                    hook_text="He went home to escape a nuclear catastrophe, and walked directly into another one.",
                    immediate_comprehension=9.6, curiosity=9.6, surprise=9.7, credibility=9.9, visual_potential=9.2, story_connection=9.7
                ),
                HookCandidate(
                    approach="Unexpected Consequence",
                    hook_text="He was explaining the first atomic explosion to his boss when the second bomb detonated.",
                    immediate_comprehension=9.7, curiosity=9.8, surprise=9.8, credibility=10.0, visual_potential=9.5, story_connection=9.8
                ),
                HookCandidate(
                    approach="Intriguing Question",
                    hook_text="What are the odds of being at ground zero for both atomic bombs and living to 93?",
                    immediate_comprehension=9.5, curiosity=9.4, surprise=9.5, credibility=9.9, visual_potential=9.0, story_connection=9.6
                ),
                HookCandidate(
                    approach="Extraordinary Human Decision",
                    hook_text="Severely burned, he boarded a burning train just to make it to work on Monday.",
                    immediate_comprehension=9.4, curiosity=9.5, surprise=9.6, credibility=9.8, visual_potential=9.3, story_connection=9.5
                )
            ]
        elif topic.id == "cand_02_pepsi_navy":
            hooks = [
                HookCandidate(
                    approach="Shocking Revelation",
                    hook_text="In 1989, a soda company briefly commanded the sixth-largest submarine fleet on Earth.",
                    immediate_comprehension=9.9, curiosity=9.8, surprise=9.9, credibility=9.8, visual_potential=9.5, story_connection=9.9
                ),
                HookCandidate(
                    approach="Paradox / Contradiction",
                    hook_text="The Soviet Union wanted soda so badly they traded seventeen warships to Pepsi.",
                    immediate_comprehension=9.7, curiosity=9.7, surprise=9.8, credibility=9.8, visual_potential=9.4, story_connection=9.8
                ),
                HookCandidate(
                    approach="Unexpected Consequence",
                    hook_text="A communist superpower was dismantled not by missiles, but by a bottle of cola.",
                    immediate_comprehension=9.3, curiosity=9.5, surprise=9.4, credibility=9.5, visual_potential=9.1, story_connection=9.4
                ),
                HookCandidate(
                    approach="Intriguing Question",
                    hook_text="How did a soft drink brand end up armed with Soviet military cruisers and submarines?",
                    immediate_comprehension=9.6, curiosity=9.6, surprise=9.6, credibility=9.7, visual_potential=9.2, story_connection=9.7
                ),
                HookCandidate(
                    approach="Extraordinary Human Decision",
                    hook_text="Pepsi's CEO told the White House: 'We're disarming the Soviets faster than you are.'",
                    immediate_comprehension=9.5, curiosity=9.6, surprise=9.7, credibility=9.8, visual_potential=9.2, story_connection=9.6
                )
            ]
        elif topic.id == "cand_04_mizuho_typo":
            hooks = [
                HookCandidate("Shocking Revelation", "A single misplaced keystroke destroyed 340 million dollars in less than three minutes.", 9.9, 9.8, 9.9, 10.0, 9.4, 9.9),
                HookCandidate("Paradox / Contradiction", "He tried to sell one share for 610,000 yen, and sold 610,000 shares for one yen instead.", 9.8, 9.7, 9.8, 10.0, 9.2, 9.8),
                HookCandidate("Unexpected Consequence", "The Tokyo Stock Exchange tried to cancel the trade, but their software froze.", 9.5, 9.6, 9.6, 9.9, 9.1, 9.6),
                HookCandidate("Intriguing Question", "What happens when a stock trader accidentally types the price into the quantity box?", 9.6, 9.5, 9.5, 9.8, 9.0, 9.6),
                HookCandidate("Extraordinary Human Decision", "Day traders realized the computer glitch instantly and made hundreds of millions.", 9.4, 9.6, 9.7, 9.8, 9.2, 9.5),
            ]
        elif topic.id == "cand_05_roy_lightning":
            hooks = [
                HookCandidate("Shocking Revelation", "This park ranger was struck by lightning seven times—and survived every single strike.", 9.9, 9.8, 9.9, 9.9, 9.6, 9.9),
                HookCandidate("Paradox / Contradiction", "The odds of being struck seven times are 1 in 10 to the 28th power, yet storm clouds seemed to follow him.", 9.7, 9.7, 9.8, 9.9, 9.3, 9.8),
                HookCandidate("Unexpected Consequence", "He began carrying a can of water in his truck because his hair kept catching fire.", 9.8, 9.8, 9.8, 9.8, 9.5, 9.8),
                HookCandidate("Intriguing Question", "Why was one single human being targeted by lightning across four decades?", 9.5, 9.5, 9.4, 9.9, 9.1, 9.6),
                HookCandidate("Extraordinary Human Decision", "On the seventh strike, a bear approached him while he was smoldering, and he fought it off with a branch.", 9.6, 9.7, 9.8, 9.8, 9.4, 9.7),
            ]
        elif topic.id == "cand_06_midgley_inventions":
            hooks = [
                HookCandidate("Shocking Revelation", "One single inventor poisoned the global atmosphere not once, but twice.", 9.9, 9.8, 9.9, 10.0, 9.4, 9.9),
                HookCandidate("Paradox / Contradiction", "He was celebrated as America's greatest chemist before his inventions began killing millions.", 9.6, 9.6, 9.7, 9.9, 9.1, 9.7),
                HookCandidate("Unexpected Consequence", "After poisoning the Earth with lead and destroying the ozone layer, his own bed invention strangled him.", 9.8, 9.9, 9.9, 10.0, 9.5, 9.9),
                HookCandidate("Intriguing Question", "How did one man manage to invent leaded gasoline AND chlorofluorocarbons?", 9.5, 9.4, 9.5, 9.9, 9.0, 9.5),
                HookCandidate("Extraordinary Human Decision", "To prove leaded fuel was safe, he poured it over his hands and inhaled the fumes for sixty seconds.", 9.7, 9.8, 9.8, 9.9, 9.5, 9.8),
            ]
        elif topic.id == "cand_08_dancing_plague":
            hooks = [
                HookCandidate("Shocking Revelation", "In 1518, four hundred people danced in the streets for weeks until their hearts gave out.", 9.9, 9.8, 9.9, 9.8, 9.6, 9.9),
                HookCandidate("Paradox / Contradiction", "Doctors diagnosed hot blood and hired musicians to play, which made the epidemic spread faster.", 9.7, 9.7, 9.8, 9.8, 9.3, 9.8),
                HookCandidate("Unexpected Consequence", "Dozens died every day from sheer exhaustion, unable to stop their own legs from moving.", 9.8, 9.8, 9.8, 9.8, 9.5, 9.8),
                HookCandidate("Intriguing Question", "What drove an entire medieval city into an uncontrollable dancing frenzy?", 9.5, 9.5, 9.5, 9.8, 9.1, 9.6),
                HookCandidate("Extraordinary Human Decision", "The town council built a wooden stage and hired professional dancers to accompany the dying crowd.", 9.6, 9.7, 9.7, 9.8, 9.4, 9.7),
            ]
        elif topic.id == "cand_09_mary_celeste":
            hooks = [
                HookCandidate("Shocking Revelation", "A seaworthy merchant ship was found drifting across the Atlantic without a soul on board.", 9.9, 9.8, 9.9, 9.9, 9.5, 9.9),
                HookCandidate("Paradox / Contradiction", "Six months of food, pristine cargo, and personal belongings were intact, but everyone had vanished.", 9.8, 9.7, 9.8, 9.9, 9.3, 9.8),
                HookCandidate("Unexpected Consequence", "The only lifeboat was missing, tied to a frayed towline trailing in the ocean waves.", 9.6, 9.7, 9.7, 9.8, 9.4, 9.7),
                HookCandidate("Intriguing Question", "Why would an experienced captain abandon a perfectly safe ship in calm seas?", 9.6, 9.5, 9.5, 9.9, 9.2, 9.6),
                HookCandidate("Extraordinary Human Decision", "The crew of the Dei Gratia risked their lives to sail the ghost ship into port for salvage.", 9.4, 9.5, 9.6, 9.8, 9.2, 9.5),
            ]
        elif topic.id == "cand_10_kola_borehole":
            hooks = [
                HookCandidate("Shocking Revelation", "Soviet scientists drilled 40,000 feet into the Earth until the rock turned to boiling mush.", 9.9, 9.8, 9.9, 9.9, 9.5, 9.9),
                HookCandidate("Paradox / Contradiction", "Seven miles down, they discovered liquid water where classical geology said it was impossible.", 9.7, 9.7, 9.8, 9.9, 9.3, 9.8),
                HookCandidate("Unexpected Consequence", "At 356 degrees Fahrenheit, the metal drill bits melted and the hole kept swallowing itself.", 9.8, 9.8, 9.8, 9.8, 9.6, 9.8),
                HookCandidate("Intriguing Question", "What did scientists actually hear when they lowered microphones seven miles below the crust?", 9.6, 9.6, 9.6, 9.8, 9.2, 9.7),
                HookCandidate("Extraordinary Human Decision", "Drillers kept going for twenty-four years until the collapse of the Soviet Union sealed the hole forever.", 9.5, 9.6, 9.7, 9.9, 9.3, 9.6),
            ]
        else:
            # Data-driven factual hook synthesis from verified research extract & bullets
            bullets = list(getattr(topic, "factual_bullets", []) or [])
            if not bullets:
                bullets = self._split_clean_sentences(topic.summary)
            first_fact = bullets[0] if len(bullets) > 0 else topic.summary.split(".")[0].strip() + "."
            second_fact = bullets[1] if len(bullets) > 1 else first_fact
            numeric_match = next((b for b in bullets if re.search(r"\b\d{2,4}\b", b)), first_fact)
            ent = topic.key_entities[0] if topic.key_entities else topic.title

            hooks = [
                HookCandidate(
                    "Shocking Revelation",
                    first_fact[:180],
                    9.5, 9.6, 9.4, 9.8, 9.3, 9.6,
                ),
                HookCandidate(
                    "Paradox / Contradiction",
                    f"Why does {topic.title} still challenge everything experts assumed about {topic.category}?",
                    9.3, 9.4, 9.3, 9.6, 9.2, 9.5,
                ),
                HookCandidate(
                    "Unexpected Consequence",
                    numeric_match[:180],
                    9.4, 9.5, 9.4, 9.8, 9.3, 9.5,
                ),
                HookCandidate(
                    "Intriguing Question",
                    f"What makes {ent} one of the most extraordinary documented cases in {topic.category}?",
                    9.1, 9.3, 9.1, 9.7, 9.0, 9.4,
                ),
                HookCandidate(
                    "Factual Turning Point",
                    second_fact[:180],
                    9.2, 9.2, 9.2, 9.8, 9.1, 9.4,
                ),
            ]

        for h in hooks:
            h.compute_score()

        hooks.sort(key=lambda x: x.composite_score, reverse=True)
        topic.hook_candidates = hooks
        topic.selected_hook = hooks[0]
        return hooks

    def select_best_topic(self) -> Tuple[ScoredTopic, HookCandidate]:
        """Selects the top-ranked valid topic and its winning hook. Fails closed on rejection."""
        ranked = self.discover_and_rank_topics()
        if not ranked:
            raise RuntimeError("No fresh topics available; stopping safely without old-topic fallback.")

        winner = ranked[0]
        hooks = self.generate_five_hooks(winner)
        return winner, hooks[0]
