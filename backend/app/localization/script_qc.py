"""Stage 4.02: Hinglish script quality checks (deterministic; no model). Language policy 2026-11-27.

``validate(req, adaptation) -> problems``; an empty list means the script is usable.

* completeness: one entry per English scene, in order, with narration, narration_tts and headline;
* visible text is English or Roman-script Hinglish: Devanagari in the narration (subtitles), headline, highlight,
  labels, units, title or category is a problem. English vocabulary is never a problem;
* natural Hinglish: the narration mixes in everyday Hindi (not a plain English script) and avoids formal, Sanskritized
  words; the voice form carries the Hindi words in Devanagari so the Hindi voice pronounces them correctly;
* the voice form says the same thing as the subtitles (same numbers, similar length per scene);
* facts: every number in the English appears, and no number the English lacks (Roman numerals may become digits);
* names: every proper name in the English narration is kept (or in the term list);
* on-screen text: the highlight is part of the headline; labels keep their count; headlines are short;
* length: the estimated spoken duration fits the Shorts window before any audio is made.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MIN_TTS_DEVANAGARI_SHARE = 0.2    # the voice form must carry its Hindi words in Devanagari (whole script)
MIN_HINDI_FUNCTION_SHARE = 0.12   # Roman narration: share of everyday Hindi words (Hinglish, not an English script)
# Unambiguous everyday Hindi words only (no English homographs such as to / is / the / me / hi / us / par).
HINDI_FUNCTION_WORDS = {
    "hai", "hain", "hota", "hoti", "hote", "tha", "thi", "ka", "ki", "ke", "ko", "se", "mein", "ne",
    "pe", "aur", "ya", "ye", "yeh", "wo", "woh", "jo", "kya", "kyun", "kyon", "kaise", "kaun", "kab", "kahan",
    "nahi", "nahin", "bhi", "toh", "ek", "kuch", "sab", "bahut", "aap", "aapko", "aapke", "aapka", "hum",
    "humare", "hamare", "unki", "unka", "unke", "iska", "iski", "iske", "uska", "uski", "uske", "isse",
    "jaise", "lekin", "magar", "kyunki", "agar", "phir", "abhi", "kar", "karta", "karti", "karte", "kiya",
    "kiye", "raha", "rahi", "rahe", "rehta", "rehti", "gaya", "gayi", "gaye", "saath", "liye", "wala", "wali",
    "wale", "dekho", "socho", "pata", "matlab", "yaani", "sirf", "pehla", "pehli", "baad", "pehle", "tak", "jab",
}
FORMAL_HINDI = {"mahatvapurn", "mahatvapoorn", "nirantar", "sanchalit", "atyant", "tatpashchat", "sarvapratham",
                "ityadi", "mastishk", "prakriya", "महत्वपूर्ण", "निरंतर", "संचालित", "अत्यंत", "तत्पश्चात", "सर्वप्रथम",
                "इत्यादि", "मस्तिष्क", "प्रक्रिया"}
MIN_SECONDS, MAX_SECONDS = 30.0, 58.0
WORDS_PER_SEC = 2.1              # measured Hindi TTS pace (2.07 words/s at +8 %, 2.26 at +18 %)
# The voice step speeds up to +18 % (2.26 words/s, still natural) when a Short would be too long, and it measures
# the real audio (<= 57 s) before anything renders; the final QC enforces 30-58 s. So the script estimate rejects
# only what even the fastest natural rate cannot fit (found in the 4.02 acceptance run: 128 words were rejected
# although they speak in about 56.6 s at +18 %).
FASTEST_NATURAL_WORDS_PER_SEC = 2.26
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


ROMAN = {"II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10",
         "XI": "11", "XII": "12"}


def roman_numbers(text: str) -> Set[str]:
    """Uppercase Roman numerals II-XII written as words ("class II"); a lone "I" is the pronoun and never counts.
    They may appear in Hindi as digits, so they are allowed there but not required (Hindi may say the number)."""
    return {ROMAN[w] for w in re.findall(r"\b[IVX]{1,4}\b", text or "") if w in ROMAN}


def proper_names(text: str) -> Set[str]:
    """Capitalised words that are not sentence-initial, plus acronyms; a deterministic, conservative heuristic.
    A word right after a colon starts a clause ("this finding: Small amounts of water ..."), so it is treated as
    sentence-initial (2026-11-28 false positive on production localization #5)."""
    names: Set[str] = set()
    for sent in re.split(r"(?<=[.!?:])\s+", text or ""):
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sent)
        for i, w in enumerate(words):
            core = w.strip("'-")
            if len(core) < 2 or core in STOP_CAPS or core in ROMAN:       # "class II" is a number, not a name
                continue
            if core.isupper() and len(core) >= 2:
                names.add(core)
            elif i > 0 and core[0].isupper():
                names.add(core)
    return names


def devanagari_share(text: str) -> float:
    dev, lat = len(DEVANAGARI.findall(text or "")), len(LATIN.findall(text or ""))
    return dev / (dev + lat) if (dev + lat) else 0.0


def visible_devanagari(texts: List[str]) -> List[str]:
    return [t for t in texts if t and DEVANAGARI.search(str(t))]


