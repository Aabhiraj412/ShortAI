"""
End-to-End Validation & Benchmark for Hybrid Dual-Engine Architecture.

Renders a full multi-modal YouTube Short combining:
1. Real Photo scene (Ken Burns camera dynamics)
2. Pop Info scene (illustrated character & thought bubble)
3. Data Story scene (monotone spline & dynamic counter)
4. Chalk Talk scene (blackboard slate & chalk stick)
5. Creator Motion Graphics scene (kinetic Anton slam & 3D tilt)
6. Split Screen scene (comparative dual-panel)
With voiceover, ducked background music, procedural SFX, and contact sheet generation.
"""

import asyncio
import json
import logging
import os
import sys
import subprocess
import time

sys.path.insert(0, r"D:\AI Arts\AI")
from PIL import Image, ImageDraw, ImageFont

from backend.app.rendering.engine import HybridVisualEngine
from backend.app.rendering.types import (
    SceneBeat,
    StoryboardPlan,
    VisualModality,
)
from backend.app.services.tts_service import generate_narration

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("HybridBenchmark")

WORKSPACE_ROOT = r"D:\AI Arts\AI"
OUTPUT_VIDEO_PATH = os.path.join(WORKSPACE_ROOT, "media", "video", "hybrid_benchmark_short.mp4")
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit", "hybrid_engine")
CONTACT_SHEET_PATH = os.path.join(WORKSPACE_ROOT, "media", "audit", "contact_sheet_hybrid_engine.png")
FFMPEG_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")


async def prepare_narration() -> str:
    """Prepares narration audio, using Edge-TTS with fallback to existing narration."""
    narration_text = (
        "From the deepest reaches of the cosmos to the everyday decisions of the mind, "
        "data reveals the hidden reality behind our world. "
        "Every formula tells a story. "
        "Stop creating boring content and start engineering videos that captivate."
    )
    narr_path = os.path.join(WORKSPACE_ROOT, "media", "audio", "hybrid_benchmark_narration.mp3")
    try:
        audio, _, _ = await generate_narration(narration_text, "en-US-ChristopherNeural", "hybrid_benchmark_narration.mp3")
        return audio
    except Exception as e:
        logger.warning("Edge-TTS network generation failed (%s), using local fallback narration", e)
        fallback = os.path.join(WORKSPACE_ROOT, "media", "audio", "sample_narration.mp3")
        return fallback if os.path.exists(fallback) else None


def extract_keyframes_and_contact_sheet(video_path: str, output_contact_sheet: str, num_frames: int = 8):
    """Extracts representative keyframes and stitches a high-resolution contact sheet."""
    os.makedirs(AUDIT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(output_contact_sheet), exist_ok=True)

    # Probe duration
    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    duration = float(res.stdout.strip()) if res.returncode == 0 else 17.5

    timestamps = [duration * (i + 0.5) / num_frames for i in range(num_frames)]
    frame_paths = []

    for i, ts in enumerate(timestamps):
        out_frame = os.path.join(AUDIT_DIR, f"frame_{i+1:02d}_{ts:.1f}s.png")
        cmd_extract = [
            FFMPEG_PATH,
            "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_frame,
        ]
        subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(out_frame):
            frame_paths.append((out_frame, ts))

    if not frame_paths:
        logger.warning("No keyframes extracted for contact sheet.")
        return

    # Stitch into 2 rows x 4 columns grid
    cols = 4
    rows = 2
    thumb_w, thumb_h = 360, 640
    padding = 24
    header_h = 90
    sheet_w = cols * thumb_w + (cols + 1) * padding
    sheet_h = rows * thumb_h + (rows + 1) * padding + header_h

    sheet = Image.new("RGB", (sheet_w, sheet_h), (12, 14, 18))
    draw = ImageDraw.Draw(sheet)

    # Header title
    draw.text((padding, 24), "HYBRID DUAL-ENGINE VISUAL QUALITY BENCHMARK", fill=(240, 243, 248))
    draw.text((padding, 56), f"Resolution: 1080x1920 @ 30 FPS | Duration: {duration:.1f}s | Multi-Engine Composite", fill=(32, 201, 151))

    for idx, (fpath, ts) in enumerate(frame_paths):
        c = idx % cols
        r = idx // cols
        x = padding + c * (thumb_w + padding)
        y = header_h + padding + r * (thumb_h + padding)

        with Image.open(fpath) as img:
            thumb = img.resize((thumb_w, thumb_h), Image.Resampling.BILINEAR)
            sheet.paste(thumb, (x, y))

        # Frame border and label
        draw.rectangle([(x, y), (x + thumb_w, y + thumb_h)], outline=(35, 45, 60), width=2)
        label = f"Beat {idx+1} ({ts:.1f}s)"
        draw.rectangle([(x + 10, y + 10), (x + 150, y + 42)], fill=(12, 14, 18, 220))
        draw.text((x + 18, y + 15), label, fill=(255, 214, 10))

    sheet.save(output_contact_sheet)
    logger.info("Saved contact sheet: %s (%dx%d)", output_contact_sheet, sheet_w, sheet_h)


