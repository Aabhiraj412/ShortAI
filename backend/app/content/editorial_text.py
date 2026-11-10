"""
Editorial Text System for CrazzyDocs YouTube Shorts Studio.

Extracts, structures, and synchronizes 2-5 word power phrases from narrative beats,
replacing verbatim word-for-word subtitles with high-impact visual storytelling.

Architecture:
- Narration Transcript   : Full spoken words & word boundaries (for sync & analytics).
- Accessibility Subtitles: Complete word-for-word captions (optional, stored for CC upload).
- Editorial Text Layer   : Short, selective power phrases burned into the default video presentation.
"""

from dataclasses import asdict, dataclass, field
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EditorialTextCue:
    """A single high-impact editorial power phrase."""
    text: str                     # 2-5 words maximum
    start_sec: float              # Precise start time in seconds
    end_sec: float                # Precise end time in seconds
    style_variant: str = "highlight" # "highlight", "metric", "alert", "punch", "question"
    emphasis_word: Optional[str] = None # Word within phrase to emphasize

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EditorialTextPlan:
    """The complete editorial text sequence for a video."""
    title: str
    total_duration_sec: float
    cues: List[EditorialTextCue] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "total_duration_sec": self.total_duration_sec,
            "cues": [c.to_dict() for c in self.cues],
        }


# High-value semantic pattern matchers
METRIC_PATTERNS = [
    re.compile(r"(\$[\d,]+(?:\.\d+)?\s*(?:billion|million|thousand|trillion)?)", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?%)", re.IGNORECASE),
    re.compile(r"(\b\d{1,4}(?:,\d{3})*\s*(?:tons|kilometers|miles|degrees|hours|days|years|seconds)\b)", re.IGNORECASE),
    re.compile(r"(\b(?:one|two|three|four|five|ten|hundred|thousand|million|billion)\s*(?:dollar|dollars|percent|centuries)\b)", re.IGNORECASE),
]

ACTION_PATTERNS = [
    re.compile(r"\b(collapse|collapsed|collapsing|takeover|acquired|bought|sold|erased|plummeted|stolen|exploded|vanished|stretching|carried|hallucinating|ruptured|crushed|leaked|submerged|flattened)\b", re.IGNORECASE),
    re.compile(r"\b(accounting mistake|rogue trader|strychnine|rat poison|event horizon|secret|impossible|breakthrough|chaotic race|wild dogs|cornfield|street boots|olympic gold|molasses|two-million-gallon|painted it brown|pressure surged|twenty-five-foot|twenty-one died|half as thick|licensed engineers|landmark settlement)\b", re.IGNORECASE),
]


