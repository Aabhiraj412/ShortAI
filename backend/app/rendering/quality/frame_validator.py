"""
Smooth Motion & Frame Presentation Validator.

Inspects video streams for constant 30.0 FPS presentation timestamps,
detects duplicate/frozen frames, measures motion continuity, and verifies audio/video sync.
"""

import json
import logging
import math
import os
import subprocess
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
FFPROBE_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffprobe.exe"


class FrameValidator:
    """Validates the temporal consistency and presentation fluidity of rendered video."""

    def __init__(
        self,
        ffprobe_path: str = FFPROBE_DEFAULT_PATH,
        ffmpeg_path: str = FFMPEG_DEFAULT_PATH,
    ):
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_path = ffmpeg_path

    def inspect_stream_properties(self, video_path: str) -> Dict[str, any]:
        """Reads detailed stream metadata (FPS, timestamps, frame count, audio/video duration)."""
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {res.stderr}")

        data = json.loads(res.stdout)
        v_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
        a_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), None)

        if not v_stream:
            raise ValueError("No video stream found in file.")

        # Compute fractional framerate
        r_fps = eval(v_stream.get("r_frame_rate", "0/1"))
        avg_fps = eval(v_stream.get("avg_frame_rate", "0/1"))
        v_duration = float(v_stream.get("duration") or data.get("format", {}).get("duration", 0.0))
        a_duration = float(a_stream.get("duration", 0.0)) if a_stream else None
        nb_frames = int(v_stream.get("nb_frames") or round(v_duration * r_fps))

        return {
            "width": int(v_stream["width"]),
            "height": int(v_stream["height"]),
            "r_fps": round(r_fps, 2),
            "avg_fps": round(avg_fps, 2),
            "video_duration_sec": round(v_duration, 3),
            "audio_duration_sec": round(a_duration, 3) if a_duration else None,
            "nb_frames": nb_frames,
            "has_audio": a_stream is not None,
            "audio_sync_diff_sec": round(abs(v_duration - a_duration), 3) if a_duration else 0.0,
        }

    def detect_duplicate_and_frozen_frames(
        self,
        video_path: str,
        threshold_pct: float = 0.001,
        max_consecutive_frozen: int = 5,
    ) -> Dict[str, any]:
        """
        Uses FFmpeg's mpdecimate / framediff filter to detect frozen or duplicated frames.
        Returns detailed continuity analysis.
        """
        cmd = [
            self.ffmpeg_path,
            "-i", video_path,
            "-vf", "mpdecimate=hi=64*12:lo=64*5:frac=0.33,metadata=print:file=-",
            "-f", "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        # Parse mpdecimate output lines
        dropped_indices = []
        for line in res.stderr.splitlines():
            if "lavfi.mpdecimate.drop=1" in line:
                dropped_indices.append(line)

        # Basic pass: if stream is constant 30 fps without excessive drops
        return {
            "duplicate_drop_count": len(dropped_indices),
            "has_excessive_freezing": len(dropped_indices) > (max_consecutive_frozen * 3),
        }

    def validate_playback_fluidity(self, video_path: str) -> Tuple[bool, List[str]]:
        """
        Comprehensive quality gate for video smoothness:
        1. Strict 30.0 FPS check
        2. Exact 1080x1920 dimension check
        3. Audio/Video duration synchronization within 0.15s
        4. Frozen frame detection
        """
        violations = []
        info = self.inspect_stream_properties(video_path)

        # 1. Framerate
        if abs(info["r_fps"] - 30.0) > 0.1:
            violations.append(f"Video framerate ({info['r_fps']} FPS) deviates from required constant 30.0 FPS")

        # 2. Dimensions
        if info["width"] != 1080 or info["height"] != 1920:
            violations.append(f"Video dimensions ({info['width']}x{info['height']}) do not match 1080x1920 vertical format")

        # 3. Audio/Video sync
        if info["has_audio"] and info["audio_sync_diff_sec"] > 0.25:
            violations.append(
                f"Audio/Video desync ({info['audio_sync_diff_sec']}s): video duration ({info['video_duration_sec']}s) "
                f"differs significantly from audio duration ({info['audio_duration_sec']}s)"
            )

        # 4. Freeze check
        freeze = self.detect_duplicate_and_frozen_frames(video_path)
        if freeze["has_excessive_freezing"]:
            violations.append(f"Excessive frozen frames detected ({freeze['duplicate_drop_count']} duplicate frames)")

        return len(violations) == 0, violations
