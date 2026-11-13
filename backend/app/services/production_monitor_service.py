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


POLICY_LABELS = {
    "unlimited_until_recovery_deadline": "Unlimited until recovery deadline",
    "bounded": "Bounded (fixed attempt count)",
}


def _parse_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def retry_view(job, cs: dict, now_utc: datetime, settings=None) -> dict[str, Any]:
    """Retry-policy state of one job, for the Production Monitor."""
    from backend.app.scheduler import retry_policy as rp
    from backend.app.scheduler.recovery_settings import POLICY_UNLIMITED, load_settings

    s = settings or load_settings()
    policy = cs.get("retry_policy") or s.retry_policy
    unlimited = policy == POLICY_UNLIMITED
    hist = [h for h in (cs.get("attempt_history") or []) if h.get("kind") in ("production", "publish")]
    failures = int(job.retry_count or 0)
    running = job.status in ("RUNNING", "RETRYING", "QUEUED")
    deadline = rp.recovery_deadline_utc(job)
    remaining = int((deadline - now_utc).total_seconds()) if deadline else None
    next_at = _parse_utc(cs.get("next_retry_at_utc"))
    last = hist[-1] if hist else None
    backoff = None
    if last and last.get("next_retry_at_utc") and last.get("failed_at_utc"):
        a, b = _parse_utc(last["failed_at_utc"]), _parse_utc(last["next_retry_at_utc"])
        if a and b:
            backoff = max(0, int((b - a).total_seconds()))
    waiting_for_change = bool(cs.get("requires_change")) and not cs.get("change_acknowledged")
    status = job.status
    if status in ("COMPLETED",):
        auto = "stopped: production succeeded"
    elif status == rp.STATUS_RECOVERY_EXPIRED:
        auto = "stopped: recovery deadline reached"
    elif running:
        auto = "attempt in progress"
    elif status == "HANDOFF_PENDING":
        auto = "publishing retry pending (no re-render, no re-upload)"
    elif status == rp.STATUS_RETRY_SCHEDULED and waiting_for_change:
        auto = "waiting for a code or configuration change"
    elif status == rp.STATUS_RETRY_SCHEDULED:
        auto = "scheduled"
    elif status in ("FAILED", "BLOCKED_QC"):
        auto = "stopped: " + ("recovery deadline reached" if remaining is not None and remaining <= 0
                              else "failure needs operator action" if cs.get("retry_terminal") else "failed")
    else:
        auto = "idle"
    operator_needed = (status in ("FAILED", "BLOCKED_QC") and (remaining is None or remaining > 0)) or (
        status == rp.STATUS_RETRY_SCHEDULED and waiting_for_change)
    return {
        "policy": policy,
        "policy_label": POLICY_LABELS.get(policy, policy),
        "max_attempts_label": "No fixed limit (until recovery deadline)" if unlimited else str(int(job.max_retries or 3)),
        "failed_attempts": failures,
        "current_attempt": failures + 1 if running else (failures if failures else None),
        "total_attempts": failures + (1 if running else 0),
        "next_retry_at_ist": _ist(next_at) if next_at and status == rp.STATUS_RETRY_SCHEDULED else None,
        "current_backoff_seconds": backoff,
        "recovery_deadline_ist": _ist(deadline) if deadline else None,
        "remaining_seconds": remaining,
        "last_failure_at_ist": _ist(_parse_utc(cs.get("last_failure_at_utc"))),
        "last_failure": (cs.get("last_error_message") or "")[:400] or None,
        "classification": cs.get("error_classification"),
        "requires_change": bool(cs.get("requires_change")),
        "waiting_for_change": waiting_for_change,
        "auto_retry_status": auto,
        "operator_action_needed": bool(operator_needed),
        "operator_retries_used": int(cs.get("operator_retries", 0)),
        "operator_retry_allowance": int(s.operator_retry_allowance) if unlimited else 1,
        "history": (cs.get("attempt_history") or [])[-10:],
    }


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
        "retry": retry_view(job, cs, now_utc),
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
        last_failed = real.filter(JobModel.status == "FAILED").order_by(JobModel.updated_at.desc()).first()
        last_failed_id = last_failed.id if last_failed else None
    retry_offer = None
    waiting = next((a for a in active if a["retry"]["waiting_for_change"]), None)
    if waiting:
        try:
            from backend.app.services import operator_retry_service
            retry_offer = operator_retry_service.evaluate(waiting["job_id"])
        except Exception:
            retry_offer = None
    elif not active and last_failed_id:
        try:
            from backend.app.services import operator_retry_service
            retry_offer = operator_retry_service.evaluate(last_failed_id)
        except Exception:
            retry_offer = None
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
        "retry_offer": retry_offer,
        "recovery_policy": _policy_summary(),
    }


def _policy_summary() -> dict[str, Any]:
    from backend.app.scheduler.recovery_settings import load_settings
    s = load_settings()
    return {**s.to_dict(), "policy_label": POLICY_LABELS.get(s.retry_policy, s.retry_policy)}
