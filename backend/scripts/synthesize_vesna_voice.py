"""
Calibrated Expressive Narration Synthesizer for Vesna Vulovic Short.
Phase 5, Stage 3.13A Standard.

Calibrated to ~52.0 - 53.0s total voiceover duration (54.5 - 55.5s with additive 2.0s branded outro).
"""
import asyncio
import json
import logging
import os
import sys

WORKSPACE = r"D:\Agents\CrazzyDocs"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from backend.app.services.expressive_tts_engine import ExpressiveTTSEngine, NarrationSegment

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("synthesize_vesna_voice")

VOICE_DIR = os.path.join(WORKSPACE, "media", "audio", "vesna_segments")
FINAL_AUDIO_PATH = os.path.join(WORKSPACE, "media", "audio", "stage_3_13_vesna_voice.mp3")
TELEMETRY_PATH = os.path.join(WORKSPACE, "media", "audio", "vesna_telemetry.json")
os.makedirs(VOICE_DIR, exist_ok=True)

# 8 High-Impact Factual Narrative Segments for Vesna Vulovic (~135 words, ~52.5s)
SEGMENTS = [
    NarrationSegment(
        id="beat_01_hook",
        emotion="shocked",
        text="She fell 33,000 feet without a parachute—and walked out of the hospital alive.",
        rate="+5%",
        pitch="+2Hz",
        pre_pause_ms=0,
        post_pause_ms=60,
        delivery_note="Fast, intense opening hook"
    ),
    NarrationSegment(
        id="beat_02_context",
        emotion="intrigued",
        text="In 1972, flight attendant Vesna Vulović boarded JAT Flight 367 over Czechoslovakia.",
        rate="+5%",
        pitch="+0Hz",
        pre_pause_ms=0,
        post_pause_ms=60,
        delivery_note="Crisp historical context"
    ),
    NarrationSegment(
        id="beat_03_bomb",
        emotion="dramatic",
        text="At 33,000 feet, a briefcase bomb detonated, tearing the airliner apart in mid-air.",
        rate="+4%",
        pitch="-2Hz",
        pre_pause_ms=0,
        post_pause_ms=80,
        delivery_note="Sudden dramatic shock"
    ),
    NarrationSegment(
        id="beat_04_plunge",
        emotion="urgent",
        text="While passengers were sucked into the sky, Vesna was trapped in the tail, pinned by a food cart.",
        rate="+5%",
        pitch="-1Hz",
        pre_pause_ms=0,
        post_pause_ms=60,
        delivery_note="Urgent descent tension"
    ),
    NarrationSegment(
        id="beat_05_impact",
        emotion="serious",
        text="Plummeting at terminal velocity, the wreckage slammed into a snowy mountain, cushioned by dense pines.",
        rate="+4%",
        pitch="-3Hz",
        pre_pause_ms=0,
        post_pause_ms=80,
        delivery_note="Heavy impact absorption"
    ),
    NarrationSegment(
        id="beat_06_rescue",
        emotion="curious",
        text="A wartime medic, Bruno Honke, found her in the freezing forest and kept her alive until help arrived.",
        rate="+5%",
        pitch="+1Hz",
        pre_pause_ms=0,
        post_pause_ms=60,
        delivery_note="Life-saving discovery"
    ),
    NarrationSegment(
        id="beat_07_recovery",
        emotion="intrigued",
        text="Despite a fractured skull and crushed spine, she miraculously learned to walk again.",
        rate="+5%",
        pitch="+2Hz",
        pre_pause_ms=0,
        post_pause_ms=60,
        delivery_note="Astonishing medical recovery"
    ),
    NarrationSegment(
        id="beat_08_legacy",
        emotion="reflective",
        text="Guinness certified it as the highest fall survived without a parachute. An unbelievable true miracle.",
        rate="+4%",
        pitch="-1Hz",
        pre_pause_ms=0,
        post_pause_ms=0,
        delivery_note="Resonant legacy close"
    )
]


async def synthesize_all():
    tts = ExpressiveTTSEngine()
    telemetry = []

    for i, seg in enumerate(SEGMENTS):
        logger.info("[%d/%d] Synthesizing %s (emotion=%s)...", i + 1, len(SEGMENTS), seg.id, seg.emotion)
        await tts.synthesize_segment(seg, VOICE_DIR)
        dur = tts.probe_duration(seg.audio_path)
        telemetry.append({
            "id": seg.id,
            "text": seg.text,
            "duration": dur,
            "emotion": seg.emotion,
            "rate": seg.rate,
            "pitch": seg.pitch
        })
        logger.info("  -> Segment duration: %.2fs", dur)

    logger.info("Concatenating segments into unified track: %s ...", FINAL_AUDIO_PATH)
    final_dur = tts.concatenate_segments(SEGMENTS, FINAL_AUDIO_PATH)
    logger.info("Final unified narration duration: %.2fs", final_dur)

    with open(TELEMETRY_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "final_duration": final_dur,
            "target_duration_window": "50.0 - 54.0s",
            "segments": telemetry
        }, f, indent=2)

    logger.info("Telemetry saved to: %s", TELEMETRY_PATH)
    return final_dur


if __name__ == "__main__":
    dur = asyncio.run(synthesize_all())
    print(f"\nVesna Vulovic voice track synthesized successfully: {dur:.2f}s")
