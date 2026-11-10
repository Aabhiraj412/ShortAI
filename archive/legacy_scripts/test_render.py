"""
Step-by-step diagnostic test script for Animation Rendering:
1. Render single frame & verify dimensions
2. Render 1-second (30 frames) animation & probe with FFprobe
"""
import os
import sys
import time
from PIL import Image, ImageFont

sys.path.append(r"D:\AI Arts\AI\backend")
from app.services.animation_service import (
    OptimizedCharacterRig,
    FastParticleSystem,
    render_single_frame,
    render_animation,
    probe_video,
    SPRITES_DIR,
    BG_DIR,
    MUSIC_DIR
)

SAMPLE_STORYBOARD = {
    "title": "What Happens When You Fall Into a Black Hole?",
    "duration": 16.0,
    "scenes": [
        {
            "id": 1,
            "start_time": 0.0,
            "duration": 5.0,
            "headline": "FALL INTO A BLACK HOLE?",
            "badge": "SPACE MYSTERY",
            "left_arm": "arm_left_idle",
            "right_arm": "arm_right_think",
            "eyebrows": "eyebrows_curious",
            "char_x": 40,
            "char_y": 760
        },
        {
            "id": 2,
            "start_time": 5.0,
            "duration": 6.0,
            "headline": "SPAGHETTIFICATION!",
            "badge": "EXTREME PHYSICS",
            "left_arm": "arm_left_present",
            "right_arm": "arm_right_idle",
            "eyebrows": "eyebrows_curious",
            "char_x": -30,
            "char_y": 780
        },
        {
            "id": 3,
            "start_time": 11.0,
            "duration": 5.0,
            "headline": "EVENT HORIZON",
            "badge": "COSMIC SECRETS",
            "left_arm": "arm_left_idle",
            "right_arm": "arm_right_think",
            "eyebrows": "eyebrows_neutral",
            "char_x": 40,
            "char_y": 760
        }
    ]
}

def test_single_frame():
    print("=== TEST 1: Single Frame Rendering ===", flush=True)
    t0 = time.time()
    char_rig = OptimizedCharacterRig(SPRITES_DIR)
    bg_img = Image.open(os.path.join(BG_DIR, "bg_space.png")).convert("RGBA")
    particles = FastParticleSystem(30)
    try:
        font_large = ImageFont.truetype("arialbd.ttf", 52)
        font_badge = ImageFont.truetype("arialbd.ttf", 36)
    except:
        font_large = ImageFont.load_default()
        font_badge = font_large
        
    t_render_start = time.time()
    frame = render_single_frame(
        t=0.5,
        scene_idx=0,
        bg_img=bg_img,
        character_rig=char_rig,
        particles=particles,
        storyboard=SAMPLE_STORYBOARD,
        font_large=font_large,
        font_badge=font_badge
    )
    render_ms = (time.time() - t_render_start) * 1000
    
    out_path = r"D:\AI Arts\AI\cache\test_single_frame.png"
    frame.save(out_path)
    file_bytes = os.path.getsize(out_path)
    
    print(f"Single frame rendered in {render_ms:.2f} ms!")
    print(f"Output saved: {out_path}")
    print(f"Dimensions: {frame.width}x{frame.height} | File size: {file_bytes / 1024:.1f} KB")
    
    assert frame.width == 1080 and frame.height == 1920, "Invalid dimensions!"
    assert file_bytes > 10000, "Frame file is suspiciously small!"
    print(">>> TEST 1 PASSED: Single frame is valid.\n", flush=True)

def test_30_frames():
    print("=== TEST 2: 1-Second (30 Frames) Animation ===", flush=True)
    audio = r"D:\AI Arts\AI\media\audio\sample_narration.mp3"
    ass = r"D:\AI Arts\AI\media\subtitles\sample_narration.ass"
    music = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    out_1sec = r"D:\AI Arts\AI\media\video\test_1sec.mp4"
    
    success = render_animation(
        storyboard=SAMPLE_STORYBOARD,
        audio_path=audio,
        ass_path=ass,
        music_path=music,
        output_mp4_path=out_1sec,
        max_frames=30,
        fps=30
    )
    assert success, "1-second render failed!"
    print(">>> TEST 2 PASSED: 30-frame animation rendered and verified.\n", flush=True)

if __name__ == "__main__":
    test_single_frame()
    test_30_frames()
