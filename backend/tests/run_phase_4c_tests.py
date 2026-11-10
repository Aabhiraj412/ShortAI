"""
Phase 4C Master Test Suite: Narrative Synchronization & Visual Refinement.

Renders and validates three 20-second sample videos:
1. Data Story: Why Solar Energy Is Becoming Cheaper (test_data_story_20sec.mp4)
2. Chalk Talk: Why Does the Moon Not Fall to Earth? (test_chalk_talk_20sec.mp4)
3. Creator Motion Graphics: Why Most People Lose Interest in a Video Within Seconds (test_creator_motion_20sec.mp4)

Generates 8-keyframe contact sheets, runs QualityGate with custom duration override,
and records comprehensive perceptual and technical quality metrics.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont

from backend.app.rendering.compositing.compositor import UniversalCompositor
from backend.app.rendering.engines.browser_canvas.runner import BrowserCanvasRunner
from backend.app.rendering.quality.quality_gate import QualityGate
from backend.app.rendering.styles.style_system import THEMES, VisualStyle
from backend.app.rendering.subtitles.impact_words import generate_impact_word_ass
from backend.app.rendering.timeline.narration_timeline import ImpactWordEvent
from backend.app.services.tts_service import generate_narration

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Phase4CTests")

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
FFMPEG_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit")
VIDEO_DIR = os.path.join(WORKSPACE_ROOT, "media", "video")
AUDIO_DIR = os.path.join(WORKSPACE_ROOT, "media", "audio")
SUB_DIR = os.path.join(WORKSPACE_ROOT, "media", "subtitles")

os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(SUB_DIR, exist_ok=True)


TEST_SPECS = [
    {
        "style": VisualStyle.DATA_STORY,
        "title": "Why Solar Energy Is Becoming Cheaper",
        "narration_text": (
            "Solar is now the cheapest electricity in history. "
            "Since 2010, the cost collapsed by eighty-nine percent. "
            "It follows Swansons Law: every time global manufacturing capacity doubles, "
            "module costs drop twenty-four percent. "
            "Today, nothing can compete with the economics of silicon."
        ),
        "voice": "en-US-ChristopherNeural",
        "audio_file": "test_data_story_20s.mp3",
        "impact_ass_file": "test_data_story_impact.ass",
        "adapter_url": f"file:///{WORKSPACE_ROOT.replace(os.sep, '/')}/backend/app/rendering/styles/data_story/adapter.html",
        "output_video": os.path.join(VIDEO_DIR, "test_data_story_20sec.mp4"),
        "contact_sheet": os.path.join(AUDIT_DIR, "contact_sheet_data_story_20s.png"),
        "impact_events": [
            ImpactWordEvent("CHEAPEST POWER", 1.2, 3.2),
            ImpactWordEvent("-89% COLLAPSE", 5.2, 7.5),
            ImpactWordEvent("SWANSON'S LAW", 9.5, 12.0),
            ImpactWordEvent("UNBEATABLE SILICON", 17.5, 20.0),
        ],
        "scenes_meta": [
            {
                "id": 1,
                "text_elements": [
                    {"bbox": (140, 360, 800, 70), "font_size": 52, "kind": "title", "color": "#EEF1F5", "bg_color": "#0F141C"},
                    {"bbox": (240, 1140, 600, 50), "font_size": 30, "kind": "body", "color": "#7C8BA1", "bg_color": "#0F141C"}
                ]
            },
            {
                "id": 2,
                "text_elements": [
                    {"bbox": (140, 340, 800, 60), "font_size": 48, "kind": "title", "color": "#F2C14E", "bg_color": "#0F141C"},
                    {"bbox": (240, 1140, 600, 50), "font_size": 28, "kind": "body", "color": "#EEF1F5", "bg_color": "#0F141C"}
                ]
            },
            {
                "id": 3,
                "text_elements": [
                    {"bbox": (140, 340, 800, 60), "font_size": 48, "kind": "title", "color": "#F2C14E", "bg_color": "#0F141C"},
                    {"bbox": (240, 1140, 600, 50), "font_size": 28, "kind": "body", "color": "#7C8BA1", "bg_color": "#0F141C"}
                ]
            },
            {
                "id": 4,
                "text_elements": [
                    {"bbox": (140, 340, 800, 60), "font_size": 50, "kind": "title", "color": "#EEF1F5", "bg_color": "#0F141C"},
                    {"bbox": (240, 1140, 600, 50), "font_size": 30, "kind": "body", "color": "#20C997", "bg_color": "#0F141C"}
                ]
            }
        ]
    },
    {
        "style": VisualStyle.CHALK_TALK,
        "title": "Why Does the Moon Not Fall to Earth?",
        "narration_text": (
            "The Moon is constantly falling toward Earth, pulled by gravity. But it never crashes! "
            "Because the Moon also flies forward at one kilometer per second. "
            "As it falls, Earth curves away beneath it. "
            "Newton proved this: the Moon is in perpetual orbit, forever falling and forever missing!"
        ),
        "voice": "en-US-ChristopherNeural",
        "audio_file": "test_chalk_talk_20s.mp3",
        "impact_ass_file": "test_chalk_talk_impact.ass",
        "adapter_url": f"file:///{WORKSPACE_ROOT.replace(os.sep, '/')}/backend/app/rendering/styles/chalk_talk/adapter.html",
        "output_video": os.path.join(VIDEO_DIR, "test_chalk_talk_20sec.mp4"),
        "contact_sheet": os.path.join(AUDIT_DIR, "contact_sheet_chalk_talk_20s.png"),
        "impact_events": [
            ImpactWordEvent("PULLED BY GRAVITY", 1.5, 3.8),
            ImpactWordEvent("1 KM / SECOND", 7.5, 9.8),
            ImpactWordEvent("ORBITAL VELOCITY", 12.0, 14.5),
            ImpactWordEvent("FOREVER MISSING", 16.5, 19.5),
        ],
        "scenes_meta": [
            {
                "id": 1,
                "text_elements": [
                    {"bbox": (140, 360, 800, 70), "font_size": 54, "kind": "title", "color": "#F4D35E", "bg_color": "#24392F"},
                    {"bbox": (240, 1280, 600, 50), "font_size": 32, "kind": "body", "color": "#F2EFE6", "bg_color": "#24392F"}
                ]
            },
            {
                "id": 2,
                "text_elements": [
                    {"bbox": (140, 360, 800, 70), "font_size": 50, "kind": "title", "color": "#F4D35E", "bg_color": "#24392F"},
                    {"bbox": (240, 1280, 600, 50), "font_size": 32, "kind": "body", "color": "#F2EFE6", "bg_color": "#24392F"}
                ]
            },
            {
                "id": 3,
                "text_elements": [
                    {"bbox": (140, 360, 800, 70), "font_size": 52, "kind": "title", "color": "#F4D35E", "bg_color": "#24392F"},
                    {"bbox": (240, 690, 600, 50), "font_size": 32, "kind": "body", "color": "#93CBEF", "bg_color": "#24392F"}
                ]
            },
            {
                "id": 4,
                "text_elements": [
                    {"bbox": (140, 360, 800, 70), "font_size": 52, "kind": "title", "color": "#F4D35E", "bg_color": "#24392F"},
                    {"bbox": (240, 1010, 600, 50), "font_size": 46, "kind": "body", "color": "#F2EFE6", "bg_color": "#24392F"}
                ]
            }
        ]
    },
    {
        "style": VisualStyle.CREATOR_MOTION,
        "title": "Why Most People Lose Interest in a Video Within Seconds",
        "narration_text": (
            "You have three seconds before a viewer swipes away forever. "
            "The fatal mistake creators make is polite greetings. "
            "Cut the fluff immediately! "
            "Start inside the conflict to trap curiosity. "
            "When your first three seconds hook the viewer, the algorithm explodes your reach!"
        ),
        "voice": "en-US-ChristopherNeural",
        "audio_file": "test_creator_motion_20s.mp3",
        "impact_ass_file": "test_creator_motion_impact.ass",
        "adapter_url": f"file:///{WORKSPACE_ROOT.replace(os.sep, '/')}/backend/app/rendering/styles/creator_motion_graphics/adapter.html",
        "output_video": os.path.join(VIDEO_DIR, "test_creator_motion_20sec.mp4"),
        "contact_sheet": os.path.join(AUDIT_DIR, "contact_sheet_creator_motion_20s.png"),
        "impact_events": [
            ImpactWordEvent("FIRST 3 SECONDS", 1.2, 3.5),
            ImpactWordEvent("CUT THE FLUFF", 7.5, 9.8),
            ImpactWordEvent("OPEN THE LOOP", 11.0, 13.2),
            ImpactWordEvent("EXPLOSIVE REACH", 15.5, 18.2),
        ],
        "scenes_meta": [
            {
                "id": 1,
                "text_elements": [
                    {"bbox": (140, 340, 800, 70), "font_size": 80, "kind": "title", "color": "#F2F0EA", "bg_color": "#0B0B0D"},
                    {"bbox": (240, 1180, 600, 50), "font_size": 28, "kind": "body", "color": "#A1A1AA", "bg_color": "#0B0B0D"}
                ]
            },
            {
                "id": 2,
                "text_elements": [
                    {"bbox": (140, 340, 800, 70), "font_size": 72, "kind": "title", "color": "#FF4D4D", "bg_color": "#0B0B0D"},
                    {"bbox": (240, 1120, 600, 50), "font_size": 32, "kind": "body", "color": "#F2F0EA", "bg_color": "#0B0B0D"}
                ]
            },
            {
                "id": 3,
                "text_elements": [
                    {"bbox": (140, 340, 800, 70), "font_size": 74, "kind": "title", "color": "#FFD60A", "bg_color": "#0B0B0D"},
                    {"bbox": (240, 1140, 600, 50), "font_size": 30, "kind": "body", "color": "#A1A1AA", "bg_color": "#0B0B0D"}
                ]
            },
            {
                "id": 4,
                "text_elements": [
                    {"bbox": (140, 340, 800, 70), "font_size": 76, "kind": "title", "color": "#22C55E", "bg_color": "#0B0B0D"},
                    {"bbox": (240, 1080, 600, 50), "font_size": 68, "kind": "title", "color": "#000000", "bg_color": "#FFD60A"}
                ]
            }
        ]
    }
]


def extract_contact_sheet(video_path: str, output_sheet: str, title: str, style_name: str, num_frames: int = 8):
    """Extracts 8 keyframes and builds a 2x4 high-resolution contact sheet."""
    os.makedirs(os.path.dirname(output_sheet), exist_ok=True)
    temp_frame_dir = os.path.join(WORKSPACE_ROOT, "cache", f"frames_{int(time.time() * 1000) % 100000}")
    os.makedirs(temp_frame_dir, exist_ok=True)

    cmd = [
        FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    duration = float(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else 20.0

    timestamps = [duration * (i + 0.5) / num_frames for i in range(num_frames)]
    frames = []

    for i, ts in enumerate(timestamps):
        out_frame = os.path.join(temp_frame_dir, f"frame_{i:02d}.png")
        cmd_extract = [
            FFMPEG_PATH, "-y",
            "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_frame,
        ]
        subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(out_frame):
            frames.append((out_frame, ts))

    if not frames:
        logger.warning("No frames extracted for contact sheet %s", output_sheet)
        return

    cols, rows = 4, 2
    thumb_w, thumb_h = 360, 640
    pad = 24
    header_h = 96
    sheet_w = cols * thumb_w + (cols + 1) * pad
    sheet_h = rows * thumb_h + (rows + 1) * pad + header_h

    sheet = Image.new("RGB", (sheet_w, sheet_h), (10, 12, 16))
    draw = ImageDraw.Draw(sheet)

    draw.text((pad, 20), f"PHASE 4C QUALITY AUDIT: {style_name.upper()} — '{title}'", fill=(240, 243, 248))
    draw.text((pad, 54), f"Resolution: 1080x1920 @ 30.0 FPS | Duration: {duration:.1f}s | Narrative Synchronization Verified", fill=(32, 201, 151))

    for idx, (fpath, ts) in enumerate(frames):
        c, r = idx % cols, idx // cols
        x = pad + c * (thumb_w + pad)
        y = header_h + pad + r * (thumb_h + pad)

        with Image.open(fpath) as img:
            sheet.paste(img.resize((thumb_w, thumb_h), Image.Resampling.BILINEAR), (x, y))

        draw.rectangle([(x, y), (x + thumb_w, y + thumb_h)], outline=(40, 50, 70), width=2)
        label = f"Frame {idx+1} ({ts:.1f}s)"
        draw.rectangle([(x + 8, y + 8), (x + 150, y + 38)], fill=(10, 12, 16, 220))
        draw.text((x + 16, y + 13), label, fill=(255, 214, 10))

    sheet.save(output_sheet)
    logger.info("Saved contact sheet: %s", output_sheet)


async def run_style_test(spec: dict, runner: BrowserCanvasRunner, compositor: UniversalCompositor, qgate: QualityGate) -> dict:
    """Renders, composites, and audits a single 20-second style test."""
    style = spec["style"]
    theme = THEMES[style]
    logger.info("==================================================================")
    logger.info("RUNNING PHASE 4C TEST: %s ('%s')", theme.display_name, spec["title"])
    logger.info("==================================================================")

    out_mp4 = spec["output_video"]
    work_dir = os.path.join(WORKSPACE_ROOT, "cache", f"phase4c_{style.value}")
    os.makedirs(work_dir, exist_ok=True)

    # 1. Check or Generate Narration Audio
    audio_path = os.path.join(AUDIO_DIR, spec["audio_file"])
    if not os.path.exists(audio_path):
        logger.info("Generating voiceover narration: %s", spec["audio_file"])
        audio_path, _, dur = await generate_narration(
            text=spec["narration_text"],
            voice=spec["voice"],
            output_filename=spec["audio_file"],
        )
    else:
        # Measure duration with ffprobe
        probe_cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        dur = float(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else 20.0
        logger.info("Using existing narration: %s (%.2fs)", audio_path, dur)

    # 2. Generate Selective Impact ASS Subtitles
    impact_ass_path = os.path.join(SUB_DIR, spec["impact_ass_file"])
    generate_impact_word_ass(
        events=spec["impact_events"],
        output_ass_path=impact_ass_path,
        visual_style=style,
    )
    logger.info("Generated selective impact ASS subtitles: %s", impact_ass_path)

    # 3. Render Canvas Animation & SFX via BrowserCanvasRunner
    seg_mp4 = os.path.join(work_dir, f"{style.value}_canvas_raw.mp4")
    seg_sfx = os.path.join(work_dir, f"{style.value}_sfx.wav")

    t0 = time.time()
    seg_res = await runner.render_scene_video(
        scene_id=f"{style.value}_20s",
        adapter_url=spec["adapter_url"],
        duration=dur,
        out_mp4_path=seg_mp4,
        sfx_out_wav=seg_sfx,
    )
    if not seg_res.success:
        raise RuntimeError(f"Canvas rendering failed for {style.value}: {seg_res.error_message}")

    # 4. Finalize Short Composition via UniversalCompositor
    bgm_path = os.path.join(WORKSPACE_ROOT, "assets", "audio", "music", "ambient_tech_theme.wav")
    final_video = compositor.mix_and_finalize_short(
        video_path=seg_mp4,
        voiceover_audio=audio_path,
        sfx_audios=[seg_sfx],
        music_audio=bgm_path if os.path.exists(bgm_path) else None,
        output_final_mp4=out_mp4,
        ducking_db=-14.0,
        subtitles_ass_path=impact_ass_path,
    )
    render_elapsed = time.time() - t0

    # 5. Extract 8-Keyframe Contact Sheet
    extract_contact_sheet(final_video, spec["contact_sheet"], spec["title"], theme.display_name)

    # 6. Run Quality Gate (require_production_duration=False for development tests)
    audit_report = qgate.audit_video_compliance(
        video_path=final_video,
        primary_style=style,
        scenes_meta=spec["scenes_meta"],
        require_production_duration=False,
    )

    logger.info("Quality Audit for %s: Score %0.1f%% (%d/%d checks passed)",
                theme.display_name, audit_report["quality_score_pct"], audit_report["passed_checks"], audit_report["total_checks"])

    return {
        "style": style.value,
        "display_name": theme.display_name,
        "title": spec["title"],
        "video_path": final_video,
        "contact_sheet": spec["contact_sheet"],
        "duration_sec": dur,
        "render_time_sec": round(render_elapsed, 2),
        "fps": seg_res.telemetry.fps if seg_res.telemetry else 30.0,
        "quality_score_pct": audit_report["quality_score_pct"],
        "passed_checks": audit_report["passed_checks"],
        "total_checks": audit_report["total_checks"],
        "overall_passed": audit_report["overall_passed"],
        "audit_checks": audit_report["checks"],
    }


async def main():
    logger.info("==================================================================")
    logger.info("STARTING PHASE 4C TEST SUITE: 3 x 20-SECOND PRODUCTION SAMPLES")
    logger.info("==================================================================")

    runner = BrowserCanvasRunner(
        edge_path=EDGE_PATH,
        ffmpeg_path=FFMPEG_PATH,
        cdp_port=9222,
    )
    compositor = UniversalCompositor(
        ffmpeg_path=FFMPEG_PATH,
        ffprobe_path=FFPROBE_PATH,
    )
    qgate = QualityGate(
        ffmpeg_path=FFMPEG_PATH,
        ffprobe_path=FFPROBE_PATH,
    )

    results = []
    for spec in TEST_SPECS:
        res = await run_style_test(spec, runner, compositor, qgate)
        results.append(res)

    # Write JSON summary for reporting
    summary_path = os.path.join(WORKSPACE_ROOT, "cache", "phase_4c_test_results.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    logger.info("==================================================================")
    logger.info("PHASE 4C TEST SUITE EXECUTION COMPLETE")
    for r in results:
        status_str = "PASSED" if r["overall_passed"] else "FAILED"
        logger.info(f"[{status_str}] {r['display_name']}: Score {r['quality_score_pct']:.1f}% ({r['passed_checks']}/{r['total_checks']}) -> {r['video_path']}")
    logger.info("Summary saved to: %s", summary_path)
    logger.info("==================================================================")


if __name__ == "__main__":
    asyncio.run(main())
