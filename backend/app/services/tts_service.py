"""
Text-to-Speech (TTS) and Subtitle Generation Service for YouTube Shorts Studio.
Supports Edge-TTS with expressive emotion modulation and automatic millisecond-accurate ASS subtitles.

Supported Emotion Delivery Tags:
- [Neutral]    : Balanced, natural conversational baseline.
- [Curious]    : Inquisitive pitch lift (+5Hz), slightly brisk rate (+4%).
- [Surprised]  : Quickened tempo (+8%), elevated pitch (+7Hz), high presence (+10% vol).
- [Excited]    : Dynamic energy (+10% rate, +5Hz pitch, +12% vol).
- [Suspenseful]: Deliberate deceleration (-6% rate, -2Hz pitch, -8% vol).
- [Serious]    : Grounded, authoritative weight (-4% rate, -3Hz pitch).
- [Dramatic]   : Deep resonant impact (-8% rate, -5Hz pitch, +8% vol).
- [Reassuring] : Warm, steady cadence (-2% rate, -1Hz pitch, -4% vol).
- [Emphatic]   : Forceful punch (+2% rate, +3Hz pitch, +15% vol).

Guarantees:
- Raw emotion tags are NEVER spoken in audio and NEVER shown in subtitles.
- Smooth natural transitions between emotional sentences.
- Clamped subtitle timing preventing leakage past narrative duration.
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple
import edge_tts

from backend.app.content.editorial_text import EditorialTextCue, EditorialTextEngine, EditorialTextPlan
from backend.app.rendering.styles.style_system import VisualStyle
from backend.app.rendering.subtitles.editorial_subtitles import render_editorial_ass_file

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-ChristopherNeural"
OUTPUT_AUDIO_DIR = r"D:\Agents\CrazzyDocs\media\audio"
OUTPUT_SUB_DIR = r"D:\Agents\CrazzyDocs\media\subtitles"
ACCESSIBILITY_SUB_DIR = os.path.join(OUTPUT_SUB_DIR, "accessibility")
EDITORIAL_SUB_DIR = os.path.join(OUTPUT_SUB_DIR, "editorial")
TRANSCRIPT_SUB_DIR = os.path.join(OUTPUT_SUB_DIR, "transcripts")
FFMPEG_EXE = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffmpeg.exe"
FFPROBE_EXE = r"D:\Agents\CrazzyDocs\tools\ffmpeg\bin\ffprobe.exe"

os.makedirs(OUTPUT_AUDIO_DIR, exist_ok=True)
os.makedirs(OUTPUT_SUB_DIR, exist_ok=True)
os.makedirs(ACCESSIBILITY_SUB_DIR, exist_ok=True)
os.makedirs(EDITORIAL_SUB_DIR, exist_ok=True)
os.makedirs(TRANSCRIPT_SUB_DIR, exist_ok=True)


@dataclass(frozen=True)
class EmotionProfile:
    name: str
    rate: str
    pitch: str
    volume: str
    pause_after_ms: int = 120


EMOTION_PROFILES: Dict[str, EmotionProfile] = {
    "neutral": EmotionProfile(name="Neutral", rate="+0%", pitch="+0Hz", volume="+0%", pause_after_ms=100),
    "curious": EmotionProfile(name="Curious", rate="+4%", pitch="+5Hz", volume="+0%", pause_after_ms=120),
    "surprised": EmotionProfile(name="Surprised", rate="+8%", pitch="+7Hz", volume="+10%", pause_after_ms=140),
    "excited": EmotionProfile(name="Excited", rate="+10%", pitch="+5Hz", volume="+12%", pause_after_ms=120),
    "suspenseful": EmotionProfile(name="Suspenseful", rate="-6%", pitch="-2Hz", volume="-8%", pause_after_ms=200),
    "serious": EmotionProfile(name="Serious", rate="-4%", pitch="-3Hz", volume="+0%", pause_after_ms=160),
    "dramatic": EmotionProfile(name="Dramatic", rate="-8%", pitch="-5Hz", volume="+8%", pause_after_ms=220),
    "reassuring": EmotionProfile(name="Reassuring", rate="-2%", pitch="-1Hz", volume="-4%", pause_after_ms=120),
    "emphatic": EmotionProfile(name="Emphatic", rate="+2%", pitch="+3Hz", volume="+15%", pause_after_ms=150),
}

EMOTION_TAG_PATTERN = re.compile(
    r"\[(Neutral|Curious|Surprised|Excited|Suspenseful|Serious|Dramatic|Reassuring|Emphatic)\]",
    re.IGNORECASE,
)


@dataclass
class NarrationSegment:
    emotion: str
    text: str
    profile: EmotionProfile


def parse_emotion_segments(raw_text: str) -> List[NarrationSegment]:
    """
    Parses a script containing bracketed emotion markers into structured segments.
    If no markers exist, defaults to a single Neutral segment.
    Strips raw tags from the segment spoken text.
    """
    matches = list(EMOTION_TAG_PATTERN.finditer(raw_text))
    if not matches:
        clean = clean_narration_text(raw_text)
        return [NarrationSegment(emotion="neutral", text=clean, profile=EMOTION_PROFILES["neutral"])] if clean else []

    segments: List[NarrationSegment] = []
    
    # Text before the first emotion tag (if any)
    if matches[0].start() > 0:
        pre_text = clean_narration_text(raw_text[:matches[0].start()])
        if pre_text:
            segments.append(NarrationSegment(emotion="neutral", text=pre_text, profile=EMOTION_PROFILES["neutral"]))

    for i, match in enumerate(matches):
        tag_emotion = match.group(1).lower()
        profile = EMOTION_PROFILES.get(tag_emotion, EMOTION_PROFILES["neutral"])
        
        start_pos = match.end()
        end_pos = matches[i + 1].start() if (i + 1) < len(matches) else len(raw_text)
        segment_raw = raw_text[start_pos:end_pos]
        clean = clean_narration_text(segment_raw)
        
        if clean:
            segments.append(NarrationSegment(emotion=tag_emotion, text=clean, profile=profile))

    return segments


def clean_narration_text(text: str) -> str:
    """Strips emotion tags, sound stage directions, emojis, and normalizes whitespace."""
    text = EMOTION_TAG_PATTERN.sub("", text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    # Remove non-ascii characters / emojis
    text = re.sub(r"[^\x00-\x7F]+", " ", text)
    return " ".join(text.split())


def format_timestamp_ass(td: timedelta) -> str:
    """Formats timedelta as ASS timestamp (H:MM:SS.cc)."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    centiseconds = int(td.microseconds / 10000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def generate_ass_subtitles(
    cues: List[Dict],
    output_ass_path: str,
    max_duration_sec: Optional[float] = None,
):
    """
    Generates Advanced SubStation Alpha (.ass) subtitles tailored for YouTube Shorts.
    - 1080x1920 vertical canvas.
    - High contrast yellow/cyan styling positioned in the safe vertical zone (MarginV: 420).
    - Clamped strictly so no subtitles display after max_duration_sec (zero outro leakage).
    """
    ass_header = """[Script Info]
Title: CrazzyDocs YouTube Shorts Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsDefault,Arial,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,6,3,2,60,60,420,1
Style: ShortsHighlight,Arial,78,&H0000F0FF,&H0000FFFF,&H00000000,&H90000000,-1,0,0,0,100,100,2,0,1,7,4,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    max_td = timedelta(seconds=max_duration_sec) if max_duration_sec is not None else None

    for cue in cues:
        start_td = cue["start"]
        end_td = cue["end"]

        # Prevent subtitle leakage beyond narrative bounds
        if max_td is not None:
            if start_td >= max_td:
                continue
            if end_td > max_td:
                end_td = max_td

        start_str = format_timestamp_ass(start_td)
        end_str = format_timestamp_ass(end_td)
        text = clean_narration_text(cue["text"]).strip().upper()
        if not text:
            continue

        # Word-split for visual punch: max 4-5 words per line
        words = text.split()
        if len(words) > 5:
            mid = len(words) // 2
            text = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])

        events.append(f"Dialogue: 0,{start_str},{end_str},ShortsHighlight,,0,0,0,,{text}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(events))
    logger.info("Generated ASS Subtitles: %s (%d dialogue lines)", output_ass_path, len(events))


def probe_audio_duration(file_path: str) -> float:
    """Returns exact audio stream duration in seconds via ffprobe."""
    if not os.path.exists(file_path):
        return 0.0
    cmd = [
        FFPROBE_EXE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 0.0


async def _synthesize_single_segment(
    segment: NarrationSegment,
    voice: str,
    temp_output_mp3: str,
    cumulative_offset_sec: float,
) -> Tuple[float, List[Dict]]:
    """Synthesizes one emotion segment, captures word boundaries, and offsets timestamps."""
    comm = edge_tts.Communicate(
        text=segment.text,
        voice=voice,
        rate=segment.profile.rate,
        pitch=segment.profile.pitch,
        volume=segment.profile.volume,
    )

    cues: List[Dict] = []
    base_offset_td = timedelta(seconds=cumulative_offset_sec)

    with open(temp_output_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                start_sec = chunk["offset"] / 10_000_000.0
                dur_sec = chunk["duration"] / 10_000_000.0
                cues.append({
                    "start": base_offset_td + timedelta(seconds=start_sec),
                    "end": base_offset_td + timedelta(seconds=start_sec + dur_sec),
                    "text": chunk["text"],
                })

    seg_duration = probe_audio_duration(temp_output_mp3)
    return seg_duration, cues


@dataclass
class NarrationBundle:
    """Complete multi-track narration bundle separating editorial text from full captions."""
    audio_path: str
    editorial_ass_path: str
    accessibility_ass_path: str
    transcript_json_path: str
    duration_sec: float


async def generate_narration(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_filename: str = "narration.mp3",
    max_duration_sec: Optional[float] = None,
    visual_style: VisualStyle = VisualStyle.POP_INFO,
) -> Tuple[str, str, float]:
    """
    Renders expressive speech audio with sentence-level emotion modulation
    and synchronized EDITORIAL TEXT power-word subtitles (2-5 words).
    
    Also generates and preserves:
    - Accessibility captions (.ass / .srt) in media/subtitles/accessibility/
    - Transcript JSON with word timestamps in media/subtitles/transcripts/
    
    Returns: (audio_path, editorial_ass_path, actual_duration)
    """
    bundle = await generate_narration_bundle(
        text=text,
        voice=voice,
        output_filename=output_filename,
        max_duration_sec=max_duration_sec,
        visual_style=visual_style,
    )
    return bundle.audio_path, bundle.editorial_ass_path, bundle.duration_sec


async def generate_narration_bundle(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_filename: str = "narration.mp3",
    max_duration_sec: Optional[float] = None,
    visual_style: VisualStyle = VisualStyle.POP_INFO,
) -> NarrationBundle:
    """
    Generates the complete multi-track narration bundle:
    1. Expressive mixed audio MP3.
    2. Editorial power-word ASS subtitles (clean, un-cluttered 2-5 words).
    3. Accessibility full-sentence subtitles (.ass & .srt).
    4. Full transcript JSON with timestamps.
    """
    audio_path = os.path.join(OUTPUT_AUDIO_DIR, output_filename)
    base_name = os.path.splitext(output_filename)[0]
    default_ass_path = os.path.join(OUTPUT_SUB_DIR, f"{base_name}.ass")
    editorial_ass_path = os.path.join(EDITORIAL_SUB_DIR, f"{base_name}_editorial.ass")
    accessibility_ass_path = os.path.join(ACCESSIBILITY_SUB_DIR, f"{base_name}_accessibility.ass")
    accessibility_srt_path = os.path.join(ACCESSIBILITY_SUB_DIR, f"{base_name}_accessibility.srt")
    transcript_json_path = os.path.join(TRANSCRIPT_SUB_DIR, f"{base_name}_transcript.json")

    segments = parse_emotion_segments(text)
    if not segments:
        raise ValueError("Cannot synthesize empty narration text.")

    temp_dir = os.path.join(OUTPUT_AUDIO_DIR, f"_tmp_{base_name}")
    os.makedirs(temp_dir, exist_ok=True)

    segment_records: List[Tuple[NarrationSegment, str, float, float]] = []
    all_cues: List[Dict] = []
    cumulative_offset = 0.0

    try:
        for idx, seg in enumerate(segments):
            seg_file = os.path.join(temp_dir, f"seg_{idx:03d}.mp3")
            seg_start = cumulative_offset
            seg_dur, seg_cues = await _synthesize_single_segment(
                segment=seg,
                voice=voice,
                temp_output_mp3=seg_file,
                cumulative_offset_sec=cumulative_offset,
            )
            segment_records.append((seg, seg_file, seg_dur, seg_start))
            all_cues.extend(seg_cues)
            cumulative_offset += seg_dur

        # If only 1 segment, copy directly
        if len(segment_records) == 1:
            shutil.copy2(segment_records[0][1], audio_path)
            total_duration = cumulative_offset
        else:
            # Concat all segments with audio re-encoding for seamless transitions
            concat_list_file = os.path.join(temp_dir, "concat.txt")
            with open(concat_list_file, "w", encoding="utf-8") as f:
                for _, sf, _, _ in segment_records:
                    norm = os.path.abspath(sf).replace("\\", "/")
                    f.write(f"file '{norm}'\n")

            cmd_concat = [
                FFMPEG_EXE, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_file,
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-ar", "48000",
                audio_path,
            ]
            subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            total_duration = probe_audio_duration(audio_path)

        # 1. Generate Accessibility Full-Sentence Subtitles (.ass & .srt)
        generate_ass_subtitles(
            cues=all_cues,
            output_ass_path=accessibility_ass_path,
            max_duration_sec=max_duration_sec or total_duration,
        )

        # 2. Generate Transcript JSON
        transcript_data = {
            "title": base_name,
            "duration_sec": total_duration,
            "full_text": clean_narration_text(text),
            "segments": [
                {
                    "emotion": seg.emotion,
                    "text": seg.text,
                    "start_sec": round(s_start, 2),
                    "duration_sec": round(s_dur, 2),
                }
                for seg, _, s_dur, s_start in segment_records
            ],
            "word_cues": [
                {
                    "start": round(c["start"].total_seconds(), 2),
                    "end": round(c["end"].total_seconds(), 2),
                    "text": c["text"],
                }
                for c in all_cues
            ],
        }
        with open(transcript_json_path, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, indent=2)

        # 3. Generate Editorial Text Power-Word Subtitles (2-5 words)
        editorial_engine = EditorialTextEngine()
        ed_cues: List[EditorialTextCue] = []

        for seg, _, s_dur, s_start in segment_records:
            # Avoid splitting abbreviations like St., Dr., Mr.
            # Normalize common abbreviations first
            normalized_seg = seg.text.replace("St. ", "St ").replace("Dr. ", "Dr ").replace("Mr. ", "Mr ").replace("vs. ", "vs ")
            sub_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized_seg) if s.strip()]
            total_words_seg = max(1, len(normalized_seg.split()))

            curr_sub_start = s_start
            for sub_sent in sub_sentences:
                sub_wc = len(sub_sent.split())
                sub_fraction = sub_wc / total_words_seg
                sub_dur = s_dur * sub_fraction

                power_phrase, variant = editorial_engine.extract_power_phrase(sub_sent)
                if power_phrase:
                    c_start = curr_sub_start + min(0.3, sub_dur * 0.15)
                    c_dur = min(2.4, max(1.6, sub_dur * 0.45))
                    c_end = min(total_duration - 0.2, c_start + c_dur)

                    # Enforce breathing room gap of at least 1.0s after previous cue
                    if ed_cues:
                        prev_end = ed_cues[-1].end_sec
                        if c_start < prev_end + 1.0:
                            c_start = prev_end + 1.0
                            c_end = min(total_duration - 0.2, c_start + c_dur)

                    if c_end > c_start:
                        ed_cues.append(EditorialTextCue(
                            text=power_phrase,
                            start_sec=round(c_start, 2),
                            end_sec=round(c_end, 2),
                            style_variant=variant,
                        ))

                curr_sub_start += sub_dur

        editorial_plan = EditorialTextPlan(
            title=base_name,
            total_duration_sec=total_duration,
            cues=ed_cues,
        )

        # Render Editorial Text to both the editorial path and default ass_path
        render_editorial_ass_file(
            plan=editorial_plan,
            output_ass_path=editorial_ass_path,
            visual_style=visual_style,
            max_duration_sec=max_duration_sec or total_duration,
        )
        render_editorial_ass_file(
            plan=editorial_plan,
            output_ass_path=default_ass_path,
            visual_style=visual_style,
            max_duration_sec=max_duration_sec or total_duration,
        )

        logger.info(
            "Narration bundle complete: %s (%.2fs) | Editorial Cues: %d | Accessibility Lines: %d",
            audio_path,
            total_duration,
            len(ed_cues),
            len(all_cues),
        )

        return NarrationBundle(
            audio_path=audio_path,
            editorial_ass_path=editorial_ass_path,
            accessibility_ass_path=accessibility_ass_path,
            transcript_json_path=transcript_json_path,
            duration_sec=total_duration,
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sample_script = (
        "[Curious] The man who bought an entire bank for one dollar. "
        "[Suspenseful] But behind this single dollar was a staggering secret. "
        "[Serious] The institution was burdened by billions in distressed liabilities. "
        "[Surprised] Within forty-eight hours, federal regulators executed an emergency takeover! "
        "[Dramatic] And what happened next changed global finance forever."
    )
    res_audio, res_ass, dur = asyncio.run(generate_narration(sample_script, output_filename="sample_expressive.mp3"))
    print(f"Done! Audio: {res_audio}, ASS: {res_ass}, Duration: {dur:.2f}s")
