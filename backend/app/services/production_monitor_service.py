"""
Production Monitor view: the active job, its stage/progress, last activity, current
error and recovery state, plus the last successful production. Read-only; computed
from persistent_jobs and the publishing ledger on every call.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

IST_TZ = ZoneInfo("Asia/Kolkata")
ACTIVE = ("QUEUED", "RUNNING", "RETRYING", "RETRY_SCHEDULED", "HANDOFF_PENDING", "WAITING_FOR_INTERNET")
STATUS_LABELS = {
    "QUEUED": "Queued", "RUNNING": "Producing", "RETRYING": "Retrying", "RETRY_SCHEDULED": "Waiting to retry",
    "HANDOFF_PENDING": "Publishing step pending", "WAITING_FOR_INTERNET": "Waiting for internet",
    "COMPLETED": "Completed", "FAILED": "Failed", "BLOCKED_QC": "Blocked by QC",
    "RECOVERY_WINDOW_EXPIRED": "Recovery window expired",
}


def _ist(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")


def _job_view(job, now_utc: datetime) -> dict[str, Any]:
    try:
        cp = json.loads(job.checkpoint_data_json or "{}")
    except ValueError:
        cp = {}
    cs = cp.get("cycle_state") or {}
    hb = job.heartbeat_at
    since = int((now_utc - hb).total_seconds()) if hb else None
    publishing = cs.get("publishing") or {}
    return {
        "job_id": job.id,
        "cycle_id": "_".join(job.id.split("_")[:2]) if "_" in job.id else job.id,
        "topic": job.topic_title or cp.get("topic_title"),
        "status": job.status,
        "status_label": STATUS_LABELS.get(job.status, job.status),
        "stage": job.current_stage,
        "progress_pct": round(float(job.progress_pct or 0.0), 1),
        "last_activity_ist": _ist(hb),
        "seconds_since_activity": since,
        "worker_id": cs.get("worker_id"),
        "error": job.error_details,
        "recovery": {
            "classification": cs.get("error_classification"),
            "production_attempts": int(job.retry_count or 0),
            "max_production_attempts": int(job.max_retries or 3),
            "publish_attempts": int(cs.get("publish_attempts", 0)),
            "next_retry_at_ist": _ist(datetime.fromisoformat(cs["next_retry_at_utc"].replace("Z", "+00:00")))
            if cs.get("next_retry_at_utc") else None,
            "terminal": bool(cs.get("retry_terminal")),
            "preserved_artifacts": sorted((cs.get("preserved_artifacts") or {}).keys()),
            "recovery_deadline_ist": cs.get("recovery_expiration_at"),
        },
        "target_publication_ist": cs.get("target_publication_at_ist"),
        "publishing_state": publishing.get("state"),
        "completed_at_ist": _ist(job.completed_at),
        "started_at_ist": _ist(job.actual_start_at),
    }


def monitor_view(now_utc: Optional[datetime] = None) -> dict[str, Any]:
    from backend.app.database.db import JobModel, SessionLocal

    now_utc = now_utc or datetime.utcnow()
    with SessionLocal() as db:
        real = db.query(JobModel).filter(~JobModel.id.like("test_%"), ~JobModel.id.like("mock_%"))
        active = [_job_view(j, now_utc) for j in real.filter(JobModel.status.in_(ACTIVE))
                  .order_by(JobModel.updated_at.desc()).limit(5).all()]
        recent = [_job_view(j, now_utc) for j in real.order_by(JobModel.updated_at.desc()).limit(6).all()]
        last_ok = real.filter(JobModel.status == "COMPLETED").order_by(JobModel.completed_at.desc()).first()
        last_success = _job_view(last_ok, now_utc) if last_ok else None
    queue_count = None
    try:
        from backend.app.services.youtube_publishing_engine import youtube_publishing_engine
        rows = youtube_publishing_engine.db.list_publications(limit=500)
        queue_count = sum(1 for r in rows if r.get("upload_status") not in ("uploaded_public", "published", "cancelled"))
    except Exception:
        pass
    return {
        "now_ist": _ist(now_utc),
        "active_job": active[0] if active else None,
        "active_jobs": active,
        "recent_jobs": recent,
        "last_success": last_success,
        "publishing_queue_count": queue_count,
    }
