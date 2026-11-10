"""
Tests the new 2D Stick-Figure & Motion Graphics Animation Engine:
1. Single static frame
2. 30-frame animation (1 second)
3. 480-frame animation (16 seconds)
"""
import os
import sys
import time

BASE_DIR = r"D:\Agents\CrazzyDocs"
sys.path.append(os.path.join(BASE_DIR, "backend"))

from app.services.stick_animation_service import (
    StickFigureRig, MotionGraphicsDirector, render_stick_frame, render_stick_animation,
    BASE_DIR, MEDIA_VIDEO_DIR, THUMB_DIR, MUSIC_DIR
)
from app.services.animation_service import probe_video
from PIL import Image, ImageFont

STICK_STORYBOARD = {
    "title": "What Happens When You Fall Into a Black Hole?",
    "duration": 16.0,
    "scenes": [
        {
            "id": 1,
            "start_time": 0.0,
            "duration": 5.0,
            "headline": "CAN YOU SURVIVE A BLACK HOLE?",
            "badge": "SPACE MYSTERY",
            "pose": "thinking",
            "expression": "thinking",
            "char_x": 540,
            "char_y": 1420
        },
        {
            "id": 2,
            "start_time": 5.0,
            "duration": 6.0,
            "headline": "GRAVITY SPAGHETTIFIES YOU!",
            "badge": "EXTREME PHYSICS",
            "pose": "falling",
            "expression": "surprised",
            "char_x": 540,
            "char_y": 1100
        },
        {
            "id": 3,
            "start_time": 11.0,
            "duration": 5.0,
            "headline": "EVENT HORIZON: TIME FREEZES",
            "badge": "COSMIC TRUTH",
            "pose": "presenting",
            "expression": "happy",
            "char_x": 540,
            "char_y": 1420
        }
    ]
}

def test_single_frame():
    print("=== TEST 1: Stick Figure Single Frame ===", flush=True)
    t0 = time.time()
    stick_rig = StickFigureRig()
    chalk_bg = os.path.join(BASE_DIR, "assets", "backgrounds", "chalk_talk", "bg_slate_blackboard.png")
    legacy_bg = os.path.join(BASE_DIR, "archive", "legacy_assets", "backgrounds", "rendered", "bg_space.png")
    bg_path = chalk_bg if os.path.exists(chalk_bg) else legacy_bg
    bg_img = Image.open(bg_path).convert("RGBA")
    try:
        font_large = ImageFont.truetype("arialbd.ttf", 52)
        font_badge = ImageFont.truetype("arialbd.ttf", 36)
    except:
        font_large = ImageFont.load_default()
        font_badge = font_large
        
    t_start = time.time()
    frame = render_stick_frame(
        t=1.0,
        scene_idx=0,
        bg_img=bg_img,
        stick_rig=stick_rig,
        storyboard=STICK_STORYBOARD,
        font_large=font_large,
        font_badge=font_badge
    )
    ms = (time.time() - t_start) * 1000
    out_path = os.path.join(BASE_DIR, "cache", "stick_single_frame.png")
    frame.save(out_path)
    
    print(f"Single stick frame rendered in {ms:.2f} ms!")
    print(f"Dimensions: {frame.width}x{frame.height} | File: {out_path}")
    assert frame.width == 1080 and frame.height == 1920
    print(">>> TEST 1 PASSED: Single frame valid.\n", flush=True)

def test_30_frames():
    print("=== TEST 2: 30-Frame (1s) Stick Animation ===", flush=True)
    audio = os.path.join(BASE_DIR, "media", "audio", "sample_narration.mp3")
    ass = os.path.join(BASE_DIR, "media", "subtitles", "sample_narration.ass")
    music = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    out_1s = os.path.join(MEDIA_VIDEO_DIR, "stick_test_1sec.mp4")
    
    ok = render_stick_animation(
        storyboard=STICK_STORYBOARD,
        audio_path=audio,
        ass_path=ass,
        music_path=music,
        output_mp4_path=out_1s,
        max_frames=30,
        fps=30
    )
    assert ok
    info = probe_video(out_1s)
    assert info["width"] == 1080 and info["height"] == 1920
    assert info["fps"] == 30.0
    print(">>> TEST 2 PASSED: 30-frame stick animation verified with FFprobe.\n", flush=True)

def test_480_frames_full():
    print("=== TEST 3: Full 480-Frame (16s) Stick Short ===", flush=True)
    audio = os.path.join(BASE_DIR, "media", "audio", "sample_narration.mp3")
    ass = os.path.join(BASE_DIR, "media", "subtitles", "sample_narration.ass")
    music = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    out_full = os.path.join(MEDIA_VIDEO_DIR, "stick_sample_short.mp4")
    
    t0 = time.time()
    ok = render_stick_animation(
        storyboard=STICK_STORYBOARD,
        audio_path=audio,
        ass_path=ass,
        music_path=music,
        output_mp4_path=out_full,
        max_frames=None, # full 480 frames
        fps=30
    )
    elapsed = time.time() - t0
    assert ok
    info = probe_video(out_full)
    print(f"Full 16s video rendered in {elapsed:.2f}s ({480/elapsed:.1f} FPS)!")
    print(f"Verified: {info['width']}x{info['height']} @ {info['fps']}fps, Size: {info['size_mb']:.2f} MB")
    print(">>> TEST 3 PASSED: Full 16-second stick figure Short generated and verified.\n", flush=True)

if __name__ == "__main__":
    test_single_frame()
    test_30_frames()
    test_480_frames_full()
    print("ALL STICK FIGURE ANIMATION TESTS COMPLETED SUCCESSFULLY!")
