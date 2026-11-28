"""
Topic coverage: does the narration actually name the topic? (2026-11-28)

One rule shared by the script writer (``grounded_script_writer.validate_sections``, which gives the LLM one revision
with the feedback below) and production QC (``ContentIntegrityGate`` CHECK_A_TOPIC_NARRATION), so the writer catches
exactly what QC would reject instead of handing it a script that is certain to fail.

Legacy CHECK_A rule: at least two title words longer than 3 letters must appear verbatim in the narration.
Live failures it produced although the narration was on topic: "Generative pre-trained transformer" narrated as
"GPTs ... built on the transformer" (cycle_5_20261128_0400), "Fiona hippopotamus" narrated as "Fiona the hippo",
"Ancient Egypt" / "Retail industry" where the generic word was missing.

This rule keeps the same threshold (two essential concepts, or the only one there is) but
  * drops generic framing words ("ancient", "industry", "finding", "into", ...) from the required set,
  * accepts inflections ("generate" for "generative", "training" for "trained", "economy" for "economics"),
    clippings of five letters or more ("hippo" for "hippopotamus") and the title's acronym ("GPT").
It is never stricter than the legacy rule: a narration the legacy rule accepts is still accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# Framing words that say how a title is told, not what it is about. Never required on their own.
GENERIC = frozenset("""
about after ancient became become before behind being does during ever every explained explaining finding first from
great hidden historical history industry inside into just life made make makes modern most mystery mysteries really
real rise secret secrets story stories strange than that their them there these they this those through truth turning
what when where which while why with world
""".split())

_SMALL = frozenset({"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "at", "by"})
_SUFFIXES = ("ations", "ation", "ically", "ical", "ings", "ing", "ics", "ies", "ied", "ive", "ers", "es", "ed",
             "er", "al", "ic", "s", "y")


def title_terms(title: str) -> List[str]:
    """The legacy CHECK_A tokenisation: words only ('Regulus (Turner)' -> 'turner'), longer than 3 letters."""
    return [w for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (title or "").lower()) if len(w) > 3]


def _stem(w: str) -> str:
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _acronyms(title: str) -> List[str]:
    """'Generative pre-trained transformer' -> ['gpt', 'gptt']: hyphenated compounds as one word and as parts,
    with and without small words; three letters or more."""
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", (title or "").lower())
    out = []
    for keep_small in (True, False):
        for split_hyphens in (False, True):
            parts = [p for w in words for p in (w.split("-") if split_hyphens else [w]) if p]
            ac = "".join(p[0] for p in parts if keep_small or p not in _SMALL)
            if len(ac) >= 3 and ac not in out:
                out.append(ac)
    return out


def _covered(term: str, narr_lower: str, tokens: List[str]) -> bool:
    if term in narr_lower:                          # the legacy substring match
        return True
    st = _stem(term)
    for tok in tokens:
        if tok.startswith(st) and len(st) >= 4:     # inflection: generate / generation for generative
            return True
        if len(tok) >= 4 and _stem(tok) == st:      # economy / economics, geography / geographical
            return True
        if len(tok) >= 5 and term.startswith(tok):  # clipping: hippo for hippopotamus
            return True
    return False


@dataclass
class Coverage:
    passed: bool
    terms: List[str]                     # every title word the legacy rule considers
    legacy_matches: List[str]            # verbatim matches (the legacy CHECK_A message lists these)
    legacy_passed: bool
    essential: List[str]                 # title words that are not generic framing
    covered: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    required: int = 0
    acronym: str = ""                    # the acronym found in the narration, if any
    acronyms: List[str] = field(default_factory=list)

    def feedback(self) -> str:
        """One revision instruction for the script writer."""
        return (f"the narration does not name the topic clearly: use at least {self.required} of these topic terms "
                f"(any natural form, or the topic's usual acronym): {', '.join(self.essential)}; "
                f"missing: {', '.join(self.missing)}")


def check(title: str, narration: str) -> Coverage:
    narr_lower = (narration or "").lower()
    terms = title_terms(title)
    legacy = [w for w in terms if w in narr_lower]
    legacy_ok = not (len(legacy) < 2 and len(terms) >= 2)
    essential = [w for w in terms if w not in GENERIC] or terms
    tokens = re.findall(r"[a-z0-9]+", narr_lower)
    acronyms = _acronyms(title)
    # written in capitals, as narration writes acronyms ("GPTs"), so the word "cat" never counts as CAT
    found_acr = next((a for a in acronyms if re.search(rf"\b{re.escape(a.upper())}s?\b", narration or "")), "")
    covered = [w for w in essential if found_acr or _covered(w, narr_lower, tokens)]
    required = min(2, len(essential))
    return Coverage(passed=legacy_ok or len(covered) >= required, terms=terms, legacy_matches=legacy,
                    legacy_passed=legacy_ok, essential=essential, covered=covered,
                    missing=[w for w in essential if w not in covered], required=required, acronym=found_acr,
                    acronyms=acronyms)
