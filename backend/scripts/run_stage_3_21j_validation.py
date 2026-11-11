"""
Controlled Single-Video End-to-End Validation Runner — Stage 3.21J.

Executes ONE real live non-publishing run inside ExecutionContext.VALIDATION:
- Live external topic discovery + Wikipedia/Phys.org verification
- 5-stage structured factual script + Expressive Edge Neural TTS
- Isolated openly licensed photo acquisition (max reuse <= 2 by SHA-256)
- Bespoke generated analytical illustrations + 30 FPS explainer MP4 animations
- 1080x1920 @ 30 FPS render + 2.0s branded outro + multi-candidate thumbnail
- Real-time HTTP polling of http://127.0.0.1:8000/api/dashboard/progress capturing
  intermediate stage, asset, rendering, and thumbnail telemetry transitions
- Visual contact sheet + canonical asset inventory + zero SQLite/queue/email side effects
"""

from datetime import datetime
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List
import urllib.request

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = r"D:\Agents\CrazzyDocs"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.content.visual_diversity_policy import VisualDiversityValidator
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
logger = logging.getLogger("stage_3_21j_runner")


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


def extract_frames_and_contact_sheet(video_path: str, reports_dir: str, duration_sec: float) -> Dict[str, Any]:
    frames_dir = os.path.join(reports_dir, "inspected_frames")
    os.makedirs(frames_dir, exist_ok=True)

    outro_ts = round(max(1.0, duration_sec - 0.8), 2)
    sample_specs = [
        ("01_hook", 1.5, "HOOK (1.5s)"),
        ("02_context_early", 7.0, "CONTEXT (7.0s)"),
        ("03_context_mid", 14.0, "CONTEXT (14.0s)"),
        ("04_escalation_a", 20.0, "ESCALATION (20.0s)"),
        ("05_escalation_b", 26.0, "ESCALATION (26.0s)"),
        ("06_revelation", 32.0, "REVELATION (32.0s)"),
        ("07_payoff_a", 39.0, "PAYOFF (39.0s)"),
        ("08_payoff_b", 45.0, "PAYOFF (45.0s)"),
        ("09_pre_outro", round(max(46.0, duration_sec - 3.0), 2), "PRE-OUTRO"),
        ("10_outro", outro_ts, f"OUTRO ({outro_ts}s)"),
    ]

    extracted: List[Dict[str, Any]] = []
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

    tile_w, tile_h = 300, 533
    cols, rows = 5, 2
    pad = 20
    header_h = 74
    sheet_w = cols * tile_w + (cols + 1) * pad
    sheet_h = header_h + rows * (tile_h + 42) + pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 18, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 20)
        title_font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
        title_font = font

    draw.text((pad, 22), "CRAZZYDOCS STAGE 3.21J — VISUAL DIVERSITY FRAME CONTACT SHEET", fill=(245, 248, 255), font=title_font)

    for idx, item in enumerate(extracted[: cols * rows]):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (tile_w + pad)
        y0 = header_h + r * (tile_h + 42)
        img = Image.open(item["path"]).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        sheet.paste(img, (x0, y0))
        draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], outline=(68, 92, 130), width=2)
        draw.text((x0 + 6, y0 + tile_h + 8), item["label"], fill=(190, 225, 255), font=font)

    contact_sheet_path = os.path.join(reports_dir, "contact_sheet_3_21j.png")
    sheet.save(contact_sheet_path, format="PNG")
    return {"contact_sheet_path": contact_sheet_path, "frames": extracted}


