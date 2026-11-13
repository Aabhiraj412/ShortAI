"""
Guarded one-time operator retry for a FAILED production cycle.

A FAILED job is terminal for the scheduler (retries exhausted or a permanent error).
After a fix is deployed, the operator may grant ONE extra attempt, but only while it
is still safe:

* the failure was temporary or a render failure (never PERMANENT, QC-blocked or
  post-render: a finished video is never regenerated);
* nothing was queued or uploaded for the job;
* it is the current occurrence of its cycle and its recovery window has at least
  MIN_WINDOW_LEFT left;
* its target publication time is at least MIN_TARGET_LEAD away (production time
  plus YouTube's 15-minute publishAt lead);
* no other production is running or pending, and the next cycle starts at least
  MIN_NEXT_CYCLE_GAP later;
* the job has not had an operator retry before.

Granting the retry is one conditional UPDATE (status must still be FAILED), so a
second request cannot double-schedule. The scheduler daemon then dispatches the job
through its normal RETRY_SCHEDULED path within one recovery-loop pass (~45 s).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import update

from backend.app.database.db import JobModel, SessionLocal
from backend.app.scheduler import retry_policy as rp

IST_TZ = ZoneInfo("Asia/Kolkata")
MIN_WINDOW_LEFT = timedelta(minutes=20)
MIN_TARGET_LEAD = timedelta(minutes=45)
MIN_NEXT_CYCLE_GAP = timedelta(minutes=45)
RETRYABLE_CLASSIFICATIONS = frozenset({rp.TRANSIENT, rp.RENDER_FAILURE})
BUSY_STATUSES = ("RUNNING", "RETRYING", "QUEUED", "RETRY_SCHEDULED", "HANDOFF_PENDING",
                 "WAITING_FOR_INTERNET", "INTERRUPTED", "PAUSED")


class RetryRefused(Exception):
    """The retry is not allowed; `.reasons` lists every failed check."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _cycle_for(job_id: str):
    from backend.app.scheduler.schedule_profiles import get_active_profile
    return next((c for c in get_active_profile().cycles if job_id.startswith(c.cycle_id + "_")), None)


def _next_cycle_start(now: datetime) -> Optional[datetime]:
    from backend.app.scheduler.schedule_profiles import get_active_profile
    starts = []
    for c in get_active_profile().cycles:
        for day in (0, 1):
            s = (now + timedelta(days=day)).replace(hour=c.start_hour_ist, minute=c.start_min_ist,
                                                     second=0, microsecond=0)
            if s > now and s.weekday() in c.active_weekdays:
                starts.append(s)
    return min(starts) if starts else None


