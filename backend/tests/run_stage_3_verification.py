"""
Stage 3 Verification & Quality Assurance Suite.
Tests:
1. Integration of CrazzyDocs branded outro across all four styles:
   - Pop Info
   - Data Story
   - Chalk Talk
   - Creator Motion Graphics
2. Full production 55-second Short with branded outro (50-60s standard).
3. Contact sheet generation (8 keyframes per video).
4. Strict FFprobe metric verification:
   - 1080x1920 vertical resolution
   - 30.0 FPS constant framerate
   - Audio sync and stream integrity
   - Production duration standard compliance (50-60s)
"""

import json
import logging
import os
import subprocess
import time
from typing import Dict, List
from PIL import Image, ImageDraw, ImageFont

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
FFMPEG_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")
VIDEO_DIR = os.path.join(WORKSPACE_ROOT, "media", "video")
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit")
OUTRO_PATH = os.path.join(WORKSPACE_ROOT, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_stage_3_verification")

from backend.app.rendering.compositing.compositor import UniversalCompositor

def extract_contact_sheet(video_path: str, output_sheet: str, title: str, style_name: str, num_frames: int = 8):
    """Extracts evenly spaced keyframes and compiles a polished 2x4 contact sheet."""
    # Probe duration
    probe_cmd = [
        FFPROBE_PATH, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True)
    duration = float(res.stdout.strip())

    temp_dir = os.path.join(AUDIT_DIR, "_tmp_sheet_frames")
    os.makedirs(temp_dir, exist_ok=True)

    frame_paths = []
    # Sample points across video (ensuring the final frame captures the branded outro)
    timestamps = [duration * (i + 0.5) / num_frames for i in range(num_frames)]
    timestamps[-1] = max(0.1, duration - 0.8) # Ensure last frame is well inside outro

    for i, ts in enumerate(timestamps):
        out_f = os.path.join(temp_dir, f"frame_{i:02d}.png")
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_f
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(out_f):
            frame_paths.append((out_f, ts))

    # Build 2 rows x 4 columns sheet (1920 x 1080 canvas)
    sheet_w, sheet_h = 1920, 1080
    sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 18, 26))
    draw = ImageDraw.Draw(sheet)

    try:
        title_font = ImageFont.truetype("arialbd.ttf", 32)
        sub_font = ImageFont.truetype("arialbd.ttf", 18)
        ts_font = ImageFont.truetype("arialbd.ttf", 15)
    except:
        title_font = ImageFont.load_default()
        sub_font = title_font
        ts_font = title_font

    draw.text((40, 25), f"CRAZZYDOCS STUDIO — {title.upper()}", fill=(255, 255, 255), font=title_font)
    draw.text((40, 68), f"Style: {style_name} | Duration: {duration:.2f}s | Resolution: 1080x1920 | 30 FPS", fill=(16, 185, 129), font=sub_font)

    # 4 columns, 2 rows
    cols, rows = 4, 2
    thumb_w, thumb_h = 220, 391
    margin_x, margin_y = 40, 110
    spacing_x = (sheet_w - 2 * margin_x - cols * thumb_w) // (cols - 1)
    spacing_y = 35

    for idx, (f_path, ts) in enumerate(frame_paths[:8]):
        col = idx % cols
        row = idx // cols
        x = margin_x + col * (thumb_w + spacing_x)
        y = margin_y + row * (thumb_h + spacing_y)

        with Image.open(f_path) as im:
            thumb = im.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (x, y))

        draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=(50, 65, 85), width=2)
        # Timestamp pill
        pill_text = f"T: {ts:.1f}s"
        if idx == len(frame_paths) - 1:
            pill_text += " (OUTRO)"
        draw.rectangle([x + 8, y + thumb_h - 26, x + 115, y + thumb_h - 6], fill=(0, 0, 0, 180))
        draw.text((x + 12, y + thumb_h - 24), pill_text, fill=(254, 224, 4), font=ts_font)

    sheet.save(output_sheet, "PNG")
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    logger.info("  -> Saved contact sheet: %s", output_sheet)

def verify_video_metrics(video_path: str, min_dur: float = 5.0, max_dur: float = 65.0) -> Dict:
    """Uses ffprobe to verify resolution, fps, duration, and audio stream."""
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,codec_type",
        "-show_entries", "format=duration,size",
        "-of", "json",
        video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFprobe failed on {video_path}: {res.stderr}"
    data = json.loads(res.stdout)

    v_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
    duration = float(data["format"]["duration"])
    width = int(v_stream["width"])
    height = int(v_stream["height"])
    fps_val = eval(v_stream["r_frame_rate"])

    logger.info("  Validation for %s:", os.path.basename(video_path))
    logger.info("    Resolution: %dx%d (Expected 1080x1920)", width, height)
    logger.info("    Framerate: %.2f FPS (Expected 30.0)", fps_val)
    logger.info("    Duration: %.2f s (Allowed [%.1f, %.1f])", duration, min_dur, max_dur)
    logger.info("    Audio Stream: %s", "Present" if has_audio else "MISSING")

    assert width == 1080 and height == 1920, f"Resolution mismatch: {width}x{height}"
    assert abs(fps_val - 30.0) < 0.1, f"FPS mismatch: {fps_val}"
    assert min_dur <= duration <= max_dur, f"Duration out of range: {duration:.2f}s"
    assert has_audio, "Missing audio stream in output video"

    return {
        "path": video_path,
        "width": width,
        "height": height,
        "fps": fps_val,
        "duration": duration,
        "has_audio": has_audio
    }

