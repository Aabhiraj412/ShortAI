"""
Synthesizes the complete multi-track narration bundle for The Great Molasses Flood of 1919.
Produces:
- media/audio/stage_3_3_molasses_voice.mp3
- media/subtitles/editorial/stage_3_3_molasses_voice_editorial.ass
- media/subtitles/accessibility/stage_3_3_molasses_voice_accessibility.ass
- media/subtitles/transcripts/stage_3_3_molasses_voice_transcript.json
"""

import asyncio
import os
import sys

from backend.app.services.tts_service import generate_narration_bundle
from backend.app.rendering.styles.style_system import VisualStyle

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"

SCRIPT_TEXT = (
    "[Curious] In January 1919, a two-million-gallon wave of molasses swallowed downtown Boston. "
    "[Suspenseful] The fifty-foot steel tank leaked constantly. "
    "Instead of repairing it, the company painted it brown to hide the leaks. "
    "[Dramatic] Rapid winter warming spiked internal gas pressure. "
    "Rivets exploded like machine guns, unleashing a twenty-five-foot wave at thirty-five miles per hour! "
    "[Serious] The crushing force flattened buildings, ripped elevated train tracks, and trapped victims in sludge. "
    "Twenty-one died and one hundred fifty were injured. "
    "[Surprised] The company blamed anarchists, but investigations proved the steel was half as thick as required—pure corporate negligence. "
    "[Emphatic] Victims won a landmark settlement, founding modern engineering licensing laws. "
    "Follow CrazzyDocs for more wild history!"
)

async def main():
    print("Synthesizing multi-track narration for The Great Molasses Flood of 1919...")
    bundle = await generate_narration_bundle(
        text=SCRIPT_TEXT,
        voice="en-US-ChristopherNeural",
        output_filename="stage_3_3_molasses_voice.mp3",
        visual_style=VisualStyle.DATA_STORY,
    )
    print(f"Audio Path: {bundle.audio_path}")
    print(f"Editorial ASS: {bundle.editorial_ass_path}")
    print(f"Accessibility ASS: {bundle.accessibility_ass_path}")
    print(f"Transcript JSON: {bundle.transcript_json_path}")
    print(f"Duration: {bundle.duration_sec:.2f}s")
    print("Synthesis complete.")

if __name__ == "__main__":
    asyncio.run(main())
