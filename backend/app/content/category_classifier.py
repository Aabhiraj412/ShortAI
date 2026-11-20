"""Stage 3.31B category classifier (v2).

Replaces substring matching (``"era" in "several"``, ``"war" in "award"``) with token-aware,
context-weighted evidence. The existing ``CreativeProductionPolicy.classify_topic`` (v1) is left
untouched; this classifier is used only when the ``category_classifier_v2`` flag is ``shadow`` or
``on``.

Evidence, strongest first
-------------------------
1. **Definition context**: the subject type in the opening "X is/was a(n) ..." sentence
   ("... is a volcano", "... is a song by", "... was a space probe"). This is the entity context.
2. **Anchors**: a small category-specific vocabulary of unambiguous terms (supernova, galaxy,
   strait, excavation, ...) that supplements, not replaces, the policy keywords.
3. **Policy keywords** (``CategorySpec.keywords``), matched on word boundaries with an optional
   plural suffix. Terms known to be ambiguous ("era", "war", "cell", "orbit", "bank", ...) are
   down-weighted outside the title.

Position matters: title > lead (first two sentences) > body. Each keyword counts at most twice.
Phrases that contain a keyword but mean something else ("science fiction", "rock star") are masked
before matching.

Outcomes
--------
* ``CLASSIFIED``   one category is clearly ahead.
* ``AMBIGUOUS``    two categories are close; the deterministic tie-break picks one (score, then
                   title evidence, then distinct terms, then the discovery hint, then the category
                   key alphabetically - never "History by default").
* ``UNKNOWN``      not enough evidence for any category (NEEDS_REVIEW).
* ``OUT_OF_SCOPE`` the subject is music/film/TV, sport or politics/elections and no in-scope
                   category has stronger evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CLASSIFIED, AMBIGUOUS, UNKNOWN, OUT_OF_SCOPE = "CLASSIFIED", "AMBIGUOUS", "UNKNOWN", "OUT_OF_SCOPE"

MIN_SCORE = 3.0          # below this nothing is assigned (UNKNOWN)
AMBIGUITY_RATIO = 0.85   # runner-up >= 85% of the winner -> AMBIGUOUS
HINT_BONUS = 2.0         # a discovery hint (feed / search category) is evidence, not a verdict

TITLE_W, LEAD_W, BODY_W = 3.0, 1.5, 0.5
ANCHOR_MULT = 1.4
WEAK_MULT_OUTSIDE_TITLE = 0.25
PHRASE_MULT = 1.25
MAX_OCCURRENCES = 2
DEFINITION_W = 6.0

# Keywords that are real category signals in a title but are common in unrelated prose.
WEAK_TERMS = frozenset({
    "era", "war", "century", "cell", "orbit", "bank", "exchange", "trade", "engine", "foundation",
    "basin", "resonance", "sensor", "dome", "algorithm", "memory", "behavior", "evolution",
    "migration", "patent", "exploit", "sprite", "transformer", "record", "expedition", "kingdom",
    "particle", "radiation", "laser", "radar", "hardware", "species", "predator", "structural",
    "desert", "latitude", "currency", "economy", "corporation", "invented", "prototype",
    "probability", "paradox", "climate", "monsoon", "clinical", "medicine", "anatomy", "illusion",
    "perception", "cognitive", "experiment", "hypothesis", "laboratory", "mars", "jupiter", "saturn",
})

# Phrases that contain a category keyword but signal something else.
MASK_PHRASES = (
    "science fiction", "science-fiction", "sci-fi", "political science", "social science",
    "star wars", "rock star", "pop star", "film star", "movie star", "all-star", "all star",
    "star player", "bank holiday", "cell phone", "memory card", "record label", "world record holder",
    "transformers film", "mars bar",
)

# Supplementary unambiguous vocabulary per policy category key.
ANCHORS: Dict[str, Tuple[str, ...]] = {
    "history": ("battle of", "world war", "civil war", "empire", "dynasty", "medieval", "ancient rome",
                "ancient egypt", "colonial", "monarchy", "siege of", "treaty of", "historian", "pharaoh",
                "renaissance", "crusade"),
    "science": ("physicist", "physicists", "chemist", "quantum mechanics", "particle physics", "dark matter",
                "superconductor", "isotope", "nuclear fission"),
    "technology": ("semiconductor", "transistor", "microprocessor", "integrated circuit", "photonic", "battery",
                   "electronics", "robotics", "hydrogen production"),
    "artificial_intelligence": ("artificial intelligence", "neural network", "neural networks", "machine learning",
                                "deep learning", "large language model"),
    "psychology": ("psychologist", "cognitive bias", "psychological"),
    "human_biology": ("protein", "proteins", "chromosome", "kinetochore", "enzyme", "gene expression"),
    "medical_science": ("patients", "disease", "clinical trial", "physician", "epidemic", "pandemic"),
    "nature_and_wildlife": ("wildlife", "bird", "birds", "mammal", "reptile", "habitat", "fauna", "flora",
                            "ecological restoration", "biodiversity", "woodcock", "earthworm"),
    "space_and_astronomy": ("supernova", "galaxy", "galaxies", "light-year", "light-years", "astronomer",
                            "astronomers", "astronomy", "white dwarf", "dwarf star", "star cluster", "milky way",
                            "comet", "asteroid", "space probe", "interstellar", "cosmic", "planetary",
                            "solar system", "spacecraft", "space telescope", "exoplanet", "neutron star"),
    "engineering": ("engineer", "engineers", "turbine", "gas turbine", "suspension bridge", "aerodynamics"),
    "geography": ("volcano", "stratovolcano", "mountain range", "peninsula", "strait", "archipelago",
                  "river", "lake", "island", "plateau", "glacier", "fjord", "canyon", "ocean basin"),
    "business_and_economics": ("company", "corporation", "stock market", "economist", "bankruptcy",
                               "merger", "inflation", "recession"),
    "archaeology": ("archaeologist", "archaeologists", "excavation", "excavated", "artifact", "artefact",
                    "burial", "ruins", "inscription"),
    "inventions_and_discoveries": ("inventor", "invention", "patented", "accidental discovery"),
    "cybersecurity": ("hacker", "cyberattack", "encryption", "malware", "ransomware"),
    "mathematics": ("mathematician", "theorem", "conjecture", "equation"),
    "everyday_science": ("surface tension", "everyday physics", "kitchen chemistry"),
    "unusual_phenomena": ("aurora", "ball lightning", "rogue wave", "mirage", "unexplained"),
    "architecture_and_megastructures": ("skyscraper", "architect", "megastructure", "cathedral", "aqueduct"),
    "environment_and_climate": ("climate change", "greenhouse gas", "emissions", "carbon dioxide", "co2",
                                "sea level", "global warming", "climate refugia", "sulfur hexafluoride"),
}

# Opening-sentence subject types ("X is a volcano") -> category key or out-of-scope domain.
DEFINITION_TYPES: Tuple[Tuple[str, str], ...] = (
    # out of scope first: a "film about a volcano" is a film
    (r"\b(?:song|single|album|studio album|ep|band|singer|rapper|musician|composer|songwriter|record producer)\b", "oos:music"),
    (r"\b(?:film|movie|television series|tv series|sitcom|episode|video game|novel|comic|genre of|literary genre|actor|actress|television presenter)\b", "oos:entertainment"),
    (r"\b(?:footballer|cricketer|basketball player|tennis player|athlete|sportsperson|football club|sports team|racing driver|boxer|olympian)\b", "oos:sport"),
    (r"\b(?:politician|political party|political theorist|political activist|activist|dissident|diplomat|election|referendum|member of parliament|prime minister|president of)\b", "oos:politics"),
    (r"\b(?:terrorist attacks?|terror attacks?|mass shooting|insurgency)\b", "oos:politics"),
    (r"\b(?:volcano|stratovolcano|mountain range|mountain|peninsula|strait|archipelago|river|lake|island|plateau|glacier|fjord|canyon|sea|ocean|desert|bay)\b", "geography"),
    (r"\b(?:space probe|spacecraft|space telescope|satellite|star|galaxy|nebula|supernova|exoplanet|planet|moon of|comet|asteroid|crewed (?:lunar )?(?:landing|mission)|space mission|spaceflight)\b", "space_and_astronomy"),
    (r"\b(?:battle|war|siege|empire|dynasty|kingdom|treaty|revolution|massacre|rebellion|coup)\b", "history"),
    (r"\b(?:species|genus|family of birds|bird|mammal|fish|insect|reptile|amphibian|plant)\b", "nature_and_wildlife"),
    (r"\b(?:ocean that existed|ancient ocean|supercontinent|prehistoric)\b", "geography"),
    (r"\b(?:theorem|conjecture|mathematical)\b", "mathematics"),
    (r"\b(?:company|corporation|conglomerate|bank)\b", "business_and_economics"),
    (r"\b(?:engine|turbine|bridge|dam)\b", "engineering"),
    (r"\b(?:disease|syndrome|disorder|vaccine)\b", "medical_science"),
    (r"\b(?:archaeological site|ancient city|artifact)\b", "archaeology"),
)
_DEF_RE = re.compile(r"\b(?:is|was|are|were)\s+(?:an?|the|one of the)\s+([^.;:]{3,120})", re.I)
_OOS_LEXICON: Dict[str, Tuple[str, ...]] = {
    "music": ("album", "single by", "recorded by", "billboard", "music video", "studio album", "singer", "rapper"),
    "entertainment": ("box office", "starring", "television series", "sitcom", "episode of", "video game", "novel by",
                      "science fiction", "film directed"),
    "sport": ("footballer", "premier league", "championship", "tournament", "season", "goalkeeper", "world cup",
              "match against", "olympic games"),
    "politics": ("election", "politician", "parliament", "political party", "prime minister", "campaign",
                 "sanctions", "dissident", "activist", "attacks"),
}
OOS_MIN = 4.0
# Stage 3.31K: out-of-scope domains that become in-scope categories once the expanded specs are registered.
EXPANDED_SCOPE: Dict[str, str] = {"entertainment": "cinema_and_entertainment", "music": "cinema_and_entertainment",
                                  "sport": "sports_and_major_events"}


@dataclass
class ClassificationResult:
    category_key: Optional[str]
    display_name: Optional[str]
    category_code: Optional[str]
    status: str
    confidence: float
    out_of_scope_domain: Optional[str] = None
    scores: List[Tuple[str, float]] = field(default_factory=list)
    evidence: Dict[str, List[str]] = field(default_factory=dict)
    hint_key: Optional[str] = None

    @property
    def assigned(self) -> bool:
        return self.status in (CLASSIFIED, AMBIGUOUS) and self.category_key is not None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["scores"] = [[k, round(v, 2)] for k, v in self.scores]
        return d


def _normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    t = re.sub(r"<[^>]+>", " ", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _mask(text: str) -> str:
    for p in MASK_PHRASES:
        text = re.sub(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", " ", text)
    return text


@lru_cache(maxsize=4096)
def _pattern(term: str) -> "re.Pattern[str]":
    t = term.lower().strip()
    # words of a phrase may be separated by spaces or hyphens ("light-years" == "light years")
    body = r"[\s\-]*".join(re.escape(p) for p in re.split(r"[\s\-]+", t) if p)
    single = " " not in t and "-" not in t
    if single and len(t) > 3 and t.endswith("y") and t[-2:-1] not in "aeiou":
        body, suffix = body[:-1], r"(?:y|ies)"          # galaxy / galaxies
    else:
        suffix = "" if t.endswith("s") or not t[-1:].isalpha() else r"(?:s|es)?"
    return re.compile(r"(?<![a-z0-9])" + body + suffix + r"(?![a-z0-9])")


def _count(term: str, text: str) -> int:
    return min(MAX_OCCURRENCES, len(_pattern(term).findall(text)))


def _split_lead(text: str) -> Tuple[str, str]:
    sents = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(sents[:2]), " ".join(sents[2:])[:4000]


def definition_type(title: str, text: str) -> Optional[str]:
    """Subject type from the opening definition sentence, e.g. 'volcano' -> 'geography'."""
    first = re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)[0][:400]
    m = _DEF_RE.search(first)
    if not m:
        return None
    phrase = " " + m.group(1).lower() + " "
    # the head noun phrase is what matters: stop at relative clauses
    phrase = re.split(r"\b(?:that|which|who|where|whose|located|known|founded|launched)\b", phrase)[0]
    for rx, target in DEFINITION_TYPES:
        if re.search(rx, phrase):
            return target
    return None


def out_of_scope_domain(text: str) -> Tuple[Optional[str], float]:
    """Strongest out-of-scope domain in free text (used for news headlines without a definition)."""
    t = _normalize(text)
    best, best_score = None, 0.0
    for dom, terms in _OOS_LEXICON.items():
        s = sum(2.0 * _count(term, t) for term in terms)
        if s > best_score:
            best, best_score = dom, s
    return (best, best_score) if best_score >= OOS_MIN else (None, best_score)


class CategoryClassifierV2:
    def __init__(self, policy=None):
        if policy is None:
            from backend.app.content.creative_production_policy import get_creative_production_policy
            policy = get_creative_production_policy()
        self.policy = policy

    def classify(self, title: str, text: str = "", hint_key: Optional[str] = None) -> ClassificationResult:
        specs = {s.key: s for s in self.policy.list_categories()}
        hint = hint_key if hint_key in specs else None
        t_title = _mask(_normalize(title))
        lead_raw, body_raw = _split_lead(text)
        t_lead, t_body = _mask(_normalize(lead_raw)), _mask(_normalize(body_raw))

        scores: Dict[str, float] = {}
        title_hits: Dict[str, int] = {}
        distinct: Dict[str, int] = {}
        evidence: Dict[str, List[str]] = {}
        for key, spec in specs.items():
            s, th, dn, ev = 0.0, 0, 0, []
            terms = [(kw, 1.0) for kw in spec.keywords] + [(a, ANCHOR_MULT) for a in ANCHORS.get(key, ())]
            seen_terms = set()
            for term, mult in terms:
                term_l = term.lower()
                if term_l in seen_terms:
                    continue
                seen_terms.add(term_l)
                weak = term_l in WEAK_TERMS and mult == 1.0
                phrase = PHRASE_MULT if " " in term_l else 1.0
                c_title, c_lead, c_body = _count(term_l, t_title), _count(term_l, t_lead), _count(term_l, t_body)
                if not (c_title or c_lead or c_body):
                    continue
                outside = LEAD_W * c_lead + BODY_W * c_body
                if weak:
                    outside *= WEAK_MULT_OUTSIDE_TITLE
                s += (TITLE_W * c_title + outside) * mult * phrase
                th += c_title
                dn += 1
                ev.append(term_l)
            scores[key], title_hits[key], distinct[key], evidence[key] = s, th, dn, ev

        def_type = definition_type(title, text)
        oos_domain: Optional[str] = None
        if def_type and def_type.startswith("oos:"):
            oos_domain = def_type[4:]
        elif def_type in scores:
            scores[def_type] += DEFINITION_W
            evidence[def_type].append(f"definition:{def_type}")
        if hint:
            scores[hint] += HINT_BONUS
            evidence[hint].append("discovery_hint")

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -title_hits[kv[0]], -distinct[kv[0]],
                                                         0 if kv[0] == hint else 1, kv[0]))
        top_key, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        top3 = [(k, v) for k, v in ranked[:3] if v > 0]
        ev = {k: evidence[k] for k, _ in top3}

        if oos_domain is None:
            dom, dom_score = out_of_scope_domain(f"{title}. {lead_raw}")
            if dom and dom_score > top:
                oos_domain = dom
        if oos_domain and not (def_type and not def_type.startswith("oos:")) and top < DEFINITION_W + MIN_SCORE:
            # Stage 3.31K: with the expanded categories registered (flag ``expanded_categories``), film/TV/music
            # and sport subjects are in scope. Politics always stays out of scope.
            mapped = EXPANDED_SCOPE.get(oos_domain)
            if mapped and mapped in specs:
                s = specs[mapped]
                ev = {mapped: [f"subject_type:{oos_domain}"], **ev}
                return ClassificationResult(mapped, s.display_name, s.category_code, CLASSIFIED, 0.75, None, top3, ev, hint)
            return ClassificationResult(None, None, None, OUT_OF_SCOPE, 0.0, oos_domain, top3, ev, hint)
        if top < MIN_SCORE:
            return ClassificationResult(None, None, None, UNKNOWN, round(top / (MIN_SCORE * 2), 3), None, top3, ev, hint)
        spec = specs[top_key]
        confidence = round(top / (top + second), 3) if top + second > 0 else 0.0
        status = AMBIGUOUS if second >= AMBIGUITY_RATIO * top else CLASSIFIED
        return ClassificationResult(top_key, spec.display_name, spec.category_code, status, confidence, None, top3, ev, hint)
