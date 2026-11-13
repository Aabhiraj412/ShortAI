"""
Quantities in narration text, whether written as digits ("US$3 million", "300,000") or spelled out
for speech ("three million dollars", "three hundred thousand"). Used to check that every number a
script states is supported by the research, which the digit-only check could not do once the model
began spelling numbers out for text-to-speech (found 2026-11-13 in the first real Gemini scripts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Set

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
           "trillion": 1_000_000_000_000}
_MULTIPLES = {"twice": 2, "double": 2, "doubled": 2, "triple": 3, "tripled": 3, "thrice": 3, "half": 0.5}
# a small spelled number is a quantitative claim only when it counts something measurable
UNIT_WORDS = {"years", "year", "days", "hours", "minutes", "seconds", "months", "weeks", "decades", "centuries",
              "dollars", "euros", "pounds", "percent", "per", "times", "kilometres", "kilometers", "km", "miles",
              "metres", "meters", "feet", "tonnes", "tons", "kilograms", "kg", "degrees", "people", "species",
              "satellites", "troops", "laureates", "winners", "prizes", "awards", "million", "billion"}
_DIGIT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s*(%|percent|hundred|thousand|million|billion|trillion))?",
                    re.I)
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)?", re.I)


@dataclass
class Quantity:
    value: float
    text: str
    claim: bool        # True when this number is a factual quantity the research must support


def _spelled_runs(text: str) -> List[Quantity]:
    words = [w.lower() for w in _WORD.findall(text or "")]
    out: List[Quantity] = []
    i = 0
    while i < len(words):
        w = words[i]
        parts = w.split("-")
        if not (parts[0] in _UNITS or parts[0] in _TENS):
            if w in _MULTIPLES:
                out.append(Quantity(_MULTIPLES[w], w, claim=True))
            i += 1
            continue
        total, current, j, run = 0.0, 0.0, i, []
        while j < len(words):
            tok = words[j]
            sub = tok.split("-")
            if all(s in _UNITS or s in _TENS for s in sub):
                current += sum(_UNITS.get(s, 0) + _TENS.get(s, 0) for s in sub)
            elif tok in _SCALES:
                scale = _SCALES[tok]
                if scale == 100:
                    current = (current or 1) * 100
                else:
                    total += (current or 1) * scale
                    current = 0
            elif tok == "and" and run and j + 1 < len(words) and (words[j + 1].split("-")[0] in _UNITS or words[j + 1].split("-")[0] in _TENS):
                pass
            else:
                break
            run.append(tok)
            j += 1
        value = total + current
        nxt = words[j] if j < len(words) else ""
        has_scale = any(t in _SCALES for t in run)
        claim = value >= 13 or has_scale or nxt in UNIT_WORDS
        out.append(Quantity(value, " ".join(run), claim))
        i = max(j, i + 1)
    return out


def quantities(text: str) -> List[Quantity]:
    out: List[Quantity] = []
    for m in _DIGIT.finditer(text or ""):
        raw, scale = m.group(1), (m.group(2) or "").lower()
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if scale in _SCALES:
            v *= _SCALES[scale]
        out.append(Quantity(v, m.group(0), claim=True))
    out.extend(_spelled_runs(re.sub(_DIGIT, " ", text or "")))
    return out


def supported_values(research: str) -> Set[float]:
    """Every value the research states, plus the bare mantissas ("$3 million" also supports "3")."""
    vals: Set[float] = set()
    for q in quantities(research):
        vals.add(round(q.value, 6))
    for m in _DIGIT.finditer(research or ""):
        try:
            vals.add(round(float(m.group(1).replace(",", "")), 6))
        except ValueError:
            pass
    return vals


def unsupported_claims(script_text: str, research: str) -> List[str]:
    known = supported_values(research)
    bad = []
    for q in quantities(script_text):
        if q.claim and round(q.value, 6) not in known:
            bad.append(q.text)
    return sorted(set(bad))
