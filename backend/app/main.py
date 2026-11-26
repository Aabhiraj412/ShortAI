"""
FastAPI Application Entry Point for AI YouTube Shorts Studio.
Provides full REST API for Dashboard, Agent controls, Pipeline runs, Media streaming, and Settings.
"""
import os
import asyncio
import re
import ipaddress
import json
import shutil
import subprocess
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Body, Depends, Request, File, UploadFile
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


DASHBOARD_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000"}


def require_same_origin(request: Request) -> None:
    """
    CSRF guard for account-level actions (YouTube connect). Loopback alone is not enough:
    any website open in the local browser can send a simple POST to 127.0.0.1. Browsers
    always send Origin / Sec-Fetch-Site on such requests, so a cross-site one is refused.
    Non-browser local clients (curl, tests) send neither and stay allowed.
    """
    require_local_control(request)
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site in ("cross-site", "same-site"):
        raise HTTPException(status_code=403, detail="Cross-site requests are not allowed for this action.")
    origin = request.headers.get("origin")
    if origin is not None:
        own = f"{request.url.scheme}://{request.url.netloc}"
        if origin not in DASHBOARD_ORIGINS and origin != own:
            raise HTTPException(status_code=403, detail="Requests from other origins are not allowed for this action.")


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
async def stage_322_connect_youtube(_: None = Depends(require_local_control), __: None = Depends(require_same_origin)):
    from backend.app.services import youtube_connection_service as ycs
    try:
        res = await asyncio.to_thread(ycs.connect, 180)
    except ycs.ConnectInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    ok, channel, error = res["authorized"], ({"title": res["channel_title"]} if res["ok"] else None), (
        None if res["ok"] else res["message"])
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


class YouTubeConnectRequest(ConfirmedActionRequest):
    confirm: bool = False


@app.get("/api/creative-engine/status")
async def api_creative_engine_status(_: None = Depends(require_local_control)):
    """Pop Info engine flag, script/asset sources, API configuration (presence only) and quality reports."""
    from backend.app.services import creative_engine_status as ces
    return await asyncio.to_thread(ces.status)


@app.get("/api/creative-engine/reports/{report_id}")
async def api_creative_engine_report(report_id: str, _: None = Depends(require_local_control)):
    from backend.app.services import creative_engine_status as ces
    if not re.fullmatch(r"[0-9a-f]{12}", report_id or ""):
        raise HTTPException(status_code=400, detail="invalid report id")
    detail = await asyncio.to_thread(ces.report_detail, report_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="report not found")
    return detail


@app.get("/api/youtube/connection")
async def api_youtube_connection(_: None = Depends(require_local_control)):
    """Canonical connection status for the dashboard card (no tokens, no secrets)."""
    from backend.app.services import youtube_connection_service as ycs
    return await asyncio.to_thread(ycs.connection_view)


@app.post("/api/youtube/connection/check")
async def api_youtube_connection_check(_: None = Depends(require_local_control), __: None = Depends(require_same_origin)):
    """Retry Connection Check: bypasses the status cache; never changes credentials."""
    from backend.app.services import youtube_connection_service as ycs
    return await asyncio.to_thread(ycs.connection_view, True)


@app.post("/api/youtube/connection/connect")
async def api_youtube_connection_connect(req: YouTubeConnectRequest, _: None = Depends(require_local_control), __: None = Depends(require_same_origin)):
    """Connect / Reconnect: opens Google's consent page in this computer's browser."""
    from backend.app.services import youtube_connection_service as ycs
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required.")
    try:
        return await asyncio.to_thread(ycs.connect, 180)
    except ycs.ConnectInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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


@app.get("/api/health/live")
async def api_health_live():
    """Stage 3.32H liveness: answers from the event loop without touching the database or Windows services."""
    from datetime import datetime, timezone
    return {"status": "alive", "pid": os.getpid(), "at_utc": datetime.now(timezone.utc).isoformat()}


@app.get("/api/health/ready")
def api_health_ready():
    """Stage 3.32H readiness: a bounded, read-only database check (runs in the threadpool, never blocks the loop)."""
    import sqlite3 as _sq
    import time as _time
    from pathlib import Path as _P
    from backend.app.core.paths import DATABASE_PATH
    start = _time.perf_counter()
    try:
        conn = _sq.connect(_P(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=3.0)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": f"database: {type(exc).__name__}",
                                                      "latency_ms": round((_time.perf_counter() - start) * 1000, 1)})
    return {"status": "ready", "latency_ms": round((_time.perf_counter() - start) * 1000, 1)}


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
    try:   # Stage 3.29: thumbnail generation/upload/verification state per row
        from backend.app.services.thumbnail_pipeline import ready_label
        states = svc.engine.thumbnail_store().all()
        for it in items:
            st = states.get(int(it.get("id") or 0), {})
            it["thumbnail_state"] = {k: st.get(k) for k in ("upload_status", "verify_status", "upload_attempts",
                                                            "verify_attempts", "uploaded_at", "verified_at", "last_error")}
            it["thumbnail_ready"] = ready_label(st) if st else ("not uploaded yet" if not it.get("youtube_video_id")
                                                                 else "no record (uploaded before Stage 3.29)")
    except Exception as exc:
        logging.getLogger(__name__).debug("thumbnail state unavailable: %s", exc)
    oauth = await asyncio.to_thread(svc.oauth.get_auth_status_cached)
    # Stage 3.30: general-bucket LOCAL ESTIMATE for the current Pacific day (uploads have their own bucket);
    # full per-bucket view: GET /api/youtube/quota
    try:
        _g = svc.engine.quota.usage()["general"]
        quota_used, quota_limit = _g["used_estimate"], _g["daily_limit"]
    except Exception:
        quota_used, quota_limit = None, None
    return {
        "items": items,
        "count": len(items),
        "youtube_oauth": oauth,
        "quota_used_today": quota_used,
        "quota_daily_limit": quota_limit,
        "quota_source": "local estimate, general bucket, Pacific day",
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


class RecoveryPolicyRequest(ConfirmedActionRequest):
    confirm: bool = False
    settings: Dict[str, Any] = Field(default_factory=dict)


@app.get("/api/ops/intelligence")
async def api_ops_intelligence(days: int = 7, _: None = Depends(require_local_control)):
    """Stage 3.32A Operational Intelligence: every section for the window (1-30 days). Read-only, cached 60 s."""
    from backend.app.services.operational_intelligence import MAX_DAYS, get_service
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"days must be between 1 and {MAX_DAYS}")
    return await asyncio.to_thread(get_service().overview, days)


@app.get("/api/ops/intelligence/{section}")
async def api_ops_intelligence_section(section: str, days: int = 7, _: None = Depends(require_local_control)):
    """One Operational Intelligence section (production_health, failure_intelligence, renderer_reliability,
    topic_intelligence, api_intelligence, recovery_analytics, reliability_trends). Read-only."""
    from backend.app.services.operational_intelligence import MAX_DAYS, OperationalIntelligence, get_service
    if section not in OperationalIntelligence.SECTIONS:
        raise HTTPException(status_code=404, detail=f"unknown section; one of {', '.join(OperationalIntelligence.SECTIONS)}")
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"days must be between 1 and {MAX_DAYS}")
    return await asyncio.to_thread(get_service().section, section, days)


@app.get("/api/ops/daily-summary")
async def api_ops_daily_summary(date: Optional[str] = None, _: None = Depends(require_local_control)):
    """Consolidated daily operational summary for an IST date (default today). Recommendations only; no actions."""
    from datetime import datetime as _dt
    from backend.app.services.operational_intelligence import get_service
    if date is not None:
        try:
            _dt.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    return await asyncio.to_thread(get_service().daily_summary, date)