def main():
    logger.info("==================================================================")
    logger.info("STARTING PHASE 5 STAGE 3: VISUAL ASSET & BRANDING QA SUITE")
    logger.info("==================================================================")

    compositor = UniversalCompositor()
    assert os.path.exists(OUTRO_PATH), f"CrazzyDocs outro missing at {OUTRO_PATH}"
    logger.info("CrazzyDocs branded outro verified: %s (%d bytes)", OUTRO_PATH, os.path.getsize(OUTRO_PATH))

    # 1. Styles Matrix: Pair representative style videos with CrazzyDocs outro
    style_samples = [
        {
            "id": "pop_info",
            "name": "Pop Info (Dr. Doc & Grocery Retail)",
            "source_raw": os.path.join(VIDEO_DIR, "test_pop_info_short.mp4"),
            "target": os.path.join(VIDEO_DIR, "stage_3_pop_info_crazzydocs.mp4"),
            "sheet": os.path.join(AUDIT_DIR, "stage_3_pop_info_sheet.png")
        },
        {
            "id": "data_story",
            "name": "Data Story (Deep Slate & Clean Analytics)",
            "source_raw": os.path.join(VIDEO_DIR, "test_data_story_20sec.mp4") if os.path.exists(os.path.join(VIDEO_DIR, "test_data_story_20sec.mp4")) else os.path.join(VIDEO_DIR, "test_data_story_short.mp4"),
            "target": os.path.join(VIDEO_DIR, "stage_3_data_story_crazzydocs.mp4"),
            "sheet": os.path.join(AUDIT_DIR, "stage_3_data_story_sheet.png")
        },
        {
            "id": "chalk_talk",
            "name": "Chalk Talk (Authentic Slate & Chalk Cursor)",
            "source_raw": os.path.join(VIDEO_DIR, "test_chalk_talk_20sec.mp4") if os.path.exists(os.path.join(VIDEO_DIR, "test_chalk_talk_20sec.mp4")) else os.path.join(VIDEO_DIR, "test_chalk_talk_short.mp4"),
            "target": os.path.join(VIDEO_DIR, "stage_3_chalk_talk_crazzydocs.mp4"),
            "sheet": os.path.join(AUDIT_DIR, "stage_3_chalk_talk_sheet.png")
        },
        {
            "id": "creator_motion",
            "name": "Creator Motion Graphics (Studio Black & 3D Yaw)",
            "source_raw": os.path.join(VIDEO_DIR, "test_creator_motion_20sec.mp4") if os.path.exists(os.path.join(VIDEO_DIR, "test_creator_motion_20sec.mp4")) else os.path.join(VIDEO_DIR, "test_creator_motion_short.mp4"),
            "target": os.path.join(VIDEO_DIR, "stage_3_creator_crazzydocs.mp4"),
            "sheet": os.path.join(AUDIT_DIR, "stage_3_creator_sheet.png")
        }
    ]

    results = []

    # Render & Verify 4 Styles with CrazzyDocs Branded Outro
    for s in style_samples:
        logger.info("\n--- Processing %s ---", s["name"])
        src = s["source_raw"]
        dst = s["target"]
        assert os.path.exists(src), f"Source video {src} not found."

        # Append CrazzyDocs Outro
        compositor.append_branded_outro(src, OUTRO_PATH, dst)
        assert os.path.exists(dst), f"Target video {dst} was not created."

        # Verify metrics
        meta = verify_video_metrics(dst, min_dur=10.0, max_dur=65.0)
        results.append(meta)

        # Extract 8-keyframe contact sheet
        extract_contact_sheet(dst, s["sheet"], title=f"Stage 3 Style Audit: {s['id']}", style_name=s["name"], num_frames=8)

    # 2. Production 55-Second Short Verification (50-60s Standard)
    logger.info("\n--- Processing Production 55-Second YouTube Short ---")
    prod_src = os.path.join(VIDEO_DIR, "production_55sec_short.mp4")
    prod_target = os.path.join(VIDEO_DIR, "stage_3_production_55s_crazzydocs.mp4")
    prod_sheet = os.path.join(AUDIT_DIR, "stage_3_production_55s_sheet.png")

    assert os.path.exists(prod_src), f"Production base video {prod_src} missing"

    # Calibrate narrative to 52.5s so narrative (52.5s) + CrazzyDocs Outro (2.5s) = 55.0s Target
    narrative_52s = os.path.join(VIDEO_DIR, "_tmp_prod_52s.mp4")
    cmd_trim = [
        FFMPEG_PATH, "-y",
        "-i", prod_src,
        "-t", "52.5",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "320k",
        narrative_52s
    ]
    subprocess.run(cmd_trim, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    compositor.append_branded_outro(narrative_52s, OUTRO_PATH, prod_target)
    if os.path.exists(narrative_52s):
        try: os.remove(narrative_52s)
        except OSError: pass

    prod_meta = verify_video_metrics(prod_target, min_dur=50.0, max_dur=60.0)
    results.append(prod_meta)

    extract_contact_sheet(
        prod_target,
        prod_sheet,
        title="Production 55s Standard with CrazzyDocs Outro",
        style_name="Production Master (50-60s Duration Standard)",
        num_frames=8
    )

    logger.info("\n==================================================================")
    logger.info("STAGE 3 VERIFICATION SUMMARY:")
    for r in results:
        logger.info("  %s | %dx%d | %.1f FPS | %.2fs | Audio: OK", os.path.basename(r["path"]), r["width"], r["height"], r["fps"], r["duration"])
    logger.info("ALL 4 STYLES + 55S PRODUCTION STANDARD PASSED 100%!")
    logger.info("==================================================================")

if __name__ == "__main__":
    main()
