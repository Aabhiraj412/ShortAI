"""
FastAPI Application Entry Point for AI YouTube Shorts Studio.
Provides full REST API for Dashboard, Agent controls, Pipeline runs, Media streaming, and Settings.
"""
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
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

# Mount static media directories for direct in-browser streaming and thumbnail display
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")
app.mount("/thumbnails", StaticFiles(directory=settings.THUMB_DIR), name="thumbnails")

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

    return {
        "current_cycle": m.current_cycle,
        "next_production_ist": m.next_production_ist,
        "target_publishing_ist": next_pub_str,
        "pipeline_stage": m.pipeline_stage,
        "active_profile": profile.name,
        "overall_health": m.overall_health,
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
    return {
        "overall_health": m.overall_health,
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
    }


@app.get("/api/dashboard/history")
async def get_dashboard_history(limit: int = 25):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_publishing_history(limit=limit)


@app.get("/api/dashboard/recovery")
async def get_dashboard_recovery(limit: int = 15):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_recovery_history(limit=limit)


@app.get("/api/dashboard/activity")
async def get_dashboard_activity(limit: int = 25):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    return production_health_dashboard.get_recent_activity(limit=limit)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects root browser access directly to the Production Health Dashboard."""
    return RedirectResponse(url="/dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