# ---------------------------------------------------------------------------------------------------------------
# Stage 3.32B Autonomy Control Plane. Reads are localhost-only. Every state-changing endpoint additionally needs
# (1) same-origin browser context (no cross-site Origin / Sec-Fetch-Site), (2) Content-Type application/json and
# (3) the per-process CSRF token from GET /api/autonomy/session in the X-CrazzyDocs-CSRF header. There is no
# generic execution endpoint: only a stored, approved and signed request can be executed, by its id.
# ---------------------------------------------------------------------------------------------------------------
import hmac as _hmac
import secrets as _secrets

AUTONOMY_CSRF_HEADER = "X-CrazzyDocs-CSRF"
_AUTONOMY_CSRF_TOKEN = _secrets.token_urlsafe(32)


def _autonomy():
    from backend.app.autonomy import control_plane as _acp
    return _acp.get_control_plane()


def require_autonomy_write(request: Request) -> None:
    require_same_origin(request)
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site and site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="Cross-site requests are not allowed for autonomy controls.")
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="Autonomy controls accept application/json only.")
    token = request.headers.get(AUTONOMY_CSRF_HEADER) or ""
    if not _hmac.compare_digest(token.encode("utf-8"), _AUTONOMY_CSRF_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token for autonomy controls.")


async def _autonomy_call(fn, *args, **kwargs):
    from backend.app.autonomy.control_plane import ControlPlaneError
    from backend.app.autonomy.registry import PayloadValidationError, ProhibitedActionError, UnknownActionError
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ControlPlaneError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc)[:300])
    except ProhibitedActionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)[:300])
    except (UnknownActionError, PayloadValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Autonomy control plane request failed")
        raise HTTPException(status_code=503, detail="Autonomy control plane unavailable; autonomous execution stays blocked.")


class _AutonomyBody(BaseModel):
    model_config = {"extra": "forbid"}
    operator: str = Field(min_length=1, max_length=64)


class AutonomyDecisionRequest(_AutonomyBody):
    reason: str = Field(min_length=1, max_length=500)


class AutonomyApproveRequest(_AutonomyBody):
    payload_hash: str = Field(min_length=64, max_length=64)
    reason: str = Field(default="", max_length=500)
    confirm: bool = False


class AutonomyExecuteRequest(_AutonomyBody):
    confirm: bool = False


class AutonomyLevelRequest(_AutonomyBody):
    scope: str = Field(min_length=1, max_length=40)
    level: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False


class AutonomyStandingRequest(_AutonomyBody):
    action_type: str = Field(min_length=1, max_length=100)
    max_per_day: int
    valid_hours: int
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False


class AutonomyKillSwitchRequest(_AutonomyBody):
    reason: str = Field(min_length=1, max_length=500)
    confirm_text: Optional[str] = Field(default=None, max_length=40)


def _require_confirm(flag: bool) -> None:
    if flag is not True:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required (confirm: true).")


@app.get("/api/autonomy/session")
async def api_autonomy_session(_: None = Depends(require_same_origin)):
    """CSRF token for the dashboard. Cross-site pages cannot read it (CORS) and are refused by Origin checks."""
    return {"csrf_header": AUTONOMY_CSRF_HEADER, "csrf_token": _AUTONOMY_CSRF_TOKEN}


@app.get("/api/autonomy/status")
async def api_autonomy_status(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autonomy().status())


@app.get("/api/autonomy/permissions")
async def api_autonomy_permissions(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autonomy().permissions())


@app.get("/api/autonomy/registry")
async def api_autonomy_registry(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autonomy().registry_view())


@app.get("/api/autonomy/kill-switches")
async def api_autonomy_kill_switches(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autonomy().kill_switches())


@app.get("/api/autonomy/approvals")
async def api_autonomy_approvals(status: Optional[str] = None, limit: int = 50, _: None = Depends(require_local_control)):
    return {"requests": await _autonomy_call(lambda: _autonomy().list_requests(status=status, limit=limit))}


@app.get("/api/autonomy/approvals/{request_id}")
async def api_autonomy_approval(request_id: str, _: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autonomy().get_request(request_id))


@app.get("/api/autonomy/executions")
async def api_autonomy_executions(limit: int = 50, failures_only: bool = False, _: None = Depends(require_local_control)):
    return {"executions": await _autonomy_call(lambda: _autonomy().list_executions(limit=limit, failures_only=failures_only))}


@app.get("/api/autonomy/audit")
async def api_autonomy_audit(limit: int = 100, before_seq: Optional[int] = None, request_id: Optional[str] = None,
                             verify: bool = False, _: None = Depends(require_local_control)):
    def _build():
        cp = _autonomy()
        out = {"events": cp.audit_events(limit=limit, before_seq=before_seq, request_id=request_id)}
        if verify:
            out["chain"] = cp.verify_audit_chain()
        return out
    return await _autonomy_call(_build)


@app.post("/api/autonomy/approvals/{request_id}/approve")
async def api_autonomy_approve(request_id: str, req: AutonomyApproveRequest, _: None = Depends(require_local_control),
                               __: None = Depends(require_autonomy_write)):
    _require_confirm(req.confirm)
    return await _autonomy_call(lambda: _autonomy().approve(request_id, req.operator, req.payload_hash, req.reason))


@app.post("/api/autonomy/approvals/{request_id}/reject")
async def api_autonomy_reject(request_id: str, req: AutonomyDecisionRequest, _: None = Depends(require_local_control),
                              __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _autonomy().reject(request_id, req.operator, req.reason))


@app.post("/api/autonomy/approvals/{request_id}/cancel")
async def api_autonomy_cancel(request_id: str, req: AutonomyDecisionRequest, _: None = Depends(require_local_control),
                              __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _autonomy().cancel(request_id, req.operator, req.reason))


@app.post("/api/autonomy/approvals/{request_id}/execute")
async def api_autonomy_execute(request_id: str, req: AutonomyExecuteRequest, _: None = Depends(require_local_control),
                               __: None = Depends(require_autonomy_write)):
    """Run ONE stored request that is APPROVED. Every gate (kill switches, level, signature, payload hash, expiry,
    daily limit) is re-checked by the control plane; the caller cannot supply an action type or payload."""
    _require_confirm(req.confirm)
    from backend.app.autonomy.control_plane import validate_operator

    def _run():
        from backend.app.autonomy.control_plane import PermissionDenied
        op = validate_operator(req.operator)
        cp = _autonomy()
        if str(cp.get_request(request_id).get("action_type") or "").startswith("production."):
            # Stage 3.36: production dispatches run only inside the scheduler daemon's Autopilot controller.
            cp._audit_now("EXECUTION_BLOCKED", f"operator:{op}", request_id=str(request_id)[:40],
                          reason="production actions are executed only by the Autopilot controller in the scheduler daemon")
            raise PermissionDenied("production actions are executed only by the Autopilot controller inside the "
                                   "scheduler daemon, never from the dashboard")
        return cp.execute(request_id, actor=f"operator:{op}")
    return await _autonomy_call(_run)


@app.put("/api/autonomy/permissions/levels")
async def api_autonomy_set_level(req: AutonomyLevelRequest, _: None = Depends(require_local_control),
                                 __: None = Depends(require_autonomy_write)):
    _require_confirm(req.confirm)
    return await _autonomy_call(lambda: _autonomy().set_level(req.scope, req.level, req.operator, req.reason))


@app.post("/api/autonomy/permissions/standing-approvals")
async def api_autonomy_grant_standing(req: AutonomyStandingRequest, _: None = Depends(require_local_control),
                                      __: None = Depends(require_autonomy_write)):
    _require_confirm(req.confirm)
    return await _autonomy_call(lambda: _autonomy().grant_standing_approval(
        req.action_type, req.operator, req.reason, max_per_day=req.max_per_day, valid_hours=req.valid_hours))


@app.post("/api/autonomy/permissions/standing-approvals/{action_type}/revoke")
async def api_autonomy_revoke_standing(action_type: str, req: AutonomyDecisionRequest, _: None = Depends(require_local_control),
                                       __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _autonomy().revoke_standing_approval(action_type, req.operator, req.reason))


@app.post("/api/autonomy/kill-switch/activate")
async def api_autonomy_kill_activate(req: AutonomyKillSwitchRequest, _: None = Depends(require_local_control),
                                     __: None = Depends(require_autonomy_write)):
    """Stops AUTONOMOUS execution only. Production (scheduler, cycles, workers, publishing) is not affected."""
    return await _autonomy_call(lambda: _autonomy().set_dashboard_kill_switch(True, req.operator, req.reason))


@app.post("/api/autonomy/kill-switch/deactivate")
async def api_autonomy_kill_deactivate(req: AutonomyKillSwitchRequest, _: None = Depends(require_local_control),
                                       __: None = Depends(require_autonomy_write)):
    if (req.confirm_text or "").strip() != "RESUME AUTONOMY":
        raise HTTPException(status_code=400, detail='Type "RESUME AUTONOMY" to deactivate the dashboard kill switch.')
    return await _autonomy_call(lambda: _autonomy().set_dashboard_kill_switch(False, req.operator, req.reason))


@app.post("/api/autonomy/assessment/run")
async def api_autonomy_assessment(req: _AutonomyBody, _: None = Depends(require_local_control),
                                  __: None = Depends(require_autonomy_write)):
    """Observe / recommend now from the Stage 3.32A daily summary. Proposals go to the approval queue; only
    standing-approved, permitted actions can execute, through every control-plane gate."""
    from backend.app.autonomy.control_plane import validate_operator
    from backend.app.autonomy.engine import run_assessment

    def _run():
        validate_operator(req.operator)
        return run_assessment(_autonomy())
    return await _autonomy_call(_run)


# ---------------------------------------------------------------------------------------------------------------
# Stage 3.32C Recommendation Center. Recommendation-only: no endpoint here executes an operational action.
# Reads are localhost-only; status changes and runs need the same CSRF protection as the autonomy controls.
# ---------------------------------------------------------------------------------------------------------------
def _recommendations():
    from backend.app.autonomy.recommendations import engine as _rec_engine
    return _rec_engine.get_engine()


class RecommendationNoteRequest(_AutonomyBody):
    note: Optional[str] = Field(default=None, max_length=500)


class RecommendationRejectRequest(_AutonomyBody):
    reason: str = Field(min_length=1, max_length=500)


class RecommendationResolveRequest(_AutonomyBody):
    evidence: str = Field(min_length=1, max_length=2000)


class RecommendationRunRequest(_AutonomyBody):
    force: bool = False


@app.get("/api/recommendations")
async def api_recommendations(status: str = "open", category: Optional[str] = None, limit: int = 100,
                              _: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _recommendations().overview(status=status, category=category, limit=limit))


@app.get("/api/recommendations/trend")
async def api_recommendations_trend(days: int = 14, _: None = Depends(require_local_control)):
    if not 1 <= days <= 60:
        raise HTTPException(status_code=400, detail="days must be between 1 and 60")
    def _build():
        eng = _recommendations()
        return eng.store.trend(days, eng.clock())
    return await _autonomy_call(_build)


@app.get("/api/recommendations/{rec_id}")
async def api_recommendation_detail(rec_id: str, _: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _recommendations().detail(rec_id))


@app.post("/api/recommendations/run")
async def api_recommendations_run(req: RecommendationRunRequest, _: None = Depends(require_local_control),
                                  __: None = Depends(require_autonomy_write)):
    """Analyse now (bounded 7-day window). Gated by the control plane: OFF -> nothing; OBSERVE -> observations only;
    any kill switch -> blocked. Never executes anything."""
    from backend.app.autonomy.control_plane import validate_operator

    def _run():
        op = validate_operator(req.operator)
        return _recommendations().run(triggered_by=f"operator:{op}", force=req.force)
    return await _autonomy_call(_run)


async def _rec_status(rec_id: str, status: str, operator: str, reason: Optional[str] = None, evidence: Optional[str] = None):
    return await _autonomy_call(lambda: _recommendations().set_status(rec_id, status, operator, reason=reason, evidence=evidence))


@app.post("/api/recommendations/{rec_id}/acknowledge")
async def api_recommendation_ack(rec_id: str, req: RecommendationNoteRequest, _: None = Depends(require_local_control),
                                 __: None = Depends(require_autonomy_write)):
    return await _rec_status(rec_id, "ACKNOWLEDGED", req.operator, reason=req.note)


@app.post("/api/recommendations/{rec_id}/approve")
async def api_recommendation_approve(rec_id: str, req: RecommendationNoteRequest, _: None = Depends(require_local_control),
                                     __: None = Depends(require_autonomy_write)):
    """Records the operator's agreement with the suggestion. Nothing is executed."""
    return await _rec_status(rec_id, "APPROVED", req.operator, reason=req.note)


@app.post("/api/recommendations/{rec_id}/reject")
async def api_recommendation_reject(rec_id: str, req: RecommendationRejectRequest, _: None = Depends(require_local_control),
                                    __: None = Depends(require_autonomy_write)):
    return await _rec_status(rec_id, "REJECTED", req.operator, reason=req.reason)


@app.post("/api/recommendations/{rec_id}/resolve")
async def api_recommendation_resolve(rec_id: str, req: RecommendationResolveRequest, _: None = Depends(require_local_control),
                                     __: None = Depends(require_autonomy_write)):
    return await _rec_status(rec_id, "RESOLVED", req.operator, evidence=req.evidence)


# -- Stage 3.32D self-healing operations ------------------------------------------------------------------------
# Every write is local-only, CSRF-protected (require_autonomy_write) and passes the 3.32B control plane: approvals,
# rejections and executions are ControlPlane.approve / reject / execute with a validated human operator identity.

def _self_healing():
    from backend.app.autonomy.self_healing import engine as _sh_engine
    return _sh_engine.get_engine()


class SelfHealingScanRequest(_AutonomyBody):
    force: bool = False


@app.get("/api/self-healing")
async def api_self_healing(limit: int = 50, _: None = Depends(require_local_control)):
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return await _autonomy_call(lambda: _self_healing().overview(limit=limit))


@app.get("/api/self-healing/incidents")
async def api_self_healing_incidents(status: str = "open", limit: int = 100, _: None = Depends(require_local_control)):
    from backend.app.autonomy.self_healing.store import STATUSES
    if status not in ("open", "all") + STATUSES:
        raise HTTPException(status_code=400, detail="status must be open, all or a self-healing status")
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return await _autonomy_call(lambda: {"incidents": _self_healing().list_incidents(status=status, limit=limit)})


@app.get("/api/self-healing/incidents/{incident_id}")
async def api_self_healing_incident(incident_id: str, _: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _self_healing().incident_detail(incident_id))


@app.get("/api/self-healing/history")
async def api_self_healing_history(limit: int = 100, _: None = Depends(require_local_control)):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    return await _autonomy_call(lambda: _self_healing().history(limit))


@app.post("/api/self-healing/scan")
async def api_self_healing_scan(req: SelfHealingScanRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    """Detect / classify / diagnose now. Gated by the control plane: OFF -> nothing; OBSERVE -> incidents only;
    RECOMMEND -> proposals into the approval queue; any kill switch -> blocked. No background loop."""
    from backend.app.autonomy.control_plane import validate_operator

    def _scan():
        op = validate_operator(req.operator)
        return _self_healing().scan(triggered_by=f"operator:{op}", force=req.force)
    return await _autonomy_call(_scan)


@app.post("/api/self-healing/recoveries/{recovery_id}/approve")
async def api_self_healing_approve(recovery_id: str, req: AutonomyApproveRequest, _: None = Depends(require_local_control),
                                   __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _self_healing().approve(recovery_id, req.operator, req.payload_hash, req.reason))


@app.post("/api/self-healing/recoveries/{recovery_id}/reject")
async def api_self_healing_reject(recovery_id: str, req: AutonomyDecisionRequest, _: None = Depends(require_local_control),
                                  __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _self_healing().reject(recovery_id, req.operator, req.reason))


@app.post("/api/self-healing/recoveries/{recovery_id}/execute")
async def api_self_healing_execute(recovery_id: str, req: _AutonomyBody, _: None = Depends(require_local_control),
                                   __: None = Depends(require_autonomy_write)):
    """Runs one APPROVED recovery through ControlPlane.execute (every 3.32B gate) after re-checking job and resource
    preconditions. Blocked while any kill switch is active or the self_healing level is below APPROVED_EXECUTION."""
    return await _autonomy_call(lambda: _self_healing().execute(recovery_id, req.operator))


# -- Stage 3.32G Controlled Autopilot -------------------------------------------------------------------------------
# One read-only view over 3.32A-F and a deterministic recommendation -> approval-request bridge. It never executes:
# execution stays ControlPlane.execute (Autonomy Control), with every gate.

def _autopilot():
    from backend.app.autonomy.autopilot import get_autopilot
    return get_autopilot()


class AutopilotProposeRequest(_AutonomyBody):
    action_type: str = Field(min_length=3, max_length=80)


@app.get("/api/autopilot")
async def api_autopilot(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _autopilot().overview())


@app.get("/api/autopilot/policy")
async def api_autopilot_policy(_: None = Depends(require_local_control)):
    from backend.app.autonomy.autopilot import action_policy
    return await _autonomy_call(action_policy)


# -- Stage 3.36: Autopilot continuous production controller ------------------------------------------------------
# Start/stop are requests persisted for the controller in the scheduler daemon: the response shows the state the
# backend actually holds (STARTING / STOPPING until the daemon has verified the transition), never assumed success.
class AutopilotControllerRequest(_AutonomyBody):
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False


def _production_controller():
    from backend.app.autonomy.production_controller import get_controller
    return get_controller()


@app.get("/api/autopilot/controller")
async def api_autopilot_controller(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _production_controller().status())


@app.post("/api/autopilot/controller/start")
async def api_autopilot_controller_start(req: AutopilotControllerRequest, _: None = Depends(require_local_control),
                                         __: None = Depends(require_autonomy_write)):
    _require_confirm(req.confirm)
    return await _autonomy_call(lambda: _production_controller().request_start(req.operator, req.reason))


@app.post("/api/autopilot/controller/stop")
async def api_autopilot_controller_stop(req: AutopilotControllerRequest, _: None = Depends(require_local_control),
                                        __: None = Depends(require_autonomy_write)):
    _require_confirm(req.confirm)
    return await _autonomy_call(lambda: _production_controller().request_stop(req.operator, req.reason))


# -- Stage 3.32H/I/J: operations, phased activation, operational reports ----------------------------------------
class ActivationAdvanceRequest(_AutonomyBody):
    target: str = Field(min_length=3, max_length=32)
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False
    authorization: str = Field(default="", max_length=64)
    authorized_actions: List[str] = Field(default_factory=list, max_length=12)


class ActivationRetreatRequest(_AutonomyBody):
    target: str = Field(min_length=2, max_length=32)
    reason: str = Field(min_length=1, max_length=500)


class ReportGenerateRequest(_AutonomyBody):
    kind: str = Field(pattern=r"^(daily|weekly)$")
    key: Optional[str] = Field(default=None, pattern=r"^\d{4}-(\d{2}-\d{2}|W\d{2})$")
    force: bool = False


def _activation():
    from backend.app.autonomy.activation import get_activation
    return get_activation()


def _reports():
    from backend.app.autonomy.reports import get_reports
    return get_reports()


def _operations_view() -> Dict[str, Any]:
    from datetime import datetime, timezone, timedelta as _td
    from backend.app.core import health_monitoring as hm
    from backend.app.core.paths import LOGS_DIR
    from backend.app.autonomy.control_plane import safe_error
    since = (datetime.now(timezone.utc) - _td(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    events = hm.read_events(LOGS_DIR, since_utc=since)
    health = {"state": hm.read_state(LOGS_DIR), "last_24h": hm.summarize_events(events),
              "recent_restarts": [e for e in events if e.get("type") == "restart"][-10:],
              "recent_transitions": [e for e in events if e.get("type") == "probe"
                                     and e.get("state") != e.get("previous_state")][-10:]}
    rendering: Dict[str, Any] = {}
    try:
        from backend.app.rendering.ffmpeg_geometry import fit_and_pad_filter
        f = fit_and_pad_filter(860, 855)
        rendering["geometry_fix_active"] = "860:854" in f and "force_divisible_by=2" in f
    except Exception as exc:
        rendering["geometry_fix_active"] = False
        rendering["geometry_error"] = safe_error(exc)
    try:
        from backend.app.services.operational_intelligence import get_service
        rr = get_service().renderer_reliability(days=7)
        fi = get_service().failure_intelligence(days=7)
        trend = (fi.get("metrics") or {}).get("daily_trend") or {}
        rendering["last_7_days"] = {
            "status": rr.get("status"),
            "metrics": {k: (rr.get("metrics") or {}).get(k) for k in (
                "renders_completed", "renders_failed", "render_success_pct", "avg_render_seconds", "ffmpeg_errors",
                "render_timeouts", "file_lock_errors", "lemo_opuscar_produced")},
            "ffmpeg_failures_by_day": {d: v.get("ffmpeg") for d, v in trend.items() if v.get("ffmpeg")},
            "unavailable": rr.get("unavailable") or {}}
    except Exception as exc:
        rendering["last_7_days"] = {"status": "unavailable", "error": safe_error(exc)}
    try:
        activation = _activation().overview()
    except Exception as exc:
        activation = {"error": safe_error(exc)}
    return {"health_monitor": health, "rendering": rendering, "activation": activation,
            "reports": _reports().list_reports(limit=14), "generated_at_utc": datetime.now(timezone.utc).isoformat()}


@app.get("/api/autopilot/operations")
async def api_autopilot_operations(_: None = Depends(require_local_control)):
    return await _autonomy_call(_operations_view)


@app.get("/api/autopilot/activation")
async def api_autopilot_activation(_: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _activation().overview())


@app.get("/api/autopilot/activation/readiness")
async def api_autopilot_activation_readiness(phase: Optional[str] = None, _: None = Depends(require_local_control)):
    return await _autonomy_call(lambda: _activation().assess(phase))


@app.post("/api/autopilot/activation/advance")
async def api_autopilot_activation_advance(req: ActivationAdvanceRequest, _: None = Depends(require_local_control),
                                           __: None = Depends(require_autonomy_write)):
    """Advances ONE phase after re-validating readiness. RECOMMEND and above need the exact authorization phrase;
    restricted execution also needs the operator-created autonomy_startup.json. Never grants standing approvals."""
    return await _autonomy_call(lambda: _activation().advance(req.target, req.operator, req.reason, confirm=req.confirm,
                                                              authorization=req.authorization,
                                                              authorized_actions=req.authorized_actions))


@app.post("/api/autopilot/activation/retreat")
async def api_autopilot_activation_retreat(req: ActivationRetreatRequest, _: None = Depends(require_local_control),
                                           __: None = Depends(require_autonomy_write)):
    return await _autonomy_call(lambda: _activation().retreat(req.target, req.operator, req.reason))


@app.get("/api/autopilot/reports")
async def api_autopilot_reports(kind: Optional[str] = None, _: None = Depends(require_local_control)):
    if kind not in (None, "daily", "weekly"):
        raise HTTPException(status_code=400, detail="kind must be daily or weekly")
    return await _autonomy_call(lambda: {"reports": _reports().list_reports(kind, 60)})


@app.get("/api/autopilot/reports/{kind}/{key}")
async def api_autopilot_report(kind: str, key: str, _: None = Depends(require_local_control)):
    if kind not in ("daily", "weekly") or not re.fullmatch(r"\d{4}-(\d{2}-\d{2}|W\d{2})", key or ""):
        raise HTTPException(status_code=404, detail="report not found")
    rep = await _autonomy_call(lambda: _reports().get(kind, key))
    if rep is None:
        raise HTTPException(status_code=404, detail="report not generated yet")
    return rep


@app.post("/api/autopilot/reports/generate")
async def api_autopilot_report_generate(req: ReportGenerateRequest, _: None = Depends(require_local_control),
                                        __: None = Depends(require_autonomy_write)):
    from backend.app.autonomy.control_plane import validate_operator
    op = await _autonomy_call(validate_operator, req.operator)
    return await _autonomy_call(lambda: _reports().generate(req.kind, req.key, generated_by=op, force=req.force))


# -- Roadmap Stages 3.31 / 3.32 / 3.33: topic, creative and rendering intelligence (persisted data only) ---------
class CreativeExperimentCreate(_AutonomyBody):
    dimension: str = Field(pattern=r"^hook_type$")
    arms: List[str] = Field(min_length=2, max_length=5)
    hypothesis: str = Field(min_length=3, max_length=500)


class CreativeExperimentStatus(_AutonomyBody):
    status: str = Field(pattern=r"^(ACTIVE|PAUSED|COMPLETED)$")


def _creative_store():
    from backend.app.creative_intel.store import CreativeStore
    return CreativeStore()


def _intelligence_view() -> Dict[str, Any]:
    from backend.app.content.topic_intelligence_flags import load_flags
    from backend.app.content.demand_signals import DemandSignalStore
    from backend.app.core.paths import DATABASE_PATH, PROJECT_ROOT
    flags = load_flags().to_dict()
    flags.pop("exhaustion_limits", None)
    signals = DemandSignalStore(str(DATABASE_PATH)).recent(hours=48, limit=300)
    seen, top = set(), []
    for s in sorted(signals, key=lambda r: -(r.get("value") or 0)):
        key = (s.get("wikipedia_title") or s.get("term") or "").lower()
        if key and key not in seen:
            seen.add(key)
            top.append({k: s.get(k) for k in ("term", "source", "region", "metric", "value", "velocity", "fetched_at_utc")})
        if len(top) >= 15:
            break
    store = _creative_store()
    bench_path = PROJECT_ROOT / "docs" / "benchmarks" / "stage_3_33_render_benchmarks.json"
    try:
        bench = json.loads(bench_path.read_text(encoding="utf-8"))
    except Exception:
        bench = None
    # Stage 3.35: analytics, learning, providers and visual composition (persisted data; no provider is called)
    analytics: Dict[str, Any] = {}
    try:
        import backend.app.services.youtube_analytics as ya
        from backend.app.services.youtube_publishing_engine import YouTubeOAuthManager
        from backend.app.creative_intel import learning
        analytics = {"auth": ya.auth_status(YouTubeOAuthManager()), "sync": ya._read_state(ya.SYNC_STATE_KEY)}
        run = learning.latest_run()
        if run:
            a = run.get("analysis") or {}
            analytics["learning"] = {"created_at_utc": run.get("created_at_utc"), "input_hash": run.get("input_hash"),
                                     **{k: a.get(k) for k in ("videos", "videos_with_data", "videos_first_7_days",
                                                              "basis", "sufficiency", "needed_for_claims", "overall")},
                                     "groups": {g: {k: {"n": v.get("n"), "median_views": (v.get("views") or {}).get("median"),
                                                        "median_avp": (v.get("averageViewPercentage") or {}).get("median")}
                                                    for k, v in grp.items()} for g, grp in (a.get("groups") or {}).items()},
                                     "correlations": {k: v for k, v in (a.get("correlations") or {}).items() if v.get("rho") is not None},
                                     "recommendations": run.get("recommendations") or []}
    except Exception as exc:
        analytics["error"] = f"{type(exc).__name__}"
    providers: Dict[str, Any] = {}
    visual: Dict[str, Any] = {}
    try:
        import sqlite3 as _sq
        from backend.app.services import provider_audit
        from backend.app.media.footage import provider_status
        from backend.app.rendering.pop_info.asset_manifest import audit_recent
        from backend.app.core.paths import MEDIA_PRODUCTIONS_DIR
        providers = {"inventory": provider_audit.summary(), "footage": provider_status()}
        c = _sq.connect(Path(DATABASE_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            jobs = [r[0] for r in c.execute("SELECT id FROM persistent_jobs WHERE status='COMPLETED' ORDER BY created_at DESC LIMIT 20")]
        finally:
            c.close()
        visual = audit_recent(Path(MEDIA_PRODUCTIONS_DIR), jobs)
        visual.pop("per_video", None)
    except Exception as exc:
        providers.setdefault("error", type(exc).__name__)
    return {
        "analytics": analytics, "providers": providers, "visual": visual,
        "topic": {"flags": flags, "demand_signals_48h": len(signals),
                  "last_signal_at_utc": signals[0]["fetched_at_utc"] if signals else None, "top_demand": top},
        "creative": {"summary": store.summary(), "records": store.list_records(15), "experiments": store.list_experiments()},
        "rendering": {"benchmarks": bench, "decision": "Keep Lemo-Opuscar + Pop Info as the production renderer; "
                                                       "see docs/STAGE_3_33_RENDERING_ARCHITECTURE_DECISION.md"},
    }


@app.get("/api/intelligence/topic-creative-rendering")
async def api_intelligence_view(_: None = Depends(require_local_control)):
    return await _autonomy_call(_intelligence_view)


@app.post("/api/creative/experiments")
async def api_creative_experiment_create(req: CreativeExperimentCreate, _: None = Depends(require_local_control),
                                         __: None = Depends(require_autonomy_write)):
    """Creates a DRAFT experiment (nothing changes until it is activated)."""
    from backend.app.autonomy.control_plane import validate_operator
    from backend.app.creative_intel.store import CreativeStoreError
    op = await _autonomy_call(validate_operator, req.operator)
    try:
        return await asyncio.to_thread(_creative_store().create_experiment, req.dimension, req.arms, req.hypothesis, op)
    except CreativeStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300])


@app.post("/api/creative/experiments/{exp_id}/status")
async def api_creative_experiment_status(exp_id: str, req: CreativeExperimentStatus, _: None = Depends(require_local_control),
                                         __: None = Depends(require_autonomy_write)):
    """ACTIVE limits the script writer's hook-style assignment to the experiment's arms (one active per dimension)."""
    from backend.app.autonomy.control_plane import validate_operator
    from backend.app.creative_intel.store import CreativeStoreError
    if not re.fullmatch(r"exp_[0-9a-f]{10}", exp_id or ""):
        raise HTTPException(status_code=404, detail="experiment not found")
    op = await _autonomy_call(validate_operator, req.operator)
    try:
        return await asyncio.to_thread(_creative_store().set_experiment_status, exp_id, req.status, op)
    except CreativeStoreError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc)[:300])


@app.post("/api/autopilot/recommendations/{rec_id}/propose")
async def api_autopilot_propose(rec_id: str, req: AutopilotProposeRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    """Creates ONE approval request for a mapped follow-up action of an open recommendation (needs RECOMMEND level
    for the action's category). Approval and execution stay in Autonomy Control."""
    if not re.fullmatch(r"rec_[0-9a-f]{16}", rec_id or ""):
        raise HTTPException(status_code=404, detail="recommendation not found")
    return await _autonomy_call(lambda: _autopilot().propose_from_recommendation(rec_id, req.action_type, req.operator))


# -- Stage 3.32E Community post drafts ---------------------------------------------------------------------------
# Local drafts only: no route here (or anywhere in backend.app.community) can publish. Reads are loopback-only;
# every write is loopback + same-origin + JSON + per-process CSRF token (require_autonomy_write) with a validated
# human operator identity, and is recorded in community_events. Images are served by id from the Community folder.

def _community():
    from backend.app.community.service import get_service
    return get_service()


async def _community_call(fn, *args, **kwargs):
    from backend.app.community.store import StoreError
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except StoreError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc)[:300])
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Community drafts unavailable: database not found.")
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Community draft request failed")
        raise HTTPException(status_code=503, detail="Community drafts unavailable; no draft was changed.")


_COMMUNITY_DRAFT_RE = re.compile(r"^cdraft_[0-9a-f]{16}$")


def _community_draft_id(draft_id: str) -> str:
    if not _COMMUNITY_DRAFT_RE.match(draft_id or ""):
        raise HTTPException(status_code=404, detail="draft not found")
    return draft_id


class _CommunityBody(BaseModel):
    """Community writes. ``operator`` is optional: the dashboard sends none, and the action is attributed to the
    neutral identity ``local-dashboard`` (an unauthenticated local dashboard request that passed loopback,
    same-origin and CSRF checks). A supplied label is validated and recorded, but it is not authentication."""
    model_config = {"extra": "forbid"}
    operator: Optional[str] = Field(default=None, max_length=64)


class CommunityVersionedRequest(_CommunityBody):
    version: int = Field(ge=1, le=1_000_000)


class CommunityEditRequest(CommunityVersionedRequest):
    text: str = Field(min_length=1, max_length=2000)


class CommunityApproveRequest(CommunityVersionedRequest):
    acknowledge_warnings: bool = False
    reason: str = Field(default="", max_length=500)


class CommunityRejectRequest(CommunityVersionedRequest):
    reason: str = Field(min_length=3, max_length=500)


class CommunityArchiveRequest(CommunityVersionedRequest):
    reason: str = Field(default="", max_length=500)


class CommunityImageRequest(CommunityVersionedRequest):
    choice: Optional[str] = Field(default=None, max_length=40)


class CommunityScheduleRequest(_CommunityBody):
    enabled: Optional[bool] = None
    paused: Optional[bool] = None
    kill_switch: Optional[bool] = None
    generation_time: Optional[str] = Field(default=None, max_length=5)
    timezone: Optional[str] = Field(default=None, max_length=64)
    formats: Optional[List[str]] = Field(default=None, max_length=8)
    text_mode: Optional[str] = Field(default=None, max_length=10)
    excluded_categories: Optional[List[str]] = Field(default=None, max_length=20)
    catch_up_hours: Optional[int] = Field(default=None, ge=0, le=23)
    manual_daily_limit: Optional[int] = Field(default=None, ge=1, le=10)
    max_auto_attempts: Optional[int] = Field(default=None, ge=1, le=3)


@app.get("/api/community")
async def api_community_overview(_: None = Depends(require_local_control)):
    return await _community_call(lambda: _community().overview())


@app.get("/api/community/schedule")
async def api_community_schedule(_: None = Depends(require_local_control)):
    return await _community_call(lambda: _community().schedule_status())


@app.get("/api/community/timezones")
async def api_community_timezones(_: None = Depends(require_local_control)):
    """Preset US and India timezones with their current UTC offset and DST state, plus every accepted IANA name."""
    return await _community_call(lambda: _community().timezones())


@app.get("/api/community/schedule/preview")
async def api_community_schedule_preview(timezone: str, generation_time: str, catch_up_hours: int = 12,
                                         _: None = Depends(require_local_control)):
    """Read-only: what a timezone and target time WOULD do (next runs, DST notes). Nothing is saved."""
    if len(timezone) > 64 or len(generation_time) > 5:
        raise HTTPException(status_code=400, detail="invalid timezone or time")
    return await _community_call(lambda: _community().preview(timezone, generation_time, catch_up_hours))


@app.post("/api/community/schedule")
async def api_community_schedule_update(req: CommunityScheduleRequest, _: None = Depends(require_local_control),
                                        __: None = Depends(require_autonomy_write)):
    changes = req.model_dump(exclude={"operator"}, exclude_none=True)
    return await _community_call(lambda: _community().update_config(req.operator, changes))


@app.get("/api/community/drafts")
async def api_community_drafts(status: str = "all", q: str = "", format: str = "", validation: str = "",
                               date_from: str = "", date_to: str = "", limit: int = 50, offset: int = 0,
                               _: None = Depends(require_local_control)):
    from backend.app.community.content import FORMATS
    from backend.app.community.store import STATUSES, VALIDATION_STATUSES
    if status not in ("all", "active", "failed") + STATUSES:
        raise HTTPException(status_code=400, detail="status must be all, active, failed or a draft status")
    if format and format not in FORMATS:
        raise HTTPException(status_code=400, detail="unknown format")
    if validation and validation not in VALIDATION_STATUSES:
        raise HTTPException(status_code=400, detail="unknown validation status")
    for d in (date_from, date_to):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HTTPException(status_code=400, detail="dates must be YYYY-MM-DD")
    if len(q) > 100:
        raise HTTPException(status_code=400, detail="search text must be at most 100 characters")
    if not 1 <= limit <= 200 or not 0 <= offset <= 100000:
        raise HTTPException(status_code=400, detail="limit must be 1-200 and offset 0-100000")
    return await _community_call(lambda: {"drafts": _community().list_drafts(
        status=status, query=q or None, fmt=format or None, validation=validation or None,
        date_from=date_from or None, date_to=date_to or None, limit=limit, offset=offset),
        "limit": limit, "offset": offset})


@app.get("/api/community/drafts/{draft_id}")
async def api_community_draft(draft_id: str, _: None = Depends(require_local_control)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().detail(did))


@app.get("/api/community/drafts/{draft_id}/validation")
async def api_community_draft_validation(draft_id: str, _: None = Depends(require_local_control)):
    did = _community_draft_id(draft_id)

    def _v():
        d = _community().detail(did)
        return {"draft_id": did, "validation_status": d["draft"].get("validation_status"),
                "validation": d["draft"].get("validation"), "image_validation": (d.get("image") or {}).get("validation")}
    return await _community_call(_v)


@app.get("/api/community/drafts/{draft_id}/image-options")
async def api_community_image_options(draft_id: str, _: None = Depends(require_local_control)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().image_options(did))


@app.get("/api/community/images/{image_id}")
async def api_community_image(image_id: str, download: int = 0, _: None = Depends(require_local_control)):
    if not re.fullmatch(r"cimg_[0-9a-f]{16}", image_id or ""):
        raise HTTPException(status_code=404, detail="image not found")
    path = await _community_call(lambda: _community().image_file(image_id))
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="crazzydocs_community_{image_id[5:]}.jpg"'
    return FileResponse(path, media_type="image/jpeg", headers=headers)


@app.get("/api/community/history")
async def api_community_history(limit: int = 50, _: None = Depends(require_local_control)):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return await _community_call(lambda: _community().history(limit))


@app.post("/api/community/generate")
async def api_community_generate(req: _CommunityBody, _: None = Depends(require_local_control),
                                 __: None = Depends(require_autonomy_write)):
    return await _community_call(lambda: _community().generate_manual(req.operator))


@app.post("/api/community/drafts/{draft_id}/edit")
async def api_community_edit(draft_id: str, req: CommunityEditRequest, _: None = Depends(require_local_control),
                             __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().edit(did, req.operator, req.version, req.text))


@app.post("/api/community/drafts/{draft_id}/regenerate-text")
async def api_community_regen_text(draft_id: str, req: CommunityVersionedRequest, _: None = Depends(require_local_control),
                                   __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().regenerate_text(did, req.operator, req.version))


@app.post("/api/community/drafts/{draft_id}/regenerate-image")
async def api_community_regen_image(draft_id: str, req: CommunityImageRequest, _: None = Depends(require_local_control),
                                    __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().regenerate_image(did, req.operator, req.version, req.choice))


@app.post("/api/community/drafts/{draft_id}/approve")
async def api_community_approve(draft_id: str, req: CommunityApproveRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    """Approves the DRAFT only. Nothing is published, uploaded or sent to YouTube."""
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().approve(did, req.operator, req.version, req.acknowledge_warnings,
                                                              req.reason))


@app.post("/api/community/drafts/{draft_id}/reject")
async def api_community_reject(draft_id: str, req: CommunityRejectRequest, _: None = Depends(require_local_control),
                               __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().reject(did, req.operator, req.version, req.reason))


@app.post("/api/community/drafts/{draft_id}/archive")
async def api_community_archive(draft_id: str, req: CommunityArchiveRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _community().archive(did, req.operator, req.version, req.reason))


# -- Stage 3.32F Community publishing (fail closed; OFF by default) ------------------------------------------------
# posts.insert publishes PUBLICLY and IMMEDIATELY and is a pilot API. Every publish passes all gates again
# (enabled + fresh API_AVAILABLE capability + no kill switch + APPROVED draft whose text and image equal the approved
# versions). An ambiguous insert is never retried. The manual handoff never claims a post was published.

def _publishing():
    from backend.app.community.publishing import PublishingService
    return PublishingService(_community())


_COMMUNITY_PUB_RE = re.compile(r"^cpub_[0-9a-f]{16}$")


def _community_pub_id(pid: str) -> str:
    if not _COMMUNITY_PUB_RE.match(pid or ""):
        raise HTTPException(status_code=404, detail="publication not found")
    return pid


class CommunityPublishRequest(CommunityVersionedRequest):
    confirm: bool = False


class CommunityAuthorizeRequest(CommunityVersionedRequest):
    """Stage 3.32F: publication authorization (separate from approval). ``publish_at`` queues it for a time."""
    confirm: bool = False
    publish_at: Optional[str] = Field(default=None, max_length=40)


class CommunityCancelPublicationRequest(_CommunityBody):
    reason: str = Field(default="", max_length=300)


class CommunityPublishingEnableRequest(_CommunityBody):
    enabled: bool
    confirm: bool = False


class CommunityResolveRequest(_CommunityBody):
    outcome: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=5, max_length=500)
    post_id: Optional[str] = Field(default=None, max_length=60)


