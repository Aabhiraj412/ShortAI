"""
Shared Audio-Narration Timeline Architecture.

Binds TTS millisecond word boundaries to semantic visual events,
selective impact-word triggers, and procedural sound cues.
Acts as the single source of truth for narrative synchronization.
"""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Dict, List, Optional


@dataclass
class WordBoundary:
    word: str
    start_sec: float
    end_sec: float


@dataclass
class ImpactWordEvent:
    text: str
    start_sec: float
    end_sec: float
    style_variant: str = "highlight"  # "highlight", "metric", "alert", "punch"
    safe_y: int = 1450                 # Mobile safe zone Y coordinate


@dataclass
class VisualSyncEvent:
    action: str                       # e.g., "draw_circle", "plunge_curve", "strikeout"
    target: str                       # e.g., "earth", "solar_cost_line", "intro_text"
    start_sec: float
    end_sec: float
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NarrationBeat:
    beat_id: str
    spoken_text: str
    start_sec: float
    end_sec: float
    semantic_intent: str
    visual_events: List[VisualSyncEvent] = field(default_factory=list)
    impact_phrase: Optional[ImpactWordEvent] = None
    sfx_cue: Optional[str] = None      # e.g., "scratch", "slam", "pluck", "whoosh"
    sfx_time_offset: float = 0.0


@dataclass
class AudioNarrationTimeline:
    title: str
    total_duration_sec: float
    beats: List[NarrationBeat] = field(default_factory=list)
    words: List[WordBoundary] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def get_beat_at(self, t: float) -> Optional[NarrationBeat]:
        for b in self.beats:
            if b.start_sec <= t <= b.end_sec:
                return b
        return self.beats[-1] if self.beats and t >= self.beats[-1].end_sec else None

    def get_all_impact_events(self) -> List[ImpactWordEvent]:
        events = []
        for b in self.beats:
            if b.impact_phrase:
                events.append(b.impact_phrase)
        return events
