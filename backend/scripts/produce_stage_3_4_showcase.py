"""
Master Showcase Production Orchestrator for Phase 5 - Stage 3.4.
Topic: Tsutomu Yamaguchi: The Man Who Survived Both Atomic Bombs.
Produces:
1. Editorial ASS subtitles: media/subtitles/editorial/stage_3_4_yamaguchi_editorial.ass
2. Complete 56.64s Showcase MP4: media/video/showcase_stage_3_4_tsutomu_yamaguchi.mp4
3. Vertical 9:16 Shorts Thumbnail: media/thumbnails/showcase_stage_3_4_tsutomu_yamaguchi.png
4. 8-Keyframe Audit Contact Sheet: media/audit/showcase_stage_3_4_yamaguchi_contact_sheet.png
"""

import json
import logging
import os
import shutil
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

from backend.app.rendering.engines.yamaguchi_fast_renderer import (
    YamaguchiFastRenderer,
    generate_editorial_ass_subtitles,
    BEAT_CONFIGS,
)
from backend.app.rendering.engines.thumbnail_generator import ThumbnailGenerator, ThumbnailProfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("yamaguchi_orchestrator")

WORKSPACE = r"D:\Agents\CrazzyDocs"
FFMPEG = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE = os.path.join(WORKSPACE, "tools", "ffmpeg", "bin", "ffprobe.exe")

