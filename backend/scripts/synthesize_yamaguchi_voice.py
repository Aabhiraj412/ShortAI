"""
Calibrated Expressive Narration Synthesizer for Tsutomu Yamaguchi Short.
Calibrated to 52.0 - 53.0s total voiceover duration (54.5 - 55.0s with additive outro).
"""
import asyncio
import json
import logging
import os
from backend.app.services.expressive_tts_engine import ExpressiveTTSEngine, NarrationSegment

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("synthesize_voice")

VOICE_DIR = r"D:\Agents\CrazzyDocs\media\audio\yamaguchi_segments"
FINAL_AUDIO_PATH = r"D:\Agents\CrazzyDocs\media\audio\stage_3_4_yamaguchi_voice.mp3"
TELEMETRY_PATH = r"D:\Agents\CrazzyDocs\media\audio\yamaguchi_telemetry.json"
os.makedirs(VOICE_DIR, exist_ok=True)

# 8 Punchy, High-Velocity Narrative Segments (Calibrated for ~52.0s speech)
SEGMENTS = [
    NarrationSegment(
        id="beat_01_hook",
        emotion="shocked",
        text="He survived the atomic blast in Hiroshima. Three days later, he was in Nagasaki.",
        rate="+16%",
        pitch="+3Hz",
        pre_pause_ms=0,
        post_pause_ms=80,
        delivery_note="Fast, stunned opening hook"
    ),
    NarrationSegment(
        id="beat_02_context",
        emotion="intrigued",
        text="In August 1945, naval engineer Tsutomu Yamaguchi was heading to the Hiroshima train station.",
        rate="+16%",
        pitch="+0Hz",
        pre_pause_ms=0,
        post_pause_ms=80,
        delivery_note="Crisp historical context"
    ),
    NarrationSegment(
        id="beat_03_blast1",
        emotion="dramatic",
        text="At 8:15 AM, the sky ignited. The atomic bomb detonated two miles away, burning him and rupturing his eardrums.",
        rate="+12%",
        pitch="-3Hz",
        pre_pause_ms=0,
        post_pause_ms=100,
        delivery_note="Heavy dramatic shock"
    ),
    NarrationSegment(
        id="beat_04_train",
        emotion="serious",
        text="Crawling through burning ruins, he miraculously caught the last refugee train home to Nagasaki.",
        rate="+16%",
        pitch="-1Hz",
        pre_pause_ms=0,
        post_pause_ms=80,
        delivery_note="Urgent survival pacing"
    ),
    NarrationSegment(
        id="beat_05_boss",
        emotion="curious",
        text="On August 9th, wrapped in bandages, he reported to his boss, who laughed: 'No single bomb can destroy a city.'",
        rate="+14%",
        pitch="+3Hz",
        pre_pause_ms=0,
        post_pause_ms=100,
        delivery_note="Skeptical tension"
    ),
    NarrationSegment(
        id="beat_06_twist",
        emotion="shocked",
        text="At that exact second, the window blew apart in blinding white fire. The second bomb had struck.",
        rate="+10%",
        pitch="-4Hz",
        pre_pause_ms=0,
        post_pause_ms=100,
        delivery_note="Thunderous shock"
    ),
    NarrationSegment(
        id="beat_07_payoff",
        emotion="emphatic",
        text="Blown across the room, Yamaguchi survived again, becoming history's only certified double atomic bomb survivor.",
        rate="+12%",
        pitch="+2Hz",
        pre_pause_ms=0,
        post_pause_ms=100,
        delivery_note="Reverent magnitude"
    ),
    NarrationSegment(
        id="beat_08_ending",
        emotion="reassuring",
        text="He lived to age ninety-three, advocating for peace. Follow CrazzyDocs for more incredible history.",
        rate="+16%",
        pitch="+0Hz",
        pre_pause_ms=0,
        post_pause_ms=50,
        delivery_note="Inspiring conclusion and signoff"
    )
]


async def run_synthesis():
    engine = ExpressiveTTSEngine()
    logger.info("Synthesizing %d calibrated narration segments...", len(SEGMENTS))
    for s in SEGMENTS:
        await engine.synthesize_segment(s, VOICE_DIR)

    total_dur = engine.concatenate_segments(SEGMENTS, FINAL_AUDIO_PATH)
    logger.info("Total synthesized narration track: %.2fs", total_dur)

    telemetry = []
    current_time = 0.0
    for s in SEGMENTS:
        telemetry.append({
            "id": s.id,
            "emotion": s.emotion,
            "text": s.text,
            "start_sec": round(current_time, 2),
            "duration_sec": round(s.duration_sec, 2),
            "end_sec": round(current_time + s.duration_sec, 2),
            "rate": s.rate,
            "pitch": s.pitch,
            "delivery_note": s.delivery_note
        })
        current_time += s.duration_sec

    with open(TELEMETRY_PATH, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    logger.info("Saved telemetry to %s", TELEMETRY_PATH)
    return total_dur, telemetry


if __name__ == "__main__":
    asyncio.run(run_synthesis())
