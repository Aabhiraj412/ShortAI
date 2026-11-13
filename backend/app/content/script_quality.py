"""
Structured script-quality evaluation: eight criteria, each 0-10 with the reason for the score.

    hook                   the opening (hook_evaluator: curiosity, specificity, surprise, stakes,
                           contradiction, clarity, relevance, continuation; penalties for definitions)
    curiosity              an open loop that the script later closes, and turns along the way
    narrative_progression  the five stages in order, each adding something new; no padding about
                           the source instead of the subject; a specific reveal
    grounding              every number (digits or spelled out) is in the research
    originality            no 9-word runs copied from the research, no template phrases
    spoken_naturalness     sentence length, written-style constructions, abbreviations
    pacing                 stage proportions, sentence-length variety, fit to the Shorts duration
    payoff                 answers the hook, specific, not templated, no call to action

It judges what text analysis can establish. Whether a script is genuinely interesting beyond these
signals still needs a human listen, and the report says so.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List

from backend.app.content.grounded_script_writer import (
    COPY_NGRAM, MAX_WORDS, MIN_WORDS, STAGES, _NUMBER, _norm_number, _words, fact_corpus,
)
from backend.app.content.hook_evaluator import _content, _stem, evaluate_hook
from backend.app.content.number_words import unsupported_claims

WORDS_PER_SEC = 2.3
_CTA = re.compile(r"\b(subscribe|follow|like and share|in this video)\b", re.I)
_TURN = re.compile(r"\b(but|yet|instead|actually|in fact|turns out|however|until|except|surprisingly)\b", re.I)
_META = re.compile(r"\b(preprint|paper|study|article|report|journal|research(er)?s? (say|show|found))\b.{0,40}?"
                   r"\b(outlines?|highlights?|shows?|says|describes?|explains?|finds|found|suggests?)\b|"
                   r"\b(is|was) (normally|best|mostly|usually) known for\b|\boutlines? this\b", re.I)
_TEMPLATE = re.compile(r"and that is how .* reshaped our understanding|at the core of .* is this finding|"
                       r"verified escalation confirmed", re.I)
_ABSTRACT = {"strategy", "approach", "reality", "impact", "role", "matter", "issue", "concept", "solution",
             "environment", "aspect", "factor", "thing", "things", "way", "nature", "story"}
_STAGE_SHARE = {"HOOK": (0.10, 0.26), "CONTEXT": (0.14, 0.32), "ESCALATION": (0.14, 0.32),
                "REVELATION": (0.12, 0.30), "PAYOFF": (0.06, 0.20)}


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _specifics(text: str) -> int:
    nums = len(re.findall(r"\d|\b(hundred|thousand|million|billion|twice|double)\b", text, re.I))
    names = sum(1 for i, w in enumerate(re.findall(r"[A-Za-z][\w'\-]*", text)) if i > 0 and w[:1].isupper())
    return nums + names


def evaluate_script(script: Dict[str, Any], topic: Any = None) -> Dict[str, Any]:
    sections = script.get("sections") or []
    texts = {str(s.get("stage", "")).upper(): s.get("text", "") for s in sections}
    full = script.get("full_script") or " ".join(s.get("text", "") for s in sections)
    words = full.split()
    sents = _sentences(full)
    title = str(getattr(topic, "title", "") or "")
    corpus = fact_corpus(topic) if topic is not None else ""
    scores: Dict[str, Dict[str, Any]] = {}

    def put(name: str, score: float, reason: str, **extra: Any) -> None:
        scores[name] = {"score": round(max(0.0, min(10.0, score)), 1), "reason": reason, **extra}

    # 1. hook
    hook_text = texts.get("HOOK") or (sents[0] if sents else "")
    rest = " ".join(t for k, t in texts.items() if k != "HOOK") if texts else " ".join(sents[1:])
    hook = evaluate_hook(hook_text, topic_title=title, rest_of_script=rest, research=corpus)
    pen = "; ".join(p["reason"] for p in hook["penalties"])
    put("hook", hook["score"], (f"penalised: {pen}" if pen else "no opening penalties") +
        f"; strongest levers: {', '.join(k for k, v in sorted(hook['characteristics'].items(), key=lambda kv: -kv[1]['score'])[:3])}"
        + ("; dictionary-style definition" if any("encyclopedia" in p["reason"] for p in hook["penalties"]) else ""),
        detail=hook)

    # 2. curiosity: open loop in the hook, closed later, with turns in the middle
    hc = hook["characteristics"]
    mid = " ".join(texts.get(k, "") for k in ("ESCALATION", "REVELATION"))
    turns = len(_TURN.findall(mid))
    put("curiosity", 10 * (0.3 * hc["curiosity"]["score"] + 0.2 * hc["surprise"]["score"]
                           + 0.3 * hc["continuation"]["score"]) + 2.0 * min(1, turns),
        f"hook open loop {hc['curiosity']['score']:.2f}, surprise {hc['surprise']['score']:.2f}, "
        f"closed later {hc['continuation']['score']:.2f}, {turns} turn(s) in the middle")

    # 3. narrative progression
    stages = tuple(texts.keys())
    seen: set = set()
    novelty: List[float] = []
    for st in STAGES:
        ws = {_stem(w) for w in _content(texts.get(st, ""))}
        if ws:
            novelty.append(len(ws - seen) / len(ws))
        seen |= ws
    meta = [m.group(0) for m in _META.finditer(full)]
    reveal = texts.get("REVELATION", "")
    reveal_new = {_stem(w) for w in _content(reveal)} - {_stem(w) for w in _content(hook_text)}
    vague_reveal = _specifics(reveal) == 0 and len(reveal_new) < 6
    base = 10 if stages == STAGES else 3
    prog = base - 4 * max(0.0, 0.6 - (statistics.mean(novelty) if novelty else 0)) - 1.5 * len(meta) - (2 if vague_reveal else 0)
    put("narrative_progression", prog,
        f"stages {'in order' if stages == STAGES else list(stages)}; mean novelty {statistics.mean(novelty) if novelty else 0:.2f}; "
        f"padding about the source: {meta or 'none'}; reveal {'vague (no specifics, little new)' if vague_reveal else 'specific'}")

    # 4. grounding (digits and spelled-out quantities)
    if corpus:
        known = {_norm_number(t) for t in _NUMBER.findall(corpus)}
        bad_digits = sorted({_norm_number(t) for t in _NUMBER.findall(full)} - known - {""})
        bad_spelled = unsupported_claims(_NUMBER.sub(" ", full), corpus)
        bad = bad_digits + bad_spelled
        n_claims = len(_NUMBER.findall(full)) + len(re.findall(r"\b(hundred|thousand|million|billion|twice|double)\b", full, re.I))
        put("grounding", 10 - 4 * len(bad), f"{n_claims} quantities stated, unsupported by the research: {bad or 'none'}")
    else:
        scores["grounding"] = {"score": None, "reason": "no research supplied: not measured"}

    # 5. originality
    templ = _TEMPLATE.findall(full)
    if corpus:
        src = _words(corpus)
        grams = {" ".join(src[i:i + COPY_NGRAM]) for i in range(len(src) - COPY_NGRAM + 1)}
        out = _words(full)
        copied = sum(1 for i in range(len(out) - COPY_NGRAM + 1) if " ".join(out[i:i + COPY_NGRAM]) in grams)
        share = copied / max(1, len(out) - COPY_NGRAM + 1)
        put("originality", 10 * (1 - min(1, share * 1.5)) - 3 * len(templ),
            f"{share:.0%} of {COPY_NGRAM}-word windows copied from the research; template phrases: {len(templ)}")
    else:
        scores["originality"] = {"score": None, "reason": "no research supplied: not measured"}

    # 6. spoken naturalness
    lens = [len(s.split()) for s in sents] or [0]
    avg = sum(lens) / len(lens)
    paren = full.count("(") + full.count(";")
    nominal = len(re.findall(r"\b\w{5,}(tion|ment|ance|ence|ity)s?\b", full)) / max(1, len(words))
    abbreviations = len(re.findall(r"\b[A-Z]{2,}\b", full))
    put("spoken_naturalness", 10 - max(0, avg - 14) * 0.6 - sum(1 for n in lens if n > 26) * 1.0 - paren * 2
        - max(0, nominal - 0.06) * 40 - max(0, abbreviations - 3) * 0.5,
        f"average sentence {avg:.1f} words, {sum(1 for n in lens if n > 26)} over 26 words, {paren} parentheses/semicolons, "
        f"written-style nouns {nominal:.0%}, {abbreviations} abbreviations")

    # 7. pacing
    total = max(1, len(words))
    off = []
    for st, (lo, hi) in _STAGE_SHARE.items():
        share = len(texts.get(st, "").split()) / total
        if texts and not lo <= share <= hi:
            off.append(f"{st} {share:.0%}")
    spread = statistics.pstdev(lens) if len(lens) > 1 else 0
    in_range = MIN_WORDS <= len(words) <= MAX_WORDS
    put("pacing", 10 - 1.5 * len(off) - (2 if spread < 2.5 else 0) - (0 if in_range else
        min(5, abs(len(words) - (MIN_WORDS if len(words) < MIN_WORDS else MAX_WORDS)) / 4)),
        f"{len(words)} words = about {len(words) / WORDS_PER_SEC:.0f} s; stage shares out of range: {off or 'none'}; "
        f"sentence-length spread {spread:.1f} words")

    # 8. payoff
    last = texts.get("PAYOFF", "") or (sents[-1] if sents else "")
    core = _CTA.sub("", last)
    hook_words = {_stem(w) for w in _content(hook_text) if len(w) > 3}
    callback = len(hook_words & {_stem(w) for w in _content(core)}) >= 2
    templated = bool(re.search(r"and that is how .* reshaped our understanding", last, re.I))
    abstract = sum(1 for w in _content(core) if w in _ABSTRACT)
    put("payoff", 4 + (3 if callback else 0) + (3 if not templated else 0) - (2 if _CTA.search(last) else 0)
        - min(2, abstract) * 0.75 + (0.5 if _specifics(core) else 0),
        f"{'answers the hook' if callback else 'no callback to the hook'}; {'templated closing line' if templated else 'specific closing'}; "
        f"{'contains a call to action' if _CTA.search(last) else 'no call to action'}; {abstract} abstract word(s)")

    measured = {k: v["score"] for k, v in scores.items() if v["score"] is not None}
    overall = round(sum(measured.values()) / len(measured) * 10, 1) if measured else None
    return {"overall": overall, "criteria": scores, "hook_score": hook["score"],
            "script_source": script.get("script_source"),
            "human_review": "interest and clarity beyond these signals need a human listen"}
