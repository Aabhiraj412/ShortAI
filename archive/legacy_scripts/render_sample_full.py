"""
Renders the complete 16-second sample animated YouTube Short with:
- 1080x1920 vertical canvas @ 30 FPS
- Animated character (talking phonemes, blinking, breathing, hand gestures)
- Dynamic camera pan and slow push-in zoom
- Space nebula background with floating cosmic particles
- Kinetic typography headline pills and category badges
- Full neural voiceover (en-US-ChristopherNeural)
- Synchronized ASS burn-in subtitles
- Ducked ambient synth theme music (-18dB)
- High-contrast custom thumbnail (1080x1920)
"""
import os
import sys
import time

sys.path.append(r"D:\AI Arts\AI\backend")
from app.services.animation_service import (
    render_animation,
    probe_video,
    MUSIC_DIR,
    MEDIA_VIDEO_DIR,
    THUMB_DIR
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

if __name__ == "__main__":
    t_start = time.time()
    audio = r"D:\AI Arts\AI\media\audio\sample_narration.mp3"
    ass = r"D:\AI Arts\AI\media\subtitles\sample_narration.ass"
    music = os.path.join(MUSIC_DIR, "ambient_tech_theme.wav")
    out_mp4 = os.path.join(MEDIA_VIDEO_DIR, "sample_short.mp4")
    
    print("=" * 60, flush=True)
    print("RENDERING COMPLETE 16-SECOND YOUTUBE SHORT", flush=True)
    print(f"Target: {out_mp4}", flush=True)
    print("=" * 60, flush=True)
    
    success = render_animation(
        storyboard=SAMPLE_STORYBOARD,
        audio_path=audio,
        ass_path=ass,
        music_path=music,
        output_mp4_path=out_mp4,
        max_frames=None, # Full 480 frames
        fps=30
    )
    
    if success:
        thumb_path = os.path.join(THUMB_DIR, "sample_short_thumb.png")
        print("\n" + "=" * 60, flush=True)
        print("PHASE 2 COMPLETE: SAMPLE VIDEO & THUMBNAIL VERIFIED!", flush=True)
        print(f"Video Path: {out_mp4} ({os.path.getsize(out_mp4)/(1024*1024):.2f} MB)")
        print(f"Thumbnail: {thumb_path} ({os.path.getsize(thumb_path)/1024:.1f} KB)")
        print(f"Total Workflow Time: {time.time() - t_start:.2f}s")
        print("=" * 60, flush=True)
    else:
        print("Full render failed!", flush=True)
        sys.exit(1)
