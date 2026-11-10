"""
CrazzyDocs Phase 5 Stage 3.3 End-to-End Showcase Production Engine.

Generates the complete showcase short:
Topic: The Great Molasses Flood of 1919 — When a Wave of Molasses Destroyed a City.
Visual System: Data Story (Obsidian slate #0F141C, Archivo/IBM Plex typography,
               blueprint schematics, fluid dynamics stats cards, Ken Burns motion).
Narration: Expressive multi-track narration (52.94s).
Editorial Subtitles: 2-5 word power phrases in safe band (MarginV: 430, ~60% visual breathing room).
Outro: Additive 2.0s icon-only CrazzyDocs circular branding.
Target Output: media/video/showcase_stage_3_3_molasses_flood.mp4 (54.94s, 1080x1920 @ 30 FPS).
Thumbnail: media/thumbnails/showcase_stage_3_3_molasses_flood.png (1280x720).
Contact Sheet: media/audit/showcase_stage_3_3_molasses_flood_contact_sheet.png.
"""

from datetime import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Tuple
from PIL import Image, ImageDraw, ImageFont

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("stage_3_3_production")

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
FFMPEG_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe")
FFPROBE_PATH = os.path.join(WORKSPACE_ROOT, "tools", "ffmpeg", "bin", "ffprobe.exe")

VIDEO_DIR = os.path.join(WORKSPACE_ROOT, "media", "video")
SCENES_DIR = os.path.join(VIDEO_DIR, "scenes")
AUDIT_DIR = os.path.join(WORKSPACE_ROOT, "media", "audit")
THUMBNAIL_DIR = os.path.join(WORKSPACE_ROOT, "media", "thumbnails")

OUTRO_PATH = os.path.join(WORKSPACE_ROOT, "assets", "branding", "crazzydocs", "crazzydocs_outro_1080x1920.mp4")
MUSIC_PATH = os.path.join(WORKSPACE_ROOT, "assets", "audio", "music", "ambient_tech_theme.wav")
VOICEOVER_PATH = os.path.join(WORKSPACE_ROOT, "media", "audio", "stage_3_3_molasses_voice.mp3")
EDITORIAL_ASS_PATH = os.path.join(WORKSPACE_ROOT, "media", "subtitles", "editorial", "stage_3_3_molasses_voice_editorial.ass")
TRANSCRIPT_JSON_PATH = os.path.join(WORKSPACE_ROOT, "media", "subtitles", "transcripts", "stage_3_3_molasses_voice_transcript.json")

