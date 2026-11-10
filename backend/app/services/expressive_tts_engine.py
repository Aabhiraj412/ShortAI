"""
Expressive Documentary Narration Engine for CrazzyDocs.

Applies sentence-level emotional modulation to text-to-speech synthesis:
- Delivery styles: Curious, Intrigued, Excited, Surprised, Suspenseful, Dramatic, Serious, Emphatic.
- Modulates speaking rate (-15% to +12%), pitch variation (-10Hz to +10Hz), and volume.
- Safe silence padding via FFmpeg adelay and apad filters (avoids zero-length anullsrc bugs).
- Per-segment timeout (18s), exponential backoff retries (up to 2 retries), and detailed instrumentation.
- Resumes incomplete batches without restarting already verified segments.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple

import edge_tts

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
FFMPEG_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")

DEFAULT_VOICE = "en-US-ChristopherNeural"


@dataclass
class NarrationSegment:
    id: str
    emotion: str            # "curious", "suspenseful", "dramatic", "shocked", "serious", "emphatic"
    text: str
    rate: str = "+0%"       # e.g. "-10%" for suspense, "+8%" for excitement
    pitch: str = "+0Hz"     # e.g. "-6Hz" for gravity, "+8Hz" for curiosity
    volume: str = "+0%"
    pre_pause_ms: int = 0
    post_pause_ms: int = 150
    delivery_note: str = ""
    audio_path: Optional[str] = None
    duration_sec: float = 0.0
    file_size_bytes: int = 0
    synthesis_time_sec: float = 0.0


class ExpressiveTTSEngine:
    """Manages emotionally nuanced, segment-by-segment documentary voiceover generation."""

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        ffmpeg_path: str = FFMPEG_PATH,
        ffprobe_path: str = FFPROBE_PATH,
        request_timeout_sec: float = 18.0,
        max_retries: int = 2,
    ):
        self.voice = voice
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.request_timeout_sec = request_timeout_sec
        self.max_retries = max_retries

    def probe_duration(self, audio_path: str) -> float:
        """Probes exact audio file duration using ffprobe."""
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            return 0.0
        cmd = [
            self.ffprobe_path, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return float(res.stdout.strip())
        except Exception as e:
            logger.warning("ffprobe failed on %s: %s", audio_path, e)
            return 0.0

    def _apply_safe_padding(
        self,
        input_mp3: str,
        output_mp3: str,
        pre_ms: int,
        post_ms: int,
        timeout: float = 10.0,
    ) -> str:
        """
        Applies pre-delay and post-padding using single-input FFmpeg audio filters.
        Avoids multi-input concat and zero-length anullsrc stream bugs entirely.
        """
        filters = []
        if pre_ms > 10:
            filters.append(f"adelay={pre_ms}|{pre_ms}")
        if post_ms > 10:
            pad_dur = post_ms / 1000.0
            filters.append(f"apad=pad_dur={pad_dur:.3f}")

        if not filters:
            return input_mp3

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", input_mp3,
            "-af", ",".join(filters),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_mp3
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if res.returncode == 0 and os.path.exists(output_mp3) and os.path.getsize(output_mp3) > 1000:
                return output_mp3
            else:
                logger.warning("FFmpeg padding failed (%s); keeping unpadded: %s", res.stderr[:100], input_mp3)
                return input_mp3
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg padding timed out for %s; using unpadded", input_mp3)
            return input_mp3

    async def synthesize_segment(
        self,
        seg: NarrationSegment,
        out_dir: str,
        force_regenerate: bool = False,
    ) -> NarrationSegment:
        """
        Synthesizes an individual segment with timeouts, retries, and detailed telemetry.
        Resumes automatically if verified output already exists.
        """
        os.makedirs(out_dir, exist_ok=True)
        raw_mp3 = os.path.join(out_dir, f"{seg.id}_raw.mp3")
        final_mp3 = os.path.join(out_dir, f"{seg.id}.mp3")

        # Cache check / Resume logic
        if not force_regenerate and os.path.exists(final_mp3) and os.path.getsize(final_mp3) > 1000:
            dur = self.probe_duration(final_mp3)
            if dur >= 1.0:
                seg.audio_path = final_mp3
                seg.duration_sec = dur
                seg.file_size_bytes = os.path.getsize(final_mp3)
                logger.info(
                    "[RESUME] Segment %s already verified (%.2fs, %d bytes) — skipping synthesis",
                    seg.id, dur, seg.file_size_bytes
                )
                return seg

        t_start = time.perf_counter()
        logger.info(
            "[TTS START] %s | emotion=%s | rate=%s | pitch=%s | text='%s...'",
            seg.id, seg.emotion, seg.rate, seg.pitch, seg.text[:35]
        )

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                communicate = edge_tts.Communicate(
                    text=seg.text,
                    voice=self.voice,
                    rate=seg.rate,
                    pitch=seg.pitch,
                    volume=seg.volume,
                )
                # Apply per-request timeout
                await asyncio.wait_for(
                    communicate.save(raw_mp3),
                    timeout=self.request_timeout_sec
                )

                if os.path.exists(raw_mp3) and os.path.getsize(raw_mp3) > 1000:
                    break
                else:
                    raise RuntimeError("Output MP3 was empty or not written")

            except Exception as e:
                last_error = e
                logger.warning(
                    "[TTS RETRY %d/%d] %s failed: %s",
                    attempt + 1, self.max_retries, seg.id, e
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    logger.error("[TTS FATAL] %s failed after %d retries: %s", seg.id, self.max_retries, e)
                    raise RuntimeError(f"TTS synthesis failed for {seg.id}: {e}")

        # Apply safe padding (using adelay/apad filters)
        padded_path = self._apply_safe_padding(
            raw_mp3,
            final_mp3,
            pre_ms=seg.pre_pause_ms,
            post_ms=seg.post_pause_ms
        )
        if padded_path != final_mp3:
            # If no padding needed, copy or rename raw to final
            if os.path.exists(raw_mp3):
                if os.path.exists(final_mp3):
                    os.remove(final_mp3)
                os.rename(raw_mp3, final_mp3)

        # Validate final output
        seg.audio_path = final_mp3
        seg.duration_sec = self.probe_duration(final_mp3)
        seg.file_size_bytes = os.path.getsize(final_mp3)
        seg.synthesis_time_sec = time.perf_counter() - t_start

        logger.info(
            "[TTS COMPLETE] %s in %.2fs | duration=%.2fs | size=%d bytes | emotion=%s",
            seg.id, seg.synthesis_time_sec, seg.duration_sec, seg.file_size_bytes, seg.emotion
        )
        return seg

    def concatenate_segments(self, segments: List[NarrationSegment], output_path: str) -> float:
        """Stitches all segments into a unified voiceover track."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        concat_txt = os.path.join(os.path.dirname(output_path), "_tts_concat_list.txt")

        with open(concat_txt, "w", encoding="utf-8") as f:
            for s in segments:
                norm_p = os.path.abspath(s.audio_path).replace("\\", "/")
                f.write(f"file '{norm_p}'\n")

        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_txt,
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-ar", "48000",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode != 0:
            raise RuntimeError(f"TTS segment concatenation failed: {res.stderr}")

        total_dur = self.probe_duration(output_path)
        logger.info("Assembled complete expressive narration track: %.2fs -> %s", total_dur, output_path)
        return total_dur

    async def test_expressive_delivery_samples(self, out_dir: str) -> Dict[str, Dict]:
        """
        Generates three short test samples with distinct emotional parameters:
        A. Neutral (+0%, +0Hz)
        B. Excited (+12%, +8Hz)
        C. Suspenseful (-14%, -8Hz)
        Measures duration and demonstrates audible variation.
        """
        test_text = "At that exact second, the window blew apart in blinding white fire."
        samples = [
            NarrationSegment("sample_neutral", "neutral", test_text, rate="+0%", pitch="+0Hz", post_pause_ms=0),
            NarrationSegment("sample_excited", "excited", test_text, rate="+12%", pitch="+8Hz", post_pause_ms=0),
            NarrationSegment("sample_suspenseful", "suspenseful", test_text, rate="-14%", pitch="-8Hz", post_pause_ms=0),
        ]
        results = {}
        for s in samples:
            res = await self.synthesize_segment(s, out_dir, force_regenerate=True)
            results[s.id] = {
                "emotion": s.emotion,
                "rate": s.rate,
                "pitch": s.pitch,
                "duration_sec": s.duration_sec,
                "file_size": s.file_size_bytes,
                "path": s.audio_path,
            }
        return results


# Global singleton
expressive_tts_engine = ExpressiveTTSEngine()
