"""
Controlled Single-Video End-to-End Validation Runner — Stage 3.21I.

Executes exactly ONE production run inside ExecutionContext.VALIDATION:
- Live external topic discovery + Wikipedia/Phys.org verification
- 5-stage structured factual script grounded in retrieved facts
- Expressive Edge Neural TTS narration + ffprobe calibration
- Isolated openly licensed asset downloads (no shared or prior-topic reuse)
- 20-beat multi-layout 1080x1920 @ 30 FPS documentary render + ASS subtitles + music ducking + 2.0s outro
- Multi-candidate 1080x1920 thumbnail selection
- 15-point QualityControlEngine + 13-point ContentIntegrityGate
- Frame extraction & labeled visual contact sheet generation
- Zero SQLite database, publishing queue, topic ledger, email, or scheduler side effects
"""

from datetime import datetime
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = r"D:\Agents\CrazzyDocs"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import DATABASE_PATH, FFMPEG_DEFAULT_PATH
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.orchestration.production_workspace import compute_file_sha256
from backend.app.services.animation_service import probe_video

FFMPEG_EXE = str(FFMPEG_DEFAULT_PATH)
STUDIO_DB_PATH = str(DATABASE_PATH)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("stage_3_21i_runner")


def snapshot_db_counts(db_path: str = STUDIO_DB_PATH) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table in (
            "content_ledger",
            "publishing_ledger",
            "publishing_ledger_archive",
            "persistent_jobs",
            "media_assets",
        ):
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = int(cur.fetchone()[0])
            except Exception:
                counts[table] = -1
    finally:
        conn.close()
    return counts


def extract_frames_and_contact_sheet(video_path: str, reports_dir: str, duration_sec: float) -> Dict:
    frames_dir = os.path.join(reports_dir, "inspected_frames")
    os.makedirs(frames_dir, exist_ok=True)

    # 8 checkpoints covering Hook, Context, Escalation, Revelation, Payoff, Late Narrative, Outro
    outro_ts = round(max(1.0, duration_sec - 0.8), 2)
    sample_specs = [
        ("01_hook", 1.5, "HOOK (1.5s)"),
        ("02_context_early", 7.0, "CONTEXT (7.0s)"),
        ("03_context_mid", 14.0, "CONTEXT (14.0s)"),
        ("04_escalation", 22.0, "ESCALATION (22.0s)"),
        ("05_revelation", 31.0, "REVELATION (31.0s)"),
        ("06_payoff", 40.0, "PAYOFF (40.0s)"),
        ("07_pre_outro", round(max(42.0, duration_sec - 3.2), 2), "PRE-OUTRO"),
        ("08_outro", outro_ts, f"OUTRO ({outro_ts}s)"),
    ]

    extracted: List[Dict] = []
    for slug, ts, label in sample_specs:
        safe_ts = min(max(0.2, ts), max(0.3, duration_sec - 0.15))
        out_png = os.path.join(frames_dir, f"frame_{slug}.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", f"{safe_ts:.2f}", "-i", video_path, "-vframes", "1", out_png],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if os.path.isfile(out_png):
            extracted.append({"label": label, "timestamp_sec": safe_ts, "path": out_png})

    # Compose 4x2 vertical contact sheet (scaled tiles with timestamp & stage header)
    tile_w, tile_h = 360, 640
    cols, rows = 4, 2
    pad = 24
    header_h = 80
    sheet_w = cols * tile_w + (cols + 1) * pad
    sheet_h = header_h + rows * (tile_h + 44) + pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 18, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 22)
        title_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
        title_font = font

    draw.text((pad, 26), "CRAZZYDOCS STAGE 3.21I — VALIDATION FRAME CONTACT SHEET", fill=(245, 248, 255), font=title_font)

    for idx, item in enumerate(extracted[: cols * rows]):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (tile_w + pad)
        y0 = header_h + r * (tile_h + 44)
        img = Image.open(item["path"]).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        sheet.paste(img, (x0, y0))
        draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], outline=(68, 92, 130), width=2)
        draw.text((x0 + 8, y0 + tile_h + 8), item["label"], fill=(190, 225, 255), font=font)

    contact_sheet_path = os.path.join(reports_dir, "contact_sheet_3_21i.png")
    sheet.save(contact_sheet_path, format="PNG")
    return {
        "contact_sheet_path": contact_sheet_path,
        "frames": extracted,
    }


def main():
    os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "VALIDATION"
    os.environ.pop("CRAZZYDOCS_TEST_MODE", None)

    pre_counts = snapshot_db_counts()
    logger.info("Pre-validation DB counts: %s", pre_counts)

    job_id = f"val_3_21i_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    orch = CrazzyDocsMasterOrchestrator()

    t0 = time.perf_counter()
    result = orch.execute_production_pipeline(
        job_id=job_id,
        execution_context=ExecutionContext.VALIDATION,
        use_live_pipeline=True,
    )
    wall_elapsed = round(time.perf_counter() - t0, 2)

    video_path = result["video_path"]
    thumb_path = result["thumbnail_path"]
    prod_dir = result["production_dir"]
    reports_dir = os.path.join(prod_dir, "reports")
    manifest_path = result["manifest_path"]

    probed = probe_video(video_path)
    frames_info = extract_frames_and_contact_sheet(video_path, reports_dir, float(probed["duration"]))

    post_counts = snapshot_db_counts()
    db_deltas = {k: post_counts[k] - pre_counts.get(k, 0) for k in post_counts}

    summary = {
        "job_id": job_id,
        "execution_context": "VALIDATION",
        "topic": result["topic"],
        "production_dir": prod_dir,
        "manifest_path": manifest_path,
        "video_path": video_path,
        "video_sha256": compute_file_sha256(video_path),
        "video_size_bytes": os.path.getsize(video_path),
        "thumbnail_path": thumb_path,
        "thumbnail_sha256": compute_file_sha256(thumb_path),
        "thumbnail_size_bytes": os.path.getsize(thumb_path),
        "probed_stream": probed,
        "beat_count": result.get("beat_count"),
        "wall_elapsed_sec": wall_elapsed,
        "qc_checks": result.get("qc_checks"),
        "integrity_report": result.get("integrity_report"),
        "notifications": result.get("notifications"),
        "publishing_status": result.get("publishing_status"),
        "contact_sheet_path": frames_info["contact_sheet_path"],
        "extracted_frames": frames_info["frames"],
        "pre_db_counts": pre_counts,
        "post_db_counts": post_counts,
        "db_deltas": db_deltas,
    }

    summary_path = os.path.join(reports_dir, "validation_run_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