def _roman_words(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z]+", text or "")]


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
        problems.append(f"terms need a Devanagari spoken form for the voice: {', '.join(map(str, spoken_missing[:6]))}")
    visible: List[str] = [str(hindi.get("title") or ""), str(hindi.get("category") or "")]
    en_all, shown_all, tts_all = "", "", ""
    total_words = 0
    for src, dst in zip(req["scenes"], got):
        sid = src["scene_id"]
        nar = str(dst.get("narration") or "").strip()
        tts = str(dst.get("narration_tts") or "").strip()
        head = str(dst.get("headline") or "").strip()
        if not nar:
            problems.append(f"{sid}: narration is empty")
            continue
        if not tts:
            problems.append(f"{sid}: narration_tts is empty")
            continue
        if not head:
            problems.append(f"{sid}: headline is empty")
        if head and len(head.split()) > 6:
            problems.append(f"{sid}: headline is too long ({len(head.split())} words; at most 5)")
        hl = str(dst.get("highlight") or "").strip()
        if src.get("highlight") and not hl:
            problems.append(f"{sid}: highlight is empty")
        if hl and hl not in head:
            problems.append(f"{sid}: highlight '{hl}' must appear inside headline")
        if len(dst.get("labels") or []) != len(src.get("labels") or []):
            problems.append(f"{sid}: labels must have {len(src.get('labels') or [])} entries")
        visible += [nar, head, hl] + [str(x) for x in (dst.get("labels") or [])] + [str(x) for x in (dst.get("units") or [])]
        if numbers_in(nar) != numbers_in(tts):
            problems.append(f"{sid}: narration and narration_tts must say the same numbers "
                            f"({sorted(numbers_in(nar))} vs {sorted(numbers_in(tts))})")
        n_words, t_words = len(nar.split()), len(tts.split())
        if t_words and not 0.6 <= n_words / t_words <= 1.6:
            problems.append(f"{sid}: narration ({n_words} words) and narration_tts ({t_words} words) must be the same "
                            "sentence in two scripts")
        en_all += " " + src.get("narration", "") + " " + src.get("headline", "")
        shown_all += " " + nar + " " + head
        tts_all += " " + tts
        total_words += t_words
    dev = visible_devanagari(visible)
    if dev:
        problems.append("visible text must be English or Roman-script Hinglish (no Devanagari): "
                        + "; ".join(str(d)[:40] for d in dev[:4]))
    words = _roman_words(shown_all)
    if words:
        share = sum(1 for w in words if w in HINDI_FUNCTION_WORDS) / len(words)
        if share < MIN_HINDI_FUNCTION_SHARE:
            problems.append(f"the narration reads as English, not natural Hinglish (only {round(share * 100)}% everyday "
                            "Hindi words); mix in Hindi the way an Indian creator talks")
    formal = sorted({w for w in _roman_words(shown_all) + tts_all.split() if w.strip("।,.?!") in FORMAL_HINDI})
    if formal:
        problems.append(f"too formal / textbook Hindi; use everyday words instead of: {', '.join(formal[:6])}")
    if tts_all.strip() and devanagari_share(tts_all) < MIN_TTS_DEVANAGARI_SHARE:
        problems.append("narration_tts must write its Hindi words in Devanagari (the Hindi voice mispronounces "
                        "Roman-script Hindi); keep English words in Latin")
    missing = numbers_in(en_all) - numbers_in(shown_all)
    extra = numbers_in(shown_all) - numbers_in(en_all) - roman_numbers(en_all)
    if missing:
        problems.append(f"numbers missing in the Hinglish: {', '.join(sorted(missing))}")
    if extra:
        problems.append(f"numbers not in the English (possible invented facts): {', '.join(sorted(extra))}")
    uncovered = [n for n in proper_names(" ".join(s.get("narration", "") for s in req["scenes"]))
                 if n.lower() not in term_en and n.lower() not in term_tokens and n not in shown_all
                 and not any(part.lower() in term_tokens or part in shown_all for part in re.split(r"[-']", n) if part)]
    if uncovered:
        problems.append(f"names/terms not preserved: {', '.join(sorted(uncovered)[:8])}")
    est = total_words / WORDS_PER_SEC
    est_fastest = total_words / FASTEST_NATURAL_WORDS_PER_SEC
    if not (MIN_SECONDS * 0.9 <= est and est_fastest <= MAX_SECONDS):
        by_id = {s.get("scene_id"): s for s in hindi.get("scenes") or []}
        counts = [(r["scene_id"], len(str(by_id.get(r["scene_id"], {}).get("narration_tts", "")).split()), r["max_words"])
                  for r in req["scenes"] if r.get("max_words")]
        over = [f"{sid} has {n} words (max {mx})"                     # worst first: the most useful feedback
                for sid, n, mx in sorted((c for c in counts if c[1] > c[2]), key=lambda c: c[2] - c[1])]
        problems.append(f"estimated spoken length {round(est)} s is outside {int(MIN_SECONDS)}-{int(MAX_SECONDS)} s "
                        f"({total_words} words); aim for about "
                        f"{round(float(req.get('total_seconds') or 50.0) * WORDS_PER_SEC)} words in total"
                        + (f"; shorten: {'; '.join(over[:6])}" if over else ""))
    return problems