class CommunityManualPostedRequest(_CommunityBody):
    post_url: Optional[str] = Field(default=None, max_length=300)


@app.get("/api/community/publishing")
async def api_community_publishing(_: None = Depends(require_local_control)):
    return await _community_call(lambda: _publishing().status())


@app.post("/api/community/publishing/capability-check")
async def api_community_capability_check(req: _CommunityBody, _: None = Depends(require_local_control),
                                         __: None = Depends(require_autonomy_write)):
    """Read-only YouTube probe (channels.list + posts.list, 1 quota unit each). Never uploads or posts."""
    return await _community_call(lambda: _publishing().check_capability(req.operator))


@app.post("/api/community/publishing/enabled")
async def api_community_publishing_enabled(req: CommunityPublishingEnableRequest, _: None = Depends(require_local_control),
                                           __: None = Depends(require_autonomy_write)):
    return await _community_call(lambda: _publishing().set_enabled(req.operator, req.enabled, req.confirm))


@app.post("/api/community/drafts/{draft_id}/publish")
async def api_community_publish(draft_id: str, req: CommunityPublishRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _publishing().publish(did, req.operator, req.version, req.confirm))


@app.post("/api/community/drafts/{draft_id}/authorize-publication")
async def api_community_authorize(draft_id: str, req: CommunityAuthorizeRequest, _: None = Depends(require_local_control),
                                  __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _publishing().authorize(did, req.operator, req.version, req.confirm,
                                                                 req.publish_at))