async def main():
    logger.info("==================================================================")
    logger.info("PHASE 4: HYBRID VISUAL ENGINE BENCHMARK SUITE")
    logger.info("Hardware: AMD Ryzen 7 3700U / 16 GB RAM / Zero Cloud GPU")
    logger.info("==================================================================")

    # 1. Prepare Voiceover
    voiceover_path = await prepare_narration()
    music_path = os.path.join(WORKSPACE_ROOT, "assets", "audio", "music", "ambient_tech_theme.wav")

    # 2. Define Multi-Modal Storyboard Plan
    plan = StoryboardPlan(
        title="The Hidden Reality",
        total_duration=17.5,
        scenes=[
            SceneBeat(
                id="photo_cosmos",
                modality=VisualModality.REAL_PHOTO,
                duration=2.5,
                narration_text="From the deepest reaches of the cosmos...",
                params={
                    "image_path": os.path.join(WORKSPACE_ROOT, "assets", "backgrounds", "rendered", "bg_space.png"),
                    "title": "THE COSMIC PERSPECTIVE",
                    "subtitle": "Deep Space Observational Array",
                },
            ),
            SceneBeat(
                id="pop_shopper",
                modality=VisualModality.POP_INFO,
                duration=3.0,
                narration_text="...to the everyday decisions of the mind...",
            ),
            SceneBeat(
                id="data_spline",
                modality=VisualModality.DATA_STORY,
                duration=3.0,
                narration_text="...data reveals the hidden reality behind our world.",
            ),
            SceneBeat(
                id="chalk_formula",
                modality=VisualModality.CHALK_TALK,
                duration=3.0,
                narration_text="Every formula tells a story.",
            ),
            SceneBeat(
                id="motion_slam",
                modality=VisualModality.CREATOR_MOTION,
                duration=3.5,
                narration_text="Stop creating boring content...",
            ),
            SceneBeat(
                id="split_summary",
                modality=VisualModality.SPLIT_SCREEN,
                duration=2.5,
                narration_text="...and start engineering videos that captivate.",
                params={
                    "top_image": os.path.join(WORKSPACE_ROOT, "assets", "backgrounds", "rendered", "bg_space.png"),
                    "bottom_image": os.path.join(WORKSPACE_ROOT, "assets", "backgrounds", "rendered", "bg_tech_lab.png"),
                    "top_label": "THEORY & COSMOS",
                    "bottom_label": "LABORATORY & REALITY",
                },
            ),
        ],
        voiceover_path=voiceover_path,
        music_track=music_path if os.path.exists(music_path) else None,
        ducking_db=-14.0,
    )

    # 3. Execute Hybrid Visual Engine
    engine = HybridVisualEngine(WORKSPACE_ROOT)
    telemetry = await engine.render_storyboard(plan, OUTPUT_VIDEO_PATH)

    # 4. Verify Final Video Properties via FFprobe
    probe_cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration",
        "-of", "json",
        OUTPUT_VIDEO_PATH,
    ]
    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
    video_info = json.loads(probe_res.stdout)
    stream = video_info["streams"][0]

    width = int(stream["width"])
    height = int(stream["height"])
    fps_val = eval(stream["r_frame_rate"])
    filesize_mb = os.path.getsize(OUTPUT_VIDEO_PATH) / (1024 * 1024)

    logger.info("------------------------------------------------------------------")
    logger.info("OUTPUT VIDEO VERIFICATION:")
    logger.info("  File: %s", OUTPUT_VIDEO_PATH)
    logger.info("  Dimensions: %dx%d (9:16 Vertical)", width, height)
    logger.info("  Framerate: %.2f FPS", fps_val)
    logger.info("  Filesize: %.2f MB", filesize_mb)
    logger.info("------------------------------------------------------------------")

    # 5. Extract Keyframes & Generate Contact Sheet
    extract_keyframes_and_contact_sheet(OUTPUT_VIDEO_PATH, CONTACT_SHEET_PATH, num_frames=8)

    # 6. Save Benchmark Telemetry Report
    bench_json_path = os.path.join(WORKSPACE_ROOT, "media", "audit", "hybrid_telemetry.json")
    with open(bench_json_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    logger.info("Benchmark complete! Telemetry saved to %s", bench_json_path)


if __name__ == "__main__":
    asyncio.run(main())
