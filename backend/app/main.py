"""
FastAPI Application Entry Point for AI YouTube Shorts Studio.
Provides full REST API for Dashboard, Agent controls, Pipeline runs, Media streaming, and Settings.
"""
import os
import asyncio
import ipaddress
import json
import shutil
import subprocess
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.execution_context import ExecutionContext, get_current_execution_context
from app.core.config import settings
from app.database.db import (
    init_db, get_db, JobModel, TopicModel, ScriptModel, StoryboardModel, VideoJobModel, PublicationModel, StudioSettingModel,
    SessionLocal,
)
from app.workers.pipeline import pipeline
from app.workers.job_manager import job_manager
from app.scheduler.scheduler import start_scheduler, get_scheduler_status
from app.services.youtube_service import YouTubePublisherService
from app.services.youtube_publishing_engine import YouTubeMetadata
from backend.app.core.process_utils import install_windows_hidden_subprocess_defaults
from backend.app.services import control_center_service

install_windows_hidden_subprocess_defaults()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    control_center_service.restore_active_schedule_profile()
    if get_current_execution_context() == ExecutionContext.PRODUCTION:
        recovered_uploads = YouTubePublisherService().engine.db.recover_interrupted_uploads()
        if recovered_uploads:
            import logging
            logging.getLogger(__name__).warning(
                "Marked %d interrupted YouTube upload(s) resumable after restart.",
                recovered_uploads,
            )
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
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
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
    publishing_mode: Optional[Literal["manual_approval"]] = None
    daily_publish_time_ist: Optional[str] = None
    default_voice: Optional[str] = None


_MISSPELT_CONFIRM_FIELDS = ("confirmed", "confirmation", "approve", "approved", "Confirm")


class ConfirmedActionRequest(BaseModel):
    """Base for state-changing requests: `confirm: true` must be sent explicitly.

    A misspelt confirmation field would otherwise be silently ignored by pydantic and
    surface as a confusing "confirmation required" error; reject it with a 422 that
    names the expected field instead.
    """

    @model_validator(mode="before")
    @classmethod
    def _reject_misspelt_confirm(cls, data: Any) -> Any:
        if isinstance(data, dict):
            wrong = [k for k in _MISSPELT_CONFIRM_FIELDS if k in data]
            if wrong:
                raise ValueError(
                    f"Unknown confirmation field {wrong[0]!r}: send \"confirm\": true to confirm this action."
                )
        return data


class OAuthClientConfigRequest(BaseModel):
    client_secrets_json: str = Field(min_length=10, max_length=100_000)


class OAuthDisconnectRequest(ConfirmedActionRequest):
    confirm: bool


class UploadQueueRequest(BaseModel):
    publication_id: int = Field(gt=0)


class PublishingPolicyRequest(ConfirmedActionRequest):
    mode: Literal["manual_approval", "automatic_private_scheduled", "automatic"]
    confirm: bool


class UploadApprovalRequest(ConfirmedActionRequest):
    confirm: bool
    publish_at: Optional[str] = None


class PublishingConfigRequest(BaseModel):
    mode: Literal["manual_approval", "automatic", "scheduled"]


class NotificationConfigRequest(BaseModel):
    host: Optional[str] = Field(default=None, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=1000)
    recipient: Optional[str] = Field(default=None, max_length=255)
    sender: Optional[str] = Field(default=None, max_length=255)
    security: Optional[Literal["STARTTLS", "SSL", "NONE"]] = None


class OperatingModeRequest(BaseModel):
    mode: Literal["PRODUCTION", "GAMING", "IDLE", "MAINTENANCE"]


class OperatingModeActivationRequest(OperatingModeRequest):
    confirm: bool


class ScheduleDraftRequest(BaseModel):
    profile_name: Literal["FOUR_CYCLES_DAILY", "SINGLE_DAILY", "CUSTOM"]
    timezone_name: str = Field(min_length=1, max_length=100)
    cycles: List[Dict]


class ActivationConfirmationRequest(BaseModel):
    confirm: bool
    spec: Optional[Dict[str, Any]] = None


class ConcurrencyRequest(BaseModel):
    maximum: int = Field(ge=1, le=4)


def _is_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    try:
        client_is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        client_is_loopback = client_host.lower() == "localhost"
    host = request.url.hostname or ""
    try:
        host_is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        host_is_loopback = host.lower() == "localhost"
    return client_is_loopback and host_is_loopback


def require_local_control(request: Request) -> None:
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="This control API is available only from localhost.")


