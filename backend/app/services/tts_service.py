"""
Text-to-Speech (TTS) and Subtitle Generation Service for YouTube Shorts Studio.
Supports Edge-TTS with automatic millisecond-accurate subtitle generation.
"""
import asyncio
import os
import re
from datetime import timedelta
from typing import Dict, List, Optional, Tuple
import edge_tts

DEFAULT_VOICE = "en-US-ChristopherNeural"
OUTPUT_AUDIO_DIR = r"D:\AI Arts\AI\media\audio"
OUTPUT_SUB_DIR = r"D:\AI Arts\AI\media\subtitles"

os.makedirs(OUTPUT_AUDIO_DIR, exist_ok=True)
os.makedirs(OUTPUT_SUB_DIR, exist_ok=True)

def format_timestamp_ass(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    centiseconds = int(td.microseconds / 10000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"

def generate_ass_subtitles(cues: List[Dict], output_ass_path: str):
    """
    Generates Advanced SubStation Alpha (.ass) subtitles tailored for YouTube Shorts.
    1080x1920 vertical canvas, bold yellow/cyan high-contrast styling, positioned safely.
    """
    ass_header = """[Script Info]
Title: AI YouTube Shorts Subtitles
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
    for cue in cues:
        start_str = format_timestamp_ass(cue["start"])
        end_str = format_timestamp_ass(cue["end"])
        text = cue["text"].strip().upper()
        # Word-split for visual punch: keep max 4-5 words per line
        words = text.split()
        if len(words) > 5:
            mid = len(words) // 2
            text = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])
        
        events.append(f"Dialogue: 0,{start_str},{end_str},ShortsHighlight,,0,0,0,,{text}")
        
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(events))
    print(f"Generated ASS Subtitles: {output_ass_path}")

async def generate_narration(
    text: str, 
    voice: str = DEFAULT_VOICE, 
    output_filename: str = "narration.mp3"
) -> Tuple[str, str, float]:
    """
    Renders speech audio and synchronized subtitles.
    Returns: (audio_path, ass_path, estimated_duration)
    """
    audio_path = os.path.join(OUTPUT_AUDIO_DIR, output_filename)
    base_name = os.path.splitext(output_filename)[0]
    ass_path = os.path.join(OUTPUT_SUB_DIR, f"{base_name}.ass")
    srt_path = os.path.join(OUTPUT_SUB_DIR, f"{base_name}.srt")
    
    communicate = edge_tts.Communicate(text=text, voice=voice)
    submaker = edge_tts.SubMaker()
    cues = []
    
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
                start_td = timedelta(microseconds=chunk["offset"] / 10)
                end_td = timedelta(microseconds=(chunk["offset"] + chunk["duration"]) / 10)
                cues.append({
                    "start": start_td,
                    "end": end_td,
                    "text": chunk["text"]
                })
                
    # Save SRT
    srt_content = submaker.get_srt()
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
        
    # Generate ASS
    generate_ass_subtitles(cues, ass_path)
    
    # Calculate duration
    duration = cues[-1]["end"].total_seconds() if cues else 0.0
    print(f"Narration generated: {audio_path} ({duration:.2f}s)")
    return audio_path, ass_path, duration

if __name__ == "__main__":
    sample_text = (
        "What happens if you fall into a black hole? "
        "First, extreme gravity stretches you like spaghetti. "
        "Next, time slows down relative to the outside universe. "
        "And once you cross the event horizon, not even light can escape!"
    )
    asyncio.run(generate_narration(sample_text, DEFAULT_VOICE, "sample_narration.mp3"))
