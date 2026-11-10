"""
Production Video Duration & Storytelling Structure Standards.

All production-ready YouTube Shorts must strictly comply with:
- Resolution: 1080x1920 (9:16 vertical)
- Frame rate: Constant 30.0 FPS
- Duration: 50.0 to 60.0 seconds (Default target: 55.0 seconds)
- Frame count: 1,500 to 1,800 frames (Default target: 1,650 frames)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Mandatory Production Duration Standards
PRODUCTION_TARGET_DURATION_SEC: float = 55.0
PRODUCTION_MIN_DURATION_SEC: float = 50.0
PRODUCTION_MAX_DURATION_SEC: float = 60.0

PRODUCTION_TARGET_FPS: float = 30.0
PRODUCTION_MIN_FRAMES: int = 1500      # 50.0s * 30 FPS
PRODUCTION_TARGET_FRAMES: int = 1650   # 55.0s * 30 FPS
PRODUCTION_MAX_FRAMES: int = 1800      # 60.0s * 30 FPS

PRODUCTION_WIDTH: int = 1080
PRODUCTION_HEIGHT: int = 1920
PRODUCTION_ASPECT_RATIO: str = "9:16"

# Pacing standards for narration (conversational speaking rate)
WORDS_PER_SECOND_AVG: float = 2.3
TARGET_SCRIPT_WORD_COUNT_MIN: int = 115   # 50.0s * 2.3 wps
TARGET_SCRIPT_WORD_COUNT_OPT: int = 126   # 55.0s * 2.3 wps
TARGET_SCRIPT_WORD_COUNT_MAX: int = 138   # 60.0s * 2.3 wps


@dataclass(frozen=True)
class StorytellingStage:
    name: str
    target_start_sec: float
    target_end_sec: float
    description: str
    target_words: int
    visual_focus: str


# 5-Stage Storytelling Architecture Guidelines
STORYTELLING_STAGES: Dict[str, StorytellingStage] = {
    "hook": StorytellingStage(
        name="Hook",
        target_start_sec=0.0,
        target_end_sec=3.0,
        description="High-stakes question, curiosity gap, or jaw-dropping visual paradox. Grabs attention within 2 seconds.",
        target_words=7,
        visual_focus="High-energy slam, bold metric, or striking paradox with zero intro fluff.",
    ),
    "context": StorytellingStage(
        name="Context",
        target_start_sec=3.0,
        target_end_sec=12.0,
        description="Frames the premise, establishes stakes, and raises the central conflict.",
        target_words=21,
        visual_focus="Setting the scene, establishing character dilemma, introducing baseline data.",
    ),
    "main_content": StorytellingStage(
        name="Main Content",
        target_start_sec=12.0,
        target_end_sec=40.0,
        description="Core progressive explanation, multi-part evidence, mechanics, or data milestones.",
        target_words=64,
        visual_focus="Sequential visual beats (e.g. 3-4 progressive scenes) evolving every 6-8 seconds to maintain engagement.",
    ),
    "payoff": StorytellingStage(
        name="Payoff",
        target_start_sec=40.0,
        target_end_sec=51.0,
        description="The breakthrough climax, unexpected revelation, or definitive insight that resolves the hook.",
        target_words=25,
        visual_focus="Visual punchline, chart peak, formula resolution, or transformative graphic.",
    ),
    "closing": StorytellingStage(
        name="Closing",
        target_start_sec=51.0,
        target_end_sec=55.0,
        description="Fast, natural takeaway and call to action that loops back to the premise.",
        target_words=9,
        visual_focus="Memorable summary badge and subscription CTA; clean loop out.",
    ),
}


def validate_production_duration(
    duration_sec: float,
    num_frames: Optional[int] = None,
    allow_custom_duration: bool = False,
    tolerance_sec: float = 0.5,
) -> Tuple[bool, List[str]]:
    """
    Validates whether a video adheres to the strict 50-60 second YouTube Shorts production standard.

    Returns:
        (is_valid: bool, violations: List[str])
    """
    if allow_custom_duration:
        return True, []

    violations = []

    # Duration check with small tolerance for container rounding
    if duration_sec < (PRODUCTION_MIN_DURATION_SEC - tolerance_sec):
        violations.append(
            f"Video duration ({duration_sec:.2f}s) is below the minimum production standard of {PRODUCTION_MIN_DURATION_SEC:.1f}s."
        )
    elif duration_sec > (PRODUCTION_MAX_DURATION_SEC + tolerance_sec):
        violations.append(
            f"Video duration ({duration_sec:.2f}s) exceeds the maximum production standard of {PRODUCTION_MAX_DURATION_SEC:.1f}s."
        )

    # Frame count check if provided
    if num_frames is not None:
        min_f = int((PRODUCTION_MIN_DURATION_SEC - tolerance_sec) * PRODUCTION_TARGET_FPS)
        max_f = int((PRODUCTION_MAX_DURATION_SEC + tolerance_sec) * PRODUCTION_TARGET_FPS)
        if num_frames < min_f or num_frames > max_f:
            violations.append(
                f"Video frame count ({num_frames} frames) is outside the expected 50-60s range ({min_f}-{max_f} frames @ 30 FPS)."
            )

    return len(violations) == 0, violations


def get_storytelling_stage_for_time(t: float) -> str:
    """Returns the storytelling stage name for a given timestamp in seconds."""
    if t < 3.0:
        return "hook"
    elif t < 12.0:
        return "context"
    elif t < 40.0:
        return "main_content"
    elif t < 51.0:
        return "payoff"
    else:
        return "closing"