FINAL_VIDEO_PATH = os.path.join(VIDEO_DIR, "showcase_stage_3_3_molasses_flood.mp4")
FINAL_THUMBNAIL_PATH = os.path.join(THUMBNAIL_DIR, "showcase_stage_3_3_molasses_flood.png")
CONTACT_SHEET_PATH = os.path.join(AUDIT_DIR, "showcase_stage_3_3_molasses_flood_contact_sheet.png")

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(SCENES_DIR, exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

from backend.app.rendering.engines.clean_slate_renderer import CleanSlateSceneRenderer
from backend.app.rendering.engines.thumbnail_generator import ThumbnailGenerator
from backend.app.rendering.compositing.compositor import UniversalCompositor, CompositorValidationError
from backend.app.services.notification_service import NotificationManager
from backend.app.content.topic_ledger import TopicLedger, ContentRecord, compute_script_fingerprint


def probe_video_metadata(video_path: str) -> Dict:
    """Probes video using ffprobe and extracts technical parameters."""
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-show_entries", "stream=width,height,r_frame_rate,codec_type,codec_name",
        "-show_entries", "format=duration,size,bit_rate",
        "-of", "json",
        video_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    v_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    a_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    duration = float(data["format"]["duration"])
    width = int(v_stream["width"])
    height = int(v_stream["height"])
    fps_val = eval(v_stream["r_frame_rate"])
    size_bytes = int(data["format"].get("size", os.path.getsize(video_path)))

    return {
        "path": video_path,
        "width": width,
        "height": height,
        "fps": fps_val,
        "duration": duration,
        "video_codec": v_stream.get("codec_name", "unknown"),
        "audio_codec": a_stream.get("codec_name", "none") if a_stream else "none",
        "has_audio": a_stream is not None,
        "size_bytes": size_bytes,
    }


def extract_contact_sheet(
    video_path: str,
    output_sheet: str,
    title: str = "THE GREAT MOLASSES FLOOD OF 1919",
    subtitle: str = "CrazzyDocs Clean-Slate Showcase Audit",
    num_frames: int = 8,
) -> str:
    """Extracts 8 representative keyframes across the narrative and outro."""
    meta = probe_video_metadata(video_path)
    duration = meta["duration"]

    temp_dir = os.path.join(AUDIT_DIR, f"_tmp_contact_{int(time.time()*1000)%10000}")
    os.makedirs(temp_dir, exist_ok=True)

    # 8 calibrated sample points:
    # 0: Hook / Tank (1.20s)
    # 1: Blueprint leaks (7.50s)
    # 2: Rupture / Thermal (16.00s)
    # 3: 25-ft wave @ 35mph (21.50s)
    # 4: Devastation / Toll (29.00s)
    # 5: Metallurgy (38.50s)
    # 6: PE Law legacy (46.00s)
    # 7: Outro icon (54.00s)
    timestamps = [
        1.20,
        7.50,
        16.00,
        21.50,
        29.00,
        38.50,
        46.00,
        min(54.00, duration - 0.70),
    ]

    labels = [
        "01: Hook & 50-Ft Steel Tank",
        "02: Blueprint & Seam Camouflage",
        "03: Thermal Surge & Rivet Rupture",
        "04: 25-Ft Wave at 35 MPH",
        "05: Infrastructure & 21 Casualties",
        "06: Metallurgy & Negligence Finding",
        "07: Modern Engineering Law Born",
        "08: Branded Outro (CrazzyDocs Icon)",
    ]

    frame_paths = []
    for i, ts in enumerate(timestamps):
        out_f = os.path.join(temp_dir, f"frame_{i:02d}.png")
        cmd = [
            FFMPEG_PATH, "-y",
            "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_f,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(out_f):
            frame_paths.append((out_f, ts, labels[i]))

    sheet_w, sheet_h = 1920, 1080
    sheet = Image.new("RGB", (sheet_w, sheet_h), (10, 14, 22))
    draw = ImageDraw.Draw(sheet)

    try:
        title_font = ImageFont.truetype("arialbd.ttf", 32)
        sub_font = ImageFont.truetype("arial.ttf", 20)
        lbl_font = ImageFont.truetype("arialbd.ttf", 15)
        ts_font = ImageFont.truetype("consola.ttf", 14)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = title_font
        lbl_font = title_font
        ts_font = title_font

    # Header Bar
    draw.text((40, 20), f"CRAZZYDOCS SHOWCASE AUDIT — {title}", fill=(240, 245, 250), font=title_font)
    draw.text(
        (40, 62),
        f"{subtitle}  |  Duration: {duration:.2f}s  |  Resolution: 1080x1920 @ 30 FPS  |  Editorial Text Safe Band (MarginV: 430)",
        fill=(32, 201, 151),
        font=sub_font,
    )

    cols, rows = 4, 2
    thumb_w, thumb_h = 220, 391
    margin_x, margin_y = 40, 105
    spacing_x = (sheet_w - 2 * margin_x - cols * thumb_w) // (cols - 1)
    spacing_y = 35

    for idx, (f_path, ts, label) in enumerate(frame_paths[:8]):
        col = idx % cols
        row = idx // cols
        x = margin_x + col * (thumb_w + spacing_x)
        y = margin_y + row * (thumb_h + spacing_y)

        # Card container
        draw.rectangle([x - 4, y - 4, x + thumb_w + 4, y + thumb_h + 38], fill=(18, 24, 34), outline=(39, 52, 73), width=1)

        thumb = Image.open(f_path).convert("RGB")
        thumb = thumb.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))

        # Timestamp badge
        draw.rectangle([x + 6, y + 6, x + 72, y + 26], fill=(15, 20, 28))
        draw.text((x + 10, y + 8), f"{ts:05.2f}s", fill=(242, 193, 78), font=ts_font)

        # Label under frame
        draw.text((x, y + thumb_h + 8), label, fill=(200, 215, 230), font=lbl_font)

    sheet.save(output_sheet, "PNG", quality=95)
    shutil.rmtree(temp_dir, ignore_errors=True)
    logger.info("Audit contact sheet saved to %s", output_sheet)
    return output_sheet


