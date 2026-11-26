"""Read-only lookups that feed the email templates with persisted facts.

Every function takes an open SQLAlchemy session, never writes, and degrades to ``None``/empty on any
error (a report must never break production or invent a value).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.app.services.email_templates import design as D
from backend.app.services.email_templates.production import (
    RecoveryInfo, StageMark, YouTubeState, YT_NOT_QUEUED, YT_PUBLISHED, YT_QUEUED, YT_SCHEDULED,
    YT_UNKNOWN, YT_UPLOAD_FAILED, YT_UPLOADED_PRIVATE, YT_UPLOADED,
)

logger = logging.getLogger(__name__)

_TIMELINE_EVENTS = ("PIPELINE_STAGE_CHANGED", "VIDEO_GENERATED", "QC_PASSED", "YOUTUBE_UPLOAD_CONFIRMED",
                    "PUBLICATION_SCHEDULE_CONFIRMED", "PRODUCTION_CYCLE_COMPLETED")
_EVENT_LABEL = {"VIDEO_GENERATED": "Video rendered", "QC_PASSED": "Quality gate passed",
                "YOUTUBE_UPLOAD_CONFIRMED": "Uploaded to YouTube", "PUBLICATION_SCHEDULE_CONFIRMED": "Publication scheduled",
                "PRODUCTION_CYCLE_COMPLETED": "Cycle completed"}


def checkpoint(job: Any) -> Dict[str, Any]:
    try:
        return json.loads(getattr(job, "checkpoint_data_json", None) or "{}") or {}
    except (TypeError, ValueError):
        return {}


def youtube_state(db, job: Any = None, cp: Optional[Dict[str, Any]] = None,
                  publishing_status: Optional[str] = None) -> YouTubeState:
    """Upload/publication state from the publishing ledger (authoritative), falling back to the job row."""
    cp = cp if cp is not None else checkpoint(job)
    row = None
    try:
        ledger_id = cp.get("queue_ledger_id")
        if ledger_id is not None:
            row = db.execute(text("SELECT upload_status, visibility, youtube_video_id, scheduled_timestamp "
                                  "FROM publishing_ledger WHERE id=:i"), {"i": int(ledger_id)}).fetchone()
        if row is None and cp.get("video_path"):
            row = db.execute(text("SELECT upload_status, visibility, youtube_video_id, scheduled_timestamp "
                                  "FROM publishing_ledger WHERE video_path=:p ORDER BY id DESC LIMIT 1"),
                             {"p": cp["video_path"]}).fetchone()
    except Exception as exc:  # table missing in an isolated database, etc.
        logger.debug("publishing_ledger lookup skipped: %s", type(exc).__name__)
        row = None
    job_vid = getattr(job, "youtube_video_id", None) if job is not None else None
    if row is not None:
        status, vis, vid, sched = (str(row[0] or "").lower(), str(row[1] or "").lower(), row[2] or job_vid, row[3])
        if "fail" in status or "error" in status:
            return YouTubeState(YT_UPLOAD_FAILED, vid, vis or None, None, "Publishing ledger status: %s" % status)
        if status in ("pending_queue", "queued", "pending") and not vid:
            return YouTubeState(YT_QUEUED)
        # The publishing reconciler promotes a row to 'uploaded_public' once YouTube confirms the video is live
        # (youtube_publishing_engine.verify_scheduled_publications) but leaves the visibility column as uploaded.
        if vid and (vis == "public" or status in ("uploaded_public", "published")):
            return YouTubeState(YT_PUBLISHED, vid, "public", sched)
        if vid and sched and ("schedul" in status or status == "publication_scheduled"):
            return YouTubeState(YT_SCHEDULED, vid, vis or "private", sched)
        if vid and vis in ("private", "unlisted"):
            return YouTubeState(YT_UPLOADED_PRIVATE, vid, vis)
        if vid:
            return YouTubeState(YT_UPLOADED, vid, vis or None)
        return YouTubeState(YT_UNKNOWN, detail="Publishing ledger status: %s" % (status or "empty"))
    if job_vid:
        return YouTubeState(YT_UPLOADED, job_vid)
    ps = str(publishing_status or "")
    if ps.startswith("suppressed"):
        return YouTubeState(YT_NOT_QUEUED)
    return YouTubeState(YT_UNKNOWN)


def stage_timeline(db, job_id: str) -> List[StageMark]:
    """Stage markers of the last attempt (after the last dispatch)."""
    try:
        rows = db.execute(text("SELECT timestamp_utc, event_type, details_json FROM production_lifecycle_events "
                               "WHERE job_id=:j ORDER BY timestamp_utc"), {"j": job_id}).fetchall()
    except Exception:
        return []
    start = 0
    for i, r in enumerate(rows):
        if r[1] == "CYCLE_DISPATCH_STARTED":
            start = i
    marks: List[StageMark] = []
    for ts, ev, det in rows[start:]:
        if ev not in _TIMELINE_EVENTS:
            continue
        label = _EVENT_LABEL.get(ev)
        if ev == "PIPELINE_STAGE_CHANGED":
            try:
                label = (json.loads(det or "{}") or {}).get("stage")
            except ValueError:
                label = None
            if label in (None, "PUBLISHING_QUEUE_HANDOFF", "COMPLETED"):
                continue
        marks.append(StageMark(label, ts))
    return marks


def recovery_info(db, job: Any, cp: Dict[str, Any], *, succeeded: bool,
                  decision: Optional[Dict[str, Any]] = None) -> RecoveryInfo:
    cs = cp.get("cycle_state") or {}
    hist = [h for h in cs.get("attempt_history") or [] if h.get("kind") in (None, "production")]
    retry_count = getattr(job, "retry_count", None) if job is not None else None
    attempts = None
    if retry_count is not None:
        attempts = int(retry_count) + (1 if succeeded else 0)
        if not succeeded and attempts == 0:
            attempts = 1
    if decision and decision.get("attempts") is not None and not succeeded:
        attempts = int(decision["attempts"])
    max_attempts = None
    if decision and decision.get("max_retries"):
        max_attempts = int(decision["max_retries"])
    first_failure = None
    if hist:
        first_failure = D.clip(hist[0].get("message"), 240)
    elif succeeded and attempts and attempts > 1:
        try:
            r = db.execute(text("SELECT error_details FROM production_lifecycle_events WHERE job_id=:j "
                                "AND event_type='CYCLE_DISPATCH_FAILED' ORDER BY timestamp_utc LIMIT 1"),
                           {"j": getattr(job, "id", "")}).fetchone()
            first_failure = D.clip(r[0], 240) if r and r[0] else None
        except Exception:
            first_failure = None
    deadline = cs.get("recovery_expiration_at") or (decision or {}).get("recovery_deadline_utc")
    status = str(getattr(job, "status", "") or "").upper()
    closed = status == "RECOVERY_WINDOW_EXPIRED"
    # The scheduler also closes a cycle BEFORE the deadline when another attempt cannot fit
    # ("no time for another attempt before the recovery deadline"). Only call it expired when it really was.
    closed_at = D.to_ist(getattr(job, "updated_at", None)) or datetime.now(timezone.utc).astimezone(D.IST)
    dl = D.to_ist(deadline)
    expired = closed and (dl is None or closed_at >= dl)
    no_time_left = closed and not expired
    retry_scheduled = None
    next_at = None
    if decision is not None:
        retry_scheduled = not bool(decision.get("terminal", True))
        if retry_scheduled and decision.get("retry_in_seconds"):
            next_at = datetime.now(timezone.utc) + timedelta(seconds=int(decision["retry_in_seconds"]))
    elif not succeeded:
        retry_scheduled = False if (closed or cs.get("retry_terminal")) else None
        next_at = cs.get("next_retry_at_utc") if retry_scheduled else None
    return RecoveryInfo(attempts=attempts, max_attempts=max_attempts, first_failure=first_failure,
              recovered_at=getattr(job, "completed_at", None) if succeeded else None,
              deadline=deadline, window_expired=expired, no_time_left=no_time_left,
              retry_scheduled=retry_scheduled, next_attempt_at=next_at)


def relative_path(path: Optional[str]) -> Optional[str]:
    """Project-relative path for display (never a raw absolute path outside the project)."""
    if not path:
        return None
    try:
        from backend.app.core.paths import PROJECT_ROOT_STR
        rel = os.path.relpath(path, PROJECT_ROOT_STR)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except (ValueError, ImportError):
        pass
    return os.path.basename(path)