@app.post("/api/community/publications/{publication_id}/cancel")
async def api_community_cancel_publication(publication_id: str, req: CommunityCancelPublicationRequest,
                                           _: None = Depends(require_local_control),
                                           __: None = Depends(require_autonomy_write)):
    pid = _community_pub_id(publication_id)
    return await _community_call(lambda: _publishing().cancel_publication(pid, req.operator, req.reason))


@app.post("/api/community/drafts/{draft_id}/handoff")
async def api_community_handoff(draft_id: str, req: _CommunityBody, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    did = _community_draft_id(draft_id)
    return await _community_call(lambda: _publishing().prepare_handoff(did, req.operator))


@app.post("/api/community/publications/{publication_id}/reconcile")
async def api_community_reconcile(publication_id: str, req: _CommunityBody, _: None = Depends(require_local_control),
                                  __: None = Depends(require_autonomy_write)):
    pid = _community_pub_id(publication_id)
    return await _community_call(lambda: _publishing().reconcile(pid, req.operator))


@app.post("/api/community/publications/{publication_id}/resolve")
async def api_community_resolve(publication_id: str, req: CommunityResolveRequest, _: None = Depends(require_local_control),
                                __: None = Depends(require_autonomy_write)):
    pid = _community_pub_id(publication_id)
    return await _community_call(lambda: _publishing().resolve_uncertain(pid, req.operator, req.outcome, req.reason, req.post_id))


@app.post("/api/community/publications/{publication_id}/manual-posted")
async def api_community_manual_posted(publication_id: str, req: CommunityManualPostedRequest,
                                      _: None = Depends(require_local_control), __: None = Depends(require_autonomy_write)):
    pid = _community_pub_id(publication_id)
    return await _community_call(lambda: _publishing().report_manual_post(pid, req.operator, req.post_url))


@app.post("/api/community/publications/{publication_id}/cancel-handoff")
async def api_community_cancel_handoff(publication_id: str, req: _CommunityBody, _: None = Depends(require_local_control),
                                       __: None = Depends(require_autonomy_write)):
    pid = _community_pub_id(publication_id)
    return await _community_call(lambda: _publishing().cancel_handoff(pid, req.operator))


@app.get("/api/youtube/quota")
async def api_youtube_quota(_: None = Depends(require_local_control)):
    """Per-bucket YouTube Data API usage (Stage 3.30): local estimate from sent requests, never Google-reported."""
    from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
    def _build():
        q = youtube_publishing_engine.quota
        out = q.summary()
        out["recent_requests"] = q.recent(25)
        return out
    return await asyncio.to_thread(_build)


@app.get("/api/youtube/analytics/status")
async def api_youtube_analytics_status(_: None = Depends(require_local_control)):
    """Analytics authorization state and latest stored metrics (Stage 3.30). Never returns tokens."""
    from backend.app.services.youtube_analytics import AnalyticsCollector
    def _build():
        col = AnalyticsCollector()
        st = col.status()
        st.pop("granted_scopes", None)
        return {**st, "videos": col.store.latest(25)}
    return await asyncio.to_thread(_build)


@app.post("/api/youtube/analytics/authorize")
async def api_youtube_analytics_authorize(payload: dict = Body(default={}), _: None = Depends(require_local_control)):
    """Operator-started incremental consent (existing scopes + yt-analytics.readonly). Opens Google's consent page."""
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Analytics authorization requires confirm=true.")
    from backend.app.services.youtube_analytics import authorize_analytics
    from backend.app.services.youtube_publishing_engine import invalidate_auth_status_cache
    res = await asyncio.to_thread(authorize_analytics)
    invalidate_auth_status_cache()
    res.pop("granted_scopes", None)
    return res


@app.post("/api/youtube/analytics/collect")
async def api_youtube_analytics_collect(_: None = Depends(require_local_control)):
    from backend.app.services.youtube_analytics import AnalyticsCollector
    return await asyncio.to_thread(lambda: AnalyticsCollector().collect())


@app.get("/api/junk-cleaner/status")
async def api_junk_cleaner_status(_: None = Depends(require_local_control)):
    """Junk Cleaner state from persisted run records (Stage 3.30)."""
    from backend.app.services import junk_cleaner
    return await asyncio.to_thread(junk_cleaner.status)


@app.post("/api/junk-cleaner/dry-run")
async def api_junk_cleaner_dry_run(_: None = Depends(require_local_control)):
    """Runs an audit that reports what the policy would allow; never moves or deletes files."""
    from backend.app.services.junk_cleaner import JunkCleaner
    rec = await asyncio.to_thread(lambda: JunkCleaner().run(trigger="dashboard_manual", live=False))
    return {k: v for k, v in rec.items() if k != "eligible_files"} | {"eligible_files_sample": rec.get("eligible_files", [])[:50]}


@app.get("/api/providers/health")
async def api_provider_health(_: None = Depends(require_local_control)):
    """Provider inventory and health; never returns credential values and never calls a provider (Stage 3.29/3.30)."""
    from backend.app.services import provider_audit
    return await asyncio.to_thread(provider_audit.summary)


@app.post("/api/providers/probe")
async def api_provider_probe(_: None = Depends(require_local_control)):
    """On-demand, throttled (10 min per provider), read-only health checks. The YouTube Data API is not probed."""
    from backend.app.services import provider_audit
    await asyncio.to_thread(provider_audit.probe)
    return await asyncio.to_thread(provider_audit.summary)


@app.get("/api/production/recovery-policy")
async def api_recovery_policy(_: None = Depends(require_local_control)):
    from backend.app.services import production_monitor_service
    return production_monitor_service._policy_summary()


@app.post("/api/production/recovery-policy")
async def api_recovery_policy_save(req: RecoveryPolicyRequest, _: None = Depends(require_local_control)):
    """Validated change of the retry/recovery policy (config/recovery_policy.json). Requires confirm."""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to change the recovery policy.")
    from backend.app.scheduler.recovery_settings import save_settings
    from backend.app.services import production_monitor_service
    try:
        save_settings(req.settings)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return production_monitor_service._policy_summary()


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


# ---------------------------------------------------------------------------------------------------------------
# Named Production Schedule Profiles (a saved-profile library over the existing schedule system). Reads are
# localhost-only; every change also needs a same-origin request. Loading goes through schedule_service.activate.
# ---------------------------------------------------------------------------------------------------------------
class ScheduleProfileSaveRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)


