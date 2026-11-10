"""
Hybrid Visual Engine - Core Types and Data Models.

Defines the scene beats, visual modalities, engine specifications,
and rendering result contracts for the AI YouTube Shorts Studio.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EngineType(str, Enum):
    """The underlying rendering engine for a scene."""
    PYTHON_CANVAS = "python_canvas"
    BROWSER_CANVAS = "browser_canvas"


class VisualModality(str, Enum):
    """The 9-element visual modalities supported by the hybrid engine."""
    REAL_PHOTO = "real_photo"
    SPLIT_SCREEN = "split_screen"
    CINEMATIC_STILL = "cinematic_still"
    POP_INFO = "pop_info"
    DATA_STORY = "data_story"
    CHALK_TALK = "chalk_talk"
    CREATOR_MOTION = "creator_motion"
    STICK_FIGURE = "stick_figure"
    MAP_TIMELINE = "map_timeline"


class TransitionType(str, Enum):
    """Transition between scene beats."""
    CUT = "cut"
    CROSSFADE = "crossfade"
    WHIP_RIGHT = "whip_right"
    WHIP_LEFT = "whip_left"
    WHIP_UP = "whip_up"
    WHIP_DOWN = "whip_down"


@dataclass
class SceneBeat:
    """Specification of an individual visual beat within a Short."""
    id: str
    modality: VisualModality
    duration: float  # In seconds
    narration_text: str = ""
    start_time: float = 0.0
    transition_in: TransitionType = TransitionType.CUT
    transition_duration: float = 0.35  # seconds
    params: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def engine(self) -> EngineType:
        """Automatically maps visual modality to the optimal rendering engine."""
        if self.modality in {
            VisualModality.POP_INFO,
            VisualModality.DATA_STORY,
            VisualModality.CHALK_TALK,
            VisualModality.CREATOR_MOTION,
        }:
            return EngineType.BROWSER_CANVAS
        return EngineType.PYTHON_CANVAS


@dataclass
class RenderTelemetry:
    """Performance telemetry captured during a render job."""
    frames_rendered: int = 0
    total_duration_sec: float = 0.0
    elapsed_time_sec: float = 0.0
    fps: float = 0.0
    peak_ram_mb: float = 0.0
    engine: str = ""


@dataclass
class SegmentResult:
    """The output of rendering a single scene beat segment."""
    scene_id: str
    video_path: str
    duration: float
    fps: float
    sfx_audio_path: Optional[str] = None
    telemetry: Optional[RenderTelemetry] = None
    success: bool = True
    error_message: Optional[str] = None


@dataclass
class StoryboardPlan:
    """The complete multi-beat storyboard plan for an entire Short."""
    title: str
    total_duration: float
    scenes: List[SceneBeat]
    voiceover_path: Optional[str] = None
    music_track: Optional[str] = None
    ducking_db: float = -14.0
    subtitles_ass_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