def _poll_live_dashboard(stop_ev: threading.Event, target_id: str, snapshots: List[Dict[str, Any]]) -> None:
    """Polls http://127.0.0.1:8000/api/dashboard/progress during live execution."""
    url = "http://127.0.0.1:8000/api/dashboard/progress"
    last_sig = None
    while not stop_ev.is_set():
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            disp = data.get("active_production") or data.get("displayed_production") or {}
            if disp.get("production_id") == target_id:
                sig = (
                    disp.get("overall_status"),
                    disp.get("overall_progress"),
                    disp.get("current_stage"),
                    (disp.get("rendering") or {}).get("completed_scenes"),
                    (disp.get("assets") or {}).get("animations"),
                    (disp.get("thumbnail") or {}).get("composition_status"),
                )
                if sig != last_sig:
                    last_sig = sig
                    snapshots.append({
                        "polled_at_utc": datetime.utcnow().isoformat() + "Z",
                        "overall_status": disp.get("overall_status"),
                        "overall_progress": disp.get("overall_progress"),
                        "current_stage": disp.get("current_stage"),
                        "current_operation": disp.get("current_operation"),
                        "assets": disp.get("assets"),
                        "rendering": disp.get("rendering"),
                        "thumbnail": disp.get("thumbnail"),
                    })
        except Exception:
            pass
        stop_ev.wait(1.0)


def main():
    os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "VALIDATION"
    os.environ.pop("CRAZZYDOCS_TEST_MODE", None)

    pre_counts = snapshot_db_counts()
    logger.info("Pre-validation DB counts: %s", pre_counts)

    job_id = f"val_3_21j_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    snapshots: List[Dict[str, Any]] = []
    stop_ev = threading.Event()
    poller = threading.Thread(target=_poll_live_dashboard, args=(stop_ev, job_id, snapshots), daemon=True)
    poller.start()

    orch = CrazzyDocsMasterOrchestrator()
    t0 = time.perf_counter()
    try:
        result = orch.execute_production_pipeline(
            job_id=job_id,
            execution_context=ExecutionContext.VALIDATION,
            use_live_pipeline=True,
        )
    finally:
        # Capture one final post-completion sample before stopping
        time.sleep(1.2)
        stop_ev.set()
        poller.join(timeout=3.0)

    wall_elapsed = round(time.perf_counter() - t0, 2)
    video_path = result["video_path"]
    thumb_path = result["thumbnail_path"]
    prod_dir = result["production_dir"]
    reports_dir = os.path.join(prod_dir, "reports")
    manifest_path = result["manifest_path"]

    probed = probe_video(video_path)
    frames_info = extract_frames_and_contact_sheet(video_path, reports_dir, float(probed["duration"]))

    storyboard_path = os.path.join(prod_dir, "storyboards", f"storyboard_{job_id}.json")
    with open(storyboard_path, "r", encoding="utf-8") as f:
        sb_json = json.load(f)
    diversity_res = VisualDiversityValidator.validate_storyboard(
        sb_json.get("beats", []),
        topic_title=result["topic"],
        max_image_reuse=2,
        check_files_on_disk=True,
        workspace_root=prod_dir,
    )

    poll_log_path = os.path.join(reports_dir, "live_dashboard_poll_snapshots.json")
    with open(poll_log_path, "w", encoding="utf-8") as f:
        json.dump({"snapshot_count": len(snapshots), "snapshots": snapshots}, f, indent=2)

    post_counts = snapshot_db_counts()
    db_deltas = {k: post_counts[k] - pre_counts.get(k, 0) for k in post_counts}

    summary = {
        "job_id": job_id,
        "execution_context": "VALIDATION",
        "topic": result["topic"],
        "production_dir": prod_dir,
        "manifest_path": manifest_path,
        "storyboard_path": storyboard_path,
        "video_path": video_path,
        "video_sha256": compute_file_sha256(video_path),
        "video_size_bytes": os.path.getsize(video_path),
        "thumbnail_path": thumb_path,
        "thumbnail_sha256": compute_file_sha256(thumb_path),
        "thumbnail_size_bytes": os.path.getsize(thumb_path),
        "probed_stream": probed,
        "beat_count": result.get("beat_count"),
        "visual_diversity": {
            "is_valid": diversity_res.is_valid,
            "total_beats": diversity_res.total_beats,
            "distinct_assets": diversity_res.distinct_assets,
            "unique_images": diversity_res.unique_images,
            "unique_animations": diversity_res.unique_animations,
            "max_reuse_observed": diversity_res.max_reuse_observed,
            "violations": diversity_res.violations,
        },
        "live_dashboard_snapshots_captured": len(snapshots),
        "live_dashboard_poll_log": poll_log_path,
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