class ScheduleProfileNameRequest(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)


class ScheduleProfileDeleteRequest(BaseModel):
    model_config = {"extra": "forbid"}
    confirm: bool = False


class ScheduleProfileLoadRequest(BaseModel):
    model_config = {"extra": "forbid"}
    confirm: bool = False
    expected_checksum: str = Field(min_length=64, max_length=64)


async def _profile_call(fn, *args, **kwargs):
    from backend.app.services import schedule_profile_library as spl
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except spl.ActivationUnsafe as exc:
        raise HTTPException(status_code=409, detail={"message": "Schedule activation refused because it is unsafe right now.",
                                                     "reasons": exc.reasons})
    except spl.ProfileError as exc:
        raise HTTPException(status_code=exc.code, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/schedule/profiles")
async def api_schedule_profiles(_: None = Depends(require_local_control)):
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.list_profiles)


@app.get("/api/schedule/profiles/{profile_id}")
async def api_schedule_profile(profile_id: str, _: None = Depends(require_local_control)):
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.get_profile, profile_id)


@app.get("/api/schedule/profiles/{profile_id}/preview")
async def api_schedule_profile_preview(profile_id: str, _: None = Depends(require_local_control)):
    """Validation, differences from the active schedule, upcoming cycles and safety blockers. Changes nothing."""
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.preview, profile_id)


