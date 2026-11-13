"""
Authoritative production schedule view for the dashboard, APIs and notifications.

One source of truth: the active schedule stored under control_center.schedule.active
(the same record the scheduler daemon loads). Everything here is computed from the
current clock on every call; nothing is cached.

A cycle is: id, name, production start (HH:MM), recovery duration (minutes), target
YouTube publication time (HH:MM, optional), enabled flag, weekdays. The target time is
only used to schedule the upload's publishAt; it never changes when production starts,
how long it may recover, or whether it may complete.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from backend.app.services import control_center_service as cc

IST_TZ = ZoneInfo("Asia/Kolkata")
CYCLE_FIELDS = ("cycle_id", "name", "start_time", "recovery_duration_minutes",
                "target_publish_time", "enabled", "active_weekdays")
PRESETS = {
    "FOUR_CYCLES_DAILY": "Four cycles: 00:00, 06:00, 12:00, 18:00 (publish 6 h after start)",
    "SINGLE_DAILY": "One daily cycle",
}


def _clean_cycle(c: dict[str, Any]) -> dict[str, Any]:
    out = {k: c.get(k) for k in CYCLE_FIELDS}
    out["enabled"] = bool(c.get("enabled", True))
    days = c.get("active_weekdays")
    # Missing means every day; an explicit empty list stays empty (and fails validation).
    out["active_weekdays"] = sorted(days) if isinstance(days, list) else [0, 1, 2, 3, 4, 5, 6]
    out["recovery_duration_minutes"] = int(c.get("recovery_duration_minutes") or 240)
    out["target_publish_time"] = (str(c.get("target_publish_time") or "").strip() or None)
    return out


def clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Only the simple fields; legacy publication-slot data is never carried forward."""
    return {
        "profile_name": str(spec.get("profile_name") or "CUSTOM").upper(),
        "timezone_name": spec.get("timezone_name") or "Asia/Kolkata",
        "schedule_version": int(spec.get("schedule_version", 1)),
        "cycles": [_clean_cycle(c) for c in (spec.get("cycles") or [])],
    }


def active_spec() -> Optional[dict[str, Any]]:
    raw = cc._read_setting(cc.ACTIVE_PROFILE_KEY)
    return json.loads(raw) if raw else None


def draft_spec() -> Optional[dict[str, Any]]:
    raw = cc._read_setting(cc.DRAFT_PROFILE_KEY)
    return json.loads(raw) if raw else None


def upcoming_cycles(now_ist: Optional[datetime] = None, count: int = 6) -> list[dict[str, Any]]:
    spec = active_spec()
    if not spec:
        return []
    return cc.compute_upcoming_production_cycles(clean_spec(spec), count=count, now_ist=now_ist)


