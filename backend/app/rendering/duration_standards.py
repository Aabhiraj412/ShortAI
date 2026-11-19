"""
Production Video Duration & Storytelling Structure Standards.

All production-ready YouTube Shorts must strictly comply with:
- Resolution: 1080x1920 (9:16 vertical)
- Frame rate: Constant 30.0 FPS
- Duration: 30.0 to 58.0 seconds, both inclusive (Stage 3.29; previously 50-58/60). Default target 55.0 s.
  Measured on the final rendered MP4, never on the planned script.
- Frame count: 900 to 1,740 frames at 30 FPS (default target 1,650 frames)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Mandatory Production Duration Standards
PRODUCTION_TARGET_DURATION_SEC: float = 55.0
PRODUCTION_MIN_DURATION_SEC: float = 30.0   # inclusive
PRODUCTION_MAX_DURATION_SEC: float = 58.0   # inclusive
ACCEPTED_DURATION_RANGE_SEC = (PRODUCTION_MIN_DURATION_SEC, PRODUCTION_MAX_DURATION_SEC)

PRODUCTION_TARGET_FPS: float = 30.0
PRODUCTION_MIN_FRAMES: int = 900       # 30.0s * 30 FPS
PRODUCTION_TARGET_FRAMES: int = 1650   # 55.0s * 30 FPS
PRODUCTION_MAX_FRAMES: int = 1740      # 58.0s * 30 FPS

PRODUCTION_WIDTH: int = 1080
PRODUCTION_HEIGHT: int = 1920
PRODUCTION_ASPECT_RATIO: str = "9:16"

# Pacing standards for narration (conversational speaking rate)
WORDS_PER_SECOND_AVG: float = 2.3
TARGET_SCRIPT_WORD_COUNT_MIN: int = 69    # 30.0s * 2.3 wps (acceptance floor)
TARGET_SCRIPT_WORD_COUNT_OPT: int = 126   # 55.0s * 2.3 wps (generation target, unchanged)
TARGET_SCRIPT_WORD_COUNT_MAX: int = 133   # 58.0s * 2.3 wps


def duration_accepted(duration_sec: float) -> bool:
    """Inclusive acceptance window for a final video: 30.0 <= d <= 58.0 (millisecond rounding only)."""
    d = round(float(duration_sec), 3)
    return PRODUCTION_MIN_DURATION_SEC <= d <= PRODUCTION_MAX_DURATION_SEC


def duration_violation(duration_sec: float) -> Optional[str]:
    """Human-readable reason when a measured duration is outside the window, else None."""
    if duration_accepted(duration_sec):
        return None
    lo, hi = ACCEPTED_DURATION_RANGE_SEC
    side = "below the minimum" if float(duration_sec) < lo else "above the maximum"
    return (f"Duration {float(duration_sec):.2f}s is {side} of the accepted {lo:.0f}-{hi:.0f}s window "
            f"(both inclusive, measured on the final MP4).")


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
    tolerance_sec: float = 0.0,
) -> Tuple[bool, List[str]]:
    """
    Validates whether a video adheres to the 30-58 second (inclusive) YouTube Shorts production standard.
    The boundaries are exact: 29.9 s and 58.1 s are rejected (tolerance defaults to 0).

    Returns:
        (is_valid: bool, violations: List[str])
    """
    if allow_custom_duration:
        return True, []

    violations = []

    # Duration check with small tolerance for container rounding
    if round(duration_sec, 3) < (PRODUCTION_MIN_DURATION_SEC - tolerance_sec):
        violations.append(
            f"Video duration ({duration_sec:.2f}s) is below the minimum production standard of {PRODUCTION_MIN_DURATION_SEC:.1f}s."
        )
    elif round(duration_sec, 3) > (PRODUCTION_MAX_DURATION_SEC + tolerance_sec):
        violations.append(
            f"Video duration ({duration_sec:.2f}s) exceeds the maximum production standard of {PRODUCTION_MAX_DURATION_SEC:.1f}s."
        )

    # Frame count check if provided
    if num_frames is not None:
        min_f = int((PRODUCTION_MIN_DURATION_SEC - tolerance_sec) * PRODUCTION_TARGET_FPS)
        max_f = int((PRODUCTION_MAX_DURATION_SEC + tolerance_sec) * PRODUCTION_TARGET_FPS)
        if num_frames < min_f or num_frames > max_f:
            violations.append(
                f"Video frame count ({num_frames} frames) is outside the expected 30-58s range ({min_f}-{max_f} frames @ 30 FPS)."
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