@app.post("/api/schedule/profiles")
async def api_schedule_profile_save(req: ScheduleProfileSaveRequest, _: None = Depends(require_local_control),
                                    __: None = Depends(require_same_origin)):
    """Save the ACTIVE production schedule as a named profile. The active schedule is not changed."""
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.save_current, req.name, req.description)


@app.post("/api/schedule/profiles/{profile_id}/rename")
async def api_schedule_profile_rename(profile_id: str, req: ScheduleProfileNameRequest, _: None = Depends(require_local_control),
                                      __: None = Depends(require_same_origin)):
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.rename, profile_id, req.name)


@app.post("/api/schedule/profiles/{profile_id}/duplicate")
async def api_schedule_profile_duplicate(profile_id: str, req: ScheduleProfileNameRequest, _: None = Depends(require_local_control),
                                         __: None = Depends(require_same_origin)):
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.duplicate, profile_id, req.name, req.description)


@app.post("/api/schedule/profiles/{profile_id}/delete")
async def api_schedule_profile_delete(profile_id: str, req: ScheduleProfileDeleteRequest, _: None = Depends(require_local_control),
                                      __: None = Depends(require_same_origin)):
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.delete, profile_id, req.confirm)


@app.post("/api/schedule/profiles/{profile_id}/load")
async def api_schedule_profile_load(profile_id: str, req: ScheduleProfileLoadRequest, _: None = Depends(require_local_control),
                                    __: None = Depends(require_same_origin)):
    """Activate a saved profile via the existing activation path. Refused while a production job is active or
    when it would remove / shorten the open recovery window. The YouTube publication schedule is not touched."""
    from backend.app.services import schedule_profile_library as spl
    return await _profile_call(spl.load, profile_id, req.confirm, req.expected_checksum)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects root browser access directly to the Production Health Dashboard."""
    return RedirectResponse(url="/dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
