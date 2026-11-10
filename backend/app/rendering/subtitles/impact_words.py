"""
Selective Impact Word Subtitle Generator.

Replaces full-text, word-by-word subtitle clutter with selective,
high-impact emphasis words (1-4 words max) synchronized to spoken peaks.
Operates strictly within the mobile subtitle safe zone (Y: 1380 - 1560).
"""

from datetime import timedelta
import os
from typing import List

from backend.app.rendering.styles.style_system import THEMES, VisualStyle
from backend.app.rendering.timeline.narration_timeline import ImpactWordEvent


def format_ass_time(sec: float) -> str:
    td = timedelta(seconds=max(0.0, sec))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    centiseconds = int(td.microseconds / 10000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def get_style_ass_header(visual_style: VisualStyle) -> str:
    """Returns style-specific ASS configuration with typography matching the active visual theme."""
    if visual_style == VisualStyle.CHALK_TALK:
        font_name = "Kalam"
        primary_color = "&H00E6EFEF"   # Chalk white in BGR (&H00BBGGRR)
        outline_color = "&H0018261F"   # Dark slate
        fontsize = 76
        border_style = 1
        outline = 4
        shadow = 3
    elif visual_style == VisualStyle.DATA_STORY:
        font_name = "Archivo"
        primary_color = "&H004EC1F2"   # Sand amber in BGR
        outline_color = "&H001C140F"   # Deep ink
        fontsize = 78
        border_style = 1
        outline = 5
        shadow = 4
    elif visual_style == VisualStyle.CREATOR_MOTION:
        font_name = "Anton"
        primary_color = "&H000AD6FF"   # Retention yellow in BGR
        outline_color = "&H000D0B0B"   # Near black
        fontsize = 92
        border_style = 1
        outline = 6
        shadow = 4
    else:  # Pop Info
        font_name = "Fredoka"
        primary_color = "&H004A5DE8"   # Coral in BGR
        outline_color = "&H002A2A1D"   # Ink
        fontsize = 76
        border_style = 1
        outline = 5
        shadow = 3

    header = f"""[Script Info]
Title: Selective Impact Words ({visual_style.value})
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ImpactPrimary,{font_name},{fontsize},{primary_color},&H00FFFFFF,{outline_color},&H80000000,-1,0,0,0,100,100,2,0,{border_style},{outline},{shadow},2,80,80,410,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header


def generate_impact_word_ass(
    events: List[ImpactWordEvent],
    output_ass_path: str,
    visual_style: VisualStyle = VisualStyle.CREATOR_MOTION,
) -> str:
    """
    Renders an ASS subtitle track containing ONLY selective impact words.
    Enforces maximum 4 words per display and zero full-sentence clutter.
    """
    os.makedirs(os.path.dirname(output_ass_path), exist_ok=True)
    header = get_style_ass_header(visual_style)
    dialogue_lines = []

    for ev in events:
        start_str = format_ass_time(ev.start_sec)
        end_str = format_ass_time(ev.end_sec)
        clean_text = ev.text.strip().upper()

        # Word count safety check: maximum 4 words
        words = clean_text.split()
        if len(words) > 4:
            clean_text = " ".join(words[:4])

        # Style override tags
        if ev.style_variant == "alert":
            color_override = "{\\c&H004D4DFF}"  # Red coral
        elif ev.style_variant == "metric":
            color_override = "{\\c&H005EC522}"  # Neon green
        elif ev.style_variant == "chalk":
            color_override = "{\\c&H005ED3F4}"  # Chalk yellow
        else:
            color_override = ""

        # Alignment tag 2 = bottom-center, marginV = 410 places it at y ≈ 1510 (safe zone)
        dialogue = f"Dialogue: 0,{start_str},{end_str},ImpactPrimary,,0,0,0,,{color_override}{clean_text}"
        dialogue_lines.append(dialogue)

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(dialogue_lines) + "\n")

    return output_ass_path