def _resolve_local_media_file(path: Optional[str], required: bool = True) -> Optional[str]:
    if not path and not required:
        return None
    if not path:
        raise HTTPException(status_code=409, detail="The production is missing a required media file.")
    media_root = Path(settings.MEDIA_DIR).resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(media_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Production media must be inside the local media directory.") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=409, detail="The production media file is unavailable.")
    return str(resolved)


def _run_approved_upload(ledger_id: int, confirmed: bool, publish_at: Optional[str]) -> None:
    service = YouTubePublisherService()
    try:
        result = service.approve_and_upload(ledger_id, confirmed, publish_at)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Approved YouTube upload failed.")
        try:
            service.engine.db.set_upload_status(ledger_id, "failed", "Upload worker failed before a confirmed result.")
        except Exception:
            logging.getLogger(__name__).exception("Could not persist upload worker failure.")
        result = {
            "success": False,
            "status": "failed",
            "video_id": None,
            "error": "Upload worker failed before a confirmed result.",
        }
    try:
        from backend.app.services.email_monitoring_service import email_monitoring_service
        record = service.get_upload(ledger_id) or {}
        success = bool(result.get("success"))
        email_monitoring_service.dispatch_stage_notification(
            "UPLOAD_COMPLETED" if success else "UPLOAD_FAILED",
            f"stage322-upload-{ledger_id}-{result.get('status')}",
            f"YouTube upload {'completed' if success else 'failed'}: {record.get('title', ledger_id)}",
            {
                "upload_id": ledger_id,
                "video_title": record.get("title"),
                "status": result.get("status"),
                "youtube_video_id": result.get("video_id"),
                "error": result.get("error"),
            },
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Could not dispatch upload notification.")

# --- Pipeline Endpoints ---
@app.post("/api/pipeline/run")
async def trigger_pipeline(
    req: PipelineTriggerRequest,
    bg_tasks: BackgroundTasks,
    _: None = Depends(require_local_control),
):
    if control_center_service.get_operating_mode() != "PRODUCTION":
        raise HTTPException(
            status_code=409,
            detail="Production execution is deferred unless operating mode is PRODUCTION.",
        )
    if not control_center_service.production_capacity()["available"]:
        raise HTTPException(status_code=409, detail="Configured production concurrency limit is reached.")
    if req.auto_upload:
        raise HTTPException(status_code=409, detail="Automatic publishing is disabled; use the manual approval publishing API.")
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
async def get_jobs(
    db: Session = Depends(get_db),
    _: None = Depends(require_local_control),
):
    return db.query(JobModel).order_by(JobModel.created_at.desc()).limit(20).all()

@app.post("/api/jobs/recover")
async def recover_jobs(_: None = Depends(require_local_control)):
    recovered = job_manager.recover_stale_jobs()
    return {"status": "success", "recovered_count": len(recovered), "recovered_jobs": recovered}

@app.post("/api/jobs/run")
async def run_daily_job(
    req: PipelineTriggerRequest,
    bg_tasks: BackgroundTasks,
    _: None = Depends(require_local_control),
):
    if control_center_service.get_operating_mode() != "PRODUCTION":
        raise HTTPException(
            status_code=409,
            detail="Production execution is deferred unless operating mode is PRODUCTION.",
        )
    if not control_center_service.production_capacity()["available"]:
        raise HTTPException(status_code=409, detail="Configured production concurrency limit is reached.")
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
async def get_publishing_status(
    db: Session = Depends(get_db),
    _: None = Depends(require_local_control),
):
    pubs = db.query(PublicationModel).order_by(PublicationModel.created_at.desc()).limit(20).all()
    yt_service = YouTubePublisherService()
    auth_status = await asyncio.to_thread(yt_service.oauth.get_auth_status_cached)
    return {
        "youtube_auth": auth_status,
        "publications": pubs,
        "scheduler": get_scheduler_status()
    }


@app.get("/api/stage-3-22/youtube/status")
async def stage_322_youtube_status(_: None = Depends(require_local_control)):
    # Off the event loop and cached: this is polled by every open dashboard tab.
    return await asyncio.to_thread(YouTubePublisherService().oauth.get_auth_status_cached)


@app.post("/api/stage-3-22/youtube/client-config")
async def stage_322_install_youtube_config(
    req: OAuthClientConfigRequest,
    _: None = Depends(require_local_control),
):
    ok, message = YouTubePublisherService().oauth.install_client_secrets(req.client_secrets_json)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"status": "installed", "message": message}


@app.post("/api/stage-3-22/youtube/connect")
async def stage_322_connect_youtube(_: None = Depends(require_local_control)):
    service = YouTubePublisherService()
    ok, channel, error = await asyncio.to_thread(
        service.oauth.start_interactive_auth, timeout_sec=180, open_browser=True
    )
    if not ok:
        try:
            from backend.app.services.email_monitoring_service import email_monitoring_service
            email_monitoring_service.dispatch_stage_notification(
                "YOUTUBE_AUTH_PROBLEM",
                "stage322-youtube-auth-reconnect",
                "CrazzyDocs YouTube authorization needs attention",
                {"status": "reconnect_required", "error": error},
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Could not dispatch OAuth problem notification.")
        raise HTTPException(status_code=400, detail=error or "OAuth authorization failed.")
    return {"status": "connected" if channel else "authorized", "channel": channel}


@app.post("/api/stage-3-22/youtube/disconnect")
async def stage_322_disconnect_youtube(
    req: OAuthDisconnectRequest,
    _: None = Depends(require_local_control),
):
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required.")
    disconnected = YouTubePublisherService().oauth.disconnect_channel()
    if not disconnected:
        raise HTTPException(status_code=500, detail="Local YouTube credentials could not be removed.")
    return {"status": "disconnected"}


@app.get("/api/stage-3-22/publishing/config")
async def stage_322_publishing_config(_: None = Depends(require_local_control)):
    return {
        "mode": settings.PUBLISHING_MODE,
        "supported_modes": {
            "manual_approval": {"enabled": True},
            "automatic": {"enabled": False, "reason": "Disabled for Stage 3.22 safety."},
            "scheduled": {
                "enabled": True,
                "requires_per_upload_manual_approval": True,
                "reason": "Scheduled publication is available only on an explicitly approved private upload.",
            },
        },
        "allowed_upload_privacy": ["private", "unlisted"],
        "scheduled_publication_requires_manual_approval": True,
    }


@app.put("/api/stage-3-22/publishing/config")
async def stage_322_update_publishing_config(
    req: PublishingConfigRequest,
    _: None = Depends(require_local_control),
):
    if req.mode == "automatic":
        raise HTTPException(status_code=409, detail="Automatic publishing is disabled in Stage 3.22.")
    if req.mode == "scheduled":
        raise HTTPException(status_code=409, detail="Keep the active policy manual_approval; set publish_at on an individually approved upload.")
    return await stage_322_publishing_config(None)


@app.post("/api/stage-3-22/publishing/queue")
async def stage_322_queue_publication(
    req: UploadQueueRequest,
    _: None = Depends(require_local_control),
    db: Session = Depends(get_db),
):
    publication = db.query(PublicationModel).filter(PublicationModel.id == req.publication_id).first()
    if not publication or publication.status != "ready_for_approval":
        raise HTTPException(status_code=409, detail="Only QC-passed productions awaiting approval can be queued.")
    video_job = db.query(VideoJobModel).filter(VideoJobModel.id == publication.video_job_id).first()
    if not video_job or video_job.status != "completed":
        raise HTTPException(status_code=409, detail="The production worker has not completed this video.")
    video_path = _resolve_local_media_file(video_job.video_path)
    thumbnail_path = _resolve_local_media_file(video_job.thumbnail_path, required=False)
    try:
        tags = json.loads(publication.tags_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=409, detail="Production metadata contains invalid tags.") from exc
    metadata = YouTubeMetadata(
        title=publication.title,
        description=publication.description or "",
        tags=tags,
        hashtags=["#Shorts"],
        category_id="28",
    )
    try:
        queued = YouTubePublisherService().queue_approved_production(
            publication.id, video_path, thumbnail_path, metadata
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    publication.status = "pending_approval"
    db.commit()
    return queued


@app.get("/api/stage-3-22/publishing/pending")
async def stage_322_pending_uploads(_: None = Depends(require_local_control)):
    statuses = ["pending_queue", "approved", "uploading", "upload_interrupted", "awaiting_auth", "failed", "uploaded_unverified"]
    return {"uploads": YouTubePublisherService().list_uploads(statuses=statuses)}


@app.get("/api/stage-3-22/publishing/history")
async def stage_322_upload_history(limit: int = 100, _: None = Depends(require_local_control)):
    return {"uploads": YouTubePublisherService().list_uploads(limit=limit)}


@app.get("/api/stage-3-22/publishing/{ledger_id}")
async def stage_322_upload_status(ledger_id: int, _: None = Depends(require_local_control)):
    upload = YouTubePublisherService().get_upload(ledger_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    return upload


@app.post("/api/stage-3-22/publishing/{ledger_id}/approve")
async def stage_322_approve_upload(
    ledger_id: int,
    req: UploadApprovalRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_local_control),
):
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail='Explicit per-upload approval is required: send "confirm": true.')
    service = YouTubePublisherService()
    upload = service.get_upload(ledger_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    if upload["status"] not in ("pending_queue", "upload_interrupted", "failed", "awaiting_auth"):
        raise HTTPException(status_code=409, detail="This publishing record is not awaiting approval.")
    background_tasks.add_task(_run_approved_upload, ledger_id, True, req.publish_at)
    return {"status": "approved", "upload_id": ledger_id, "upload_status": "queued_for_upload"}


@app.get("/api/stage-3-22/notifications/config")
async def stage_322_notification_config(_: None = Depends(require_local_control)):
    from backend.app.services.email_monitoring_service import email_monitoring_service
    return email_monitoring_service.get_notification_configuration()


@app.put("/api/stage-3-22/notifications/config")
async def stage_322_update_notification_config(
    req: NotificationConfigRequest,
    _: None = Depends(require_local_control),
):
    from backend.app.services.email_monitoring_service import email_monitoring_service
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="At least one notification setting is required.")
    return email_monitoring_service.save_notification_configuration(fields)


@app.get("/api/stage-3-22/notifications/history")
async def stage_322_notification_history(limit: int = 100, _: None = Depends(require_local_control)):
    from backend.app.services.email_monitoring_service import email_monitoring_service
    return {"notifications": email_monitoring_service.get_notification_history(limit=limit)}

# --- Stage 3.23 Control Center ---
@app.get("/api/control-center/state")
async def control_center_state(_: None = Depends(require_local_control)):
    return control_center_service.get_control_center_state()


@app.post("/api/control-center/operating-mode/draft")
async def control_center_save_mode_draft(
    req: OperatingModeRequest,
    _: None = Depends(require_local_control),
):
    try:
        pending = control_center_service.save_operating_mode_draft(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"active_mode": control_center_service.get_operating_mode(), "pending": pending}


@app.post("/api/control-center/operating-mode/activate")
async def control_center_activate_mode(
    req: OperatingModeActivationRequest,
    _: None = Depends(require_local_control),
):
    try:
        result = control_center_service.activate_operating_mode(req.mode, req.confirm)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@app.delete("/api/control-center/operating-mode/draft")
async def control_center_cancel_mode_draft(_: None = Depends(require_local_control)):
    control_center_service.cancel_operating_mode_draft()
    return {"status": "cancelled"}


@app.get("/api/control-center/schedule/preset/{profile_name}")
async def control_center_schedule_preset(
    profile_name: Literal["FOUR_CYCLES_DAILY", "SINGLE_DAILY", "CUSTOM"],
    _: None = Depends(require_local_control),
):
    try:
        return control_center_service.preset_schedule(profile_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/control-center/schedule/draft")
async def control_center_save_schedule_draft(
    req: ScheduleDraftRequest,
    _: None = Depends(require_local_control),
):
    try:
        draft = control_center_service.save_schedule_draft(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "draft": draft,
        "draft_validation": {
            "valid": False,
            "stale": True,
            "draft_revision": draft.get("revision"),
            "validated_revision": None,
            "validated_at_ist": None,
        },
    }


@app.post("/api/control-center/schedule/validate")
async def control_center_validate_schedule_draft(
    req: Optional[ScheduleDraftRequest] = None,
    _: None = Depends(require_local_control),
):
    try:
        return control_center_service.validate_saved_schedule_draft(req.model_dump() if req is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/control-center/schedule/activate")
async def control_center_activate_schedule(
    req: ActivationConfirmationRequest,
    _: None = Depends(require_local_control),
):
    try:
        result = control_center_service.activate_schedule_draft(req.confirm, spec=req.spec)
        from backend.app.scheduler.scheduler import scheduler as apscheduler
        from backend.app.scheduler.scheduler import configure_scheduler_jobs

        if apscheduler.running:
            configure_scheduler_jobs()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


class UnifiedScheduleActivateRequest(BaseModel):
    confirm: bool = False
    production_spec: Optional[Dict[str, Any]] = None
    publication_spec: Optional[Dict[str, Any]] = None


@app.get("/api/control-center/schedule/timeline")
async def control_center_schedule_timeline(_: None = Depends(require_local_control)):
    return control_center_service.get_live_cycle_schedule_status()


@app.get("/api/control-center/unified-schedule")
async def control_center_unified_schedule(_: None = Depends(require_local_control)):
    return control_center_service.get_unified_schedule_state()


@app.post("/api/control-center/unified-schedule/draft")
async def control_center_save_unified_draft(
    req: UnifiedScheduleActivateRequest,
    _: None = Depends(require_local_control),
):
    try:
        return control_center_service.save_unified_schedule_draft(
            prod_spec=req.production_spec,
            pub_spec=req.publication_spec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/control-center/unified-schedule/validate")
async def control_center_validate_unified_schedule(
    req: UnifiedScheduleActivateRequest,
    _: None = Depends(require_local_control),
):
    return control_center_service.validate_unified_schedule(
        prod_spec=req.production_spec,
        pub_spec=req.publication_spec,
    )


@app.post("/api/control-center/unified-schedule/activate")
async def control_center_activate_unified_schedule(
    req: UnifiedScheduleActivateRequest,
    _: None = Depends(require_local_control),
):
    from backend.app.scheduler.scheduler import scheduler as apscheduler
    from backend.app.scheduler.scheduler import configure_scheduler_jobs

    sync_fn = configure_scheduler_jobs if apscheduler.running else None
    result = control_center_service.activate_unified_schedule(
        confirm=req.confirm,
        prod_spec=req.production_spec,
        pub_spec=req.publication_spec,
        sync_scheduler_fn=sync_fn,
    )
    if result.get("status") == "FAILED":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.delete("/api/control-center/schedule/draft")
async def control_center_cancel_schedule_draft(_: None = Depends(require_local_control)):
    control_center_service.cancel_schedule_draft()
    return {"status": "cancelled"}


@app.put("/api/control-center/concurrency")
async def control_center_set_concurrency(
    req: ConcurrencyRequest,
    _: None = Depends(require_local_control),
):
    try:
        maximum = control_center_service.set_max_concurrency(req.maximum)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"maximum": maximum, "capacity": control_center_service.production_capacity()}


@app.get("/api/control-center/publishing")
async def control_center_publishing_status(_: None = Depends(require_local_control)):
    readiness = await asyncio.to_thread(control_center_service.evaluate_publishing_readiness)
    stored = control_center_service.get_stored_publishing_policy()
    return {
        "mode": control_center_service.get_publishing_policy(),
        "stored_mode": stored,
        "legacy_automatic_ignored": stored in control_center_service.LEGACY_PUBLISHING_POLICIES,
        "automatic_enabled": False,
        "autonomous_ready": readiness["autonomous_ready"],
        "readiness": readiness,
        "reason": "Publishing is user-controlled: each video needs a user-selected publication time.",
        "allowed_upload_privacy": ["private", "unlisted"],
    }


@app.put("/api/control-center/publishing/policy")
async def control_center_update_publishing_policy(
    req: PublishingPolicyRequest,
    _: None = Depends(require_local_control),
):
    try:
        return control_center_service.set_publishing_policy(req.mode, req.confirm)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class DeploymentRestartRequest(BaseModel):
    confirm: bool = False
    reason: str = ""


@app.get("/api/control-center/deployment")
async def control_center_deployment_status(_: None = Depends(require_local_control)):
    """PIDs, start times, loaded vs on-disk code revision (STALE_CODE), sync, heartbeats."""
    from backend.app.services import deployment_diagnostics
    return await asyncio.to_thread(deployment_diagnostics.get_deployment_status)


@app.post("/api/control-center/deployment/restart")
async def control_center_request_daemon_restart(
    req: DeploymentRestartRequest,
    _: None = Depends(require_local_control),
):
    """Requests a controlled scheduler restart; the daemon exits only at a safe point."""
    from backend.app.services import deployment_diagnostics
    try:
        return deployment_diagnostics.request_daemon_restart(req.confirm, req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/control-center/resources")
async def control_center_resource_status(_: None = Depends(require_local_control)):
    disk = shutil.disk_usage(settings.BASE_DIR)
    memory: dict[str, Any] = {"status": "NOT AVAILABLE"}
    cpu: dict[str, Any] = {"status": "NOT AVAILABLE"}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        def filetime_value(value: FileTime) -> int:
            return (value.high << 32) | value.low

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory = {
                "status": "AVAILABLE",
                "total_bytes": status.ullTotalPhys,
                "available_bytes": status.ullAvailPhys,
                "used_percent": round(
                    (status.ullTotalPhys - status.ullAvailPhys) * 100 / status.ullTotalPhys,
                    1,
                ),
            }
        idle_a, kernel_a, user_a = FileTime(), FileTime(), FileTime()
        idle_b, kernel_b, user_b = FileTime(), FileTime(), FileTime()
        if ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle_a), ctypes.byref(kernel_a), ctypes.byref(user_a)
        ):
            time.sleep(0.1)
            if ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle_b), ctypes.byref(kernel_b), ctypes.byref(user_b)
            ):
                idle_delta = filetime_value(idle_b) - filetime_value(idle_a)
                total_delta = (
                    filetime_value(kernel_b) - filetime_value(kernel_a)
                    + filetime_value(user_b) - filetime_value(user_a)
                )
                if total_delta > 0:
                    cpu = {
                        "status": "AVAILABLE",
                        "used_percent": round(
                            max(0.0, min(100.0, 100 * (total_delta - idle_delta) / total_delta)),
                            1,
                        ),
                    }
    with SessionLocal() as session:
        active_jobs = session.query(JobModel).filter(
            JobModel.status.in_(["RUNNING", "RETRYING"])
        ).count()

    gpu: dict[str, Any] = {"status": "NOT AVAILABLE"}
    rendering_processes: dict[str, Any] = {"status": "NOT AVAILABLE"}
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            smi = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=creation_flags,
            )
            if smi.returncode == 0 and smi.stdout.strip():
                parts = [p.strip() for p in smi.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 4:
                    gpu = {
                        "status": "AVAILABLE",
                        "name": parts[0],
                        "used_percent": float(parts[1]),
                        "memory_used_mb": float(parts[2]),
                        "memory_total_mb": float(parts[3]),
                    }
        except Exception:
            pass
        try:
            tl = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=creation_flags,
            )
            if tl.returncode == 0:
                lines = [ln for ln in tl.stdout.splitlines() if ln.strip().lower().startswith('"ffmpeg.exe"')]
                rendering_processes = {
                    "status": "AVAILABLE",
                    "ffmpeg_count": len(lines),
                }
        except Exception:
            pass

    return {
        "cpu": cpu,
        "memory": memory,
        "gpu": gpu,
        "disk": {
            "status": "AVAILABLE",
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        },
        "active_production_jobs": active_jobs,
        "maximum_production_concurrency": control_center_service.get_max_concurrency(),
        "rendering_processes": rendering_processes,
        "operating_mode": control_center_service.get_operating_mode(),
        "production_control": control_center_service.get_production_control_state(),
    }


