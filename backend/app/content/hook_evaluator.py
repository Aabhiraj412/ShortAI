"""
Hook evaluator: does the opening give a viewer a reason to keep watching?

Scores eight observable characteristics of the opening sentence(s), each with evidence, and
subtracts for openings that kill curiosity. It replaces a check that only asked "is there a cue
word and is it not a definition", which scored almost every hook as excellent.

    curiosity      an open loop: a question, a withheld answer, a "but"/"yet" turn
    specificity    concrete names, numbers, places, objects rather than abstractions
    surprise       counter-intuitive or extreme markers (more than, never, only, opposite, first)
    stakes         something to gain, lose or risk
    contradiction  an assumption set up and then broken
    clarity        a short, direct first sentence a listener can parse once
    relevance      the hook is about this topic, not generic
    continuation   the hook leaves something unanswered that the script answers later

Penalties: dictionary or encyclopedia openings, generic openers ("Did you know", "Have you ever
wondered", "In today's video"), throat-clearing ("Let's talk about"), and an opening that gives the
whole answer away. Scores are 0-10. The evaluator judges text; it cannot judge delivery.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

_STOP = set("""a an the and or but if of in on at to for from by with as is are was were be been it its this that
these those there their they them he she his her we our you your i me my not no so than then into over under
about after before also just very more most much many such can could would should will may might which who
what when where why how all any each every some one two do does did has have had""".split())

_DEFINITION = re.compile(r"^\s*(?!(?:there|it|this|that|here|what|which)\b)(the\s+)?[A-Z][\w'\- ]{0,60}?\s+"
                         r"(is|are|was|were)\s+(a|an|the|one of|known as)\b", re.I)
_GENERIC = re.compile(r"^\s*(did you know|have you ever (wondered|thought)|in (today'?s|this) (video|short)|"
                      r"welcome|hey (guys|everyone)|today we|let'?s (talk|dive|explore|look)|"
                      r"here'?s (everything|what you need)|ever wondered)\b", re.I)
_THROAT = re.compile(r"\b(let me tell you|in this video|we('| a)re going to|stay tuned|keep watching)\b", re.I)
_QUESTION = re.compile(r"\?")
_TURN = re.compile(r"\b(but|yet|except|instead|actually|in fact|turns out|until|not what|wasn'?t|isn'?t|doesn'?t|didn'?t|"
                   r"never|no one|nobody)\b", re.I)
_SURPRISE = re.compile(r"\b(more than|less than|twice|double|triple|times (more|less|the)|only|never|first|last|"
                       r"opposite|biggest|largest|smallest|fastest|most|least|every|nobody|no one|secret|"
                       r"accident|strange|impossible|record|outlier|without|no \w+ (?:could|can|would|has|had))\b", re.I)
_STAKES = re.compile(r"\b(risk|danger|threat|lose|lost|losing|die|died|death|deadly|survive|save|saved|cost|costs|"
                     r"pay|pays|paid|fail|failure|collapse|crash|destroy|wreck|war|fight|freeze|crisis|harm|"
                     r"million|billion|lives|life)\b", re.I)
_ASSUMPTION = re.compile(r"\b(most people|everyone (thinks|assumes|knows)|you (think|probably|might)|we (think|assume|expect)|"
                         r"common(ly)? (belief|assumption)|picture|imagine|assume|expect|supposed to)\b", re.I)
_ABSTRACT = re.compile(r"\b(things?|stuff|aspects?|factors?|various|many|several|important|interesting|amazing|"
                       r"significant|fascinating|incredible)\b", re.I)


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _content(text: str) -> List[str]:
    return [w.lower().strip("'") for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", text) if w.lower() not in _STOP]


def _stem(w: str) -> str:
    for suf in ("ing", "ers", "ies", "es", "ed", "er", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def evaluate_hook(hook_text: str, *, topic_title: str = "", rest_of_script: str = "",
                  research: str = "") -> Dict[str, Any]:
    """0-10 hook score with per-characteristic evidence. `rest_of_script` is used for continuation."""
    sents = _sentences(hook_text)
    first = sents[0] if sents else ""
    opening = " ".join(sents[:2])
    words_first = first.split()
    parts: Dict[str, Dict[str, Any]] = {}

    def put(name: str, score: float, why: str) -> None:
        parts[name] = {"score": round(max(0.0, min(1.0, score)), 2), "evidence": why}

    q = bool(_QUESTION.search(opening))
    turn = _TURN.search(opening)
    put("curiosity", (0.6 if q else 0) + (0.4 if turn else 0) + (0.2 if re.search(r"\b(why|how|what)\b", opening, re.I) else 0),
        f"question={q}; turn word={turn.group(0) if turn else None}")

    numbers = re.findall(r"\d[\d,.]*|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b",
                         opening, re.I)
    proper = [w for i, w in enumerate(re.findall(r"[A-Za-z][\w'\-]*", opening)) if w[:1].isupper() and i > 0
              and w.lower() not in _STOP]
    abstract = _ABSTRACT.findall(opening)
    spec = 0.35 * min(2, len(numbers)) + 0.2 * min(2, len(proper)) - 0.2 * len(abstract)
    put("specificity", spec + (0.2 if len(_content(opening)) >= 6 else 0),
        f"numbers={numbers[:3]}; names={proper[:3]}; vague words={abstract[:3]}")

    sur = _SURPRISE.findall(opening)
    put("surprise", 0.45 * min(2, len(sur)), f"markers={[s if isinstance(s, str) else s[0] for s in sur][:3]}")

    st = _STAKES.findall(opening)
    put("stakes", 0.5 * min(2, len(st)), f"markers={[s if isinstance(s, str) else s[0] for s in st][:3]}")

    assume = _ASSUMPTION.search(opening)
    put("contradiction", (0.6 if assume else 0) + (0.4 if (assume and turn) or re.search(r"\b(not|isn'?t|doesn'?t|wrong|opposite)\b", opening, re.I) else 0),
        f"assumption={assume.group(0) if assume else None}; negation/turn={bool(turn)}")

    n = len(words_first)
    clauses = len(re.findall(r",|;|\bwhich\b|\bwho\b", first))
    put("clarity", 1.0 - max(0, n - 16) * 0.06 - max(0, clauses - 1) * 0.2 - (0.3 if n < 4 else 0),
        f"first sentence {n} words, {clauses} clause breaks")

    topic_words = {_stem(w) for w in _content(topic_title)}
    research_words = {_stem(w) for w in _content(research)} if research else set()
    hook_words = {_stem(w) for w in _content(opening)}
    rel_t = len(hook_words & topic_words) / max(1, min(4, len(topic_words))) if topic_words else 0.5
    rel_r = len(hook_words & research_words) / max(1, len(hook_words)) if research_words else 0.5
    put("relevance", 0.6 * min(1.0, rel_t) + 0.4 * rel_r, f"topic overlap {rel_t:.2f}, research overlap {rel_r:.2f}")

    later = {_stem(w) for w in _content(rest_of_script)}
    key = {w for w in hook_words if len(w) > 3}
    answered = len(key & later) / max(1, len(key)) if later else 0.0
    gives_away = bool(re.search(r"\b(because|the answer is|that'?s why|which means)\b", opening, re.I))
    put("continuation", (0.5 if (q or turn) else 0.15) + (0.5 * min(1.0, answered * 1.6) if later else 0.25)
        - (0.3 if gives_away else 0),
        f"open loop={q or bool(turn)}; {answered:.0%} of the hook's key words return later; gives answer away={gives_away}")

    # A strong hook needs the basics (clear, on-topic, an open loop) and pulls with two or three levers;
    # it is not expected to use all four, so only the best two levers count.
    core = (parts["clarity"]["score"] + parts["relevance"]["score"] + parts["continuation"]["score"]
            + 1.4 * parts["curiosity"]["score"]) / 4.4
    levers = sorted((parts[k]["score"] for k in ("specificity", "surprise", "stakes", "contradiction")), reverse=True)
    pull = (levers[0] + levers[1]) / 2
    raw = 10 * (0.5 * core + 0.5 * pull)

    penalties: List[Dict[str, Any]] = []
    title_lead = " ".join(topic_title.split()[:3]).lower()
    if _DEFINITION.match(first) or (title_lead and re.match(rf"^\s*(the\s+)?{re.escape(title_lead)}\b.*?\b(is|are|was|were)\b", first, re.I)
                                    and len(words_first) > 8 and not q):
        penalties.append({"reason": "encyclopedia-style opening: the topic is defined instead of made interesting", "points": 3.5})
    if _GENERIC.search(first):
        penalties.append({"reason": "generic opener that could start any video", "points": 2.0})
    if _THROAT.search(opening):
        penalties.append({"reason": "throat-clearing instead of content", "points": 1.5})
    if n > 28:
        penalties.append({"reason": f"first sentence too long to land in one listen ({n} words)", "points": 1.0})
    score = max(0.0, min(10.0, raw - sum(p["points"] for p in penalties)))
    return {"score": round(score, 1), "raw": round(raw, 1), "characteristics": parts, "penalties": penalties,
            "opening": opening[:200]}