def next_production_cycle(now_ist: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """The next cycle whose production start is still ahead of `now_ist`."""
    now = (now_ist or datetime.now(IST_TZ)).astimezone(IST_TZ)
    for item in upcoming_cycles(now, count=12):
        if not item.get("in_progress_window"):
            return item
    return None


def current_cycle_window(now_ist: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """The cycle whose recovery window contains `now_ist` (may have started yesterday)."""
    now = (now_ist or datetime.now(IST_TZ)).astimezone(IST_TZ)
    for item in upcoming_cycles(now, count=12):
        if item.get("in_progress_window"):
            return item
    return None


def _job_status_for(cycle_id: str, now: datetime) -> Optional[dict[str, Any]]:
    from backend.app.database.db import JobModel, SessionLocal
    from backend.app.scheduler.schedule_profiles import get_active_profile
    cycle = next((c for c in get_active_profile().cycles if c.cycle_id == cycle_id), None)
    if cycle is None:
        return None
    job_id = cycle.format_job_id(now)
    with SessionLocal() as db:
        job = db.query(JobModel).filter(JobModel.id == job_id).first()
        if not job:
            return None
        return {"job_id": job.id, "status": job.status, "stage": job.current_stage,
                "progress_pct": job.progress_pct}


def schedule_view(now_ist: Optional[datetime] = None) -> dict[str, Any]:
    now = (now_ist or datetime.now(IST_TZ)).astimezone(IST_TZ)
    spec = active_spec()
    upcoming = cc.compute_upcoming_production_cycles(clean_spec(spec), count=8, now_ist=now) if spec else []
    nxt = next((u for u in upcoming if not u.get("in_progress_window")), None)
    current = next((u for u in upcoming if u.get("in_progress_window")), None)
    rows = []
    for c in (clean_spec(spec)["cycles"] if spec else []):
        status = "Disabled" if not c["enabled"] else "Scheduled"
        job = None
        if c["enabled"]:
            if current and current["cycle_id"] == c["cycle_id"]:
                status = "In recovery window"
                try:
                    job = _job_status_for(c["cycle_id"], now)
                except Exception:
                    job = None
                if job:
                    status = {"RUNNING": "Producing", "COMPLETED": "Completed", "FAILED": "Failed",
                              "RETRY_SCHEDULED": "Retry scheduled", "HANDOFF_PENDING": "Publishing pending",
                              "RECOVERY_WINDOW_EXPIRED": "Window expired"}.get(job["status"], job["status"].title())
            elif nxt and nxt["cycle_id"] == c["cycle_id"]:
                status = "Next"
        rows.append({**c, "status": status, "job": job,
                     "next_start_ist": next((u["production_start_ist"] for u in upcoming if u["cycle_id"] == c["cycle_id"]), None)})
    sync = cc.get_scheduler_sync_state(str((spec or {}).get("revision") or ""), int((spec or {}).get("schedule_version", 1)))
    draft = draft_spec()
    return {
        "timezone": "Asia/Kolkata",
        "now_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "has_active_schedule": spec is not None,
        "revision": (spec or {}).get("revision"),
        "version": (spec or {}).get("schedule_version"),
        "activated_at_ist": (spec or {}).get("activated_at_ist"),
        "cycles": rows,
        "current_cycle": current,
        "next_cycle": nxt,
        "upcoming": upcoming,
        "draft": clean_spec(draft) if draft else None,
        "draft_differs": bool(draft and (draft.get("revision") != (spec or {}).get("revision"))),
        "daemon": {
            "loaded_revision": sync.get("loaded_revision"),
            "synced_at_ist": sync.get("synced_at_ist"),
            "in_sync": bool(sync.get("is_synchronized")),
        },
        "presets": PRESETS,
    }


def preset(name: str) -> dict[str, Any]:
    spec = clean_spec(cc.preset_schedule(name))
    for i, c in enumerate(spec["cycles"], start=1):
        c["name"] = f"Cycle {i}"   # short, editable names (no legacy target wording)
    return spec


def validate(spec: dict[str, Any]) -> dict[str, Any]:
    res = cc.validate_production_schedule_detailed(clean_spec(spec))
    return {k: res[k] for k in ("valid", "errors", "warnings", "revision", "cycles", "preview_next_cycles", "profile_name")}


def save_draft(spec: dict[str, Any]) -> dict[str, Any]:
    cc.save_schedule_draft(clean_spec(spec))
    return validate(spec)


def activate(spec: dict[str, Any], confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise PermissionError("Activating a schedule requires explicit confirmation.")
    check = validate(spec)
    if not check["valid"]:
        raise ValueError("; ".join(check["errors"]))
    cleaned = clean_spec(spec)
    cleaned["profile_name"] = check["profile_name"]
    cc.validate_saved_schedule_draft(cleaned)   # persists draft + validation record
    active = cc.activate_schedule_draft(confirm=True)["active_schedule"]
    cc.restore_active_schedule_profile()        # the daemon picks the new revision up on its next poll
    return {"status": "ACTIVATED", "revision": active.get("revision"), "version": active.get("schedule_version"),
            "warnings": check["warnings"]}