@app.get("/api/control-center/cycles/live")
async def control_center_live_cycles(_: None = Depends(require_local_control)):
    return control_center_service.get_live_cycle_schedule_status()


@app.post("/api/control-center/production/start")
async def control_center_start_production(
    req: PipelineTriggerRequest,
    bg_tasks: BackgroundTasks,
    _: None = Depends(require_local_control),
):
    if control_center_service.get_operating_mode() != "PRODUCTION":
        raise HTTPException(
            status_code=409,
            detail="Production execution requires Operating Mode to be set to PRODUCTION.",
        )
    if req.auto_upload:
        raise HTTPException(
            status_code=409,
            detail="Direct auto_upload is blocked; completed productions follow Stage 3.22 publishing policy.",
        )
    if not control_center_service.production_capacity()["available"]:
        raise HTTPException(status_code=409, detail="Configured production concurrency limit is reached.")

    from backend.app.scheduler.schedule_profiles import IST_TZ
    from datetime import datetime
    job_id = f"manual_prod_{datetime.now(IST_TZ).strftime('%Y%m%d_%H%M%S')}"
    control_center_service.set_production_control_state("RUNNING", reason=f"Started {job_id} by operator")

    def _run_manual_production():
        from backend.app.orchestration.crazzydocs_master_orchestrator import master_orchestrator
        from backend.app.core.execution_context import ExecutionContext
        try:
            master_orchestrator.execute_production_pipeline(
                job_id=job_id,
                execution_context=ExecutionContext.PRODUCTION,
            )
        finally:
            if control_center_service.get_production_control_state().get("state") == "RUNNING":
                control_center_service.set_production_control_state("IDLE", reason="Production finished")

    bg_tasks.add_task(_run_manual_production)
    return {"status": "started", "job_id": job_id}


