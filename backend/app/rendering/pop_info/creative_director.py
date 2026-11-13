"""
Pop Info creative director (revision 3.28).

Video level: one creative direction per film, read from the topic and the whole script: the
primary illustrated environment (where the story lives), a secondary one for the turn of the
story, a palette mood and the pacing.

Scene level: a story-driven selector. Content-driven layouts (numbers -> stat/compare, years ->
timeline, mechanisms -> process, an assigned licensed photo -> photo) are never overridden. Only
the scenes that the planner filled with a general-purpose layout (spotlight / callout / question)
are reconsidered: a scene about a place or a setting becomes an illustrated environment, a short
emphatic line becomes typography-led ("kinetic"). Diversity constraints then guarantee a mix of
visual modes without randomness: every mode that the story supports appears, and no mode runs
three scenes in a row when an alternative exists.

No mascots or characters: environments are places (src: environments.js), drawn in code.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

ENVIRONMENTS = ("lab", "city_street", "office", "bank", "space", "factory", "research_facility", "nature",
                "historical", "newsroom", "hospital", "classroom", "digital_network")

# keyword -> environment. Whole-word, lower-case; a keyword may be a phrase.
ENV_KEYWORDS: Dict[str, Sequence[str]] = {
    "lab": ("chemistry", "chemical", "molecule", "molecules", "experiment", "experiments", "laboratory", "lab",
            "vaccine", "dna", "gene", "genes", "cell", "cells", "bacteria", "virus", "enzyme", "protein", "compound"),
    "city_street": ("city", "cities", "street", "streets", "town", "urban", "traffic", "neighbourhood",
                    "neighborhood", "downtown", "avenue", "village", "shop", "shops", "market"),
    "office": ("company", "companies", "business", "office", "startup", "corporate", "manager", "employees",
               "employee", "contract", "boss", "meeting", "brand", "marketing", "ceo", "founded", "founder"),
    "bank": ("money", "bank", "banks", "dollars", "dollar", "price", "prices", "economy", "financial", "gold",
             "coin", "coins", "fund", "funds", "tax", "loan", "currency", "wealth", "rich", "prize", "award",
             "investors", "investment", "cost", "costs", "paid", "pays", "salary", "budget", "auction"),
    "space": ("space", "planet", "planets", "star", "stars", "moon", "orbit", "galaxy", "nasa", "astronaut",
              "telescope", "universe", "black hole", "black holes", "comet", "asteroid", "cosmic", "solar", "mars"),
    "factory": ("factory", "factories", "manufacturing", "industrial", "machine", "machines", "steel",
                "assembly", "engine", "engines", "production line", "mill", "plant workers", "industry"),
    "research_facility": ("physics", "physicist", "physicists", "theorist", "theorists", "research",
                          "researchers", "scientist", "scientists", "particle", "particles", "collider",
                          "observatory", "institute", "quantum", "string theory", "strings", "detector"),
    "nature": ("forest", "forests", "ocean", "oceans", "river", "rivers", "animal", "animals", "plant", "plants",
               "tree", "trees", "mountain", "mountains", "species", "bird", "birds", "fish", "sea", "island",
               "volcano", "wild", "wildlife", "lake", "desert", "jungle", "weather", "climate", "storm"),
    "historical": ("century", "centuries", "ancient", "king", "queen", "empire", "medieval", "painting",
                   "painted", "painter", "artist", "museum", "history", "historic", "emperor", "roman", "war",
                   "gallery", "oil painting", "exhibited", "royal", "dynasty", "legend", "myth"),
    "newsroom": ("newspaper", "newspapers", "news", "journalist", "journalists", "magazine", "press",
                 "editor", "editors", "broadcast", "media", "television", "tv", "radio", "headline",
                 "headlines", "reporter", "reporters", "published", "weekly", "daily", "column"),
    "hospital": ("hospital", "doctor", "doctors", "patient", "patients", "medicine", "medical", "disease",
                 "surgery", "nurse", "health", "illness", "treatment", "symptoms", "drug", "drugs", "brain"),
    "classroom": ("school", "schools", "student", "students", "teacher", "teachers", "lesson", "learn",
                  "learning", "education", "exam", "children", "university", "college", "class", "study"),
    "digital_network": ("internet", "computer", "computers", "software", "data", "network", "networks",
                        "online", "algorithm", "algorithms", "ai", "digital", "code", "website", "app", "apps",
                        "phone", "smartphone", "social media", "robot", "robots", "chip", "chips"),
}
_CATEGORY_ENV = {"science": "lab", "space": "space", "astronomy": "space", "physics": "research_facility",
                 "history": "historical", "art": "historical", "culture": "historical", "nature": "nature",
                 "biology": "nature", "animals": "nature", "geography": "nature", "technology": "digital_network",
                 "tech": "digital_network", "business": "office", "economics": "bank", "finance": "bank",
                 "health": "hospital", "medicine": "hospital", "psychology": "hospital", "education": "classroom",
                 "media": "newsroom", "journalism": "newsroom", "engineering": "factory", "industry": "factory"}
MOOD = {"space": "night", "digital_network": "night", "newsroom": "studio", "historical": "warm",
        "office": "warm", "bank": "warm", "classroom": "warm", "factory": "industrial"}

# Layouts the planner chooses when the narration has no content cue: these may be re-cast.
GENERIC = frozenset({"spotlight", "callout", "question"})
CONTENT = frozenset({"title", "reveal", "stat", "compare", "timeline", "process", "photo"})
VISUAL_MODE = {"photo": "photo", "environment": "environment", "kinetic": "typography", "callout": "typography",
               "stat": "diagram", "compare": "diagram", "timeline": "diagram", "process": "diagram",
               "title": "illustration", "spotlight": "illustration", "question": "illustration",
               "reveal": "illustration"}
DIAGRAMS = frozenset({"stat", "compare", "timeline", "process", "question", "spotlight"})

_SETTING = re.compile(r"\b(in|inside|at|across|around|through|where|there|from|onto|on)\s+(the|a|an|its|their|his|her)\b", re.I)
_EMPHATIC = re.compile(r"\b(not|never|no one|nobody|nothing|only|but|yet|instead|still|even|actually|"
                       r"really|truth|secret|wrong|forget|imagine)\b|!", re.I)
_WORD = re.compile(r"[a-z][a-z'-]*")
# a kinetic line ending on one of these reads as cut off ("winners have", "people never")
_AUX = frozenset({"have", "has", "had", "do", "does", "did", "never", "always", "ever", "people", "get", "got"})


def _hits(text: str) -> Dict[str, int]:
    low = " " + " ".join(_WORD.findall((text or "").lower())) + " "
    out: Dict[str, int] = {}
    for env, words in ENV_KEYWORDS.items():
        n = sum(low.count(f" {w} ") for w in words)
        if n:
            out[env] = n
    return out


def scene_environment(text: str) -> Optional[str]:
    """The environment a single narration line points at, if any (strongest keyword match)."""
    h = _hits(text)
    if not h:
        return None
    return max(sorted(h), key=lambda e: h[e])


def video_direction(*, job_id: str, topic_title: str, category: str, texts: Sequence[str],
                    scene_secs: Sequence[float] = ()) -> Dict[str, Any]:
    """One creative direction for the whole film (deterministic; derived from the content)."""
    title_hits = _hits(topic_title)
    body_hits = _hits(" ".join(texts))
    score: Dict[str, float] = {}
    for env, n in body_hits.items():
        score[env] = score.get(env, 0.0) + n
    for env, n in title_hits.items():
        score[env] = score.get(env, 0.0) + 3.0 * n           # the title names the subject
    cat = (category or "").lower()
    cat_env = next((e for k, e in _CATEGORY_ENV.items() if k in cat), None)
    if cat_env:
        score[cat_env] = score.get(cat_env, 0.0) + 1.5
    ranked = sorted(score, key=lambda e: (-score[e], ENVIRONMENTS.index(e)))
    primary = ranked[0] if ranked else (cat_env or "lab")
    secondary = next((e for e in ranked[1:] if score[e] >= 1.0), None)
    avg = (sum(scene_secs) / len(scene_secs)) if scene_secs else 5.0
    return {
        "primary_environment": primary,
        "secondary_environment": secondary,
        "environment_scores": {e: round(score[e], 1) for e in ranked[:5]},
        "palette_mood": MOOD.get(primary, "bright"),
        "pacing": "brisk" if avg < 4.5 else "measured" if avg > 6.0 else "steady",
        "principles": [
            "content decides the layout: numbers, years and mechanisms are always diagrams",
            "a licensed photo is used where a real subject is named and the photo was verified",
            "settings are shown as illustrated places; emphatic lines as kinetic type",
            "no visual mode three scenes running when the story allows another",
        ],
    }


def _strong_line(headline: str) -> bool:
    """Kinetic type makes the line the whole picture: it needs two real content words."""
    from backend.app.rendering.pop_info.scene_planner import _WEAK_EDGE, _WEAK_END, _content_words
    words = (headline or "").split()
    last = words[-1].strip(".,!?;:\"'").lower() if words else ""
    weak_end = last in _WEAK_EDGE or last in _WEAK_END or last in _AUX
    return bool(words) and 2 <= len(_content_words(headline)) and len(words) <= 6 and not weak_end


def _is_emphatic(text: str, headline: str) -> bool:
    words = _WORD.findall((text or "").lower())
    return _strong_line(headline) and (bool(_EMPHATIC.search(text or "")) or len(words) <= 12)


def _is_setting(text: str, direction: Dict[str, Any]) -> bool:
    return bool(scene_environment(text)) or bool(_SETTING.search(text or ""))


def caps(n_scenes: int) -> Dict[str, int]:
    """Per-film budget: type is emphasis, so it stays rare; places carry more of the film."""
    return {"kinetic": 1 if n_scenes < 9 else 2, "environment": max(2, round(n_scenes * 0.3))}


def cast_scene(template: str, *, stage: str, text: str, headline: str, previous: Optional[str],
               recent_modes: Sequence[str], direction: Dict[str, Any], used: Optional[Dict[str, int]] = None,
               n_scenes: int = 8) -> str:
    """Re-casts a general-purpose layout as an environment or kinetic scene when the story calls for it."""
    if template not in GENERIC:
        return template
    used = used or {}
    budget = caps(n_scenes)
    candidates: List[str] = []
    if _is_setting(text, direction) and stage in ("HOOK", "CONTEXT", "ESCALATION"):
        candidates.append("environment")
    if _is_emphatic(text, headline) and stage in ("ESCALATION", "REVELATION", "CONTEXT"):
        candidates.append("kinetic")
    candidates = [c for c in candidates if used.get(c, 0) < budget[c]]
    for cand in candidates:
        mode = VISUAL_MODE[cand]
        if cand != previous and not (len(recent_modes) >= 2 and recent_modes[-1] == recent_modes[-2] == mode):
            return cand
    return template


def environment_for(text: str, stage: str, direction: Dict[str, Any], previous_env: Optional[str]) -> str:
    """Where this scene takes place: its own strongest cue, else the film's place (the turn of the
    story moves to the secondary place when there is one)."""
    own = scene_environment(text)
    if own and own in (direction.get("primary_environment"), direction.get("secondary_environment")):
        return own
    if own and _hits(text).get(own, 0) >= 2:
        return own
    if stage in ("ESCALATION", "REVELATION") and direction.get("secondary_environment"):
        return direction["secondary_environment"]
    return direction.get("primary_environment") or "lab"


def enforce_diversity(scenes: List[Dict[str, Any]], direction: Dict[str, Any]) -> List[str]:
    """Story-safe diversity pass over the plan (mutates scenes). Only general-purpose scenes change.
    Returns a log of what changed and why."""
    log: List[str] = []
    n = len(scenes)

    def ok_at(i: int, template: str) -> bool:
        return all(not (0 <= j < n) or scenes[j]["template"] != template for j in (i - 1, i + 1))

    def recast(template: str, why: str, prefer) -> bool:
        cands = [i for i in range(1, n - 1) if scenes[i]["template"] in GENERIC and ok_at(i, template)]
        cands.sort(key=prefer)
        if not cands:
            return False
        i = cands[0]
        log.append(f"{scenes[i]['scene_id']}: {scenes[i]['template']} -> {template} ({why})")
        scenes[i]["template"] = template
        return True

    templates = [s["template"] for s in scenes]
    if n >= 5 and "environment" not in templates:
        recast("environment", "every film shows its place at least once",
               lambda i: (not _is_setting(scenes[i]["narration"], direction), i))
    templates = [s["template"] for s in scenes]
    if n >= 6 and "kinetic" not in templates and any(
            scenes[i]["template"] in GENERIC and _strong_line(scenes[i]["headline"]) for i in range(1, n - 1)):
        recast("kinetic", "one typography-led emphasis beat",
               lambda i: (not _strong_line(scenes[i]["headline"]),
                          not _is_emphatic(scenes[i]["narration"], scenes[i]["headline"]), -i))
    # no visual mode three in a row when a general-purpose scene can change
    for i in range(2, n):
        modes = [VISUAL_MODE.get(scenes[j]["template"], "illustration") for j in (i - 2, i - 1, i)]
        if modes[0] == modes[1] == modes[2]:
            for j in (i, i - 1):
                if scenes[j]["template"] in GENERIC and 0 < j < n - 1:
                    for alt in ("environment", "kinetic", "spotlight", "callout"):
                        over = alt in ("kinetic", "environment") and sum(
                            1 for x in scenes if x["template"] == alt) >= caps(n)[alt]
                        if VISUAL_MODE[alt] != modes[0] and ok_at(j, alt) and not over and (
                                alt != "kinetic" or _strong_line(scenes[j]["headline"])):
                            log.append(f"{scenes[j]['scene_id']}: {scenes[j]['template']} -> {alt} (three {modes[0]} scenes in a row)")
                            scenes[j]["template"] = alt
                            break
                    break
    return log


def backdrop_for(template: str, stage: str) -> str:
    """Hybrid storytelling: diagrams of the setup sit inside the story's place (washed so the diagram
    stays the subject); the title is the establishing shot. Payoff scenes stay clean."""
    if template == "title":
        return "environment"
    if template in DIAGRAMS and stage in ("HOOK", "CONTEXT", "ESCALATION"):
        return "environment"
    return ""