def evaluate(job_id: str, now_ist: Optional[datetime] = None) -> dict[str, Any]:
    """Read-only: whether an operator retry would be accepted now, and why not."""
    now = (now_ist or datetime.now(IST_TZ)).astimezone(IST_TZ)
    reasons: list[str] = []
    info: dict[str, Any] = {"job_id": job_id, "now_ist": now.strftime("%Y-%m-%d %H:%M:%S IST")}
    with SessionLocal() as s:
        job = s.get(JobModel, job_id)
        if job is None:
            return {**info, "eligible": False, "reasons": ["Job not found."]}
        cp = json.loads(job.checkpoint_data_json or "{}")
        cs = cp.get("cycle_state") or {}
        busy = [j.id for j in s.query(JobModel).filter(JobModel.status.in_(BUSY_STATUSES), JobModel.id != job_id).all()
                if not (j.id or "").startswith(("test_", "mock_"))]

    if job.status != "FAILED":
        reasons.append(f"Job status is {job.status}; only FAILED jobs can be retried.")
    classification = cs.get("error_classification")
    if classification not in RETRYABLE_CLASSIFICATIONS:
        reasons.append(f"Failure classification {classification or 'UNKNOWN'} is not retryable "
                       f"(allowed: {', '.join(sorted(RETRYABLE_CLASSIFICATIONS))}).")
    if cp.get("queue_handoff") or cp.get("queue_ledger_id") or job.youtube_video_id:
        reasons.append("A video was already produced, queued or uploaded for this job.")
    if int(cs.get("operator_retries", 0)) >= 1:
        reasons.append("This job already had its one operator retry.")
    if busy:
        reasons.append(f"Another production is active or pending: {', '.join(busy)}.")

    cycle = _cycle_for(job_id)
    if cycle is None:
        reasons.append("Job does not belong to a cycle in the active schedule.")
    else:
        start = cycle.occurrence_start(now)
        if cycle.format_job_id(now) != job_id:
            reasons.append("Job is not the current occurrence of its cycle.")
        window_end = start + timedelta(hours=cycle.recovery_window_hours)
        target = cycle.target_publication_at(start)
        info.update(recovery_ends_ist=window_end.strftime("%Y-%m-%d %H:%M IST"),
                    target_publication_ist=target.strftime("%Y-%m-%d %H:%M IST") if target else None)
        if window_end - now < MIN_WINDOW_LEFT:
            reasons.append(f"Less than {int(MIN_WINDOW_LEFT.total_seconds() // 60)} min of the recovery window "
                           f"remains (ends {window_end.strftime('%H:%M IST')}).")
        if target is None:
            reasons.append("Cycle has no target publication time.")
        elif target - now < MIN_TARGET_LEAD:
            reasons.append(f"Target publication {target.strftime('%H:%M IST')} is less than "
                           f"{int(MIN_TARGET_LEAD.total_seconds() // 60)} min away.")
        nxt = _next_cycle_start(now)
        info["next_cycle_start_ist"] = nxt.strftime("%Y-%m-%d %H:%M IST") if nxt else None
        if nxt is not None and nxt - now < MIN_NEXT_CYCLE_GAP:
            reasons.append(f"Next cycle starts at {nxt.strftime('%H:%M IST')}, less than "
                           f"{int(MIN_NEXT_CYCLE_GAP.total_seconds() // 60)} min away.")
    return {**info, "eligible": not reasons, "reasons": reasons, "status": job.status,
            "attempts": int(job.retry_count or 0), "max_retries": int(job.max_retries or 0),
            "classification": classification}


def request_retry(job_id: str, confirm: bool, now_ist: Optional[datetime] = None) -> dict[str, Any]:
    """Grant one extra attempt. Raises PermissionError without confirm, RetryRefused when unsafe."""
    if confirm is not True:
        raise PermissionError("An operator retry requires explicit confirmation.")
    check = evaluate(job_id, now_ist)
    if not check["eligible"]:
        raise RetryRefused(check["reasons"])

    now_utc = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    with SessionLocal() as s:
        job = s.get(JobModel, job_id)
        cp = json.loads(job.checkpoint_data_json or "{}")
        cs = cp.setdefault("cycle_state", {})
        cs["operator_retries"] = int(cs.get("operator_retries", 0)) + 1
        cs["operator_retry_requested_at_utc"] = now_utc.isoformat() + "Z"
        cs["next_retry_at_utc"] = now_utc.isoformat() + "Z"
        cs["retry_terminal"] = False
        new_max = int(job.retry_count or 0) + 1
        # Conditional: only the request that still sees FAILED wins.
        res = s.execute(
            update(JobModel)
            .where(JobModel.id == job_id, JobModel.status == "FAILED")
            .values(status=rp.STATUS_RETRY_SCHEDULED, max_retries=new_max,
                    recovery_reason="OPERATOR_RETRY", checkpoint_data_json=json.dumps(cp))
        )
        if res.rowcount != 1:
            s.rollback()
            raise RetryRefused(["Job is no longer FAILED (another retry request won)."])
        s.commit()

    from backend.app.services.control_center_service import record_change
    from backend.app.services.lifecycle_events import record_lifecycle_event
    record_lifecycle_event("OPERATOR_RETRY_REQUESTED", rp.STATUS_RETRY_SCHEDULED,
                           cycle_id=job_id.rsplit("_", 2)[0], job_id=job_id,
                           details={"attempt": new_max, "previous_attempts": new_max - 1,
                                    "target_publication_ist": check.get("target_publication_ist"),
                                    "recovery_ends_ist": check.get("recovery_ends_ist")})
    record_change("operator_retry", {"job_id": job_id, "status": "FAILED"},
                  {"job_id": job_id, "status": rp.STATUS_RETRY_SCHEDULED, "max_retries": new_max})
    return {"status": rp.STATUS_RETRY_SCHEDULED, "job_id": job_id, "attempt": new_max,
            "target_publication_ist": check.get("target_publication_ist"),
            "recovery_ends_ist": check.get("recovery_ends_ist"),
            "message": "Retry scheduled; the scheduler dispatches it within about a minute."}
