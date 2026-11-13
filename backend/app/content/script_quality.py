"""
Structured script-quality evaluation (0-10 per criterion, with the reason for each score).

It scores what text analysis can actually establish: hook shape in the first ~5 spoken words,
spoken (not article) style, the five-stage structure, grounding of numbers in the research,
copying from the source, dictionary-style openings, fit to the Shorts duration, the payoff, and
narratability for text-to-speech. It does not claim to judge how *interesting* a script is beyond
those signals; that remains a human-review checkpoint and the report says so.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from backend.app.content.grounded_script_writer import (
    COPY_NGRAM, MAX_WORDS, MIN_WORDS, STAGES, _NUMBER, _norm_number, _words, fact_corpus,
)

_DEFINITION = re.compile(r"^\s*([A-Z][\w'\- ]{0,60}?)\s+(is|are|was|were)\s+(a|an|the|one of)\b", re.I)
_HOOK_CUES = re.compile(r"\?|\b(why|how|what if|imagine|never|nobody|most people|but|secret|surprising|only|"
                        r"more than|times|\d)\b", re.I)
_CTA = re.compile(r"\b(subscribe|follow|like and share|in this video)\b", re.I)
WORDS_PER_SEC = 2.3


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def evaluate_script(script: Dict[str, Any], topic: Any = None) -> Dict[str, Any]:
    sections = script.get("sections") or []
    full = script.get("full_script") or " ".join(s.get("text", "") for s in sections)
    words = full.split()
    sents = _sentences(full)
    scores: Dict[str, Dict[str, Any]] = {}

    def put(name: str, score: float, reason: str) -> None:
        scores[name] = {"score": round(max(0.0, min(10.0, score)), 1), "reason": reason}

    opening = " ".join(words[:6])
    hook_text = " ".join((sections[0].get("text", "") if sections else (sents[0] if sents else "")).split()[:25])
    title = str(getattr(topic, "title", "") or "")
    title_lead = " ".join(title.split()[:3]).lower()
    first = sents[0] if sents else ""
    definitional = bool(_DEFINITION.match(first)) or bool(
        title_lead and re.match(rf"^\s*(the\s+)?{re.escape(title_lead)}.*?(is|are|was|were)", first, re.I))
    cue = bool(_HOOK_CUES.search(hook_text))
    put("hook", (6 if cue else 2) + (0 if definitional else 4) - (3 if len(first.split()) > 22 else 0) if sents else 0,
        f"hook '{hook_text[:80]}': {'has' if cue else 'no'} curiosity cue; "
        f"{'dictionary-style definition' if definitional else 'not a definition'}; first sentence {len(first.split())} words")

    lens = [len(s.split()) for s in sents] or [0]
    avg = sum(lens) / len(lens)
    paren = full.count("(") + full.count(";")
    put("spoken_style", 10 - max(0, avg - 14) * 0.6 - paren * 2 - sum(1 for n in lens if n > 30),
        f"average sentence {avg:.1f} words, {sum(1 for n in lens if n > 30)} sentences over 30 words, {paren} parentheses/semicolons")

    stages = tuple(str(s.get("stage", "")).upper() for s in sections)
    put("structure", 10 if stages == STAGES else 3, f"stages {list(stages)}")

    corpus = fact_corpus(topic) if topic is not None else ""
    if corpus:
        known = {_norm_number(t) for t in _NUMBER.findall(corpus)}
        used = {_norm_number(t) for t in _NUMBER.findall(full)} - {""}
        bad = sorted(used - known)
        put("grounding", 10 if not bad else max(0, 10 - 4 * len(bad)),
            f"{len(used)} numbers used, ungrounded: {bad or 'none'}")
        src = _words(corpus)
        grams = {" ".join(src[i:i + COPY_NGRAM]) for i in range(len(src) - COPY_NGRAM + 1)}
        out = _words(full)
        copied = sum(1 for i in range(len(out) - COPY_NGRAM + 1) if " ".join(out[i:i + COPY_NGRAM]) in grams)
        share = copied / max(1, len(out) - COPY_NGRAM + 1)
        put("originality", 10 * (1 - min(1, share * 1.5)), f"{share:.0%} of {COPY_NGRAM}-word windows copied from the research")
    else:
        scores["grounding"] = {"score": None, "reason": "no research supplied: not measured"}
        scores["originality"] = {"score": None, "reason": "no research supplied: not measured"}

    secs = len(words) / WORDS_PER_SEC
    in_range = MIN_WORDS <= len(words) <= MAX_WORDS
    put("duration_fit", 10 if in_range else max(0, 10 - abs(len(words) - (MIN_WORDS if len(words) < MIN_WORDS else MAX_WORDS)) / 3),
        f"{len(words)} words = about {secs:.0f} s spoken (target {MIN_WORDS}-{MAX_WORDS} words)")

    last = sections[-1].get("text", "") if sections else ""
    payoff_core = _CTA.sub("", last)
    hook_words = set(_words(sections[0].get("text", ""))) if sections else set()
    callback = len(hook_words & set(_words(payoff_core))) >= 2
    templated = bool(re.search(r"and that is how .* reshaped our understanding", last, re.I))
    put("payoff", 4 + (3 if callback else 0) + (3 if not templated else 0) - (2 if _CTA.search(last) else 0),
        f"{'answers the hook' if callback else 'no callback to the hook'}; {'templated closing line' if templated else 'specific closing'}; "
        f"{'contains a call to action' if _CTA.search(last) else 'no call to action'}")

    abbreviations = len(re.findall(r"\b[A-Z]{2,}\b", full))
    put("narratability", 10 - paren * 2 - max(0, abbreviations - 3) * 0.5 - (2 if "/" in full else 0),
        f"{abbreviations} all-caps abbreviations, {paren} parentheses/semicolons")

    measured = {k: v["score"] for k, v in scores.items() if v["score"] is not None}
    overall = round(sum(measured.values()) / len(measured) * 10, 1) if measured else None
    return {"overall": overall, "criteria": scores, "script_source": script.get("script_source"),
            "human_review": "interest and clarity beyond these signals need a human listen"}
