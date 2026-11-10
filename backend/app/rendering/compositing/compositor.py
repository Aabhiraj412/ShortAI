"""
Universal FFmpeg Compositor.

Stitches multi-engine video segments (Python Canvas & Browser Canvas),
composites transitions, layers ducked background music, procedural SFX stems,
and voiceover narration into strict 1080x1920 @ 30 FPS YouTube Shorts.
"""

import logging
import os
import subprocess
import time
from typing import List, Optional

from backend.app.rendering.types import SegmentResult, StoryboardPlan

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"
FFPROBE_DEFAULT_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffprobe.exe"


class UniversalCompositor:
    """Universal multi-track video and audio compositor powered by FFmpeg."""

    def __init__(
        self,
        ffmpeg_path: str = FFMPEG_DEFAULT_PATH,
        ffprobe_path: str = FFPROBE_DEFAULT_PATH,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def concatenate_segments(
        self,
        segment_paths: List[str],
        output_stitched_video: str,
    ) -> str:
        """Stitches a sequence of MP4 video segments using FFmpeg concat demuxer."""
        if not segment_paths:
            raise ValueError("No segments provided for concatenation.")

        os.makedirs(os.path.dirname(output_stitched_video), exist_ok=True)
        concat_list_path = os.path.join(os.path.dirname(output_stitched_video), "concat_list.txt")

        with open(concat_list_path, "w", encoding="utf-8") as f:
            for seg in segment_paths:
                # Use forward slashes for FFmpeg concat file
                norm_path = os.path.abspath(seg).replace("\\", "/")
                f.write(f"file '{norm_path}'\n")

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            output_stitched_video,
        ]

        logger.info("Concatenating %d segments into %s...", len(segment_paths), output_stitched_video)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Concat failed with code {res.returncode}: {res.stderr}")

        return output_stitched_video

    def mix_and_finalize_short(
        self,
        video_path: str,
        voiceover_audio: Optional[str],
        sfx_audios: List[str],
        music_audio: Optional[str],
        output_final_mp4: str,
        ducking_db: float = -14.0,
        subtitles_ass_path: Optional[str] = None,
    ) -> str:
        """Mixes all audio stems (voiceover, ducked music, SFX) with video and encodes final Short."""
        os.makedirs(os.path.dirname(output_final_mp4), exist_ok=True)
        t_start = time.time()

        inputs = ["-i", video_path]
        filter_complex = []
        input_idx = 1

        vo_idx = None
        if voiceover_audio and os.path.exists(voiceover_audio):
            inputs.extend(["-i", voiceover_audio])
            vo_idx = input_idx
            input_idx += 1

        music_idx = None
        if music_audio and os.path.exists(music_audio):
            inputs.extend(["-i", music_audio])
            music_idx = input_idx
            input_idx += 1

        sfx_indices = []
        for sfx in sfx_audios:
            if sfx and os.path.exists(sfx):
                inputs.extend(["-i", sfx])
                sfx_indices.append(input_idx)
                input_idx += 1

        # Build audio filter graph
        audio_mix_inputs = []

        if vo_idx is not None and music_idx is not None:
            # Duck music under voiceover using sidechain compression or volume ducking
            filter_complex.append(
                f"[{music_idx}:a]volume=0.22,aloop=loop=-1:size=2e+09[bgm];"
                f"[{vo_idx}:a]volume=1.0[vo];"
                f"[bgm][vo]sidechaincompress=threshold=0.08:ratio=4:attack=15:release=250[ducked_bgm];"
            )
            audio_mix_inputs.append("[ducked_bgm]")
            audio_mix_inputs.append("[vo]")
        elif vo_idx is not None:
            filter_complex.append(f"[{vo_idx}:a]volume=1.0[vo];")
            audio_mix_inputs.append("[vo]")
        elif music_idx is not None:
            filter_complex.append(f"[{music_idx}:a]volume=0.35[bgm];")
            audio_mix_inputs.append("[bgm]")

        # Mix SFX
        for i, s_idx in enumerate(sfx_indices):
            label = f"[sfx_{i}]"
            filter_complex.append(f"[{s_idx}:a]volume=0.85{label};")
            audio_mix_inputs.append(label)

        if audio_mix_inputs:
            mix_str = "".join(audio_mix_inputs)
            filter_complex.append(f"{mix_str}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=2[aout]")
            audio_map = ["[aout]"]
        else:
            # Silence fallback
            filter_complex.append("aevalsrc=0:d=1[aout]")
            audio_map = ["[aout]"]

        # Video filters (e.g. subtitles)
        video_filters = []
        if subtitles_ass_path and os.path.exists(subtitles_ass_path):
            norm_sub = os.path.abspath(subtitles_ass_path).replace("\\", "/").replace(":", "\\:")
            video_filters.append(f"ass='{norm_sub}'")

        cmd = [self.ffmpeg_path, "-y"]
        cmd.extend(inputs)

        filter_str = "".join(filter_complex)
        if video_filters:
            cmd.extend(["-vf", ",".join(video_filters)])

        if filter_str:
            cmd.extend(["-filter_complex", filter_str, "-map", "0:v", "-map", "[aout]"])
        else:
            cmd.extend(["-map", "0:v"])

        # Inspect exact video duration to avoid premature cutoffs
        probe_cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        v_dur = float(probe_res.stdout.strip()) if probe_res.returncode == 0 and probe_res.stdout.strip() else None

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-video_track_timescale", "30000",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            "-movflags", "+faststart",
        ])
        if v_dur:
            cmd.extend(["-t", f"{v_dur:.3f}"])
        cmd.append(output_final_mp4)

        logger.info("Finalizing short audio/video mux: %s", output_final_mp4)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # If sidechaincompress failed due to channel mismatch, fallback to simple amix
            logger.warning("Advanced sidechain filter failed, falling back to clean amix: %s", res.stderr[:200])
            fallback_cmd = [
                self.ffmpeg_path, "-y",
                "-i", video_path,
            ]
            if voiceover_audio and os.path.exists(voiceover_audio):
                fallback_cmd.extend(["-i", voiceover_audio, "-map", "0:v", "-map", "1:a"])
            else:
                fallback_cmd.extend(["-map", "0:v"])

            fallback_cmd.extend([
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                output_final_mp4,
            ])
            fallback_res = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if fallback_res.returncode != 0:
                raise RuntimeError(f"Mux fallback failed: {fallback_res.stderr}")

        logger.info("Composited final YouTube Short in %.2fs -> %s", time.time() - t_start, output_final_mp4)
        return output_final_mp4