def produce_showcase_video() -> Dict:
    """Executes the complete production workflow end-to-end."""
    t_start = time.perf_counter()
    notif = NotificationManager()
    renderer = CleanSlateSceneRenderer()
    compositor = UniversalCompositor(ffmpeg_path=FFMPEG_PATH, ffprobe_path=FFPROBE_PATH)

    logger.info("================================================================================")
    logger.info("STARTING CRAZZYDOCS SHOWCASE PRODUCTION: THE GREAT MOLASSES FLOOD OF 1919")
    logger.info("================================================================================")

    # 1. Define Scene Timeline
    scenes = [
        ("seg_01_hook", 6.41, renderer.draw_scene_01),
        ("seg_02_blueprint", 8.81, renderer.draw_scene_02),
        ("seg_03_rupture", 11.52, renderer.draw_scene_03),
        ("seg_04_devastation", 10.46, renderer.draw_scene_04),
        ("seg_05_metallurgy", 7.03, renderer.draw_scene_05),
        ("seg_06_legacy", 8.71, renderer.draw_scene_06),
    ]

    # 2. Render Scene Segments with authentic historical photos & Ken Burns motion
    segment_paths = []
    # Force re-render to apply the Stage 3.3.1 historical documentary visuals
    force_rerender = True
    for scene_id, dur_sec, draw_fn in scenes:
        seg_mp4 = os.path.join(SCENES_DIR, f"{scene_id}.mp4")
        needs_render = True
        if not force_rerender and os.path.exists(seg_mp4) and os.path.getsize(seg_mp4) > 1000:
            try:
                m = probe_video_metadata(seg_mp4)
                if abs(m["duration"] - dur_sec) < 0.2 and m["width"] == 1080 and m["height"] == 1920:
                    logger.info("Reusing verified existing segment: %s (%.2fs)", scene_id, m["duration"])
                    needs_render = False
            except Exception:
                needs_render = True

        if needs_render:
            logger.info("Rendering fresh documentary historical scene: %s (%.2fs, %d frames)...", scene_id, dur_sec, int(dur_sec * 30))
            renderer.render_scene(scene_id, dur_sec, seg_mp4, draw_fn, timeout=180.0)

        segment_paths.append(seg_mp4)

    # 3. Concatenate Scene Segments into Raw Narrative MP4
    stitched_raw_path = os.path.join(VIDEO_DIR, "stage_3_3_molasses_narrative_raw.mp4")
    logger.info("Concatenating %d clean-slate scene segments...", len(segment_paths))
    compositor.concatenate_segments(segment_paths, stitched_raw_path)
    raw_meta = probe_video_metadata(stitched_raw_path)
    logger.info("Stitched narrative video duration: %.2fs (target 52.94s)", raw_meta["duration"])

    # 4. Mix Audio & Burn Editorial Subtitles
    muxed_path = os.path.join(VIDEO_DIR, "stage_3_3_molasses_narrative_muxed.mp4")
    logger.info("Muxing multi-track audio (voiceover + ducked music) & burning editorial text subtitles...")
    compositor.mix_and_finalize_short(
        video_path=stitched_raw_path,
        voiceover_audio=VOICEOVER_PATH,
        sfx_audios=[],
        music_audio=MUSIC_PATH,
        output_final_mp4=muxed_path,
        subtitles_ass_path=EDITORIAL_ASS_PATH,
    )
    mux_meta = probe_video_metadata(muxed_path)
    logger.info("Muxed narrative video duration: %.2fs (target 52.94s)", mux_meta["duration"])

    # 5. Append Additive Branded Outro (Strict 8-Point Validation)
    logger.info("Appending 2.0s minimalist CrazzyDocs circular outro to main narrative...")
    compositor.append_branded_outro(
        main_video_path=muxed_path,
        outro_video_path=OUTRO_PATH,
        output_final_path=FINAL_VIDEO_PATH,
        subtitles_ass_path=EDITORIAL_ASS_PATH,
    )
    final_meta = probe_video_metadata(FINAL_VIDEO_PATH)
    logger.info("Final showcase video created: %s", FINAL_VIDEO_PATH)
    logger.info("  Resolution: %dx%d", final_meta["width"], final_meta["height"])
    logger.info("  FPS: %.2f", final_meta["fps"])
    logger.info("  Duration: %.2f s (Expected ~54.94s)", final_meta["duration"])
    logger.info("  Size: %d bytes (%.2f MB)", final_meta["size_bytes"], final_meta["size_bytes"] / 1048576)

    # Strict Assertions
    assert final_meta["width"] == 1080 and final_meta["height"] == 1920, f"Resolution error: {final_meta['width']}x{final_meta['height']}"
    assert abs(final_meta["fps"] - 30.0) < 0.1, f"FPS error: {final_meta['fps']}"
    assert 50.0 <= final_meta["duration"] <= 60.0, f"Duration error: {final_meta['duration']}"
    assert final_meta["has_audio"], "Missing audio stream in final video"

    # 6. Generate Mandatory 9:16 Vertical YouTube Shorts Thumbnail (1080x1920)
    logger.info("Generating high-impact 1080x1920 (9:16) vertical Shorts thumbnail...")
    thumb_gen = ThumbnailGenerator()
    thumb_path = thumb_gen.generate_shorts_molasses_thumbnail(
        output_path=FINAL_THUMBNAIL_PATH,
        title_text="THE DEADLY WAVE",
    )
    thumb_im = Image.open(thumb_path)
    logger.info("Thumbnail verified: %s, dimensions=%s, size=%d bytes", thumb_path, thumb_im.size, os.path.getsize(thumb_path))
    assert thumb_im.size == (1080, 1920), f"Thumbnail dimension error: {thumb_im.size} (must be 1080x1920)"

    # 7. Generate Audit Contact Sheet
    logger.info("Generating 8-keyframe showcase contact sheet...")
    contact_sheet = extract_contact_sheet(
        video_path=FINAL_VIDEO_PATH,
        output_sheet=CONTACT_SHEET_PATH,
        title="THE GREAT MOLASSES FLOOD OF 1919",
        subtitle="Clean-Slate Data Story Production Showcase",
    )

    # 8. Register Topic in TopicLedger
    logger.info("Recording topic and fingerprint into persistent TopicLedger...")
    ledger = TopicLedger()
    with open(TRANSCRIPT_JSON_PATH, "r", encoding="utf-8") as f:
        t_data = json.load(f)

    record = ContentRecord(
        topic_id="topic_stage_3_3_molasses_flood",
        topic_title="The Great Molasses Flood of 1919 — When a Wave of Molasses Destroyed a City",
        opening_hook="In January 1919, a two-million-gallon wave of molasses swallowed downtown Boston.",
        main_subject="Great Molasses Flood of 1919 (Boston, MA)",
        key_entities=["Purity Distilling Co", "USIA", "North End Boston", "Hugh W Ogden", "Professional Engineer"],
        script_fingerprint=compute_script_fingerprint(t_data.get("full_text", "")),
        category="untold_history",
        visual_style="data_story",
        created_at=datetime.now().isoformat(),
        publication_status="approved",
    )
    ledger.record_content(record)
    logger.info("Topic recorded in ledger: %s", record.topic_id)

    # 9. Send Success Notification
    notif.send_success(
        job_id="stage_3_3_showcase_molasses",
        stage="production",
        title="Molasses Flood Showcase Complete",
        video_path=FINAL_VIDEO_PATH,
        thumbnail_path=FINAL_THUMBNAIL_PATH,
        duration_sec=final_meta["duration"],
        details={
            "resolution": f"{final_meta['width']}x{final_meta['height']}",
            "fps": final_meta["fps"],
            "video_size_mb": round(final_meta["size_bytes"] / 1048576, 2),
            "contact_sheet": CONTACT_SHEET_PATH,
        },
    )

    t_total = time.perf_counter() - t_start
    logger.info("================================================================================")
    logger.info("SHOWCASE PRODUCTION COMPLETED SUCCESSFULLY IN %.2f SECONDS", t_total)
    logger.info("  Final MP4: %s", FINAL_VIDEO_PATH)
    logger.info("  Thumbnail: %s", FINAL_THUMBNAIL_PATH)
    logger.info("  Contact Sheet: %s", CONTACT_SHEET_PATH)
    logger.info("================================================================================")

    return {
        "status": "SUCCESS",
        "video_path": FINAL_VIDEO_PATH,
        "thumbnail_path": FINAL_THUMBNAIL_PATH,
        "contact_sheet_path": CONTACT_SHEET_PATH,
        "metadata": final_meta,
        "total_time_sec": t_total,
    }


if __name__ == "__main__":
    try:
        produce_showcase_video()
    except Exception as e:
        logger.exception("Showcase production failed: %s", e)
        NotificationManager().send_failure(
            stage="showcase_production",
            error_summary=str(e),
            job_id="stage_3_3_showcase_molasses",
        )
        sys.exit(1)
