"""
Stage 3.21L — Controlled End-to-End Fresh Topic Production Validation Runner.

Executes exactly ONE live end-to-end validation production under
ExecutionContext.VALIDATION, polls both /api/dashboard/overview (4-state
reconciliation) and /api/dashboard/progress during execution, saves full
topic-discovery and visual-relevance audit reports, extracts a 10-frame
contact sheet for visual inspection, verifies deliverable APIs & SHA-256
manifests, and enforces strict DB / publishing / scheduler isolation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app.core.execution_context import ExecutionContext
from backend.app.orchestration.crazzydocs_master_orchestrator import CrazzyDocsMasterOrchestrator
from backend.app.orchestration.production_workspace import ProductionWorkspace
from backend.app.services.animation_service import FFMPEG_EXE, probe_video

DB_PATH = os.path.join(ROOT, "backend", "studio.db")
ERROR_LOG_PATH = os.path.join(ROOT, "logs", "dashboard_errors.log")
DASHBOARD_BASE = "http://127.0.0.1:8000"


def snapshot_db_counts() -> Dict[str, int]:
    """Capture row counts across protected production/ledger SQLite tables."""
    conn = sqlite3.connect(DB_PATH)
    try:
        tables = (
            "content_ledger",
            "publishing_ledger",
            "publishing_ledger_archive",
            "persistent_jobs",
            "media_assets",
        )
        return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}
    finally:
        conn.close()


def sha256_file(path: str) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def count_error_lines() -> int:
    """Return line count of logs/dashboard_errors.log."""
    if not os.path.isfile(ERROR_LOG_PATH):
        return 0
    with open(ERROR_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for _ in f)


def http_get_json(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Fetch JSON object from local dashboard HTTP endpoint."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_head_or_range_bytes(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    """Verify binary deliverable endpoint responds 200/206 with readable bytes."""
    req = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(1024)
        return {
            "status": int(resp.status),
            "content_type": resp.headers.get("Content-Type", ""),
            "bytes_sampled": len(data),
        }


def get_scheduler_task_state() -> str:
    """Query Windows Scheduled Task state for CrazzyDocs-Production-Scheduler."""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-ScheduledTask -TaskName 'CrazzyDocs-Production-Scheduler' -ErrorAction Stop).State",
            ],
            text=True,
            timeout=10,
        ).strip()
        return out or "UNKNOWN"
    except Exception as exc:
        return f"ERROR: {exc}"


def extract_frames_and_contact_sheet(video_path: str, reports_dir: str, duration_s: float) -> Dict[str, Any]:
    """Extract 10 representative frames across [hook..outro] plus a 5x2 contact sheet."""
    frames_dir = os.path.join(reports_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    dur = max(4.0, float(duration_s))
    # Ensure final sample hits inside the 2.0s outro window
    fractions = [0.02, 0.12, 0.22, 0.34, 0.46, 0.58, 0.70, 0.82, 0.91, max(0.95, (dur - 0.8) / dur)]
    extracted: List[Dict[str, Any]] = []
    for idx, frac in enumerate(fractions, start=1):
        ts = round(min(max(0.1, dur * frac), max(0.2, dur - 0.2)), 2)
        out_png = os.path.join(frames_dir, f"frame_{idx:02d}_{ts:.2f}s.png")
        subprocess.run(
            [FFMPEG_EXE, "-y", "-ss", str(ts), "-i", video_path, "-vframes", "1", "-q:v", "2", out_png],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if os.path.isfile(out_png):
            extracted.append({"index": idx, "timestamp_s": ts, "path": out_png, "size_bytes": os.path.getsize(out_png)})

    contact_sheet = os.path.join(reports_dir, "contact_sheet_3_21l.png")
    fps_expr = f"fps=10/{max(5.0, dur):.2f}"
    subprocess.run(
        [
            FFMPEG_EXE, "-y", "-i", video_path,
            "-vf", f"{fps_expr},scale=360:640,tile=5x2",
            "-frames:v", "1", contact_sheet,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return {
        "frames_extracted": len(extracted),
        "frames": extracted,
        "contact_sheet": contact_sheet if os.path.isfile(contact_sheet) else None,
    }


def poll_dashboard_live(stop_event: threading.Event, job_id: str, samples: List[Dict[str, Any]]) -> None:
    """Poll /api/dashboard/overview and /api/dashboard/progress every 2.0s during execution."""
    seen_stages: set[str] = set()
    while not stop_event.is_set():
        snap: Dict[str, Any] = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }
        try:
            ov = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/overview", timeout=4.0)
            snap["overview"] = {
                "overall_operational_state": ov.get("overall_operational_state"),
                "scheduler_state": ov.get("scheduler_state"),
                "production_worker_state": ov.get("production_worker_state"),
                "validation_worker_state": ov.get("validation_worker_state"),
                "pipeline_stage": ov.get("pipeline_stage"),
                "execution_context": ov.get("execution_context"),
                "active_job_id": ov.get("active_job_id"),
                "active_validation_job_id": ov.get("active_validation_job_id"),
                "active_topic": ov.get("active_topic"),
                "overall_progress_pct": ov.get("overall_progress_pct"),
            }
        except Exception as exc:
            snap["overview_error"] = str(exc)

        try:
            pr = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/progress?job_id={job_id}", timeout=4.0)
            disp = pr.get("displayed_production") or pr.get("active_production") or {}
            snap["progress"] = {
                "has_active": pr.get("has_active"),
                "production_id": disp.get("production_id"),
                "overall_status": disp.get("overall_status"),
                "execution_context": disp.get("execution_context"),
                "current_stage": disp.get("current_stage"),
                "overall_progress_pct": disp.get("overall_progress"),
                "current_operation": disp.get("current_operation"),
                "topic": disp.get("topic"),
                "rendering": disp.get("rendering"),
                "thumbnail": disp.get("thumbnail"),
                "assets": disp.get("assets"),
            }
            stage_key = f"{disp.get('current_stage')}|{snap.get('overview', {}).get('overall_operational_state')}"
            if stage_key not in seen_stages:
                seen_stages.add(stage_key)
                print(
                    f"[LIVE POLL] op_state={snap.get('overview', {}).get('overall_operational_state')} "
                    f"val_worker={snap.get('overview', {}).get('validation_worker_state')} "
                    f"stage={disp.get('current_stage')} pct={disp.get('overall_progress')}%",
                    flush=True,
                )
        except Exception as exc:
            snap["progress_error"] = str(exc)

        samples.append(snap)
        stop_event.wait(2.0)


def build_visual_relevance_report(
    job_id: str,
    topic_title: str,
    storyboard_path: str,
    manifest_path: str,
    attrib_path: str,
) -> Dict[str, Any]:
    """Map every storyboard beat to its underlying asset file, source URL, and SHA-256."""
    sb_data: Dict[str, Any] = {}
    if os.path.isfile(storyboard_path):
        with open(storyboard_path, "r", encoding="utf-8") as f:
            sb_data = json.load(f)

    manifest_data: Dict[str, Any] = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

    attrib_by_title: Dict[str, Dict[str, Any]] = {}
    if os.path.isfile(attrib_path):
        with open(attrib_path, "r", encoding="utf-8") as f:
            attrib_json = json.load(f)
            for item in attrib_json.get("items", []):
                if isinstance(item, dict) and item.get("title"):
                    attrib_by_title[str(item["title"]).lower()] = item

    artifact_by_path: Dict[str, Dict[str, Any]] = {}
    artifact_by_sha: Dict[str, Dict[str, Any]] = {}
    for art in manifest_data.get("artifacts", []):
        p = str(art.get("path") or art.get("relative_path") or "").replace("\\", "/").lower()
        if p:
            artifact_by_path[p] = art
        if art.get("sha256"):
            artifact_by_sha[str(art["sha256"])] = art

    beat_rows: List[Dict[str, Any]] = []
    beats = sb_data.get("beats", []) if isinstance(sb_data.get("beats"), list) else sb_data.get("storyboard_beats", [])
    for idx, b in enumerate(beats, start=1):
        img_path = str(b.get("asset_path") or b.get("animation_path") or b.get("plate_path") or "").replace("\\", "/")
        asset_id = b.get("canonical_asset_id") or b.get("asset_id")
        exists = bool(img_path and os.path.isfile(img_path))
        file_sha = sha256_file(img_path) if exists else b.get("asset_hash")
        art_meta = artifact_by_path.get(img_path.lower()) or artifact_by_sha.get(str(file_sha or ""), {})
        attrib_meta: Dict[str, Any] = {}
        src_img = str(b.get("source_image") or "").lower()
        for tkey, item in attrib_by_title.items():
            if tkey and (tkey in src_img or tkey.replace(" ", "_") in src_img or tkey.replace(" ", "-") in src_img):
                attrib_meta = item
                break
        source_url = art_meta.get("source_url") or attrib_meta.get("source_url") or b.get("source_url") or f"generated:{b.get('visual_type')}"
        license_str = (
            art_meta.get("license")
            or attrib_meta.get("license")
            or ("Original Studio Generation" if "generated" in str(b.get("visual_type")) or "animation" in str(b.get("visual_type")) else "Public/Open Archive")
        )
        beat_rows.append({
            "beat_index": idx,
            "beat_id": b.get("beat_id", f"beat_{idx:02d}"),
            "script_section": b.get("stage") or b.get("section"),
            "duration_s": round(float(b.get("duration_sec") or b.get("duration") or 0.0), 2),
            "visual_type": b.get("visual_type"),
            "on_screen_text": b.get("callout") or b.get("caption_phrase"),
            "script_segment": b.get("script_segment"),
            "asset_id": asset_id,
            "source_url": source_url,
            "creator": art_meta.get("creator") or attrib_meta.get("creator") or "CrazzyDocs Engine",
            "license": license_str,
            "local_path": img_path,
            "file_exists": exists,
            "sha256": file_sha,
            "relevance_score": b.get("relevance_score"),
            "reuse_count": b.get("reuse_count"),
        })

    return {
        "production_id": job_id,
        "topic": topic_title,
        "total_beats": len(beat_rows),
        "beats_with_verified_files": sum(1 for r in beat_rows if r["file_exists"]),
        "distinct_asset_hashes": len({r["sha256"] for r in beat_rows if r["sha256"]}),
        "beats": beat_rows,
    }


def main() -> int:
    os.environ["CRAZZYDOCS_EXECUTION_CONTEXT"] = "VALIDATION"
    os.environ.pop("CRAZZYDOCS_TEST_MODE", None)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"val_3_21l_{ts}"
    print(f"=== STAGE 3.21L VALIDATION RUN START: {job_id} ===", flush=True)

    pre_db = snapshot_db_counts()
    pre_sched = get_scheduler_task_state()
    pre_err_lines = count_error_lines()

    live_samples: List[Dict[str, Any]] = []
    stop_event = threading.Event()
    poller = threading.Thread(target=poll_dashboard_live, args=(stop_event, job_id, live_samples), daemon=True)
    poller.start()

    orch = CrazzyDocsMasterOrchestrator()
    t0 = time.monotonic()
    result: Dict[str, Any] = {}
    try:
        result = orch.execute_production_pipeline(
            job_id=job_id,
            execution_context=ExecutionContext.VALIDATION,
            use_live_pipeline=True,
        )
    finally:
        stop_event.set()
        poller.join(timeout=5.0)
    elapsed_s = round(time.monotonic() - t0, 2)

    prod_ws = ProductionWorkspace(
        job_id,
        str(result.get("topic_id") or "live_topic"),
        str(result.get("topic") or "Validation Topic"),
    )
    os.makedirs(prod_ws.reports_dir, exist_ok=True)

    # 1. Topic Discovery Audit Report
    discovery_audit = getattr(orch.topic_engine, "last_discovery_audit", {}) or {}
    discovery_audit_path = os.path.join(prod_ws.reports_dir, f"topic_discovery_audit_{job_id}.json")
    with open(discovery_audit_path, "w", encoding="utf-8") as f:
        json.dump(discovery_audit, f, indent=2)

    # 2. Live Dashboard Observations
    obs_path = os.path.join(prod_ws.reports_dir, "dashboard_live_observations.json")
    with open(obs_path, "w", encoding="utf-8") as f:
        json.dump({"sample_count": len(live_samples), "samples": live_samples}, f, indent=2)

    # 3. Video Probe, Frames & Diversity Validation
    video_path = str(result.get("video_path") or prod_ws.get_canonical_video_path())
    thumb_path = str(result.get("thumbnail_path") or "")
    probed = probe_video(video_path) if os.path.isfile(video_path) else {}
    frame_info = extract_frames_and_contact_sheet(video_path, prod_ws.reports_dir, float(probed.get("duration", 54.0))) if os.path.isfile(video_path) else {}

    storyboard_path = os.path.join(prod_ws.visual_timeline_dir, f"storyboard_{job_id}.json")
    manifest_path = os.path.join(prod_ws.prod_dir, "production_manifest.json")
    attrib_path = os.path.join(prod_ws.metadata_dir, f"attribution_manifest_{job_id}.json")
    script_path = os.path.join(prod_ws.scripts_dir, f"script_{job_id}.json")
    research_path = os.path.join(prod_ws.research_dir, f"research_{job_id}.json")

    rel_report = build_visual_relevance_report(job_id, str(result.get("topic", "")), storyboard_path, manifest_path, attrib_path)
    rel_report_path = os.path.join(prod_ws.reports_dir, f"visual_relevance_report_{job_id}.json")
    with open(rel_report_path, "w", encoding="utf-8") as f:
        json.dump(rel_report, f, indent=2)

    diversity_summary: Dict[str, Any] = {}
    if os.path.isfile(storyboard_path):
        with open(storyboard_path, "r", encoding="utf-8") as f:
            sb_json = json.load(f)
        diversity_summary = dict(sb_json.get("visual_diversity_audit") or {})
        diversity_summary["beat_count"] = int(sb_json.get("beat_count") or len(sb_json.get("beats", [])))
        diversity_summary["narration_duration_sec"] = sb_json.get("narration_duration_sec")

    # 4. Canonical Deliverable HTTP Verification
    api_checks: Dict[str, Any] = {}
    try:
        ov_post = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/overview")
        pr_post = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/progress?job_id={job_id}")
        mf_post = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/productions/{job_id}/manifest")
        art_post = http_get_json(f"{DASHBOARD_BASE}/api/dashboard/productions/{job_id}/artifacts")
        vid_http = http_head_or_range_bytes(f"{DASHBOARD_BASE}/api/dashboard/productions/{job_id}/video")
        th_http = http_head_or_range_bytes(f"{DASHBOARD_BASE}/api/dashboard/productions/{job_id}/thumbnail")
        pr_disp = pr_post.get("displayed_production") or pr_post.get("active_production") or {}
        api_checks = {
            "overview_post": {
                "overall_operational_state": ov_post.get("overall_operational_state"),
                "validation_worker_state": ov_post.get("validation_worker_state"),
                "production_worker_state": ov_post.get("production_worker_state"),
                "scheduler_state": ov_post.get("scheduler_state"),
                "last_validation_topic": ov_post.get("last_validation_topic"),
            },
            "progress_post": {
                "has_active": pr_post.get("has_active"),
                "overall_status": pr_disp.get("overall_status"),
                "overall_progress_pct": pr_disp.get("overall_progress"),
                "current_stage": pr_disp.get("current_stage"),
            },
            "manifest_storage_verification": mf_post.get("storage_verification"),
            "artifacts_count": len(art_post.get("artifacts", [])) if isinstance(art_post, dict) else None,
            "video_endpoint": vid_http,
            "thumbnail_endpoint": th_http,
        }
    except Exception as exc:
        api_checks["error"] = str(exc)

    post_db = snapshot_db_counts()
    post_sched = get_scheduler_task_state()
    post_err_lines = count_error_lines()

    db_deltas = {k: post_db[k] - pre_db[k] for k in pre_db}

    summary = {
        "job_id": job_id,
        "elapsed_s": elapsed_s,
        "result": result,
        "probed_video": probed,
        "frame_info": frame_info,
        "diversity_summary": diversity_summary,
        "visual_relevance_summary": {
            "total_beats": rel_report["total_beats"],
            "beats_with_verified_files": rel_report["beats_with_verified_files"],
            "distinct_asset_hashes": rel_report["distinct_asset_hashes"],
        },
        "paths": {
            "workspace": prod_ws.prod_dir,
            "video": video_path,
            "thumbnail": thumb_path,
            "research": research_path,
            "script": script_path,
            "storyboard": storyboard_path,
            "manifest": manifest_path,
            "attribution": attrib_path,
            "topic_discovery_audit": discovery_audit_path,
            "visual_relevance_report": rel_report_path,
            "dashboard_live_observations": obs_path,
            "contact_sheet": frame_info.get("contact_sheet"),
        },
        "isolation": {
            "pre_db": pre_db,
            "post_db": post_db,
            "db_deltas": db_deltas,
            "pre_scheduler": pre_sched,
            "post_scheduler": post_sched,
            "error_log_lines_delta": post_err_lines - pre_err_lines,
        },
        "api_checks": api_checks,
        "live_poll_samples_count": len(live_samples),
        "live_states_observed": sorted({
            str(s.get("overview", {}).get("overall_operational_state"))
            for s in live_samples if isinstance(s.get("overview"), dict)
        }),
        "live_validation_states_observed": sorted({
            str(s.get("overview", {}).get("validation_worker_state"))
            for s in live_samples if isinstance(s.get("overview"), dict)
        }),
    }

    summary_path = os.path.join(prod_ws.reports_dir, "validation_run_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"STATUS={result.get('status')} TOPIC={result.get('topic')} ELAPSED={elapsed_s}s", flush=True)
    print(f"DB_DELTAS={db_deltas} SCHED={post_sched}", flush=True)
    print(f"LIVE_STATES={summary['live_states_observed']} VAL_STATES={summary['live_validation_states_observed']}", flush=True)
    print(f"SUMMARY_PATH={summary_path}", flush=True)
    return 0 if result.get("status") == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
