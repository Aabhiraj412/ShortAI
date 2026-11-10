"""
Tests for Stick-Figure Animation Deprecation & Creative Policy Enforcement.
Phase 5, Stage 3.18 Standard.
"""
import os
import sys
import pytest

BASE_DIR = r"D:\Agents\CrazzyDocs"
sys.path.append(os.path.join(BASE_DIR, "backend"))

from app.services.stick_animation_service import (
    render_stick_animation,
    BASE_DIR,
    MEDIA_VIDEO_DIR,
    MUSIC_DIR,
)

STICK_STORYBOARD = {
    "title": "What Happens When You Fall Into a Black Hole?",
    "duration": 5.0,
    "scenes": [
        {
            "id": 1,
            "start_time": 0.0,
            "duration": 5.0,
            "headline": "CAN YOU SURVIVE A BLACK HOLE?",
            "badge": "SPACE MYSTERY",
            "pose": "thinking",
            "expression": "thinking",
            "char_x": 540,
            "char_y": 1420,
        }
    ],
}


def test_stick_animation_strictly_blocked_by_policy():
    """Verifies that calling render_stick_animation raises PermissionError under Content Integrity Policy."""
    audio = os.path.join(BASE_DIR, "media", "audio", "sample_narration.mp3")
    ass = os.path.join(BASE_DIR, "media", "subtitles", "sample_narration.ass")
    music = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    out_1s = os.path.join(MEDIA_VIDEO_DIR, "stick_test_blocked.mp4")

    with pytest.raises(PermissionError) as exc_info:
        render_stick_animation(
            storyboard=STICK_STORYBOARD,
            audio_path=audio,
            ass_path=ass,
            music_path=music,
            output_mp4_path=out_1s,
            max_frames=1,
            fps=30,
        )

    assert "Creative Policy Violation" in str(exc_info.value)