@app.post("/api/control-center/production/pause")
async def control_center_pause_production(_: None = Depends(require_local_control)):
    return control_center_service.request_production_pause()


@app.post("/api/control-center/production/resume")
async def control_center_resume_production(_: None = Depends(require_local_control)):
    return control_center_service.request_production_resume()


@app.post("/api/control-center/production/stop")
async def control_center_stop_production(_: None = Depends(require_local_control)):
    return control_center_service.request_production_stop()


@app.get("/api/control-center/scheduler/status")
async def control_center_scheduler_status(_: None = Depends(require_local_control)):
    from backend.app.services.production_health_dashboard import production_health_dashboard
    from backend.scripts.manage_windows_startup import get_all_tasks_status
    m = production_health_dashboard.get_health_metrics()
    svc = production_health_dashboard.get_services_status()
    tasks = production_health_dashboard.get_windows_task_status(use_cache=False)
    states = production_health_dashboard.get_reconciled_operational_states(
        metrics=m, services=svc, windows_tasks=tasks
    )
    return {
        "scheduler_state": states.get("scheduler_state"),
        "scheduler_details": states.get("scheduler_details"),
        "scheduler_daemon": svc.get("scheduler_daemon"),
        "scheduler_heartbeat": svc.get("scheduler_heartbeat"),
        "windows_tasks": tasks,
        "next_production_ist": m.next_production_ist,
        "overall_health": m.overall_health,
        "health_reasons": m.health_reasons,
    }


