"""
Pop Info scene planner: script + narration timings -> one visual brief per scene.

The planner is deterministic for a given (job, script): every choice that varies between
videos (templates, backgrounds, transitions, icon tie-breaks) is driven by a seed derived
from the job, so a retry rebuilds the same film while two productions differ.

A scene brief carries everything the film page and the sound designer need:
narration, template, main subject, supporting objects, background, emphasis text,
animation direction, transition in, asset requirements and the fallback when an asset is
missing. Briefs never contain invented facts: numbers come from the narration itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

ENGINE_VERSION = "pop_info_v1"
ENGINE_REVISION = "3.27.1"   # hybrid photo treatments, reveal split, phrase scoring

# Design space is 540 x 960 (rendered at 2x = 1080 x 1920). Shorts overlays cover the right
# edge (action buttons) and the bottom (title, channel, music); text must stay inside SAFE.
DESIGN_W, DESIGN_H = 540, 960
SAFE = {"x0": 36, "x1": 468, "y0": 84, "y1": 780}

TEMPLATES = ("title", "stat", "compare", "timeline", "process", "question", "spotlight", "callout", "photo", "reveal")
TRANSITIONS = ("slide", "circle_reveal", "wipe", "pop_zoom", "split")
# real photographs are composited in several ways, never the same one twice in a row
PHOTO_TREATMENTS = ("full_bleed", "card", "circle", "split")
PHOTO_MOTIONS = ("push_in", "pull_out", "pan_left", "pan_right", "rise")
REVEAL_TARGET_SEC = 6.0
BACKGROUNDS = ("cream", "sage", "sand", "sky", "blush")

# icon -> trigger words (lower-case stems). Order matters only for ties.
ICON_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "atom": ("atom", "physic", "quantum", "particle", "nuclear", "electron", "proton", "string theor"),
    "planet": ("planet", "universe", "cosmo", "galax", "space", "orbit", "big bang", "star"),
    "telescope": ("telescope", "observ", "astronom", "sky", "map of the sky", "survey"),
    "satellite": ("satellite", "debris", "junk", "spacecraft", "orbit"),
    "rocket": ("rocket", "launch", "mission", "nasa", "esa", "spaceflight"),
    "coin": ("dollar", "money", "cost", "cheap", "price", "economic", "fund", "pay", "$", "budget"),
    "trophy": ("award", "prize", "winner", "laureate", "honou", "champion", "record"),
    "brain": ("brain", "mind", "conscious", "psycholog", "think", "memory", "illusion", "perceiv", "philosoph"),
    "eye": ("see", "vision", "look", "notice", "attention", "watch", "visual"),
    "leaf": ("plant", "forest", "mangrove", "nature", "climate", "green", "ecosystem", "tree"),
    "wave": ("ocean", "sea", "flood", "water", "coast", "tide", "storm"),
    "rhino": ("rhino", "animal", "species", "wildlife", "mammal", "herd", "loner"),
    "drone": ("drone", "autonomous", "robot", "fly", "aerial"),
    "bridge": ("bridge", "concrete", "steel", "infrastructure", "crack", "structure", "engineer"),
    "building": ("city", "building", "house", "architecture", "tower"),
    "gear": ("machine", "engine", "mechanism", "process", "system", "technology", "industr"),
    "chart": ("data", "percent", "%", "rate", "growth", "statistic", "measure", "study"),
    "magnifier": ("detect", "inspect", "search", "discover", "investigat", "evidence", "found"),
    "lightbulb": ("idea", "insight", "invent", "breakthrough", "solution", "innovat"),
    "clock": ("year", "time", "century", "decade", "history", "first", "age", "ancient"),
    "book": ("book", "fiction", "story", "write", "author", "novel", "literature", "genre"),
    "dna": ("gene", "dna", "biolog", "cell", "evolution", "organism"),
    "flame": ("fire", "heat", "burn", "temperature", "hot", "energy"),
    "lightning": ("electric", "power", "voltage", "shock", "lightning", "battery"),
    "shield": ("protect", "safe", "defen", "security", "risk", "save"),
    "globe": ("world", "global", "country", "earth", "nation", "international", "map"),
    "people": ("people", "human", "society", "public", "crowd", "researcher", "scientist"),
    "heart": ("health", "heart", "life", "medic", "body", "love"),
}
CATEGORY_ICON = {"science": "atom", "space": "planet", "psychology": "brain", "nature": "leaf",
                 "environment": "leaf", "climate": "wave", "artificial": "gear", "technology": "gear",
                 "history": "clock", "business": "coin", "health": "heart", "biology": "dna"}

_STOP = set("""a an the and or but if of in on at to for from by with without as is are was were be been being
it its this that these those there their they them he she his her we our you your i me my not no so than then
into over under about after before also just very more most much many such can could would should will may might
which who whom whose what when where why how all any each every some one two its it's isn't""".split())

_MAGNITUDES = ("million", "billion", "trillion", "thousand", "hundred")
_UNITS = ("km", "kilometres", "kilometers", "metres", "meters", "miles", "years", "year", "days", "hours",
          "minutes", "seconds", "dollars", "us", "euros", "pounds", "tonnes", "tons", "kg", "degrees", "times",
          "people", "species", "percent")
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s*%|\s+[a-zA-Z]+(?:\s+[a-zA-Z]+){0,2})?")
_MECHANISM = ("because", "causes", "cause", "works", "process", "turns", "into", "which means", "so that",
              "leads", "triggers", "allows", "by using", "uses", "then")


@dataclass
class NumberFact:
    value: float
    text: str            # as spoken, e.g. "3"
    unit: str            # e.g. "MILLION US DOLLARS"
    is_year: bool


@dataclass
class SceneBrief:
    scene_id: str
    stage: str
    start_sec: float
    end_sec: float
    narration: str
    template: str
    main_subject: str
    supporting_objects: List[str]
    background: str
    composition: str
    headline: str
    highlight: str
    numbers: List[Dict[str, Any]]
    animation_direction: str
    transition_in: str
    asset_requirements: List[str]
    fallback: str
    photo: Optional[Dict[str, Any]] = None
    sfx: List[Dict[str, Any]] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)


def _seed_int(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12], 16)


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def extract_numbers(text: str) -> List[NumberFact]:
    out: List[NumberFact] = []
    for m in _NUM_RE.finditer(text or ""):
        raw, tail = m.group(1), (m.group(2) or "").strip()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        unit = ""
        if tail:
            words = tail.lower().split()
            if words[0] in ("%", "percent"):
                unit = "%"
            elif words[0] in _MAGNITUDES:
                tail_units = []
                for w in words[1:3]:
                    if w not in _UNITS:
                        break
                    tail_units.append(w)
                unit = " ".join([words[0]] + tail_units)
            elif words[0] in _UNITS:
                unit = words[0]
            unit = unit.upper()
        is_year = (not unit) and raw.isdigit() and len(raw) == 4 and 1000 <= value <= 2100
        out.append(NumberFact(value=value, text=raw, unit=unit, is_year=is_year))
    return out


def _content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", text) if w.lower() not in _STOP]


_WEAK_EDGE = _STOP | {"while", "because", "although", "though", "often", "typically", "ever", "really", "just",
                      "core", "kind", "sort", "part", "lot", "way", "thing", "things", "say", "says", "said",
                      "like", "something", "makes", "make", "made", "get", "gets", "got"}
# the word after a window that still needs it: an object, a number or a complement follows
_CONTINUES = {"a", "an", "the", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "hundred",
              "thousand", "million", "billion", "others", "another", "them", "it", "its", "their", "his", "her", "to",
              "of", "than", "that", "into", "for", "with", "missions", "outcomes"}
# a phrase that ends on one of these reads as cut off ("Fundamental Physics reshaped")
_WEAK_END = {"reshaped", "shows", "showed", "shown", "outlines", "highlights", "means", "meant", "became", "become",
             "becomes", "remains", "remained", "seems", "seemed", "appears", "appeared", "began", "begins", "starts",
             "started", "goes", "went", "comes", "came", "takes", "took", "gives", "gave", "puts", "keeps", "kept",
             "let", "lets", "found", "finds", "used", "uses", "called", "known", "based", "still", "even", "actually",
             "simply", "really", "quickly", "completely", "right", "up", "down", "out", "off", "back", "away"}


def key_phrase(text: str, max_words: int = 5) -> Tuple[str, str]:
    """(headline, highlight): the strongest 2-to-max_words window of the narration. Never invents words.

    Windows never cross a clause boundary, start and end on a content word, and score higher for
    names, numbers and content density, so the emphasis reads as a phrase, not a cut fragment.
    """
    best, best_score = "", -9.0
    for clause in [c for c in re.split(r"[,;:.!?\u2014\u2013]|\s-\s", text or "") if c.strip()]:
        words = clause.split()
        for i in range(len(words)):
            for n in range(2, max_words + 1):
                win = words[i:i + n]
                if len(win) < n:
                    break
                bare = [w.strip("\"'()").lower() for w in win]
                nxt = words[i + n].strip("\"'(),.").lower() if i + n < len(words) else ""
                if (bare[0] in _WEAK_EDGE or bare[-1] in _WEAK_EDGE or bare[-1] in _WEAK_END or bare[-1] in _VAGUE_LABEL
                        or bare[0] in _VAGUE_LABEL or (bare[-1].endswith("s") and bare[-1][:-1] in _VERB_STEMS)):
                    continue
                if bare[-1].endswith("ly") and len(bare[-1]) > 4:
                    continue   # "...grows rapidly" style dangling adverb
                content = sum(1 for w in bare if w not in _STOP)
                caps = sum(1 for k, w in enumerate(win) if w[:1].isupper() and (i + k) > 0)
                digits = any(re.search(r"\d", w) for w in win)
                # verbs ending in -ed/-ing inside the window are fine; at the end they rarely are
                end_verbish = bare[-1].endswith(("ed", "ing")) and not win[-1][:1].isupper()
                whole_clause = n == len(words)
                cut_off = nxt in _CONTINUES
                clause_end = not nxt   # ends where the clause ends: its object or noun is included
                score = (content / n + 0.35 * digits + 0.2 * min(caps, 2) + 0.06 * n
                         - (0.25 if end_verbish else 0) + (0.12 if whole_clause else 0) - (0.6 if cut_off else 0)
                         + (0.3 if clause_end else 0))
                if score > best_score:
                    best, best_score = " ".join(win), score
    if not best and max_words < 8:
        return key_phrase(text, max_words + 3)   # a longer window before giving up
    if not best:
        return "", ""   # no clean phrase: the caller picks another source, never a raw word run
    headline = best.strip(" .,!?;:\"'")
    return headline, pick_highlight(headline)


def title_card(topic_title: str, script_text: str) -> str:
    """Short subject name for the opening card: the title itself when short, else the title's most-spoken
    two-word phrase ('space junk', 'climate anxiety'). Never a hook phrase about something else."""
    title = re.sub(r"\s*\([^)]*\)", "", topic_title or "").strip()
    words = title.split()
    if len(words) <= 5:
        return title
    low = (script_text or "").lower()
    toks = [w.strip(".,:;!?\"'") for w in words]
    pairs = [f"{a} {b}" for a, b in zip(toks, toks[1:]) if a.lower() not in _STOP and b.lower() not in _STOP]
    best = max(pairs, key=lambda q: (low.count(q.lower()), -pairs.index(q)), default="")
    if best and low.count(best.lower()):
        return best[:1].upper() + best[1:]
    return " ".join(words[:4])


def pick_highlight(headline: str) -> str:
    """The word the eye should land on: a number, else a proper noun, else the longest content word."""
    tokens = [w.strip(".,!?;:\"'") for w in headline.split()]
    nums = [w for w in tokens if re.search(r"\d", w)]
    if nums:
        return nums[0]
    proper = [w for w in tokens[1:] if w[:1].isupper() and w.lower() not in _STOP]
    if proper:
        return max(proper, key=len)
    content = _content_words(headline)
    return max(content, key=len) if content else ""


_VAGUE_LABEL = {"exact", "same", "different", "certain", "other", "others", "another", "many", "much", "more", "most",
                "takes", "take", "make", "makes", "does", "need", "needs", "want", "wants", "real", "simple", "simply",
                "whole", "entire", "new", "old", "good", "great", "big", "small", "first", "last", "next", "yet",
                "expect", "growing", "this", "that", "they", "there", "fuel"}
_VERB_STEMS = {"produce", "cause", "push", "spark", "fuel", "freeze", "digest", "reveal", "show", "pay", "give", "take",
               "make", "turn", "lead", "trigger", "drive", "help", "keep", "hold", "move", "change", "outline", "highlight"}


def node_labels(text: str, n: int = 3) -> List[str]:
    """Short labels for process nodes: distinct content words in spoken order, names first."""
    # capitalised only because it starts a sentence ("Meanwhile", "Beyond") is not a name
    starts = {m.group(1) for m in re.finditer(r"(?:^|[.!?]\s+)([A-Z][\w'\-]*)", text or "")}
    words = [w.strip(".,!?;:\"'") for w in _content_words(text)]
    words = [w.lower() if w in starts else w for w in words]
    bad = _WEAK_EDGE | _WEAK_END | _VAGUE_LABEL | {"meanwhile", "beyond", "however", "instead", "also", "still"}
    words = [w for w in words if w.lower() not in bad and not w.lower().endswith(("ly", "ing", "ed"))
             and not (w.lower().endswith("s") and w.lower()[:-1] in _VERB_STEMS)]
    seen, out = set(), []
    for w in sorted(words, key=lambda w: (not w[:1].isupper(), -len(w), words.index(w))):
        if len(w) > 3 and w.lower() not in seen:
            seen.add(w.lower()); out.append(w)
    picked = sorted(out[:n], key=words.index)
    return [w[:1].upper() + w[1:] for w in picked]


def match_icons(text: str) -> List[str]:
    low = f" {(text or '').lower()} "
    scored: List[Tuple[int, int, str]] = []
    for order, (icon, keys) in enumerate(ICON_KEYWORDS.items()):
        hits = sum(1 for k in keys if (f" {k}" in low) or (k in ("$", "%") and k in low))
        if hits:
            scored.append((-hits, order, icon))
    return [icon for _, _, icon in sorted(scored)]


def topic_icon(title: str, category: str) -> str:
    hits = match_icons(title)
    if hits:
        return hits[0]
    low = (category or "").lower()
    return next((icon for key, icon in CATEGORY_ICON.items() if key in low), "lightbulb")


def _choose_template(stage: str, text: str, numbers: Sequence[NumberFact], is_first: bool, is_last: bool,
                     previous: Optional[str], photo_available: bool, recent: Sequence[str] = (),
                     scene_photo: bool = False) -> str:
    if scene_photo and not is_first and not is_last:
        return "photo"
    if is_first:
        return "title"
    if is_last:
        return "reveal"
    years = [n for n in numbers if n.is_year]
    quantities = [n for n in numbers if not n.is_year]
    low = text.lower()
    ranked: List[str] = []
    if len(years) >= 1:
        ranked.append("timeline")
    if len(quantities) >= 2:
        ranked.append("compare")
    if quantities:
        ranked.append("stat")
    if text.strip().endswith("?"):
        ranked.append("question")
    if any(cue in low for cue in _MECHANISM):
        ranked.append("process")
    if photo_available and stage in ("CONTEXT", "ESCALATION", "REVELATION"):
        ranked.append("photo")
    for template in ranked:
        if template != previous:
            return template
    # no content cue: rotate the general-purpose layouts, never repeating either of the last two
    for template in ("spotlight", "callout", "process", "spotlight", "callout"):
        if template not in recent[-2:]:
            return template
    return "callout"


MAX_SCENE_SEC = 6.0
_CTA = re.compile(r"\b(subscribe|follow|like and share|for more)\b", re.I)


def _split_long(groups: List[List[str]], span: float, max_sec: float) -> List[List[str]]:
    """Splits groups whose share of the span exceeds max_sec at clause boundaries (one idea per scene)."""
    total = sum(len(" ".join(g)) for g in groups) or 1
    out: List[List[str]] = []
    for g in groups:
        text = " ".join(g)
        share = span * len(text) / total
        if share <= max_sec * 1.15:
            out.append(g)
            continue
        parts = int(-(-share // max_sec))
        clauses = [c.strip() for c in re.split(r"(?<=[,;:])\s+|\s+(?=(?:and|but|which|while|because|so)\s)", text) if c.strip()]
        if len(clauses) < 2:
            out.append(g)
            continue
        target = len(text) / parts
        cur, acc = [], 0
        for k, c in enumerate(clauses):
            cur.append(c)
            acc += len(c)
            # cut at the clause boundary nearest the target length, keeping the rest for the next scene
            if acc >= target * 0.6 and k < len(clauses) - 1:
                out.append([" ".join(cur)])
                cur, acc = [], 0
        if cur:
            if out and len(" ".join(cur)) < target * 0.35:
                out[-1] = [out[-1][0] + " " + " ".join(cur)]
            else:
                out.append([" ".join(cur)])
    return out


def plan_scenes(*, job_id: str, topic_title: str, category: str, sections: Sequence[Dict[str, Any]],
                timings: Sequence[Dict[str, Any]], total_duration: float,
                photos: Sequence[Dict[str, Any]] = (),
                scene_photos: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Builds the film plan. `timings` are the narration section timings (start_sec/end_sec)."""
    seed = _seed_int(job_id, topic_title)
    by_id = {t.get("id"): t for t in timings or []}
    chunks: List[Tuple[str, str, float, float]] = []      # (stage, text, start, end)
    for sec in sections:
        tm = by_id.get(sec.get("id")) or {}
        start, end = float(tm.get("start_sec", 0.0)), float(tm.get("end_sec", 0.0))
        text = sec.get("text", "")
        sentences = split_sentences(text) or [text]
        # one scene per idea: split long sections at sentence boundaries into ~3.5-7 s scenes
        n_target = max(1, min(len(sentences), round((end - start) / 5.5))) if end > start else 1
        groups: List[List[str]] = [[] for _ in range(n_target)]
        total_chars = sum(len(s) for s in sentences) or 1
        acc = 0
        for s in sentences:
            idx = min(n_target - 1, int(acc / total_chars * n_target))
            groups[idx].append(s)
            acc += len(s)
        groups = [g for g in groups if g]
        if sec is not sections[-1]:
            # the closing section is split at sentences only (below): cutting its last sentence at a
            # clause leaves a fragment like "or freeze you entirely" as the payoff
            groups = _split_long(groups, (end - start), MAX_SCENE_SEC)
        g_chars = [sum(len(s) for s in g) for g in groups]
        cursor = start
        for g, chars in zip(groups, g_chars):
            span = (end - start) * chars / max(1, sum(g_chars)) if end > start else 0.0
            chunks.append((sec.get("stage", ""), " ".join(g), cursor, cursor + span))
            cursor += span
    if not chunks:
        raise ValueError("No narration sections to plan")
    # a trailing call to action is not an idea of its own: it rides on the payoff scene
    if len(chunks) > 1 and _CTA.search(chunks[-1][1]) and len(_content_words(_CTA.sub("", chunks[-1][1]))) <= 3:
        st, tx, s0, _ = chunks[-2]
        chunks[-2:] = [(st, f"{tx} {chunks[-1][1]}", s0, chunks[-1][3])]
    # the final scene holds through the outro tail, but a reveal longer than ~6 s loses the viewer:
    # earlier sentences (or clauses) of the payoff become their own scene; no narration is dropped.
    stage, text, s0, spoken_end = chunks[-1]
    end = max(total_duration, spoken_end)
    chunks[-1] = (stage, text, s0, end)
    if end - s0 > REVEAL_TARGET_SEC * 1.15:
        parts = split_sentences(text)   # never split inside a sentence: "...solutions, | or freeze you entirely"
        if len(parts) >= 2:
            speech = max(0.1, min(spoken_end, end) - s0)
            total_chars = sum(len(x) for x in parts) or 1
            # keep as many trailing parts in the reveal as fit the target, at least one
            keep, acc = 1, len(parts[-1])
            for k in range(len(parts) - 2, 0, -1):
                if (acc + len(parts[k])) / total_chars * speech + (end - min(spoken_end, end)) > REVEAL_TARGET_SEC:
                    break
                acc += len(parts[k]); keep += 1
            head, tail = " ".join(parts[:-keep]), " ".join(parts[-keep:])
            cut = s0 + speech * len(head) / total_chars
            chunks[-1:] = [(stage, head, s0, cut), (stage, tail, cut, end)]

    base_icon = topic_icon(topic_title, category)
    photos = [p for p in photos if p.get("path")]
    used_photos: set = set()
    icon_uses: Dict[str, int] = {}
    used_headlines: set = set()
    scenes: List[SceneBrief] = []
    prev_template: Optional[str] = None
    prev_icon: Optional[str] = None
    prev_transition: Optional[str] = None
    bg_offset = seed % len(BACKGROUNDS)
    tr_offset = (seed // 7) % len(TRANSITIONS)
    for i, (stage, text, start, end) in enumerate(chunks):
        is_first, is_last = i == 0, i == len(chunks) - 1
        numbers = extract_numbers(text)
        photo_left = [p for p in photos if p["path"] not in used_photos]
        assigned = (scene_photos or {}).get(i)
        template = _choose_template(stage, text, numbers, is_first, is_last, prev_template,
                                    bool(photo_left) and not scene_photos, [s.template for s in scenes],
                                    scene_photo=bool(assigned and assigned.get("path")))
        if template == "process" and len(node_labels(text)) < 3:
            # three real labels or no process diagram: numbered placeholder nodes say nothing
            template = "callout" if prev_template != "callout" else "spotlight"
        icons = sorted(match_icons(text), key=lambda ic: icon_uses.get(ic, 0) >= 2)
        main = next((ic for ic in icons if ic != prev_icon), None) or (base_icon if base_icon != prev_icon else
                                                                      next((ic for ic in ("lightbulb", "magnifier", "star") if ic != prev_icon)))
        if is_first:
            main = icons[0] if icons else base_icon
        supporting = [ic for ic in icons if ic != main][:2]
        if len(supporting) < 2:
            supporting += [ic for ic in (base_icon, "lightbulb", "magnifier") if ic != main and ic not in supporting][:2 - len(supporting)]
        headline, highlight = key_phrase(text if not is_first else (text or topic_title))
        if is_first:
            headline = title_card(topic_title, " ".join(c[1] for c in chunks))
            highlight = pick_highlight(headline)
        spoken = " ".join(x for x in split_sentences(text) if not _CTA.search(x)) or ""
        if _CTA.search(text):
            headline, highlight = key_phrase(spoken) if spoken else ("", "")
        if not headline or len(_content_words(headline)) < 2:
            # a call to action or a one-word fragment is no payoff: use a wider phrase, else the subject
            alt, alt_hi = key_phrase(spoken, 8) if spoken else ("", "")
            headline, highlight = (alt, alt_hi) if len(_content_words(alt)) >= 2 else key_phrase(topic_title, 6)
        if headline.lower() in used_headlines and not (is_last and headline == key_phrase(topic_title, 6)[0]):
            # never repeat the same emphasis text: try each clause of this narration, then a plain window
            options = [key_phrase(c) for c in re.split(r"[,;:]", spoken) if c.strip()] + [key_phrase(spoken, 8)]
            headline, highlight = next(((h, hl) for h, hl in options if h and h.lower() not in used_headlines
                                        and len(_content_words(h)) >= 2), (headline, highlight))
            highlight = highlight or pick_highlight(headline)
        used_headlines.add(headline.lower())
        photo = None
        if template == "photo":
            photo = dict(assigned) if assigned else dict(photo_left[0])
            used_photos.add(photo["path"])
            # treatment: numbers -> split (photo above, counter below); a named person -> circle
            # spotlight; otherwise rotate full-bleed and card. Never the same treatment or move twice.
            prev_photo = next((s.photo for s in reversed(scenes) if s.photo), None) or {}
            person = bool(re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", photo.get("title") or "")) and not numbers
            wanted = ["split"] if numbers else (["circle"] if person else [])
            wanted += [PHOTO_TREATMENTS[(seed // 3 + i + k) % len(PHOTO_TREATMENTS)] for k in range(len(PHOTO_TREATMENTS))]
            photo["treatment"] = next(t for t in wanted if t != prev_photo.get("treatment"))
            moves = [PHOTO_MOTIONS[(seed // 11 + i + k) % len(PHOTO_MOTIONS)] for k in range(len(PHOTO_MOTIONS))]
            photo["motion"] = next(m for m in moves if m != prev_photo.get("motion"))
        if is_first:
            transition = "iris_open"
        else:
            options = [TRANSITIONS[(tr_offset + i + k) % len(TRANSITIONS)] for k in range(len(TRANSITIONS))]
            transition = next(o for o in options if o != prev_transition)
        background = BACKGROUNDS[(bg_offset + i) % len(BACKGROUNDS)]
        nums = [asdict(n) for n in numbers][:3]
        reqs = [f"procedural:icon:{main}"] + [f"procedural:icon:{s}" for s in supporting]
        if photo:
            reqs.insert(0, f"photo:{photo.get('sha256') or photo['path']}")
        fallback = (f"procedural icon '{main}' spotlight if the photo fails to load"
                    if photo else "all visuals are procedural vector art; no external asset can be missing")
        scenes.append(SceneBrief(
            scene_id=f"scene_{i + 1:02d}", stage=stage, start_sec=round(start, 3), end_sec=round(end, 3),
            narration=text, template=template, main_subject=main, supporting_objects=supporting,
            background=background, composition=_COMPOSITION[template], headline=headline, highlight=highlight,
            numbers=nums, animation_direction=_ANIMATION[template], transition_in=transition,
            asset_requirements=reqs, fallback=fallback, photo=photo,
            sfx=_sfx_cues(template, transition, start, end, nums, is_last),
            labels=node_labels(text) if template == "process" else [],
        ))
        prev_template, prev_icon, prev_transition = template, main, transition
        icon_uses[main] = icon_uses.get(main, 0) + 1
    plan = {
        "engine": ENGINE_VERSION, "engine_revision": ENGINE_REVISION, "job_id": job_id, "topic": topic_title, "category": category,
        "duration_sec": round(max(total_duration, scenes[-1].end_sec), 3), "seed": seed,
        "design": {"width": DESIGN_W, "height": DESIGN_H, "safe": SAFE},
        "scenes": [asdict(s) for s in scenes],
    }
    plan["fingerprints"] = plan_fingerprints(plan)
    return plan


_COMPOSITION = {
    "title": "headline stack upper third, subject bubble centred, sparkles around",
    "stat": "teal idea bubble centred with subject icon, counter and unit tag; label below",
    "compare": "two growing bars side by side on a baseline, values above, headline on top",
    "timeline": "dotted path drawing down the frame with year pins, subject at the destination",
    "process": "three subject nodes stacked vertically linked by self-drawing arrows",
    "question": "large coral question disc, subject orbiting, headline as the question",
    "spotlight": "subject in a big disc with a turning dashed ring, two supporting objects orbiting",
    "callout": "typographic card: large headline on a tilted paper card, subject badge, underline swoosh",
    "photo": "real photograph composited by treatment (full-bleed, taped card, circle spotlight, split with counter), credit pill",
    "reveal": "rays behind a ringed teal disc with the subject, payoff headline card",
}
_ANIMATION = {
    "title": "iris opens, subject pops with back-ease and wobble, headline words pop in order",
    "stat": "bubble pops, number counts up with an eased curve and locks, tag pops",
    "compare": "bars grow in turn with overshoot, values count with them",
    "timeline": "path draws itself, pins drop on each year, labels pop",
    "process": "nodes pop in order, arrows draw between them",
    "question": "question disc pops and sways, subject orbits slowly",
    "spotlight": "disc pops, ring turns, satellites drift in from the sides",
    "callout": "card swings in, words pop in order, swoosh draws under the key word, badge pops",
    "photo": "photo enters with its treatment's motion (push, pull, pan or rise around the focus point); overlay graphics pop on top",
    "reveal": "rays turn, disc pops, payoff card rises; iris closes at the end",
}


def _sfx_cues(template: str, transition: str, start: float, end: float, numbers: List[Dict[str, Any]],
              is_last: bool) -> List[Dict[str, Any]]:
    cues = [{"t": round(start, 3), "kind": "whoosh" if transition != "pop_zoom" else "pop", "gain": 0.45}]
    cues.append({"t": round(start + 0.35, 3), "kind": "pop", "gain": 0.6})
    if template in ("stat", "compare") and numbers:
        c0, c1 = start + 0.6, min(end - 0.3, start + 2.2)
        k = 0
        x = c0
        while x < c1 and k < 18:
            cues.append({"t": round(x, 3), "kind": "tick", "gain": 0.28})
            x += 0.07 + 0.12 * ((x - c0) / max(0.01, c1 - c0))
            k += 1
        cues.append({"t": round(c1, 3), "kind": "tap", "gain": 0.6})
    if template == "timeline":
        for j in range(3):
            cues.append({"t": round(start + 0.8 + j * 0.6, 3), "kind": "tap", "gain": 0.45})
    if template == "process":
        for j in range(3):
            cues.append({"t": round(start + 0.4 + j * 0.55, 3), "kind": "tap", "gain": 0.4})
    if is_last:
        cues.append({"t": round(max(start, end - 0.9), 3), "kind": "whoosh", "gain": 0.5})
    return cues


def plan_fingerprints(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Creative-identity fingerprints of the visual plan (scene sequence, concepts, motion)."""
    scenes = plan.get("scenes", [])

    def h(obj: Any) -> str:
        return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()

    seq = [s["template"] if not s.get("photo") else f"photo:{s['photo'].get('treatment', 'card')}" for s in scenes]
    concepts = [f"{s['template']}:{s['main_subject']}" for s in scenes]
    return {
        "scene_sequence": seq,
        "scene_sequence_fingerprint": h(seq),
        "visual_concepts": concepts,
        "visual_concepts_fingerprint": h(concepts),
        "transition_sequence": [s["transition_in"] for s in scenes],
        "typography": "fredoka_700_headline+lilita_numbers+yellow_highlight_pill",
        "animation_choices": sorted({s["template"] for s in scenes}),
    }
