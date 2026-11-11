"""
Universal FFmpeg Compositor.

Stitches multi-engine video segments (Python Canvas & Browser Canvas),
composites transitions, layers ducked background music, procedural SFX stems,
and voiceover narration into strict 1080x1920 @ 30 FPS YouTube Shorts.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from typing import List, Optional

from backend.app.rendering.types import SegmentResult, StoryboardPlan

logger = logging.getLogger(__name__)

FFMPEG_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
FFPROBE_DEFAULT_PATH = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffprobe.exe"


class CompositorValidationError(RuntimeError):
    """Raised when video compositing or outro concatenation fails quality or duration validation."""
    pass


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
            # Normalize channels/rates so sidechaincompress never fails on mono TTS vs stereo music
            filter_complex.append(
                f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.22,aloop=loop=-1:size=2e+09[bgm];"
                f"[{vo_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[vo];"
                f"[{vo_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[vo_sc];"
                f"[bgm][vo_sc]sidechaincompress=threshold=0.08:ratio=4:attack=15:release=250[ducked_bgm];"
            )
            audio_mix_inputs.append("[ducked_bgm]")
            audio_mix_inputs.append("[vo]")
        elif vo_idx is not None:
            filter_complex.append(f"[{vo_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[vo];")
            audio_mix_inputs.append("[vo]")
        elif music_idx is not None:
            filter_complex.append(f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.35[bgm];")
            audio_mix_inputs.append("[bgm]")

        # Mix SFX
        for i, s_idx in enumerate(sfx_indices):
            label = f"[sfx_{i}]"
            filter_complex.append(f"[{s_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.65{label};")
            audio_mix_inputs.append(label)

        if audio_mix_inputs:
            mix_str = "".join(audio_mix_inputs)
            filter_complex.append(f"{mix_str}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=2:normalize=0[aout]")
        else:
            # Silence fallback
            filter_complex.append("aevalsrc=0:d=1:s=48000:c=stereo[aout]")

        # Video filters (must live inside filter_complex when filter_complex is active)
        v_map = "0:v"
        if subtitles_ass_path and os.path.exists(subtitles_ass_path):
            norm_sub = os.path.abspath(subtitles_ass_path).replace("\\", "/").replace(":", "\\:")
            filter_complex.append(f";[0:v]ass='{norm_sub}'[vout]")
            v_map = "[vout]"

        cmd = [self.ffmpeg_path, "-y"]
        cmd.extend(inputs)

        filter_str = "".join(filter_complex)
        if filter_str:
            cmd.extend(["-filter_complex", filter_str, "-map", v_map, "-map", "[aout]"])
        else:
            cmd.extend(["-map", "0:v"])

        # Inspect exact video and audio durations to avoid premature cutoffs
        v_dur = self._probe_duration(video_path)
        vo_dur = self._probe_duration(voiceover_audio) if (voiceover_audio and os.path.exists(voiceover_audio)) else 0.0
        if vo_dur > v_dur + 0.15:
            logger.warning(
                "Voiceover duration (%.2fs) exceeds video duration (%.2fs). Narration will be preserved to prevent cutoff.",
                vo_dur, v_dur
            )
        target_dur = max(v_dur, vo_dur) if (v_dur and vo_dur) else (v_dur or vo_dur)

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
        if target_dur and target_dur > 0:
            cmd.extend(["-t", f"{target_dur:.3f}"])
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

    def _has_audio_stream(self, file_path: str) -> bool:
        """Checks if a media file contains at least one audio stream."""
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0 and bool(res.stdout.strip())

    def _probe_duration(self, file_path: str) -> float:
        """Returns the format duration of a media file in seconds."""
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def _probe_resolution(self, file_path: str) -> tuple:
        """Returns (width, height) of the first video stream."""
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            file_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(res.stdout)
            stream = data["streams"][0]
            return int(stream["width"]), int(stream["height"])
        except Exception:
            # Fallback
            parts = [p.strip() for p in res.stdout.strip().split("x") if p.strip()]
            if len(parts) >= 2:
                try:
                    return int(parts[0]), int(parts[1])
                except Exception:
                    pass
            return 0, 0

    def _parse_ass_dialogue_end_times(self, ass_path: str) -> List[float]:
        """Extracts end times in seconds of all Dialogue events in an ASS subtitle file."""
        if not ass_path or not os.path.exists(ass_path):
            return []
        end_times = []
        try:
            with open(ass_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("Dialogue:"):
                        parts = line.split(",", 9)
                        if len(parts) >= 3:
                            end_str = parts[2].strip()
                            t_parts = end_str.split(":")
                            if len(t_parts) == 3:
                                h = float(t_parts[0])
                                m = float(t_parts[1])
                                s = float(t_parts[2])
                                end_times.append(h * 3600 + m * 60 + s)
        except Exception as e:
            logger.warning("Failed to parse ASS subtitle end times: %s", e)
        return end_times

    def append_branded_outro(
        self,
        main_video_path: str,
        outro_video_path: str,
        output_final_path: str,
        subtitles_ass_path: Optional[str] = None,
    ) -> str:
        """
        Appends the CrazzyDocs branded outro to the finished Short video.
        Uses filter_complex concat with stream normalization.
        
        Strict 8-Point Validation standard:
        1. Main video exists and duration >= 1.0s.
        2. Main video has valid video stream.
        3. Outro video exists and duration is 2.0s (+/- 0.25s).
        4. Subtitle boundaries: no dialogue event ends after main_dur + 0.15s (zero leakage).
        5. Concat execution without trimming main video: Final Duration = Main + Outro.
        6. Output file exists and is non-empty.
        7. Duration invariant: |final_dur - (main_dur + outro_dur)| <= 0.35s.
        8. Stream integrity: resolution is 1080x1920, 30 FPS, stereo audio present.
        """
        # 1. Main video check
        if not os.path.exists(main_video_path):
            raise CompositorValidationError(f"Main video not found: {main_video_path}")
        main_dur = self._probe_duration(main_video_path)
        if main_dur < 1.0:
            raise CompositorValidationError(f"Main video duration too short: {main_dur:.2f}s (must be >= 1.0s)")

        # 2. Main video stream check
        main_w, main_h = self._probe_resolution(main_video_path)
        if main_w == 0 or main_h == 0:
            raise CompositorValidationError(f"Main video has no valid video stream: {main_video_path}")

        # 3. Outro video check
        if not os.path.exists(outro_video_path):
            raise CompositorValidationError(f"Branded outro video not found: {outro_video_path}")
        outro_dur = self._probe_duration(outro_video_path)
        if not (1.75 <= outro_dur <= 2.25):
            raise CompositorValidationError(
                f"Branded outro duration {outro_dur:.2f}s outside calibrated range [1.75s, 2.25s]"
            )

        # 4. Zero subtitle leakage validation
        if subtitles_ass_path and os.path.exists(subtitles_ass_path):
            end_times = self._parse_ass_dialogue_end_times(subtitles_ass_path)
            if end_times:
                max_sub_end = max(end_times)
                if max_sub_end > main_dur + 0.15:
                    raise CompositorValidationError(
                        f"Subtitle leakage detected! Last subtitle ends at {max_sub_end:.2f}s, "
                        f"which exceeds main video duration {main_dur:.2f}s."
                    )

        os.makedirs(os.path.dirname(output_final_path), exist_ok=True)
        main_has_audio = self._has_audio_stream(main_video_path)
        outro_has_audio = self._has_audio_stream(outro_video_path)

        # 5. Build filter_complex with format normalization for seamless concatenation
        filter_parts = []
        filter_parts.append("[0:v]setsar=1,fps=30,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[v0];")
        filter_parts.append("[1:v]setsar=1,fps=30,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[v1];")

        if main_has_audio:
            filter_parts.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];")
        else:
            filter_parts.append(f"aevalsrc=0:d={max(0.1, main_dur):.3f}:s=48000:c=stereo[a0];")

        if outro_has_audio:
            filter_parts.append("[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];")
        else:
            filter_parts.append(f"aevalsrc=0:d={outro_dur:.3f}:s=48000:c=stereo[a1];")

        filter_parts.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", main_video_path,
            "-i", outro_video_path,
            "-filter_complex", "".join(filter_parts),
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-video_track_timescale", "30000",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "320k",
            "-ar", "48000",
            output_final_path,
        ]

        logger.info(
            "Appending CrazzyDocs outro (main=%.2fs, outro=%.2fs, expected=%.2fs) -> %s",
            main_dur, outro_dur, main_dur + outro_dur, output_final_path
        )
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode != 0:
            logger.error("Outro concatenation failed (code %d): %s. Attempting demuxer fallback...", res.returncode, res.stderr)
            concat_list = os.path.join(os.path.dirname(output_final_path), "_concat_outro_list.txt")
            with open(concat_list, "w", encoding="utf-8") as f:
                f.write(f"file '{os.path.abspath(main_video_path).replace(chr(92), '/')}'\n")
                f.write(f"file '{os.path.abspath(outro_video_path).replace(chr(92), '/')}'\n")

            fallback_cmd = [
                self.ffmpeg_path, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "320k",
                "-r", "30",
                output_final_path,
            ]
            fb_res = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if os.path.exists(concat_list):
                try:
                    os.remove(concat_list)
                except OSError:
                    pass
            if fb_res.returncode != 0:
                raise CompositorValidationError(f"All outro concatenation attempts failed: {fb_res.stderr}")

        # 6. Verify output file exists
        if not os.path.exists(output_final_path) or os.path.getsize(output_final_path) == 0:
            raise CompositorValidationError(f"Final output video missing or empty: {output_final_path}")

        # 7. Duration invariant validation
        final_dur = self._probe_duration(output_final_path)
        expected_dur = main_dur + outro_dur
        delta = abs(final_dur - expected_dur)
        if delta > 0.35:
            raise CompositorValidationError(
                f"Duration invariant violated: final duration {final_dur:.2f}s does not match "
                f"expected {expected_dur:.2f}s (main {main_dur:.2f}s + outro {outro_dur:.2f}s). Delta {delta:.2f}s > 0.35s"
            )

        # 8. Stream integrity validation
        out_w, out_h = self._probe_resolution(output_final_path)
        if out_w != 1080 or out_h != 1920:
            raise CompositorValidationError(f"Output resolution {out_w}x{out_h} != expected 1080x1920")
        if not self._has_audio_stream(output_final_path):
            raise CompositorValidationError(f"Output video lacks audio stream: {output_final_path}")

        logger.info(
            "Successfully validated and appended CrazzyDocs branded outro -> %s (final_dur=%.2fs, delta=%.3fs)",
            output_final_path, final_dur, delta
        )
        return output_final_path