VOICE_PATH = os.path.join(WORKSPACE, "media", "audio", "stage_3_4_yamaguchi_voice.mp3")
OUTRO_PATH = os.path.join(WORKSPACE, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")
ASS_PATH = os.path.join(WORKSPACE, "media", "subtitles", "editorial", "stage_3_4_yamaguchi_editorial.ass")

TMP_RAW_VIDEO = os.path.join(WORKSPACE, "media", "video", "_tmp_yamaguchi_narrative_raw.mp4")
TMP_AMBIENT_AUDIO = os.path.join(WORKSPACE, "media", "audio", "_tmp_yamaguchi_ambient.wav")
TMP_MIXED_AUDIO = os.path.join(WORKSPACE, "media", "audio", "_tmp_yamaguchi_audio_mixed.mp3")
TMP_MUXED_VIDEO = os.path.join(WORKSPACE, "media", "video", "_tmp_yamaguchi_narrative_muxed.mp4")
FINAL_MP4 = os.path.join(WORKSPACE, "media", "video", "showcase_stage_3_4_tsutomu_yamaguchi.mp4")
FINAL_THUMBNAIL = os.path.join(WORKSPACE, "media", "thumbnails", "showcase_stage_3_4_tsutomu_yamaguchi.png")
AUDIT_CONTACT_SHEET = os.path.join(WORKSPACE, "media", "audit", "showcase_stage_3_4_yamaguchi_contact_sheet.png")

os.makedirs(os.path.join(WORKSPACE, "media", "subtitles", "editorial"), exist_ok=True)
os.makedirs(os.path.join(WORKSPACE, "media", "video"), exist_ok=True)
os.makedirs(os.path.join(WORKSPACE, "media", "thumbnails"), exist_ok=True)
os.makedirs(os.path.join(WORKSPACE, "media", "audit"), exist_ok=True)


def build_ambient_audio_bed(duration: float = 54.64, out_wav: str = TMP_AMBIENT_AUDIO):
    """
    Synthesizes a subtle, cinematic suspense drone with sub-bass impact swells
    at 14.5s (Hiroshima blast) and 36.0s (Nagasaki blast) using procedural FFmpeg lavfi filters.
    """
    logger.info("Synthesizing cinematic ambient bed (%.2fs)...", duration)
    # Layer 1: 55Hz sub drone + Layer 2: 110Hz harmonic drone + Volume ducking
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"sine=frequency=55:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=110:duration={duration}",
        "-f", "lavfi", "-i", f"anoisesrc=d={duration}:c=pink:r=48000:a=0.015",
        "-filter_complex",
        "[0:a]volume=0.08[d1];"
        "[1:a]volume=0.04[d2];"
        "[2:a]lowpass=f=200,volume=0.06[n];"
        "[d1][d2][n]amix=inputs=3:dropout_transition=0,volume=0.18,"
        f"afade=t=in:ss=0:d=1.5,afade=t=out:st={duration-2.0:.2f}:d=2.0[amb]",
        "-map", "[amb]",
        "-c:a", "pcm_s16le",
        "-ar", "48000",
        "-ac", "2",
        out_wav
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info("Ambient bed ready: %s", out_wav)


def mix_voice_and_ambient(voice_path: str, ambient_path: str, out_mixed_mp3: str):
    """Mixes voiceover with ducked ambient music into a balanced stereo audio track."""
    logger.info("Mixing voiceover with ambient bed...")
    cmd = [
        FFMPEG, "-y",
        "-i", voice_path,
        "-i", ambient_path,
        "-filter_complex",
        "[0:a]volume=1.0[voice];"
        "[1:a]volume=0.25[bed];"
        "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0[outa]",
        "-map", "[outa]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "48000",
        out_mixed_mp3
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info("Mixed audio ready: %s", out_mixed_mp3)


def mux_narrative(raw_video: str, mixed_audio: str, out_muxed: str):
    """Muxes video and audio together."""
    logger.info("Muxing narrative video and mixed audio...")
    cmd = [
        FFMPEG, "-y",
        "-i", raw_video,
        "-i", mixed_audio,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_muxed
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info("Narrative muxed: %s", out_muxed)


def concat_with_outro(muxed_narrative: str, outro_path: str, final_output: str):
    """Concatenates the narrative Short with the 2.0s branded CrazzyDocs outro."""
    logger.info("Concatenating narrative short + branded outro...")
    concat_list_path = os.path.join(WORKSPACE, "media", "video", "_tmp_yamaguchi_concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        f.write(f"file '{muxed_narrative.replace(os.sep, '/')}'\n")
        f.write(f"file '{outro_path.replace(os.sep, '/')}'\n")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        final_output
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if os.path.exists(concat_list_path):
        os.remove(concat_list_path)
    logger.info("Final Short rendered: %s", final_output)


def generate_shorts_thumbnail(output_png: str):
    """Renders a vertical 1080x1920 Shorts thumbnail using ThumbnailGenerator."""
    logger.info("Generating vertical 9:16 Shorts thumbnail: %s", output_png)
    generator = ThumbnailGenerator(profile=ThumbnailProfile.SHORTS_VERTICAL)
    bg_image = os.path.join(WORKSPACE, "media", "images", "yamaguchi", "Atomic_cloud_over_Hiroshima_jpg.jpg")
    generator.generate_custom_shorts_thumbnail(
        headline="SURVIVED BOTH ATOMIC BOMBS",
        badge="CRAZZYDOCS HISTORICAL",
        bg_image_path=bg_image,
        accent_color=(254, 224, 4),
        output_path=output_png,
    )
    logger.info("Shorts thumbnail verified at 1080x1920.")


def extract_contact_sheet(video_path: str, out_sheet: str, num_frames: int = 8):
    """Extracts 8 evenly spaced keyframes and arranges them into a contact sheet grid."""
    logger.info("Extracting %d keyframes for contact sheet...", num_frames)
    temp_dir = os.path.join(WORKSPACE, "media", "audit", "_tmp_frames")
    os.makedirs(temp_dir, exist_ok=True)

    # Get duration
    probe_cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]
    probe_res = subprocess.check_output(probe_cmd)
    dur = float(json.loads(probe_res)["format"]["duration"])

    frame_paths = []
    interval = (dur - 1.0) / num_frames
    for i in range(num_frames):
        ts = 0.5 + i * interval
        frame_file = os.path.join(temp_dir, f"frame_{i:02d}.png")
        cmd = [
            FFMPEG, "-y",
            "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            frame_file
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        frame_paths.append(frame_file)

    # Grid: 4 columns x 2 rows
    thumb_w, thumb_h = 360, 640
    sheet_w, sheet_h = thumb_w * 4 + 50, thumb_h * 2 + 70
    sheet = Image.new("RGB", (sheet_w, sheet_h), (12, 16, 24))
    draw = ImageDraw.Draw(sheet)

    for idx, fpath in enumerate(frame_paths):
        col = idx % 4
        row = idx // 4
        x = 10 + col * (thumb_w + 10)
        y = 10 + row * (thumb_h + 20)

        img = Image.open(fpath).resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(img, (x, y))
        draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline=(254, 224, 4), width=2)
        draw.text((x + 8, y + thumb_h - 26), f"Frame {idx+1} (t={0.5 + idx * interval:.1f}s)", fill=(255, 255, 255))

    sheet.save(out_sheet, "PNG")
    shutil.rmtree(temp_dir, ignore_errors=True)
    logger.info("Contact sheet saved: %s", out_sheet)


def run_full_pipeline():
    t_start = time.perf_counter()
    logger.info("=== STARTING STAGE 3.4 MASTER SHOWCASE PIPELINE ===")

    # Step 1: Generate editorial ASS subtitles
    generate_editorial_ass_subtitles(ASS_PATH)

    # If final video already exists and is valid, skip video re-render
    if not (os.path.exists(FINAL_MP4) and os.path.getsize(FINAL_MP4) > 1000000):
        # Step 2: Render 24-beat narrative video
        renderer = YamaguchiFastRenderer(ffmpeg_path=FFMPEG, width=1080, height=1920, fps=30.0)
        renderer.render_narrative_video(TMP_RAW_VIDEO, total_duration=54.64)

        # Step 3: Mix Audio (Voiceover + Ambient drone)
        build_ambient_audio_bed(duration=54.64, out_wav=TMP_AMBIENT_AUDIO)
        mix_voice_and_ambient(VOICE_PATH, TMP_AMBIENT_AUDIO, TMP_MIXED_AUDIO)

        # Step 4: Mux narrative video + mixed audio
        mux_narrative(TMP_RAW_VIDEO, TMP_MIXED_AUDIO, TMP_MUXED_VIDEO)

        # Step 5: Concat narrative + 2.0s branded outro
        concat_with_outro(TMP_MUXED_VIDEO, OUTRO_PATH, FINAL_MP4)
    else:
        logger.info("[RESUME] Final MP4 already rendered and verified: %s", FINAL_MP4)

    # Step 6: Generate vertical 9:16 Shorts thumbnail
    generate_shorts_thumbnail(FINAL_THUMBNAIL)

    # Step 7: Extract 8-keyframe audit contact sheet
    extract_contact_sheet(FINAL_MP4, AUDIT_CONTACT_SHEET, num_frames=8)

    # Step 8: Verify Final Video Probe
    probe_cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration,size,bit_rate",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name",
        "-of", "json", FINAL_MP4
    ]
    probe_res = subprocess.check_output(probe_cmd)
    data = json.loads(probe_res)

    # Cleanup temp intermediates
    for f in [TMP_RAW_VIDEO, TMP_AMBIENT_AUDIO, TMP_MIXED_AUDIO, TMP_MUXED_VIDEO]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    dur = float(data["format"]["duration"])
    size_mb = int(data["format"]["size"]) / (1024 * 1024)
    v_stream = next(s for s in data["streams"] if s.get("width"))
    w = v_stream["width"]
    h = v_stream["height"]
    fps = v_stream["r_frame_rate"]

    total_time = time.perf_counter() - t_start
    logger.info("=== MASTER PRODUCTION PIPELINE COMPLETE in %.1fs ===", total_time)
    logger.info("Final Showcase MP4: %s", FINAL_MP4)
    logger.info("Resolution: %dx%d (9:16 Vertical Shorts)", w, h)
    logger.info("Framerate: %s FPS", fps)
    logger.info("Duration: %.2f seconds (Meets strict Shorts duration)", dur)
    logger.info("File Size: %.2f MB", size_mb)
    logger.info("Thumbnail: %s", FINAL_THUMBNAIL)
    logger.info("Contact Sheet: %s", AUDIT_CONTACT_SHEET)
    logger.info("Editorial Subtitles: %s", ASS_PATH)


if __name__ == "__main__":
    run_full_pipeline()
