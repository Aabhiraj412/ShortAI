"""Stage 4.02: Hindi script quality checks (deterministic; no model).

``validate(req, hindi) -> problems`` returns human-readable problems; an empty list means the script is usable.

* completeness: one entry per English scene, in order, with non-empty narration and headline;
* readability: the spoken narration is Devanagari (names and terms are written as pronounced);
* facts: every number in the English appears in the Hindi, and the Hindi has no number the English lacks
  (Devanagari digits and thousands separators are normalised);
* names: every proper name in the English narration is covered by the term list (with a spoken form) or kept;
* on-screen text: the highlight is part of the headline; labels keep their count; headlines are short;
* length: the estimated spoken duration fits the Shorts window (30-58 s) before any audio is made.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MIN_DEVANAGARI_SHARE = 0.85
MIN_SECONDS, MAX_SECONDS = 30.0, 58.0
WORDS_PER_SEC = 2.1              # measured Hindi TTS pace (2.07 words/s at +8 %, 2.26 at +18 %)
STOP_CAPS = {"The", "A", "An", "In", "On", "At", "It", "Its", "This", "That", "These", "Those", "And", "But", "Or",
             "So", "When", "What", "Why", "How", "Who", "Where", "Which", "If", "As", "For", "From", "To", "Of", "By",
             "With", "Each", "Every", "Some", "Many", "Most", "Today", "Now", "Then", "There", "Here", "They", "We",
             "You", "He", "She", "His", "Her", "Our", "Your", "Their", "One", "Two", "Three", "First", "Yes", "No",
             "Imagine", "Did", "Do", "Does", "Is", "Are", "Was", "Were", "Can", "Could", "Would", "Should", "Not",
             # translated, not transliterated: months, days, nationality / language adjectives
             "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
             "English", "British", "Indian", "American", "French", "German", "Chinese", "Japanese", "European",
             "African", "Asian", "Roman", "Greek", "Russian", "Spanish", "Italian", "Earth", "Sun", "Moon",
             "Dr", "Mr", "Mrs", "Ms", "Prof", "Saint", "St"}


def numbers_in(text: str) -> Set[str]:
    t = (text or "").translate(DEV_DIGITS)
    t = re.sub(r"(?<=\d)[,٬](?=\d{3}\b)", "", t)
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+(?:\.\d+)?", t)}


def proper_names(text: str) -> Set[str]:
    """Capitalised words that are not sentence-initial, plus acronyms; a deterministic, conservative heuristic."""
    names: Set[str] = set()
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sent)
        for i, w in enumerate(words):
            core = w.strip("'-")
            if len(core) < 2 or core in STOP_CAPS:
                continue
            if core.isupper() and len(core) >= 2:
                names.add(core)
            elif i > 0 and core[0].isupper():
                names.add(core)
    return names


def devanagari_share(text: str) -> float:
    dev, lat = len(DEVANAGARI.findall(text or "")), len(LATIN.findall(text or ""))
    return dev / (dev + lat) if (dev + lat) else 0.0


def validate(req: Dict[str, Any], hindi: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not isinstance(hindi, dict):
        return ["the answer is not an object"]
    want = [s["scene_id"] for s in req["scenes"]]
    got = hindi.get("scenes") or []
    if [str(s.get("scene_id")) for s in got] != want:
        problems.append(f"scenes must be exactly {want} in order (got {[s.get('scene_id') for s in got]})")
        return problems
    terms = [t for t in (hindi.get("terms") or []) if isinstance(t, dict)]
    term_en = {str(t.get("en") or "").lower() for t in terms}
    term_tokens = {w.lower() for t in terms for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", str(t.get("en") or ""))}
    spoken_missing = [t.get("en") for t in terms if t.get("en") and not DEVANAGARI.search(str(t.get("spoken") or ""))]
    if spoken_missing:
        problems.append(f"terms need a Devanagari spoken form: {', '.join(map(str, spoken_missing[:6]))}")
    en_all, hi_all = "", ""
    total_words = 0
    for src, dst in zip(req["scenes"], got):
        sid = src["scene_id"]
        nar, head = str(dst.get("narration_hi") or "").strip(), str(dst.get("headline_hi") or "").strip()
        if not nar:
            problems.append(f"{sid}: narration_hi is empty")
            continue
        if not head:
            problems.append(f"{sid}: headline_hi is empty")
        share = devanagari_share(nar)
        if share < MIN_DEVANAGARI_SHARE:
            problems.append(f"{sid}: narration_hi must be spoken Devanagari (only {round(share * 100)}% Devanagari)")
        if head and len(head.split()) > 6:
            problems.append(f"{sid}: headline_hi is too long ({len(head.split())} words; at most 5)")
        hl = str(dst.get("highlight_hi") or "").strip()
        if src.get("highlight") and not hl:
            problems.append(f"{sid}: highlight_hi is empty")
        if hl and hl not in head:
            problems.append(f"{sid}: highlight_hi '{hl}' must appear inside headline_hi")
        if len(dst.get("labels_hi") or []) != len(src.get("labels") or []):
            problems.append(f"{sid}: labels_hi must have {len(src.get('labels') or [])} entries")
        en_all += " " + src.get("narration", "") + " " + src.get("headline", "")
        hi_all += " " + nar + " " + head
        total_words += len(nar.split())
    missing = numbers_in(en_all) - numbers_in(hi_all)
    extra = numbers_in(hi_all) - numbers_in(en_all)
    if missing:
        problems.append(f"numbers missing in Hindi: {', '.join(sorted(missing))}")
    if extra:
        problems.append(f"numbers not in the English (possible invented facts): {', '.join(sorted(extra))}")
    uncovered = [n for n in proper_names(" ".join(s.get("narration", "") for s in req["scenes"]))
                 if n.lower() not in term_en and n.lower() not in term_tokens and n not in hi_all
                 and not any(part.lower() in term_tokens for part in re.split(r"[-']", n) if part)]
    if uncovered:
        problems.append(f"names/terms not preserved or not in the term list: {', '.join(sorted(uncovered)[:8])}")
    est = total_words / WORDS_PER_SEC
    if not MIN_SECONDS * 0.9 <= est <= MAX_SECONDS * 1.05:
        problems.append(f"estimated spoken length {round(est)} s is outside {int(MIN_SECONDS)}-{int(MAX_SECONDS)} s "
                        f"({total_words} words); aim for about "
                        f"{round(float(req.get('total_seconds') or 50.0) * WORDS_PER_SEC)} words in total")
    return problems
