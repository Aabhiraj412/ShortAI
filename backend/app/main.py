"""
FastAPI Application Entry Point for AI YouTube Shorts Studio.
Provides full REST API for Dashboard, Agent controls, Pipeline runs, Media streaming, and Settings.
"""
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import (
    init_db, get_db, JobModel, TopicModel, ScriptModel, StoryboardModel, VideoJobModel, PublicationModel, StudioSettingModel
)
from app.workers.pipeline import pipeline
from app.workers.job_manager import job_manager
from app.scheduler.scheduler import start_scheduler, get_scheduler_status
from app.services.youtube_service import YouTubePublisherService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    if os.getenv("ENABLE_EMBEDDED_SCHEDULER", "false").lower() in ("true", "1"):
        start_scheduler()
    yield
    # Shutdown

app = FastAPI(
    title="AI YouTube Shorts Studio API",
    description="Backend API powering autonomous 2D/2.5D animated YouTube Shorts production.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static directories for assets, branding, and media streaming
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")
app.mount("/thumbnails", StaticFiles(directory=settings.THUMB_DIR), name="thumbnails")


@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    """Serves the official YouTube icon as browser favicon."""
    icon_path = os.path.join(STATIC_DIR, "branding", "Youtube-Icon.png")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(settings.BASE_DIR, "media", "source", "branding", "Youtube-Icon.png")
    if not os.path.exists(icon_path):
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(
        icon_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, must-revalidate"}
    )

# Request Models
class PipelineTriggerRequest(BaseModel):
    manual_topic: Optional[str] = None
    manual_script: Optional[str] = None
    auto_upload: bool = False

class SettingsUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    publishing_mode: Optional[str] = None
    daily_publish_time_ist: Optional[str] = None
    default_voice: Optional[str] = None

# --- Pipeline Endpoints ---
@app.post("/api/pipeline/run")
async def trigger_pipeline(req: PipelineTriggerRequest, bg_tasks: BackgroundTasks):
    if pipeline.is_running:
        raise HTTPException(status_code=400, detail="Pipeline is already running.")
    
    bg_tasks.add_task(
        pipeline.run_pipeline,
        manual_topic=req.manual_topic,
        manual_script=req.manual_script,
        auto_upload=req.auto_upload
    )
    return {"status": "started", "message": "Pipeline initiated in background."}

@app.get("/api/pipeline/status")
async def get_pipeline_status():
    return {
        "is_running": pipeline.is_running,
        "current_stage": pipeline.current_stage,
        "logs": pipeline.logs[-35:]
    }

# --- Persistent Job Queue Endpoints ---
@app.get("/api/jobs")
async def get_jobs(db: Session = Depends(get_db)):
    return db.query(JobModel).order_by(JobModel.created_at.desc()).limit(20).all()

@app.post("/api/jobs/recover")
async def recover_jobs():
    recovered = job_manager.recover_stale_jobs()
    return {"status": "success", "recovered_count": len(recovered), "recovered_jobs": recovered}

@app.post("/api/jobs/run")
async def run_daily_job(req: PipelineTriggerRequest, bg_tasks: BackgroundTasks):
    job, is_new = job_manager.get_or_create_daily_job()
    bg_tasks.add_task(job_manager.run_job, job.id, manual_topic=req.manual_topic)
    return {"status": "started", "job_id": job.id, "is_new": is_new}

# --- Overview & Dashboard Stats ---
@app.get("/api/overview")
async def get_overview(db: Session = Depends(get_db)):
    v_jobs = db.query(VideoJobModel).all()
    pubs = db.query(PublicationModel).all()
    topics = db.query(TopicModel).all()
    
    total_created = len(v_jobs)
    published = len([p for p in pubs if p.status == "published"])
    pending = len([p for p in pubs if p.status in ("draft", "ready_for_approval", "scheduled")])
    failed = len([v for v in v_jobs if v.status in ("failed", "qc_failed")])
    
    sched = get_scheduler_status()
    
    next_pub = sched.get("next_scheduled_run") or sched.get("publishing_job", {}).get("next_run")
    
    return {
        "total_created": total_created,
        "published": published,
        "pending": pending,
        "failed": failed,
        "next_publication": next_pub,
        "pipeline_state": pipeline.current_stage,
        "publishing_mode": settings.PUBLISHING_MODE
    }

# --- Topics Endpoints ---
@app.get("/api/topics")
async def get_topics(db: Session = Depends(get_db)):
    return db.query(TopicModel).order_by(TopicModel.created_at.desc()).limit(20).all()

# --- Scripts Endpoints ---
@app.get("/api/scripts")
async def get_scripts(db: Session = Depends(get_db)):
    return db.query(ScriptModel).order_by(ScriptModel.created_at.desc()).limit(15).all()

# --- Storyboard Endpoints ---
@app.get("/api/storyboards")
async def get_storyboards(db: Session = Depends(get_db)):
    return db.query(StoryboardModel).order_by(StoryboardModel.created_at.desc()).limit(10).all()

# --- Videos & Jobs Endpoints ---
@app.get("/api/videos")
async def get_videos(db: Session = Depends(get_db)):
    jobs = db.query(VideoJobModel).order_by(VideoJobModel.created_at.desc()).limit(20).all()
    results = []
    for j in jobs:
        v_url = None
        t_url = None
        if j.video_path and os.path.exists(j.video_path):
            v_url = f"/media/video/{os.path.basename(j.video_path)}"
        if j.thumbnail_path and os.path.exists(j.thumbnail_path):
            t_url = f"/thumbnails/{os.path.basename(j.thumbnail_path)}"
        results.append({
            "id": j.id,
            "status": j.status,
            "duration": j.duration,
            "file_size_mb": j.file_size_mb,
            "video_url": v_url,
            "thumbnail_url": t_url,
            "created_at": j.created_at.isoformat() if j.created_at else None
        })
    return results

# --- Publishing Endpoints ---
@app.get("/api/publishing")
async def get_publishing_status(db: Session = Depends(get_db)):
    pubs = db.query(PublicationModel).order_by(PublicationModel.created_at.desc()).limit(20).all()
    yt_service = YouTubePublisherService()
    auth_status = yt_service.get_auth_status()
    return {
        "youtube_auth": auth_status,
        "publications": pubs,
        "scheduler": get_scheduler_status()
    }

# --- Settings Endpoints ---
@app.get("/api/settings")
async def get_settings():
    return {
        "gemini_api_key_configured": bool(settings.GEMINI_API_KEY),
        "publishing_mode": settings.PUBLISHING_MODE,
        "daily_publish_time_ist": settings.DAILY_PUBLISH_TIME_IST,
        "default_voice": settings.DEFAULT_VOICE,
        "base_dir": settings.BASE_DIR
    }

@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    if req.gemini_api_key is not None:
        settings.GEMINI_API_KEY = req.gemini_api_key
    if req.publishing_mode is not None:
        settings.PUBLISHING_MODE = req.publishing_mode
    if req.daily_publish_time_ist is not None:
        settings.DAILY_PUBLISH_TIME_IST = req.daily_publish_time_ist
    if req.default_voice is not None:
        settings.DEFAULT_VOICE = req.default_voice
    return {"status": "success", "message": "Settings updated successfully."}

# --- Browser Production Health Dashboard Endpoints ---
@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the rich, browser-accessible CrazzyDocs production health dashboard."""
    tmpl_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if not os.path.exists(tmpl_path):
        raise HTTPException(status_code=404, detail="Dashboard template not found.")
    with open(tmpl_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)


@app.get("/api/dashboard/overview")
async def get_dashboard_overview():
    from backend.app.services.production_health_dashboard import production_health_dashboard
    from backend.app.scheduler.schedule_profiles import get_active_profile, IST_TZ
    from datetime import datetime, timedelta
    
    m = production_health_dashboard.get_health_metrics()
    states = production_health_dashboard.get_reconciled_operational_states(metrics=m)
    profile = get_active_profile()
    now_ist = datetime.now(IST_TZ)
    
    # Calculate target publishing time
    next_pub_str = "Calculating..."
    for cycle in profile.cycles:
        c_start, c_pub, _, _ = cycle.calculate_datetimes(now_ist)
        if c_pub > now_ist:
            next_pub_str = f"{c_pub.strftime('%Y-%m-%d %H:%M:%S IST')} ({cycle.name})"
            break
    if next_pub_str == "Calculating...":
        tomorrow = now_ist + timedelta(days=1)
        _, c_pub, _, _ = profile.cycles[0].calculate_datetimes(tomorrow)
        next_pub_str = f"{c_pub.strftime('%Y-%m-%d %H:%M:%S IST')} ({profile.cycles[0].name})"

    from backend.app.content.creative_production_policy import (
        CategoryDistributionTracker,
        get_creative_production_policy,
    )
    from backend.app.core.execution_context import ExecutionContext

    creative_policy = get_creative_production_policy(ExecutionContext.PRODUCTION).to_summary_dict()
    category_distribution = CategoryDistributionTracker().get_distribution_counts(window=20)

    return {
        "current_cycle": m.current_cycle,
        "next_production_ist": m.next_production_ist,
        "target_publishing_ist": next_pub_str,
        "pipeline_stage": m.pipeline_stage,
        "active_profile": profile.name,
        "overall_health": m.overall_health,
        "scheduler_state": states["scheduler_state"],
        "production_worker_state": states["production_worker_state"],
        "production_worker": states["production_worker"],
        "validation_worker_state": states["validation_worker_state"],
        "validation_worker": states["validation_worker"],
        "overall_operational_state": states["overall_operational_state"],
        "creative_policy": creative_policy,
        "category_distribution": category_distribution,
        "timestamp_ist": m.timestamp_ist,
    }


@app.get("/api/dashboard/stats")
async def get_dashboard_stats(filter: str = "all"):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_production_statistics(time_filter=filter)


@app.get("/api/dashboard/health")
async def get_dashboard_health():
    from backend.app.services.production_health_dashboard import production_health_dashboard
    m = production_health_dashboard.get_health_metrics()
    services = production_health_dashboard.get_services_status()
    tasks = production_health_dashboard.get_windows_task_status()
    states = production_health_dashboard.get_reconciled_operational_states(
        metrics=m, services=services, windows_tasks=tasks
    )
    return {
        "overall_health": m.overall_health,
        "operational_states": states,
        "recovery_status": m.recovery_status,
        "network_status": m.network_status,
        "scheduler_heartbeat": m.scheduler_heartbeat,
        "scheduler_daemon": services["scheduler_daemon"],
        "dashboard_service": services["dashboard_service"],
        "publishing_queue": m.publishing_queue,
        "failed_qc_count": m.failed_qc_jobs["count"],
        "email_notifications": m.email_notifications,
        "windows_task": tasks,
        "windows_tasks": tasks,
        "database": production_health_dashboard.get_database_health(),
        "disk": production_health_dashboard.get_disk_health(),
        "archive_summary": production_health_dashboard.get_archived_publishing_summary(),
        "publishing_ledger_safety": production_health_dashboard.get_publishing_ledger_safety(),
    }


@app.get("/api/dashboard/history")
async def get_dashboard_history(limit: int = 25):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_publishing_history(limit=limit)


@app.get("/api/dashboard/archive")
async def get_dashboard_archive(limit: int = 25):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return {
        "summary": production_health_dashboard.get_archived_publishing_summary(),
        "recent_archived": production_health_dashboard.get_archived_publishing_history(limit=limit),
    }


@app.get("/api/dashboard/recovery")
async def get_dashboard_recovery(limit: int = 15):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_recovery_history(limit=limit)


@app.get("/api/dashboard/activity")
async def get_dashboard_activity(limit: int = 25):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_recent_activity(limit=limit)


def _validate_production_id(production_id: str) -> str:
    import re
    if not re.match(r"^[A-Za-z0-9_.\-]+$", production_id or ""):
        raise HTTPException(status_code=400, detail="Invalid production ID")
    return production_id


def _resolve_production_deliverables(production_id: str) -> dict:
    from backend.app.core.paths import PROJECT_ROOT_STR
    _validate_production_id(production_id)
    prod_dir = os.path.abspath(os.path.join(PROJECT_ROOT_STR, "media", "productions", production_id))
    video_dir = os.path.join(prod_dir, "video")
    thumb_dir = os.path.join(prod_dir, "thumbnails")
    manifest_path = os.path.join(prod_dir, "production_manifest.json")

    video_path = None
    for cand in (
        os.path.join(video_dir, f"showcase_{production_id}.mp4"),
        os.path.join(video_dir, f"final_{production_id}.mp4"),
    ):
        if os.path.isfile(cand):
            video_path = cand
            break
    if not video_path and os.path.isdir(video_dir):
        for fn in sorted(os.listdir(video_dir)):
            if fn.lower().endswith(".mp4"):
                video_path = os.path.join(video_dir, fn)
                break

    thumb_path = None
    for cand in (
        os.path.join(thumb_dir, f"thumb_{production_id}.png"),
        os.path.join(thumb_dir, "variants", "cand_01_topic_focal.png"),
        os.path.join(thumb_dir, "variants", "cand_3_mystery.png"),
    ):
        if os.path.isfile(cand):
            thumb_path = cand
            break
    if not thumb_path and os.path.isdir(thumb_dir):
        for fn in sorted(os.listdir(thumb_dir)):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                thumb_path = os.path.join(thumb_dir, fn)
                break

    manifest_exists = os.path.isfile(manifest_path)
    return {
        "production_id": production_id,
        "workspace_abs_dir": prod_dir,
        "workspace_exists": os.path.isdir(prod_dir),
        "video_path": video_path,
        "video_exists": bool(video_path and os.path.isfile(video_path)),
        "video_size_bytes": os.path.getsize(video_path) if (video_path and os.path.isfile(video_path)) else 0,
        "video_url": f"/api/dashboard/productions/{production_id}/video" if (video_path and os.path.isfile(video_path)) else None,
        "thumbnail_path": thumb_path,
        "thumbnail_exists": bool(thumb_path and os.path.isfile(thumb_path)),
        "thumbnail_size_bytes": os.path.getsize(thumb_path) if (thumb_path and os.path.isfile(thumb_path)) else 0,
        "thumbnail_url": f"/api/dashboard/productions/{production_id}/thumbnail" if (thumb_path and os.path.isfile(thumb_path)) else None,
        "manifest_path": manifest_path if manifest_exists else None,
        "manifest_exists": manifest_exists,
        "manifest_url": f"/api/dashboard/productions/{production_id}/manifest" if manifest_exists else None,
        "artifacts_url": f"/api/dashboard/productions/{production_id}/artifacts",
    }


def _enrich_progress_record(rec: Optional[dict]) -> Optional[dict]:
    if not rec or not isinstance(rec, dict):
        return rec
    pid = rec.get("production_id")
    if pid:
        rec = dict(rec)
        rec["storage"] = _resolve_production_deliverables(pid)
    return rec


@app.get("/api/dashboard/progress")
async def get_dashboard_progress(include_tests: bool = False):
    from backend.app.services.production_observability import production_observability
    payload = production_observability.get_dashboard_payload(include_tests=include_tests)
    if isinstance(payload, dict):
        payload = dict(payload)
        payload["active_production"] = _enrich_progress_record(payload.get("active_production"))
        payload["displayed_production"] = _enrich_progress_record(payload.get("displayed_production"))
        payload["history"] = [_enrich_progress_record(h) for h in (payload.get("history") or [])]
    return payload


@app.get("/api/dashboard/progress/active")
async def get_dashboard_active_progress(include_tests: bool = False):
    from backend.app.services.production_observability import production_observability
    active = _enrich_progress_record(production_observability.get_active_production(include_tests=include_tests))
    return {"has_active": active is not None, "active_production": active}


@app.get("/api/dashboard/progress/history")
async def get_dashboard_progress_history(limit: int = 15, include_tests: bool = False):
    from backend.app.services.production_observability import production_observability
    items = [_enrich_progress_record(h) for h in production_observability.get_history(limit=limit, include_tests=include_tests)]
    return {"productions": items}


@app.get("/api/dashboard/progress/{production_id}")
async def get_dashboard_production_progress(production_id: str, include_tests: bool = True):
    from backend.app.services.production_observability import production_observability
    rec = production_observability.get_production(production_id, include_tests=include_tests)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Production progress record '{production_id}' not found")
    return _enrich_progress_record(rec)


@app.get("/api/dashboard/productions/{production_id}/thumbnail")
async def get_production_thumbnail(production_id: str):
    info = _resolve_production_deliverables(production_id)
    thumb_p = info.get("thumbnail_path")
    base_dir = os.path.abspath(os.path.join(info["workspace_abs_dir"], "thumbnails"))
    if thumb_p and os.path.isfile(thumb_p) and os.path.abspath(thumb_p).startswith(base_dir):
        media_t = "image/jpeg" if thumb_p.lower().endswith((".jpg", ".jpeg")) else "image/png"
        return FileResponse(thumb_p, media_type=media_t)
    raise HTTPException(status_code=404, detail="Thumbnail preview not yet generated")


@app.get("/api/dashboard/productions/{production_id}/video")
async def get_production_video(production_id: str):
    info = _resolve_production_deliverables(production_id)
    vid_p = info.get("video_path")
    base_dir = os.path.abspath(os.path.join(info["workspace_abs_dir"], "video"))
    if vid_p and os.path.isfile(vid_p) and os.path.abspath(vid_p).startswith(base_dir):
        return FileResponse(vid_p, media_type="video/mp4", filename=os.path.basename(vid_p))
    raise HTTPException(status_code=404, detail="Final video file not yet generated")


@app.get("/api/dashboard/productions/{production_id}/manifest")
async def get_production_manifest(production_id: str):
    import json
    from backend.app.orchestration.production_workspace import compute_file_sha256
    info = _resolve_production_deliverables(production_id)
    m_path = info.get("manifest_path")
    if not m_path or not os.path.isfile(m_path):
        raise HTTPException(status_code=404, detail=f"Manifest not found for production '{production_id}'")
    with open(m_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    verified_artifacts = []
    missing_count = 0
    mismatch_count = 0
    for art in manifest.get("artifacts", []):
        fp = art.get("file_path") or ""
        exists = bool(fp and os.path.isfile(fp))
        disk_size = os.path.getsize(fp) if exists else 0
        sha_ok = True
        if exists and art.get("sha256"):
            sha_ok = (compute_file_sha256(fp) == art.get("sha256"))
        if not exists:
            missing_count += 1
        elif not sha_ok:
            mismatch_count += 1
        verified_artifacts.append({**art, "disk_exists": exists, "disk_size_bytes": disk_size, "sha256_verified": sha_ok})
    manifest["storage_verification"] = {
        "total_artifacts": len(verified_artifacts),
        "missing_count": missing_count,
        "sha256_mismatch_count": mismatch_count,
        "all_verified": (missing_count == 0 and mismatch_count == 0),
    }
    manifest["resolved_storage"] = info
    manifest["artifacts"] = verified_artifacts
    return manifest


@app.get("/api/dashboard/productions/{production_id}/artifacts")
async def get_production_artifacts(production_id: str):
    info = _resolve_production_deliverables(production_id)
    if not info["workspace_exists"]:
        raise HTTPException(status_code=404, detail=f"Production workspace '{production_id}' not found")
    return info


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects root browser access directly to the Production Health Dashboard."""
    return RedirectResponse(url="/dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
