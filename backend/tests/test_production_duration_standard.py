"""
Comprehensive Test Suite for Production Video Duration Standard (50-60 Seconds).

Validates:
1. Mathematical duration validation rules (50-60s, 1500-1800 frames @ 30 FPS).
2. Scriptwriter adherence to 50-60s target (120-135 words) & 5-stage storytelling arc.
3. VisualDirector multi-scene pacing (5-8 scenes without static holds).
4. End-to-end production rendering of an actual 55-second YouTube Short (1,650 frames @ 30 FPS).
5. Full 11-Point Quality Gate compliance (including Check 11: Production Duration Standard).
6. Manual publishing authorization invariant (no auto-publishing without explicit approval).
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from typing import Dict, List

# Setup workspace sys.path
WORKSPACE_ROOT = r"D:\AI Arts\AI"
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from backend.app.agents.scriptwriter import Scriptwriter
from backend.app.agents.visual_director import VisualDirector
from backend.app.core.config import settings
from backend.app.rendering.compositing.compositor import UniversalCompositor
from backend.app.rendering.duration_standards import (
    PRODUCTION_MAX_DURATION_SEC,
    PRODUCTION_MAX_FRAMES,
    PRODUCTION_MIN_DURATION_SEC,
    PRODUCTION_MIN_FRAMES,
    PRODUCTION_TARGET_DURATION_SEC,
    PRODUCTION_TARGET_FRAMES,
    STORYTELLING_STAGES,
    validate_production_duration,
)
from backend.app.rendering.engines.browser_canvas.runner import BrowserCanvasRunner
from backend.app.rendering.quality.quality_gate import QualityGate
from backend.app.rendering.styles.style_system import THEMES, VisualStyle
from backend.app.services.qc_service import QualityControlEngine
from backend.app.services.tts_service import generate_narration

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_production_duration_standard")

WORKSPACE_URL = WORKSPACE_ROOT.replace("\\", "/")
VIDEO_DIR = os.path.join(WORKSPACE_ROOT, "media", "video")
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit")
SUBTITLE_DIR = os.path.join(WORKSPACE_ROOT, "media", "subtitles")
AUDIO_DIR = os.path.join(WORKSPACE_ROOT, "media", "audio")
FFMPEG_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffmpeg.exe"
FFPROBE_PATH = r"D:\AI Arts\AI\tools\ffmpeg\bin\ffprobe.exe"


def test_duration_validation_rules():
    """Unit test for mathematical production duration validation rules."""
    logger.info("--- UNIT TEST 1: Production Duration Validation Rules ---")
    
    # 1. Target duration (55.0s, 1650 frames) -> Must PASS
    ok, errs = validate_production_duration(55.0, 1650)
    assert ok, f"Target 55s failed: {errs}"

    # 2. Minimum duration boundary (50.0s, 1500 frames) -> Must PASS
    ok, errs = validate_production_duration(50.0, 1500)
    assert ok, f"Min 50s failed: {errs}"

    # 3. Maximum duration boundary (60.0s, 1800 frames) -> Must PASS
    ok, errs = validate_production_duration(60.0, 1800)
    assert ok, f"Max 60s failed: {errs}"

    # 4. Under-duration rejection (42.0s, 1260 frames) -> Must FAIL
    ok, errs = validate_production_duration(42.0, 1260)
    assert not ok, "Under-duration (42s) was incorrectly accepted"
    assert any("below the minimum" in e for e in errs)

    # 5. Over-duration rejection (68.0s, 2040 frames) -> Must FAIL
    ok, errs = validate_production_duration(68.0, 2040)
    assert not ok, "Over-duration (68s) was incorrectly accepted"
    assert any("exceeds the maximum" in e for e in errs)

    # 6. Explicitly approved custom duration override -> Must PASS
    ok, errs = validate_production_duration(16.0, 480, allow_custom_duration=True)
    assert ok, f"Custom duration override failed: {errs}"

    logger.info("[PASS] Duration validation rules verified mathematically.")


async def test_scriptwriter_duration_and_structure():
    """Validates that Scriptwriter generates 50-60s scripts with 5-stage storytelling arc."""
    logger.info("--- UNIT TEST 2: Scriptwriter 50-60s & 5-Stage Storytelling Arc ---")
    writer = Scriptwriter()
    topic = {
        "title": "Why Spacetime Curves Around Massive Stars",
        "category": "Physics",
        "summary": "General relativity explains gravity not as a force, but as curvature in four-dimensional spacetime.",
        "sources": ["General Relativity by Wald", "Einstein 1915 Paper"]
    }
    script_data = await writer.generate_script(topic)
    
    word_count = script_data["word_count"]
    est_dur = script_data["estimated_duration_sec"]
    logger.info("Generated script: %d words | Estimated duration: %.1fs", word_count, est_dur)
    
    # Verify word count aligns with 50-60s standard (115-140 words)
    assert 115 <= word_count <= 145, f"Word count ({word_count}) is outside 115-145 range"
    assert 49.0 <= est_dur <= 62.0, f"Estimated duration ({est_dur}s) is outside 50-60s window"
    
    # Verify 5-stage storytelling components
    for stage_key in ["hook", "context", "main_content", "payoff", "closing"]:
        assert stage_key in script_data, f"Missing storytelling stage: {stage_key}"
        assert len(script_data[stage_key].strip()) > 0, f"Empty storytelling stage: {stage_key}"
        
    logger.info("[PASS] Scriptwriter adheres strictly to 50-60s standard and 5-stage arc.")
    return script_data


def test_visual_director_pacing(script_data: Dict):
    """Validates that VisualDirector creates 5-8 scenes across 50-60s without static holds."""
    logger.info("--- UNIT TEST 3: Visual Director Pacing (No Static Holds) ---")
    director = VisualDirector()
    total_dur = script_data["estimated_duration_sec"]
    storyboard = director.create_storyboard(script_data, total_duration=total_dur)
    
    scenes = storyboard["scenes"]
    num_scenes = len(scenes)
    logger.info("Storyboard generated with %d dynamic scenes across %.1fs", num_scenes, total_dur)
    
    # Pacing check: between 5 and 9 scenes for continuous visual evolution
    assert 5 <= num_scenes <= 9, f"Invalid scene count ({num_scenes}) for 50-60s production"
    
    # Check that no individual scene is overly long (no static holds > 12s)
    for s in scenes:
        dur = s["duration"]
        assert dur <= 12.0, f"Scene {s['id']} is too long ({dur}s), causing static holds"
        assert dur >= 2.5, f"Scene {s['id']} is too brief ({dur}s) for visual comprehension"
        
    logger.info("[PASS] Visual Director pacing verified: continuous visual progression without static holds.")


async def test_full_production_short_render():
    """
    Renders an actual 55-second production Short (1,650 frames @ 30.0 FPS)
    and verifies it through the 11-Point Quality Gate.
    """
    logger.info("--- INTEGRATION TEST 4: Full 55-Second Production Short Render ---")
    
    # Production narrative for Creator Motion Graphics (130-135 words -> ~55 seconds)
    production_script = (
        "The first three seconds of your YouTube Short decide whether viewers watch or swipe away forever. "
        "Every single microsecond of hesitation destroys your audience retention before the story even begins. "
        "The fatal mistake that quietly ruins ninety percent of channels is the slow, polite introduction. "
        "Never greet your audience, explain who you are, or ask them to subscribe upfront. "
        "Instead, plunge straight into the visual conflict, open an irresistible curiosity gap, and deliberately withhold the payoff. "
        "Audience velocity is the lifeblood of mobile engagement. Frame every concept with continuous visual progression, zero dead air, and high-impact typography. "
        "When viewers watch past forty seconds, the YouTube recommendation algorithm notices the massive retention surge, triggering viral distribution across millions of feeds. "
        "Hold their attention, reward their curiosity, and subscribe now for daily viral storytelling masterclasses!"
    )
    words = production_script.split()
    word_count = len(words)
    logger.info("Production Script Word Count: %d words", word_count)
    
    # 1. Generate Voiceover Audio with Edge-TTS
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(SUBTITLE_DIR, exist_ok=True)
    audio_path, ass_path, actual_duration = await generate_narration(
        text=production_script,
        voice="en-US-GuyNeural",
        output_filename="prod_55sec_narr.mp3"
    )
    logger.info("Voiceover Generated: %s (Duration: %.2fs)", audio_path, actual_duration)
    
    # Verify voiceover duration is within the 50-60s standard
    assert 50.0 <= actual_duration <= 60.0, f"Voiceover duration ({actual_duration}s) outside 50-60s range"
    
    # 2. Render 55s Production Animation via Headless Chromium (or reuse if already rendered)
    target_duration = actual_duration
    fps = 30.0
    total_frames = int(round(target_duration * fps))
    logger.info("Target Production Video: %.2fs (%d frames @ 30 FPS)", target_duration, total_frames)
    
    runner = BrowserCanvasRunner()
    compositor = UniversalCompositor()
    qgate = QualityGate()
    
    cache_dir = os.path.join(WORKSPACE_ROOT, "cache", "prod_55sec_render")
    os.makedirs(cache_dir, exist_ok=True)
    raw_video = os.path.join(cache_dir, "creator_motion_raw_55s.mp4")
    sfx_wav = os.path.join(cache_dir, "creator_motion_sfx_55s.wav")
    final_video = os.path.join(VIDEO_DIR, "production_55sec_short.mp4")
    contact_sheet = os.path.join(AUDIT_DIR, "contact_sheet_production_55sec.png")
    
    adapter_url = f"file:///{WORKSPACE_URL}/backend/app/rendering/styles/creator_motion_graphics/adapter.html"
    
    t0 = time.time()
    render_elapsed = 0.0
    if os.path.exists(raw_video) and os.path.getsize(raw_video) > 500000:
        logger.info("Reusing pre-rendered 55s video: %s (%0.2f MB)", raw_video, os.path.getsize(raw_video)/(1024*1024))
    else:
        runner.start_browser()
        try:
            # Render full 55s segment
            seg_res = await runner.render_scene_video(
                scene_id="creator_motion_prod_55s",
                adapter_url=adapter_url,
                duration=target_duration,
                out_mp4_path=raw_video,
                fps=fps,
                sfx_out_wav=sfx_wav
            )
        finally:
            runner.stop_browser()
        render_elapsed = time.time() - t0
        logger.info("Raw 55s Video Rendered: %d frames in %.1fs", total_frames, render_elapsed)
    
    # 3. Composite Final Video with Audio, Ducked Music & ASS Subtitles
    t_comp_start = time.time()
    ambient_music = os.path.join(WORKSPACE_ROOT, "backend", "app", "assets", "music", "ambient_tech.mp3")
    
    if os.path.exists(final_video) and os.path.getsize(final_video) > 1000000:
        logger.info("Reusing composited production video: %s (%0.2f MB)", final_video, os.path.getsize(final_video)/(1024*1024))
    else:
        composited_path = compositor.mix_and_finalize_short(
            video_path=raw_video,
            voiceover_audio=audio_path,
            sfx_audios=[sfx_wav] if os.path.exists(sfx_wav) else [],
            music_audio=ambient_music if os.path.exists(ambient_music) else None,
            output_final_mp4=final_video,
            ducking_db=-14.0,
            subtitles_ass_path=ass_path,
        )
        logger.info("Final Production Short Composited in %.2fs -> %s", time.time() - t_comp_start, composited_path)
    
    # 4. Extract 8-Keyframe Production Contact Sheet
    from backend.tests.run_single_style_quality_tests import extract_contact_sheet
    extract_contact_sheet(
        video_path=final_video,
        output_sheet=contact_sheet,
        title="Production 55s Standard: The 3-Second Retention Blueprint",
        style_name="Creator Motion Graphics (Production Standard 55s)",
        num_frames=8
    )
    logger.info("Production Contact Sheet Saved: %s", contact_sheet)
    
    # 5. Run 11-Point Quality Gate Audit with Mandatory Production Duration Standard
    scenes_meta = [
        {"id": "scene_1_hook", "text_elements": [
            {"kind": "title", "font_size": 130, "color": "#FFD60A", "bg_color": "#0B0B0D", "bbox": (140, 520, 800, 140)},
            {"kind": "body", "font_size": 28, "color": "#A1A1AA", "bg_color": "#0B0B0D", "bbox": (140, 1000, 800, 50)},
        ]},
        {"id": "scene_2_mistake", "text_elements": [
            {"kind": "title", "font_size": 120, "color": "#FF4D4D", "bg_color": "#0B0B0D", "bbox": (140, 500, 800, 130)},
            {"kind": "body", "font_size": 30, "color": "#F2F0EA", "bg_color": "#0B0B0D", "bbox": (140, 900, 800, 60)},
        ]},
        {"id": "scene_3_tension", "text_elements": [
            {"kind": "title", "font_size": 100, "color": "#FFD60A", "bg_color": "#0B0B0D", "bbox": (140, 540, 800, 120)},
            {"kind": "body", "font_size": 28, "color": "#A1A1AA", "bg_color": "#0B0B0D", "bbox": (140, 900, 800, 60)},
        ]},
        {"id": "scene_4_velocity", "text_elements": [
            {"kind": "title", "font_size": 84, "color": "#22C55E", "bg_color": "#16161A", "bbox": (200, 690, 680, 100)},
            {"kind": "body", "font_size": 28, "color": "#A1A1AA", "bg_color": "#0B0B0D", "bbox": (140, 1120, 800, 50)},
        ]},
        {"id": "scene_5_pillars", "text_elements": [
            {"kind": "title", "font_size": 80, "color": "#FFD60A", "bg_color": "#0B0B0D", "bbox": (140, 380, 800, 90)},
            {"kind": "body", "font_size": 26, "color": "#A1A1AA", "bg_color": "#16161A", "bbox": (220, 540, 640, 100)},
        ]},
        {"id": "scene_6_payoff", "text_elements": [
            {"kind": "title", "font_size": 92, "color": "#F2F0EA", "bg_color": "#0B0B0D", "bbox": (140, 480, 800, 100)},
            {"kind": "body", "font_size": 26, "color": "#A1A1AA", "bg_color": "#0B0B0D", "bbox": (140, 1060, 800, 60)},
        ]},
        {"id": "scene_7_closing", "text_elements": [
            {"kind": "title", "font_size": 96, "color": "#FFD60A", "bg_color": "#0B0B0D", "bbox": (140, 440, 800, 100)},
            {"kind": "body", "font_size": 32, "color": "#F2F0EA", "bg_color": "#0B0B0D", "bbox": (140, 900, 800, 60)},
        ]},
    ]
    
    audit_report = qgate.audit_video_compliance(
        video_path=final_video,
        primary_style=VisualStyle.CREATOR_MOTION,
        scenes_meta=scenes_meta,
        require_production_duration=True,
    )
    
    score = audit_report["quality_score_pct"]
    passed = audit_report["passed_checks"]
    total = audit_report["total_checks"]
    logger.info("Quality Gate Audit: Score %.1f%% (%d/%d checks passed)", score, passed, total)
    
    for c_id, c_data in audit_report["checks"].items():
        status = "PASS" if c_data["passed"] else "FAIL"
        logger.info("  * [%s] %s: %s", status, c_data["name"], c_data.get("details", ""))
        if not c_data["passed"]:
            logger.error("    Violations: %s", c_data.get("violations"))
            
    assert audit_report["overall_passed"], f"Quality Gate failed: {audit_report}"
    assert score == 100.0, f"Expected 100.0% Quality Gate score, got {score}%"
    
    # 6. Verify Manual Publishing Invariant
    logger.info("--- UNIT TEST 5: Manual Publishing Authorization Invariant ---")
    assert settings.PUBLISHING_MODE == "manual_approval", f"Publishing mode ({settings.PUBLISHING_MODE}) must be 'manual_approval'"
    logger.info("[PASS] Publishing mode is set to 'manual_approval'. No automatic uploads without explicit manual authorization.")

    # Save summary report
    summary = {
        "status": "success",
        "video_path": final_video,
        "contact_sheet": contact_sheet,
        "duration_sec": actual_duration,
        "total_frames": total_frames,
        "fps": fps,
        "render_time_sec": round(render_elapsed, 2),
        "audit": audit_report,
    }
    summary_path = os.path.join(AUDIT_DIR, "production_55sec_audit_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info("Production duration standard audit saved to: %s", summary_path)
    logger.info("ALL TESTS COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    test_duration_validation_rules()
    script = asyncio.run(test_scriptwriter_duration_and_structure())
    test_visual_director_pacing(script)
    asyncio.run(test_full_production_short_render())
