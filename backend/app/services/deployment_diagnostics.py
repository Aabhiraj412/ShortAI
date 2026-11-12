"""
Deployment diagnostics: which code is each long-running process actually executing?

The scheduler daemon records, in studio_settings, its PID, start time and the code
revision it imported (core.code_revision.LOADED_CODE_REVISION), and refreshes a
heartbeat. get_deployment_status() compares that with the source currently on disk and
reports STALE_CODE when they differ, so edits are never silently ignored again.

Controlled restart: request_daemon_restart() only sets a flag. The daemon exits by
itself at its next safe point (no job QUEUED/RUNNING/RETRYING), and the startup
manager's monitor starts it again with the current code. Nothing here kills processes.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from datetime import datetime
from typing import Any, Optional

from backend.app.core import code_revision
from backend.app.database.db import JobModel
from backend.app.services import control_center_service as control

DAEMON_DEPLOYMENT_KEY = "control_center.daemon.deployment"
RESTART_REQUEST_KEY = "control_center.daemon.restart_requested"
# Statuses meaning a production run is in flight; restarting would interrupt it.
ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING", "RETRYING")
HEARTBEAT_STALE_SECONDS = 180


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def is_pid_alive(pid: Optional[int]) -> bool:
    """Read-only liveness check (never signals the process; os.kill(pid, 0) terminates on Windows)."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def _read_json(key: str) -> dict[str, Any]:
    raw = control._read_setting(key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def register_daemon_start() -> dict[str, Any]:
    record = {
        "pid": os.getpid(),
        "started_at_utc": _now_iso(),
        "loaded_code_revision": code_revision.LOADED_CODE_REVISION,
        "python": sys.executable,
        "last_heartbeat_utc": _now_iso(),
    }
    control._write_settings({DAEMON_DEPLOYMENT_KEY: json.dumps(record)})
    return record


def record_daemon_heartbeat() -> None:
    record = _read_json(DAEMON_DEPLOYMENT_KEY)
    if record.get("pid") != os.getpid():
        return
    record["last_heartbeat_utc"] = _now_iso()
    control._write_settings({DAEMON_DEPLOYMENT_KEY: json.dumps(record)})


def active_jobs() -> list[dict[str, Any]]:
    with control.SessionLocal() as db:
        rows = db.query(JobModel).filter(JobModel.status.in_(ACTIVE_JOB_STATUSES)).all()
        return [{"job_id": j.id, "status": j.status, "stage": j.current_stage} for j in rows]


def safe_restart_state() -> dict[str, Any]:
    jobs = [j for j in active_jobs() if not j["job_id"].startswith(("test_", "mock_"))]
    return {"safe": not jobs, "blocking_jobs": jobs}


def request_daemon_restart(confirm: bool, reason: str = "") -> dict[str, Any]:
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to request a scheduler restart.")
    req = {"requested_at_utc": _now_iso(), "reason": reason or "Operator requested controlled restart"}
    control._write_settings({RESTART_REQUEST_KEY: json.dumps(req)})
    control.record_change("daemon_restart_request", None, req)
    return {"requested": True, **req, "safe_now": safe_restart_state()}


def pending_restart_request() -> dict[str, Any]:
    return _read_json(RESTART_REQUEST_KEY)


def clear_restart_request() -> None:
    control._write_settings({RESTART_REQUEST_KEY: None})


def should_exit_for_restart() -> tuple[bool, str]:
    """Daemon-side check: exit only if a restart was requested AND no job is in flight."""
    if not pending_restart_request():
        return False, "no request"
    state = safe_restart_state()
    if not state["safe"]:
        ids = ", ".join(j["job_id"] for j in state["blocking_jobs"])
        return False, f"waiting for active job(s) to reach a safe state: {ids}"
    return True, "no active production job"


def _worker_heartbeat() -> dict[str, Any]:
    with control.SessionLocal() as db:
        job = (
            db.query(JobModel)
            .filter(JobModel.status.in_(("RUNNING", "RETRYING")))
            .order_by(JobModel.heartbeat_at.desc())
            .first()
        )
        if job is None:
            return {"active_job_id": None, "heartbeat_utc": None, "stage": None}
        return {
            "active_job_id": job.id,
            "heartbeat_utc": job.heartbeat_at.replace(microsecond=0).isoformat() + "Z" if job.heartbeat_at else None,
            "stage": job.current_stage,
        }


def _last_successful_production() -> Optional[dict[str, Any]]:
    with control.SessionLocal() as db:
        job = (
            db.query(JobModel)
            .filter(JobModel.status == "COMPLETED")
            .filter(~JobModel.id.like("test\\_%", escape="\\"))
            .filter(~JobModel.id.like("mock\\_%", escape="\\"))
            .order_by(JobModel.completed_at.desc())
            .first()
        )
        if job is None:
            return None
        return {
            "job_id": job.id,
            "topic_title": job.topic_title,
            "completed_at_utc": job.completed_at.replace(microsecond=0).isoformat() + "Z" if job.completed_at else None,
        }


def get_deployment_status(now_utc: Optional[datetime] = None) -> dict[str, Any]:
    now_utc = now_utc or datetime.utcnow()
    disk_rev = code_revision.current_source_revision()
    daemon = _read_json(DAEMON_DEPLOYMENT_KEY)
    sync = _read_json(control.SCHEDULER_SYNC_KEY)
    warnings: list[str] = []

    daemon_pid = daemon.get("pid") or sync.get("daemon_pid")
    daemon_alive = is_pid_alive(daemon_pid)
    daemon_rev = daemon.get("loaded_code_revision")
    hb = _parse_iso(daemon.get("last_heartbeat_utc"))
    hb_age = (now_utc - hb).total_seconds() if hb else None

    if not daemon:
        daemon_state = "UNKNOWN_REVISION"
        warnings.append(
            "Scheduler daemon has not reported a code revision. It predates deployment "
            "diagnostics, so it is running code of unknown age: treat it as STALE CODE."
        )
    elif not daemon_alive:
        daemon_state = "NOT_RUNNING"
        warnings.append(f"Scheduler daemon PID {daemon_pid} is not running.")
    elif daemon_rev != disk_rev:
        daemon_state = "STALE_CODE"
        warnings.append(
            f"STALE CODE: scheduler daemon PID {daemon_pid} loaded revision {daemon_rev}, "
            f"source on disk is {disk_rev}. Request a controlled restart."
        )
    else:
        daemon_state = "CURRENT"
    if daemon and daemon_alive and hb_age is not None and hb_age > HEARTBEAT_STALE_SECONDS:
        warnings.append(f"Scheduler daemon heartbeat is {int(hb_age)}s old.")

    dashboard_rev = code_revision.LOADED_CODE_REVISION
    dashboard_stale = dashboard_rev != disk_rev
    if dashboard_stale:
        warnings.append(
            f"STALE CODE: dashboard PID {os.getpid()} loaded revision {dashboard_rev}, source on disk is {disk_rev}."
        )

    try:
        active = control.get_control_center_state()["schedule"]["active"]
        active_sched_rev = str(active.get("revision") or "")
    except Exception:
        active_sched_rev = ""
    loaded_sched_rev = sync.get("loaded_revision")
    if active_sched_rev and loaded_sched_rev and active_sched_rev != loaded_sched_rev:
        warnings.append(
            f"Scheduler loaded schedule revision {loaded_sched_rev}, active schedule is {active_sched_rev}."
        )

    restart_req = pending_restart_request()
    overall = "OK"
    if daemon_state in ("STALE_CODE", "UNKNOWN_REVISION") or dashboard_stale:
        overall = "STALE_CODE"
    elif daemon_state == "NOT_RUNNING":
        overall = "DAEMON_NOT_RUNNING"

    return {
        "status": overall,
        "source_revision_on_disk": disk_rev,
        "daemon": {
            "state": daemon_state,
            "pid": daemon_pid,
            "alive": daemon_alive,
            "started_at_utc": daemon.get("started_at_utc"),
            "loaded_code_revision": daemon_rev,
            "last_heartbeat_utc": daemon.get("last_heartbeat_utc"),
            "heartbeat_age_seconds": int(hb_age) if hb_age is not None else None,
        },
        "dashboard": {
            "pid": os.getpid(),
            "loaded_code_revision": dashboard_rev,
            "stale_code": dashboard_stale,
        },
        "scheduler": {
            "active_schedule_revision": active_sched_rev or None,
            "loaded_schedule_revision": loaded_sched_rev,
            "last_sync_ist": sync.get("synced_at_ist"),
        },
        "worker": _worker_heartbeat(),
        "last_successful_production": _last_successful_production(),
        "restart": {
            "requested": bool(restart_req),
            "request": restart_req or None,
            **safe_restart_state(),
        },
        "warnings": warnings,
    }