@app.post("/api/control-center/scheduler/enable")
async def control_center_enable_scheduler(
    req: ActivationConfirmationRequest,
    _: None = Depends(require_local_control),
):
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required to enable the scheduler.")
    from backend.scripts.manage_windows_startup import SCHEDULER_TASK_NAME, invalidate_tasks_cache
    from backend.app.core.paths import PROJECT_ROOT_STR
    res = subprocess.run(
        ["schtasks", "/Change", "/TN", SCHEDULER_TASK_NAME, "/ENABLE"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
    )
    if res.returncode != 0:
        raise HTTPException(status_code=409, detail=res.stderr.strip() or res.stdout.strip() or "Failed to enable Windows scheduler task.")
    daemon_script = os.path.join(PROJECT_ROOT_STR, "backend", "scripts", "run_scheduler_daemon.py")
    py_exe = os.path.join(PROJECT_ROOT_STR, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(py_exe):
        py_exe = os.path.join(PROJECT_ROOT_STR, ".venv", "Scripts", "python.exe")
    from backend.app.services.production_health_dashboard import production_health_dashboard
    invalidate_tasks_cache()
    production_health_dashboard.invalidate_task_cache()
    svc = production_health_dashboard.get_services_status()
    if not (svc.get("scheduler_daemon") or {}).get("is_running"):
        subprocess.Popen(
            [py_exe, daemon_script],
            cwd=PROJECT_ROOT_STR,
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) if os.name == "nt" else 0,
        )
    return {"status": "enabled", "task_name": SCHEDULER_TASK_NAME}


@app.post("/api/control-center/scheduler/disable")
async def control_center_disable_scheduler(
    req: ActivationConfirmationRequest,
    _: None = Depends(require_local_control),
):
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required to disable the scheduler.")
    from backend.scripts.manage_windows_startup import SCHEDULER_TASK_NAME, invalidate_tasks_cache
    from backend.app.scheduler.reconciliation_service import LOCK_FILE_PATH
    subprocess.run(
        ["schtasks", "/Change", "/TN", SCHEDULER_TASK_NAME, "/DISABLE"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
    )
    try:
        subprocess.run(
            ["schtasks", "/End", "/TN", SCHEDULER_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
        )
    except Exception:
        pass
    from backend.app.services.production_health_dashboard import production_health_dashboard
    invalidate_tasks_cache()
    production_health_dashboard.invalidate_task_cache()
    hb = production_health_dashboard._get_scheduler_heartbeat()
    pid = hb.get("pid")
    if pid and pid != os.getpid() and os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        if os.path.exists(LOCK_FILE_PATH):
            try:
                os.remove(LOCK_FILE_PATH)
            except OSError:
                pass
    return {"status": "disabled", "task_name": SCHEDULER_TASK_NAME}

# --- Settings Endpoints ---
@app.get("/api/settings")
async def get_settings(_: None = Depends(require_local_control)):
    return {
        "gemini_api_key_configured": bool(settings.GEMINI_API_KEY),
        "publishing_mode": settings.PUBLISHING_MODE,
        "daily_publish_time_ist": settings.DAILY_PUBLISH_TIME_IST,
        "default_voice": settings.DEFAULT_VOICE,
        "base_dir": settings.BASE_DIR
    }

@app.post("/api/settings")
async def update_settings(
    req: SettingsUpdateRequest,
    _: None = Depends(require_local_control),
):
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
    
    # Calculate next publication target from the enabled weekday-aware cycles.
    next_pub_str = "Calculating..."
    upcoming_publications = []
    for cycle in profile.cycles:
        for day_offset in range(8):
            candidate_date = now_ist + timedelta(days=day_offset)
            if candidate_date.weekday() not in cycle.active_weekdays:
                continue
            _, c_pub, _, _ = cycle.calculate_datetimes(candidate_date)
            if c_pub > now_ist:
                upcoming_publications.append((c_pub, cycle.name))
                break
    if upcoming_publications:
        target, cycle_name = min(upcoming_publications, key=lambda item: item[0])
        next_pub_str = f"{target.strftime('%Y-%m-%d %H:%M:%S IST')} ({cycle_name})"
    else:
        next_pub_str = "No enabled publication targets"

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
        "operating_mode": control_center_service.get_operating_mode(),
        "production_control": control_center_service.get_production_control_state(),
        "overall_health": m.overall_health,
        "health_reasons": m.health_reasons,
        "scheduler_state": states["scheduler_state"],
        "scheduler_details": states.get("scheduler_details", {}),
        "production_worker_state": states["production_worker_state"],
        "production_worker": states["production_worker"],
        "validation_worker_state": states["validation_worker_state"],
        "validation_worker": states["validation_worker"],
        "overall_operational_state": states["overall_operational_state"],
        "publishing_mode": control_center_service.get_publishing_policy(),
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
        "health_reasons": m.health_reasons,
        "operating_mode": control_center_service.get_operating_mode(),
        "publishing_mode": control_center_service.get_publishing_policy(),
        "production_control": control_center_service.get_production_control_state(),
        "scheduler_details": states.get("scheduler_details", {}),
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
    ws_exists = os.path.isdir(prod_dir)
    quarantined = False
    if not ws_exists:
        q_root = os.path.join(PROJECT_ROOT_STR, "media", "quarantine")
        if os.path.isdir(q_root):
            for _root, dirnames, _files in os.walk(q_root):
                if production_id in dirnames:
                    quarantined = True
                    break
    return {
        "production_id": production_id,
        "workspace_abs_dir": prod_dir,
        "workspace_exists": ws_exists,
        "quarantined": quarantined,
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
        payload["active_production_run"] = _enrich_progress_record(payload.get("active_production_run"))
        payload["latest_production_run"] = _enrich_progress_record(payload.get("latest_production_run"))
        payload["active_validation_run"] = _enrich_progress_record(payload.get("active_validation_run"))
        payload["latest_validation_run"] = _enrich_progress_record(payload.get("latest_validation_run"))
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


class PublicationScheduleSpecRequest(BaseModel):
    preset_name: str = "CUSTOM"
    timezone_name: str = "Asia/Kolkata"
    weekdays: List[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    missed_slot_behavior: str = "next_slot"
    min_prep_buffer_minutes: int = 10
    wait_for_slot: bool = True
    recovery_overlap_policy: str = "STRICT"
    slots: List[Dict[str, Any]] = Field(default_factory=list)


class PublicationSchedulePresetRequest(BaseModel):
    preset_name: str


class PublicationScheduleActivateRequest(BaseModel):
    confirm: bool = False
    spec: Optional[Dict[str, Any]] = None


class ProductionConfigRequest(BaseModel):
    renderer: str = "lemo-opuscar"
    concurrency: int = 1
    duration_target_sec: int = 54
    resolution: str = "1080x1920"
    fps: int = 30
    beat_count: int = 20
    diversity_min_assets: int = 10
    qc_strictness: str = "strict"
    recovery_policy: str = "checkpoint_resume"
    max_cpu_pct: int = 92
    max_ram_pct: int = 90
    retry_limit: int = 2
    confirm: bool = True


class StorageConfigRequest(BaseModel):
    production_output_root: str
    final_video_dir: str
    thumbnail_dir: str
    temp_render_dir: str
    archive_dir: str
    quarantine_dir: str
    confirm: bool = True


class OpenFolderRequest(BaseModel):
    path: str


def _redact_log_secrets(text: str) -> str:
    import re
    redacted = re.sub(r"AIza[0-9A-Za-z\-_]{20,}", "AIza***REDACTED***", text)
    redacted = re.sub(r"sk-[0-9A-Za-z\-_]{16,}", "sk-***REDACTED***", redacted)
    redacted = re.sub(r"Bearer\s+[0-9A-Za-z\-_.=]+", "Bearer ***REDACTED***", redacted, flags=re.IGNORECASE)
    redacted = re.sub(
        r"(client_secret|refresh_token|access_token|api_key|smtp_password)\s*[:=]\s*\S+",
        r"\1=***REDACTED***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


@app.get("/api/control-center/publication-schedule")
async def api_get_publication_schedule(_: None = Depends(require_local_control)):
    return control_center_service.get_publication_schedule()


@app.get("/api/control-center/publication-recovery")
async def api_get_publication_recovery(_: None = Depends(require_local_control)):
    return control_center_service.get_publication_recovery_timeline()


@app.post("/api/control-center/publication-schedule/preset")
async def api_preset_publication_schedule(req: PublicationSchedulePresetRequest, _: None = Depends(require_local_control)):
    try:
        spec = control_center_service.preset_publication_schedule(req.preset_name)
        return control_center_service.save_publication_schedule_draft(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control-center/publication-schedule/draft")
async def api_save_publication_schedule_draft(req: PublicationScheduleSpecRequest, _: None = Depends(require_local_control)):
    return control_center_service.save_publication_schedule_draft(req.model_dump())


@app.post("/api/control-center/publication-schedule/validate")
async def api_validate_publication_schedule(req: Optional[PublicationScheduleSpecRequest] = None, _: None = Depends(require_local_control)):
    if req is not None:
        return control_center_service.validate_saved_publication_schedule_draft(req.model_dump())
    return control_center_service.validate_saved_publication_schedule_draft()


@app.post("/api/control-center/publication-schedule/activate")
async def api_activate_publication_schedule(req: PublicationScheduleActivateRequest, _: None = Depends(require_local_control)):
    try:
        return control_center_service.activate_publication_schedule_draft(
            confirm=req.confirm,
            spec=req.spec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control-center/publication-schedule/cancel")
async def api_cancel_publication_schedule_draft(_: None = Depends(require_local_control)):
    control_center_service.cancel_publication_schedule_draft()
    return {"cancelled": True}


@app.get("/api/control-center/production-config")
async def api_get_production_config():
    return control_center_service.get_production_config()


@app.post("/api/control-center/production-config")
async def api_save_production_config(req: ProductionConfigRequest, _: None = Depends(require_local_control)):
    try:
        data = req.model_dump()
        confirm = bool(data.pop("confirm", True))
        return control_center_service.save_production_config(data, confirm=confirm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/control-center/storage-config")
async def api_get_storage_config():
    cfg = control_center_service.get_storage_config()
    val = control_center_service.validate_storage_config(cfg)
    return {"config": cfg, "validation": val}


@app.post("/api/control-center/storage-config/validate")
async def api_validate_storage_config(req: StorageConfigRequest, _: None = Depends(require_local_control)):
    data = req.model_dump()
    data.pop("confirm", None)
    return control_center_service.validate_storage_config(data)


@app.post("/api/control-center/storage-config")
async def api_save_storage_config(req: StorageConfigRequest, _: None = Depends(require_local_control)):
    try:
        data = req.model_dump()
        confirm = bool(data.pop("confirm", True))
        return control_center_service.save_storage_config(data, confirm=confirm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/control-center/storage/open-folder")
async def api_open_storage_folder(req: OpenFolderRequest, _: None = Depends(require_local_control)):
    target = Path(req.path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Folder does not exist: {target}")
    if os.name == "nt" and hasattr(os, "startfile"):
        os.startfile(str(target))
    return {"opened": True, "path": str(target)}


@app.get("/api/control-center/storage/browse")
async def api_browse_storage_folder(path: str = ""):
    from backend.app.core.paths import MEDIA_DIR
    base = Path(path).resolve() if path else MEDIA_DIR.resolve()
    if not base.exists() or not base.is_dir():
        base = MEDIA_DIR.resolve()
    subdirs = sorted([str(p) for p in base.iterdir() if p.is_dir()])[:100]
    parent = str(base.parent) if base.parent != base else str(base)
    return {"current": str(base), "parent": parent, "directories": subdirs}


@app.get("/api/control-center/processes")
async def api_get_process_monitor():
    from backend.app.core.process_utils import hidden_subprocess_kwargs
    from backend.scripts.manage_windows_startup import get_all_tasks_status

    procs: List[Dict[str, Any]] = []
    keywords = ("crazzydocs", "uvicorn", "run_dashboard", "run_scheduler_daemon", "startup_manager", "lemo-opuscar", "video.mjs", "ffmpeg")
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match 'python|node|ffmpeg' } | "
            "Select-Object ProcessId,Name,CommandLine,WorkingSetSize | "
            "ConvertTo-Json -Compress"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=6,
            **hidden_subprocess_kwargs(),
        )
        raw = (res.stdout or "").strip()
        if raw:
            import json as _json
            parsed = _json.loads(raw)
            rows = parsed if isinstance(parsed, list) else [parsed]
            for info in rows:
                cmd = str(info.get("CommandLine") or "")
                name = str(info.get("Name") or "")
                joined = f"{name} {cmd}".lower()
                if any(k in joined for k in keywords):
                    mem_mb = round(float(info.get("WorkingSetSize") or 0) / (1024 * 1024), 1)
                    role = "worker"
                    if "uvicorn" in joined or "run_dashboard" in joined:
                        role = "dashboard_server"
                    elif "run_scheduler_daemon" in joined:
                        role = "scheduler_daemon"
                    elif "startup_manager" in joined:
                        role = "startup_or_monitor"
                    elif "video.mjs" in joined or "lemo-opuscar" in joined:
                        role = "lemo_opuscar_renderer"
                    elif "ffmpeg" in joined:
                        role = "ffmpeg_encoder"
                    procs.append({
                        "pid": info.get("ProcessId"),
                        "name": name,
                        "role": role,
                        "memory_mb": mem_mb,
                        "status": "running",
                        "window_mode": "HIDDEN (pythonw / CREATE_NO_WINDOW)" if ("pythonw" in name.lower() or role != "worker") else "BACKGROUND",
                    })
    except Exception:
        pass
    return {
        "processes": procs,
        "scheduled_tasks": get_all_tasks_status(ttl_seconds=10.0),
    }


@app.get("/api/control-center/logs")
async def api_get_filtered_logs(
    level: str = "ALL",
    category: str = "ALL",
    search: str = "",
    limit: int = 120,
):
    from backend.app.core.paths import LOGS_DIR
    limit = max(10, min(500, int(limit)))
    lvl_up = str(level or "ALL").upper()
    cat_low = str(category or "ALL").lower()
    q_low = str(search or "").strip().lower()
    entries: List[Dict[str, Any]] = []
    if LOGS_DIR.exists():
        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:6]
        for lf in log_files:
            if cat_low != "all" and cat_low not in lf.name.lower():
                continue
            try:
                lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()[-250:]
                for ln in reversed(lines):
                    if lvl_up != "ALL" and lvl_up not in ln.upper():
                        continue
                    if q_low and q_low not in ln.lower():
                        continue
                    entries.append({
                        "file": lf.name,
                        "message": _redact_log_secrets(ln),
                    })
                    if len(entries) >= limit:
                        break
            except Exception:
                continue
            if len(entries) >= limit:
                break
    return {"count": len(entries), "entries": entries}


@app.get("/api/control-center/logs/download")
async def api_download_redacted_logs():
    from fastapi.responses import PlainTextResponse
    res = await api_get_filtered_logs(limit=400)
    body = "\n".join(f"[{e['file']}] {e['message']}" for e in res["entries"])
    return PlainTextResponse(
        content=body,
        headers={"Content-Disposition": "attachment; filename=crazzydocs_logs_redacted.txt"},
    )


class QueueMetadataUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    hashtags: Optional[List[str]] = None
    thumbnail_path: Optional[str] = None


class QueueScheduleRequest(ConfirmedActionRequest):
    scheduled_ist: str
    confirm: bool = False  # must be sent explicitly; an empty body never schedules
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    hashtags: Optional[List[str]] = None
    thumbnail_path: Optional[str] = None


class QueueConfirmRequest(ConfirmedActionRequest):
    confirm: bool = False  # must be sent explicitly; an empty body never publishes


@app.get("/api/publishing/queue")
async def api_list_publishing_queue(limit: int = 100, _: None = Depends(require_local_control)):
    svc = YouTubePublisherService()
    items = await asyncio.to_thread(svc.list_uploads, limit=limit)
    oauth = await asyncio.to_thread(svc.oauth.get_auth_status_cached)
    quota_used = svc.engine.db.get_quota_used_today()
    return {
        "items": items,
        "count": len(items),
        "youtube_oauth": oauth,
        "quota_used_today": quota_used,
        "quota_daily_limit": svc.engine.max_daily_quota,
        "timezone": "Asia/Kolkata",
    }


@app.get("/api/publishing/queue/{ledger_id}/thumbnail-preview")
async def api_queue_thumbnail_preview(ledger_id: int, _: None = Depends(require_local_control)):
    rec = YouTubePublisherService().engine.db.get_publication(ledger_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    thumb = rec.get("thumbnail_path")
    if not thumb or not os.path.isfile(thumb):
        raise HTTPException(status_code=404, detail="Thumbnail file not found.")
    return FileResponse(thumb)


@app.put("/api/publishing/queue/{ledger_id}")
async def api_update_queue_item(
    ledger_id: int,
    req: QueueMetadataUpdateRequest,
    _: None = Depends(require_local_control),
):
    try:
        item = YouTubePublisherService().update_queue_item(
            ledger_id,
            title=req.title,
            description=req.description,
            tags=req.tags,
            hashtags=req.hashtags,
            thumbnail_path=req.thumbnail_path,
        )
        return {"status": "UPDATED", "item": item}
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/publishing/queue/{ledger_id}/thumbnail")
async def api_upload_queue_thumbnail(
    ledger_id: int,
    file: UploadFile = File(...),
    _: None = Depends(require_local_control),
):
    svc = YouTubePublisherService()
    rec = svc.engine.db.get_publication(ledger_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    ext = os.path.splitext(file.filename or "thumb.png")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Unsupported thumbnail format. Use PNG, JPG, or WEBP.")
    thumb_dir = os.path.join(settings.BASE_DIR, "output", "custom_thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    dest = os.path.join(thumb_dir, f"ledger_{ledger_id}_{int(time.time())}{ext}")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Thumbnail exceeds YouTube's 2MB limit.")
    with open(dest, "wb") as f:
        f.write(content)
    try:
        item = svc.update_queue_item(ledger_id, thumbnail_path=dest)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "THUMBNAIL_UPDATED", "thumbnail_path": dest, "item": item}


@app.post("/api/publishing/queue/{ledger_id}/schedule")
async def api_schedule_queue_item(
    ledger_id: int,
    req: QueueScheduleRequest,
    _: None = Depends(require_local_control),
):
    try:
        return YouTubePublisherService().edit_and_schedule(
            ledger_id,
            scheduled_ist=req.scheduled_ist,
            confirm=req.confirm,
            title=req.title,
            description=req.description,
            tags=req.tags,
            hashtags=req.hashtags,
            thumbnail_path=req.thumbnail_path,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/publishing/queue/{ledger_id}/publish-now")
async def api_publish_queue_item_now(
    ledger_id: int,
    req: QueueConfirmRequest,
    _: None = Depends(require_local_control),
):
    try:
        return YouTubePublisherService().publish_now(ledger_id, confirm=req.confirm)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/publishing/queue/{ledger_id}/cancel-schedule")
async def api_cancel_queue_item_schedule(
    ledger_id: int,
    req: QueueConfirmRequest,
    _: None = Depends(require_local_control),
):
    try:
        return YouTubePublisherService().cancel_schedule(ledger_id, confirm=req.confirm)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/publishing/verify-scheduled")
async def api_verify_scheduled_publications(_: None = Depends(require_local_control)):
    from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
    return youtube_publishing_engine.verify_scheduled_publications()


# --- Production Schedule (simple, authoritative) --------------------------------
class ScheduleSpecRequest(BaseModel):
    profile_name: str = "CUSTOM"
    timezone_name: str = "Asia/Kolkata"
    cycles: List[Dict[str, Any]] = Field(default_factory=list)


class ScheduleActivateRequest(ConfirmedActionRequest):
    confirm: bool = False
    spec: ScheduleSpecRequest


@app.get("/api/schedule")
async def api_schedule_view(_: None = Depends(require_local_control)):
    from backend.app.services import schedule_service
    return schedule_service.schedule_view()


@app.get("/api/production/monitor")
async def api_production_monitor(_: None = Depends(require_local_control)):
    from backend.app.services import production_monitor_service
    return production_monitor_service.monitor_view()


class OperatorRetryRequest(ConfirmedActionRequest):
    confirm: bool = False


@app.get("/api/production/jobs/{job_id}/retry")
async def api_operator_retry_eligibility(job_id: str, _: None = Depends(require_local_control)):
    from backend.app.services import operator_retry_service
    return operator_retry_service.evaluate(job_id)


@app.post("/api/production/jobs/{job_id}/retry")
async def api_operator_retry(job_id: str, req: OperatorRetryRequest, _: None = Depends(require_local_control)):
    """One guarded extra attempt for a FAILED cycle (see operator_retry_service)."""
    from backend.app.services import operator_retry_service
    try:
        return operator_retry_service.request_retry(job_id, req.confirm)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except operator_retry_service.RetryRefused as exc:
        raise HTTPException(status_code=409, detail=" ".join(exc.reasons))


@app.get("/api/schedule/presets/{name}")
async def api_schedule_preset(name: str, _: None = Depends(require_local_control)):
    from backend.app.services import schedule_service
    try:
        return schedule_service.preset(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/schedule/validate")
async def api_schedule_validate(req: ScheduleSpecRequest, _: None = Depends(require_local_control)):
    from backend.app.services import schedule_service
    return schedule_service.validate(req.model_dump())


@app.put("/api/schedule/draft")
async def api_schedule_save_draft(req: ScheduleSpecRequest, _: None = Depends(require_local_control)):
    from backend.app.services import schedule_service
    try:
        return schedule_service.save_draft(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/schedule/activate")
async def api_schedule_activate(req: ScheduleActivateRequest, _: None = Depends(require_local_control)):
    from backend.app.services import schedule_service
    try:
        return schedule_service.activate(req.spec.model_dump(), req.confirm)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects root browser access directly to the Production Health Dashboard."""
    return RedirectResponse(url="/dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