class EditorialTextEngine:
    """Extracts, optimizes, and structures editorial text power phrases from narrative scripts."""

    def __init__(self):
        pass

    def extract_power_phrase(self, sentence: str) -> Tuple[str, str]:
        """
        Extracts a punchy 2-5 word power phrase and style variant from a spoken sentence.
        Avoids generic filler words, prioritizing metrics, named entities, and dramatic actions.
        """
        clean = re.sub(r"\[.*?\]", "", sentence).strip()
        clean = re.sub(r"[^\w\s\$\%\-]", "", clean)
        words = clean.split()
        if not words:
            return "", "highlight"

        clean_upper = clean.upper()

        # 1. Check for specific financial / numerical metrics
        for pat in METRIC_PATTERNS:
            m = pat.search(clean)
            if m:
                metric_str = m.group(1).upper()
                # Find adjacent context word
                if "LOST" in clean_upper or "LOSS" in clean_upper:
                    return f"{metric_str} LOST", "alert"
                if "PRICE" in clean_upper or "COST" in clean_upper:
                    return f"COST: {metric_str}", "metric"
                if "BOUGHT" in clean_upper or "PURCHASED" in clean_upper:
                    return f"BOUGHT FOR {metric_str}", "highlight"
                if "COLLAPSE" in clean_upper or "DROP" in clean_upper:
                    return f"{metric_str} COLLAPSE", "alert"
                return f"{metric_str}", "metric"

        # 2. Check for dramatic action concepts
        for pat in ACTION_PATTERNS:
            m = pat.search(clean)
            if m:
                action_word = m.group(1).upper()
                if "TWO-MILLION-GALLON" in clean_upper or ("MOLASSES" in clean_upper and "SWALLOWED" in clean_upper):
                    return "2.3M GALLON TSUNAMI", "alert"
                if "LEAKED CONSTANTLY" in clean_upper or "FIFTY-FOOT" in clean_upper or "STEEL TANK" in clean_upper:
                    return "LEAKING STEEL TANK", "alert"
                if "PAINTED IT BROWN" in clean_upper or "HIDE THE LEAKS" in clean_upper:
                    return "PAINTED TO HIDE LEAKS", "alert"
                if "PRESSURE SURGED" in clean_upper or "GAS PRESSURE" in clean_upper or "FERMENTATION" in clean_upper:
                    return "INTERNAL PRESSURE SURGE", "alert"
                if "TWENTY-FIVE-FOOT" in clean_upper or "THIRTY-FIVE MILES" in clean_upper:
                    return "25-FT WAVE AT 35 MPH", "punch"
                if "FLATTENED BUILDINGS" in clean_upper or "CRUSHING FORCE" in clean_upper or "FLATTENED" in clean_upper:
                    return "BUILDINGS FLATTENED", "punch"
                if "TWENTY-ONE DIED" in clean_upper or "ONE HUNDRED FIFTY" in clean_upper:
                    return "21 DEAD, 150 INJURED", "metric"
                if "HALF AS THICK" in clean_upper or "CORPORATE OVERSIGHT" in clean_upper or "CORPORATE NEGLIGENCE" in clean_upper:
                    return "STEEL HALF THICKNESS", "alert"
                if "LICENSED ENGINEERS" in clean_upper or "LANDMARK SETTLEMENT" in clean_upper:
                    return "ENGINEERING LICENSING BORN", "highlight"
                if "FOLLOW CRAZZYDOCS" in clean_upper:
                    return "FOLLOW CRAZZYDOCS", "highlight"
                if "STRYCHNINE" in clean_upper:
                    return "STRYCHNINE COCKTAIL", "alert"
                if "ROGUE TRADER" in clean_upper:
                    return "ROGUE TRADER", "alert"
                if "RAT POISON" in clean_upper and "BRANDY" in clean_upper:
                    return "RAT POISON & BRANDY", "alert"
                if "RAT POISON" in clean_upper:
                    return "RAT POISON BREAKFAST", "alert"
                if "CHAOTIC RACE" in clean_upper:
                    return "MOST CHAOTIC RACE", "highlight"
                if "WILD DOGS" in clean_upper or "CORNFIELD" in clean_upper:
                    return "CHASED INTO CORNFIELD", "punch"
                if "STREET BOOTS" in clean_upper or "HOUR NAP" in clean_upper:
                    return "ONE-HOUR NAP", "punch"
                if "CARRIED ACROSS" in clean_upper or "OLYMPIC GOLD" in clean_upper:
                    return "CARRIED TO GOLD", "punch"
                if "ACCOUNTING MISTAKE" in clean_upper:
                    return "ONE ACCOUNTING MISTAKE", "alert"
                if "EVENT HORIZON" in clean_upper:
                    return "THE EVENT HORIZON", "highlight"
                if "FREE FALL" in clean_upper:
                    return "PERPETUAL FREE FALL", "highlight"
                return f"{action_word}!", "punch"

        # 3. Questions / Paradoxes
        if "?" in sentence or clean_upper.startswith("WHY") or clean_upper.startswith("HOW") or clean_upper.startswith("COULD"):
            if len(words) <= 4:
                return " ".join(words[:4]).upper() + "?", "question"
            return "THE PARADOX?", "question"

        # 4. Fallback: Take the 2-4 most informative core words (skip stopwords)
        stopwords = {"THE", "A", "AN", "IN", "ON", "AT", "TO", "FOR", "OF", "WITH", "BY", "AND", "OR", "BUT", "IS", "ARE", "WAS", "WERE"}
        info_words = [w.upper() for w in words if w.upper() not in stopwords]
        if len(info_words) >= 2:
            return " ".join(info_words[:3]), "highlight"
        
        return " ".join(words[:min(4, len(words))]).upper(), "highlight"

    def build_editorial_plan_from_beats(
        self,
        title: str,
        total_duration: float,
        beats: List[Dict],
    ) -> EditorialTextPlan:
        """
        Builds a synchronized EditorialTextPlan from script scene beats.
        Ensures each beat has at most one focused 2-5 word cue with visual breathing room.
        """
        cues: List[EditorialTextCue] = []

        for b in beats:
            start_t = float(b.get("start_time", 0.0))
            dur = float(b.get("duration", 4.0))
            spoken = b.get("spoken_text", b.get("narration_text", b.get("headline", "")))
            
            # Use explicit on_screen_text / editorial_text if provided, otherwise extract
            explicit_text = b.get("editorial_text") or b.get("on_screen_text")
            if explicit_text:
                power_text = explicit_text.strip().upper()
                variant = b.get("style_variant", "highlight")
            else:
                power_text, variant = self.extract_power_phrase(spoken)

            if not power_text:
                continue

            # Word count safety clamp: max 5 words
            words = power_text.split()
            if len(words) > 5:
                power_text = " ".join(words[:5])

            # Timing: Display cue during peak emphasis (starts slightly after beat start, lasts 1.8-2.6s)
            cue_start = start_t + min(0.35, dur * 0.15)
            cue_dur = min(2.4, max(1.6, dur * 0.55))
            cue_end = min(total_duration - 0.2, cue_start + cue_dur)

            # Avoid placing cues inside the final outro
            if cue_start >= total_duration - 2.0:
                continue
            if cue_end > total_duration - 2.0:
                cue_end = total_duration - 2.0

            cues.append(EditorialTextCue(
                text=power_text,
                start_sec=round(cue_start, 2),
                end_sec=round(cue_end, 2),
                style_variant=variant,
            ))

        return EditorialTextPlan(
            title=title,
            total_duration_sec=total_duration,
            cues=cues,
        )
