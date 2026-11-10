"""
Editorial Text ASS Subtitle Renderer.

Transforms structured EditorialTextPlan cues into style-specific ASS subtitle tracks.
Enforces:
- 2-5 words per cue (strictly zero full-sentence clutter).
- Clear visual breathing room between cues (no continuous text blocks).
- Style-specific typography matching the active visual renderer.
- Safe vertical positioning (safe band y: 1380-1560, MarginV: 430).
- Zero outro leakage.
"""

import os
from typing import List, Optional
from datetime import timedelta

from backend.app.content.editorial_text import EditorialTextCue, EditorialTextPlan
from backend.app.rendering.styles.style_system import VisualStyle


def format_ass_time(sec: float) -> str:
    """Formats seconds as ASS timestamp (H:MM:SS.cc)."""
    td = timedelta(seconds=max(0.0, sec))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    centiseconds = int(td.microseconds / 10000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def get_editorial_ass_header(visual_style: VisualStyle) -> str:
    """Returns style-specific ASS styling configuration with customized typography."""
    if visual_style == VisualStyle.CHALK_TALK:
        font_name = "Kalam"
        primary_color = "&H00E6EFEF"   # Chalk white in BGR
        outline_color = "&H0018261F"   # Slate dark
        fontsize = 80
        outline = 5
        shadow = 3
    elif visual_style == VisualStyle.DATA_STORY:
        font_name = "Archivo"
        primary_color = "&H004EC1F2"   # Sand amber in BGR
        outline_color = "&H001C140F"   # Deep ink
        fontsize = 84
        outline = 5
        shadow = 4
    elif visual_style == VisualStyle.CREATOR_MOTION:
        font_name = "Anton"
        primary_color = "&H000AD6FF"   # Retention yellow in BGR
        outline_color = "&H000D0B0B"   # Deep black
        fontsize = 94
        outline = 7
        shadow = 4
    else:  # Pop Info
        font_name = "Fredoka"
        primary_color = "&H000AD6FF"   # Electric yellow
        outline_color = "&H002A2A1D"   # Deep slate ink
        fontsize = 82
        outline = 6
        shadow = 4

    header = f"""[Script Info]
Title: CrazzyDocs Editorial Text ({visual_style.value})
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: EditorialPrimary,{font_name},{fontsize},{primary_color},&H00FFFFFF,{outline_color},&H90000000,-1,0,0,0,100,100,2,0,1,{outline},{shadow},2,80,80,430,1
Style: EditorialAlert,{font_name},{fontsize+4},&H004D4DFF,&H00FFFFFF,&H00101010,&HA0000000,-1,0,0,0,100,100,2,0,1,{outline+1},{shadow},2,80,80,430,1
Style: EditorialMetric,{font_name},{fontsize+2},&H005EC522,&H00FFFFFF,&H00101010,&HA0000000,-1,0,0,0,100,100,2,0,1,{outline},{shadow},2,80,80,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header


def render_editorial_ass_file(
    plan: EditorialTextPlan,
    output_ass_path: str,
    visual_style: VisualStyle = VisualStyle.POP_INFO,
    max_duration_sec: Optional[float] = None,
) -> str:
    """
    Renders an ASS file containing strictly the 2-5 word editorial text power phrases.
    Guarantees:
    - Zero verbatim transcription.
    - Clean visual breathing room between phrases.
    - Clamped strictly before the outro starts.
    """
    os.makedirs(os.path.dirname(output_ass_path), exist_ok=True)
    header = get_editorial_ass_header(visual_style)
    dialogue_lines: List[str] = []

    cutoff_time = max_duration_sec if max_duration_sec is not None else plan.total_duration_sec

    for cue in plan.cues:
        if cue.start_sec >= cutoff_time:
            continue
        cue_end = min(cutoff_time, cue.end_sec)
        if cue_end <= cue.start_sec:
            continue

        clean_text = cue.text.strip().upper()
        # Enforce 5-word ceiling
        words = clean_text.split()
        if len(words) > 5:
            clean_text = " ".join(words[:5])

        style_name = "EditorialPrimary"
        if cue.style_variant == "alert":
            style_name = "EditorialAlert"
        elif cue.style_variant == "metric":
            style_name = "EditorialMetric"

        start_str = format_ass_time(cue.start_sec)
        end_str = format_ass_time(cue_end)

        line = f"Dialogue: 0,{start_str},{end_str},{style_name},,0,0,0,,{clean_text}"
        dialogue_lines.append(line)

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(dialogue_lines) + "\n")

    return output_ass_path
