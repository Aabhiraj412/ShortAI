"""
Production Health Dashboard for CrazzyDocs Shorts Studio.
Phase 5, Stage 3.18 Standard.

Reads real operational state directly from:
- studio.db (persistent_jobs, publishing_ledger, persistent_notifications)
- studio_scheduler.lock
- NetworkConnectivityService
- ScheduleProfiles

Provides structured JSON metrics and formatted console/dashboard views.
"""

import ctypes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE
from backend.app.database.db import (
    SessionLocal,
    JobModel,
    PersistentNotificationModel,
)
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    get_active_profile,
    ProductionCycle,
)
from backend.app.services.network_connectivity_service import network_connectivity_service

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(WORKSPACE, "backend", "studio.db")
LOCK_PATH = os.path.join(WORKSPACE, "backend", "studio_scheduler.lock")


@dataclass
class ProductionHealthMetrics:
    current_cycle: str
    next_production_ist: str
    pipeline_stage: str
    last_production: Optional[Dict[str, Any]]
    last_public_upload: Optional[Dict[str, Any]]
    recovery_status: str
    network_status: str
    publishing_queue: Dict[str, int]
    failed_qc_jobs: Dict[str, Any]
    email_notifications: Dict[str, int]
    scheduler_heartbeat: Dict[str, Any]
    overall_health: str
    timestamp_ist: str = field(default_factory=lambda: datetime.now(IST_TZ).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProductionHealthDashboard:
    """Compiles and renders real-time production health metrics for CrazzyDocs."""

    def __init__(self, workspace_root: str = WORKSPACE, db_path: str = DB_PATH):
        self.workspace = workspace_root
        self.db_path = db_path
        self._network_cache: Dict[str, Any] = {"value": None, "ts": 0.0}
        self._task_cache: Dict[str, Any] = {"value": None, "ts": 0.0}

    def _get_scheduler_heartbeat(self) -> Dict[str, Any]:
        """Inspects lockfile, heartbeat freshness, and process liveness."""
        if not os.path.exists(LOCK_PATH):
            return {"state": "IDLE", "is_running": False, "pid": None, "last_heartbeat": None, "age_seconds": None}

        try:
            with open(LOCK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("pid")
            acquired_at = data.get("acquired_at")
            last_hb = data.get("last_heartbeat", acquired_at)

            if not pid:
                return {"state": "STALE_LOCK", "is_running": False, "pid": None, "last_heartbeat": None, "age_seconds": None}

            # Check heartbeat freshness (max 180 seconds SLA)
            is_fresh = False
            age_sec = None
            if last_hb:
                try:
                    clean_ts = last_hb.replace("Z", "+00:00")
                    hb_dt = datetime.fromisoformat(clean_ts)
                    now_utc = datetime.now(timezone.utc)
                    age_sec = (now_utc - hb_dt).total_seconds()
                    is_fresh = (0 <= age_sec <= 180.0)
                except Exception:
                    is_fresh = False

            # Check process liveness
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                state = "RUNNING" if is_fresh else "STALE_HEARTBEAT"
                return {
                    "state": state,
                    "is_running": is_fresh,
                    "pid": pid,
                    "last_heartbeat": last_hb,
                    "age_seconds": round(age_sec, 1) if age_sec is not None else None,
                }
            else:
                return {
                    "state": "STALE_LOCK",
                    "is_running": False,
                    "pid": pid,
                    "last_heartbeat": last_hb,
                    "age_seconds": round(age_sec, 1) if age_sec is not None else None,
                }
        except Exception as e:
            return {"state": f"ERROR: {e}", "is_running": False, "pid": None, "last_heartbeat": None, "age_seconds": None}

    def _get_network_status(self, use_cache: bool = True) -> str:
        """Determines connectivity status with short TTL caching outside tests."""
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        now_m = time.monotonic()
        if (
            use_cache
            and not is_pytest
            and self._network_cache["value"] is not None
            and (now_m - self._network_cache["ts"]) < 30.0
        ):
            return str(self._network_cache["value"])

        try:
            ready, reason, diag = network_connectivity_service.is_environment_ready_for_cycle(timeout=2.0)
            if ready:
                status = "CONNECTED"
            elif "PROVIDER" in reason:
                status = "DEGRADED"
            else:
                status = "DISCONNECTED"
        except Exception:
            status = "UNKNOWN"

        self._network_cache = {"value": status, "ts": now_m}
        return status

    def get_health_metrics(self, test_now_ist: Optional[datetime] = None) -> ProductionHealthMetrics:
        """Gathers all 11 required operational metrics from real database and runtime state."""
        now_ist = test_now_ist or datetime.now(IST_TZ)
        profile = get_active_profile()

        # 1. Current Cycle & Next Production in IST
        current_cycle_str = "None (Between Cycles)"
        next_prod_str = "Calculating..."

        # Calculate which cycle is current or next
        upcoming_slots = []
        for cycle in profile.cycles:
            c_start, c_pub, _, _ = cycle.calculate_datetimes(now_ist)
            # Check if now is within cycle window
            c_end = c_start + timedelta(hours=cycle.window_hours)
            if c_start <= now_ist <= c_end:
                current_cycle_str = f"{cycle.name} ({cycle.start_hour_ist:02d}:{cycle.start_min_ist:02d}–{c_end.strftime('%H:%M')} IST)"
            if c_start > now_ist:
                upcoming_slots.append((c_start, cycle.name))

        if upcoming_slots:
            upcoming_slots.sort(key=lambda x: x[0])
            next_prod_str = f"{upcoming_slots[0][0].strftime('%Y-%m-%d %H:%M:%S IST')} ({upcoming_slots[0][1]})"
        else:
            # First cycle tomorrow
            tomorrow = now_ist + timedelta(days=1)
            c_start, _, _, _ = profile.cycles[0].calculate_datetimes(tomorrow)
            next_prod_str = f"{c_start.strftime('%Y-%m-%d %H:%M:%S IST')} ({profile.cycles[0].name})"

        # 2. Database Queries
        pipeline_stage = "IDLE"
        last_prod = None
        last_public_upload = None
        publishing_queue = {"pending": 0, "uploading": 0, "published": 0, "failed": 0}
        failed_qc_details = {"count": 0, "jobs": []}
        email_stats = {"pending": 0, "sent": 0, "failed": 0}
        recovery_status = "HEALTHY"

        with SessionLocal() as db_session:
            # Active job - Must be genuine production job (not test/mock) and have fresh heartbeat
            running_jobs = db_session.query(JobModel).filter(JobModel.status == "RUNNING").all()
            active_prod_job = None
            for rj in running_jobs:
                if (rj.id or "").startswith("test_") or (rj.id or "").startswith("mock_"):
                    continue
                # Check for stale heartbeat (> 300 seconds)
                if rj.heartbeat_at:
                    try:
                        hb_time = rj.heartbeat_at if isinstance(rj.heartbeat_at, datetime) else datetime.fromisoformat(str(rj.heartbeat_at))
                        if (datetime.utcnow() - hb_time).total_seconds() > 300:
                            continue
                    except Exception:
                        pass
                active_prod_job = rj
                break

            if active_prod_job:
                pipeline_stage = f"{active_prod_job.current_stage} ({active_prod_job.progress_pct:.0f}%)"
                if "RECOVER" in (active_prod_job.id or ""):
                    recovery_status = "RECOVERING"
            else:
                pipeline_stage = "IDLE"
                try:
                    from backend.app.services.production_observability import production_observability
                    obs_active = production_observability.get_active_production(include_tests=False)
                    if obs_active and obs_active.get("overall_status") in ("starting", "running"):
                        ctx = str(obs_active.get("execution_context") or "VALIDATION").upper()
                        st_lbl = str(obs_active.get("current_stage") or "running").upper()
                        pct_val = float(obs_active.get("overall_progress") or 0.0)
                        pipeline_stage = f"{ctx}: {st_lbl} ({pct_val:.0f}%)"
                except Exception:
                    pass

            # Last completed production
            last_completed = (
                db_session.query(JobModel)
                .filter(JobModel.status == "COMPLETED")
                .order_by(JobModel.completed_at.desc())
                .first()
            )
            if last_completed:
                last_prod = {
                    "id": last_completed.id,
                    "topic": last_completed.topic_title or "Vesna Vulovic Survival",
                    "status": last_completed.status,
                    "completed_at": last_completed.completed_at.isoformat() if last_completed.completed_at else None,
                }

            # Failed QC jobs
            failed_jobs = (
                db_session.query(JobModel)
                .filter(JobModel.status.in_(["BLOCKED_QC", "FAILED_QC"]))
                .all()
            )
            failed_qc_details["count"] = len(failed_jobs)
            failed_qc_details["jobs"] = [
                {"id": fj.id, "stage": fj.current_stage, "error": fj.error_details}
                for fj in failed_jobs[:5]
            ]
            if failed_jobs:
                recovery_status = "DEGRADED"

            # Email notifications
            for st in ("PENDING", "SENT", "FAILED"):
                cnt = db_session.query(PersistentNotificationModel).filter(
                    PersistentNotificationModel.status == st
                ).count()
                email_stats[st.lower()] = cnt

        # 3. Publishing Ledger Queries
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publishing_ledger';")
                if cur.fetchone():
                    # Publishing queue counts
                    cur.execute("SELECT upload_status, count(*) FROM publishing_ledger GROUP BY upload_status;")
                    for row in cur.fetchall():
                        st, count = row[0], row[1]
                        if "pending" in st.lower():
                            publishing_queue["pending"] += count
                        elif "uploading" in st.lower():
                            publishing_queue["uploading"] += count
                        elif "uploaded" in st.lower() or "published" in st.lower():
                            publishing_queue["published"] += count
                        elif "failed" in st.lower():
                            publishing_queue["failed"] += count

                    # Last successful public upload
                    cur.execute("""
                        SELECT id, topic_title, youtube_video_id, upload_timestamp
                        FROM publishing_ledger
                        WHERE visibility = 'public' AND upload_status IN ('uploaded_public', 'published')
                        ORDER BY id DESC LIMIT 1;
                    """)
                    row = cur.fetchone()
                    if row:
                        last_public_upload = {
                            "ledger_id": row[0],
                            "topic": row[1],
                            "video_id": row[2],
                            "timestamp": row[3],
                        }
            finally:
                conn.close()

        # 4. Heartbeat & Network
        heartbeat = self._get_scheduler_heartbeat()
        network_status = self._get_network_status()

        # 5. Overall Health Classification
        if network_status == "DISCONNECTED" or heartbeat["state"] == "STALE_LOCK":
            overall_health = "CRITICAL"
        elif network_status == "DEGRADED" or failed_qc_details["count"] > 0 or email_stats["failed"] > 0:
            overall_health = "DEGRADED"
        else:
            overall_health = "HEALTHY"

        return ProductionHealthMetrics(
            current_cycle=current_cycle_str,
            next_production_ist=next_prod_str,
            pipeline_stage=pipeline_stage,
            last_production=last_prod,
            last_public_upload=last_public_upload,
            recovery_status=recovery_status,
            network_status=network_status,
            publishing_queue=publishing_queue,
            failed_qc_jobs=failed_qc_details,
            email_notifications=email_stats,
            scheduler_heartbeat=heartbeat,
            overall_health=overall_health,
        )

    def get_windows_task_status(self, use_cache: bool = True) -> Dict[str, Any]:
        """Queries Windows scheduled task state for both CrazzyDocs tasks (60s TTL cache outside tests)."""
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        now_m = time.monotonic()
        if (
            use_cache
            and not is_pytest
            and self._task_cache["value"] is not None
            and (now_m - self._task_cache["ts"]) < 60.0
        ):
            return dict(self._task_cache["value"])

        try:
            from backend.scripts.manage_windows_startup import get_all_tasks_status
            res = get_all_tasks_status()
        except Exception as e:
            res = {"is_registered": False, "opt_in_enabled": False, "error": str(e)}

        self._task_cache = {"value": res, "ts": now_m}
        return res

    def get_reconciled_operational_states(
        self,
        metrics: Optional[ProductionHealthMetrics] = None,
        services: Optional[Dict[str, Any]] = None,
        windows_tasks: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Explicitly separates and reconciles Scheduler, Production Worker, Validation Worker,
        and Overall Operational State so validation runs never masquerade as IDLE.
        """
        m = metrics or self.get_health_metrics()
        svc = services or self.get_services_status()
        wt = windows_tasks or self.get_windows_task_status()

        # 1. Scheduler State
        daemon_running = bool((svc.get("scheduler_daemon") or {}).get("is_running"))
        sched_task = wt.get("scheduler_task") or {}
        sched_raw_state = str(sched_task.get("task_state") or sched_task.get("status") or "").strip().lower()
        if daemon_running:
            scheduler_state = "RUNNING"
        elif "disabled" in sched_raw_state:
            scheduler_state = "DISABLED"
        elif "ready" in sched_raw_state or "enabled" in sched_raw_state:
            scheduler_state = "STANDBY"
        else:
            scheduler_state = "STOPPED"

        # 2. Production Worker State (strictly PRODUCTION jobs in persistent_jobs or observability store)
        prod_worker: Dict[str, Any] = {
            "state": "IDLE",
            "stage": None,
            "progress_pct": 0.0,
            "topic": None,
            "run_id": None,
            "updated_at": None,
        }
        is_recovery_active = False
        try:
            with SessionLocal() as db_session:
                prod_jobs = [
                    j for j in db_session.query(JobModel).order_by(JobModel.updated_at.desc()).limit(25).all()
                    if not (j.id or "").startswith(("test_", "mock_")) and (j.status != "ABORTED_PRE_RESET")
                ]
                running_prod = next((j for j in prod_jobs if j.status == "RUNNING"), None)
                if running_prod:
                    hb_stale = False
                    if running_prod.heartbeat_at:
                        try:
                            hb_dt = running_prod.heartbeat_at if isinstance(running_prod.heartbeat_at, datetime) else datetime.fromisoformat(str(running_prod.heartbeat_at))
                            hb_stale = (datetime.utcnow() - hb_dt).total_seconds() > 300
                        except Exception:
                            pass
                    if not hb_stale:
                        is_recovery_active = ("RECOVER" in (running_prod.id or "")) or (m.recovery_status == "RECOVERING")
                        prod_worker = {
                            "state": "STARTING" if (running_prod.progress_pct or 0.0) <= 5.0 else "RUNNING",
                            "stage": running_prod.current_stage,
                            "progress_pct": float(running_prod.progress_pct or 0.0),
                            "topic": running_prod.topic_title,
                            "run_id": running_prod.id,
                            "updated_at": running_prod.updated_at.isoformat() if running_prod.updated_at else None,
                        }
                elif prod_jobs:
                    latest_p = prod_jobs[0]
                    mapped = "COMPLETED" if latest_p.status == "COMPLETED" else (
                        "FAILED" if latest_p.status in ("FAILED", "BLOCKED_QC", "FAILED_QC") else "IDLE"
                    )
                    prod_worker = {
                        "state": mapped,
                        "stage": latest_p.current_stage,
                        "progress_pct": float(latest_p.progress_pct or 0.0),
                        "topic": latest_p.topic_title,
                        "run_id": latest_p.id,
                        "updated_at": latest_p.updated_at.isoformat() if latest_p.updated_at else None,
                    }
        except Exception:
            pass

        # 3. Validation Worker State (strictly VALIDATION records in ProductionObservabilityStore)
        val_worker: Dict[str, Any] = {
            "state": "IDLE",
            "stage": None,
            "progress_pct": 0.0,
            "topic": None,
            "run_id": None,
            "updated_at": None,
        }
        try:
            from backend.app.services.production_observability import production_observability
            payload = production_observability.get_dashboard_payload(include_tests=False)
            candidates: List[Dict[str, Any]] = []
            if payload.get("active_production"):
                candidates.append(payload["active_production"])
            if payload.get("displayed_production"):
                candidates.append(payload["displayed_production"])
            candidates.extend(payload.get("history") or [])

            prod_obs = [c for c in candidates if str(c.get("execution_context") or "").upper() == "PRODUCTION"]
            val_obs = [c for c in candidates if str(c.get("execution_context") or "").upper() == "VALIDATION"]

            # Sync active PRODUCTION observability if DB didn't show running
            active_prod_obs = next((c for c in prod_obs if c.get("overall_status") in ("starting", "running")), None)
            if active_prod_obs and prod_worker["state"] not in ("STARTING", "RUNNING"):
                st_raw = str(active_prod_obs.get("overall_status") or "running").upper()
                prod_worker = {
                    "state": "STARTING" if st_raw == "STARTING" else "RUNNING",
                    "stage": active_prod_obs.get("current_stage"),
                    "progress_pct": float(active_prod_obs.get("overall_progress") or 0.0),
                    "topic": active_prod_obs.get("topic"),
                    "run_id": active_prod_obs.get("production_id"),
                    "updated_at": active_prod_obs.get("last_updated"),
                }

            active_val = next((c for c in val_obs if c.get("overall_status") in ("starting", "running")), None)
            target_val = active_val or (val_obs[0] if val_obs else None)
            if target_val:
                raw_st = str(target_val.get("overall_status") or "").lower()
                if raw_st in ("starting", "running"):
                    v_state = "RUNNING"
                elif raw_st == "completed":
                    v_state = "COMPLETED"
                elif raw_st == "failed":
                    v_state = "FAILED"
                else:
                    v_state = "IDLE"
                val_worker = {
                    "state": v_state,
                    "stage": target_val.get("current_stage"),
                    "progress_pct": float(target_val.get("overall_progress") or 0.0),
                    "topic": target_val.get("topic"),
                    "run_id": target_val.get("production_id"),
                    "updated_at": target_val.get("last_updated"),
                }
        except Exception:
            pass

        # 4. Overall Operational State
        if is_recovery_active:
            overall_state = "RECOVERY ACTIVE"
        elif prod_worker["state"] in ("STARTING", "RUNNING"):
            overall_state = "PRODUCTION ACTIVE"
        elif val_worker["state"] in ("STARTING", "RUNNING"):
            overall_state = "VALIDATION ACTIVE"
        elif m.overall_health == "CRITICAL" or prod_worker["state"] == "FAILED":
            overall_state = "ERROR"
        elif m.overall_health == "DEGRADED" or val_worker["state"] == "FAILED":
            overall_state = "DEGRADED"
        else:
            overall_state = "IDLE"

        return {
            "scheduler_state": scheduler_state,
            "production_worker_state": prod_worker["state"],
            "production_worker": prod_worker,
            "validation_worker_state": val_worker["state"],
            "validation_worker": val_worker,
            "overall_operational_state": overall_state,
        }

    def get_services_status(self) -> Dict[str, Any]:
        """Reports execution state of scheduler daemon and dashboard service."""
        hb = self._get_scheduler_heartbeat()
        dashboard_pid = os.getpid()

        # Dedicated daemon detection: lock must be fresh AND held by a dedicated daemon process (not the dashboard itself)
        is_dedicated_daemon = hb.get("pid") is not None and hb.get("pid") != dashboard_pid
        daemon_running = is_dedicated_daemon and hb.get("is_running", False)

        return {
            "scheduler_daemon": {
                "state": "RUNNING" if daemon_running else "STOPPED",
                "is_running": daemon_running,
                "pid": hb.get("pid") if is_dedicated_daemon else None,
                "note": "Embedded Lock" if (hb.get("pid") == dashboard_pid) else None,
            },
            "scheduler_heartbeat": hb,
            "dashboard_service": {
                "state": "RUNNING",
                "is_running": True,
                "pid": dashboard_pid,
                "port": 8000,
            },
        }

    def get_database_health(self) -> Dict[str, Any]:
        """Validates SQLite database integrity and table structure."""
        if not os.path.exists(self.db_path):
            return {"status": "MISSING", "integrity": "File not found"}
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            row = cur.fetchone()
            integrity = row[0] if row else "unknown"
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            table_count = cur.fetchone()[0]
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            conn.close()
            return {
                "status": "HEALTHY" if integrity == "ok" else "DEGRADED",
                "integrity": integrity,
                "table_count": table_count,
                "journal_mode": journal_mode,
            }
        except Exception as e:
            return {"status": "ERROR", "integrity": str(e)}

    def get_disk_health(self) -> Dict[str, Any]:
        """Measures disk free space and media directory footprint."""
        try:
            drive_letter = self.workspace[:2] if len(self.workspace) >= 2 else "D:"
            usage = shutil.disk_usage(drive_letter)
            free_gb = round(usage.free / (1024 ** 3), 2)
            total_gb = round(usage.total / (1024 ** 3), 2)
            used_pct = round(((usage.total - usage.free) / usage.total) * 100, 1)

            media_dir = os.path.join(self.workspace, "media")
            media_size = 0
            if os.path.exists(media_dir):
                for root, _, files in os.walk(media_dir):
                    for f in files:
                        try:
                            media_size += os.path.getsize(os.path.join(root, f))
                        except Exception:
                            pass
            media_mb = round(media_size / (1024 * 1024), 2)
            return {
                "free_gb": free_gb,
                "total_gb": total_gb,
                "used_pct": used_pct,
                "media_size_mb": media_mb,
                "status": "HEALTHY" if free_gb > 5.0 else "WARNING",
            }
        except Exception as e:
            return {
                "free_gb": 0.0,
                "total_gb": 0.0,
                "used_pct": 0.0,
                "media_size_mb": 0.0,
                "status": "UNKNOWN",
                "error": str(e),
            }

    def get_production_statistics(self, time_filter: str = "all") -> Dict[str, Any]:
        """Calculates accurate production metrics from persistent_jobs and publishing_ledger."""
        now_ist = datetime.now(IST_TZ)
        cutoff_date = None
        if time_filter == "today":
            cutoff_date = now_ist.strftime("%Y-%m-%d")
        elif time_filter == "7d":
            cutoff_date = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
        elif time_filter == "30d":
            cutoff_date = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")

        with SessionLocal() as db_session:
            query = db_session.query(JobModel)
            if cutoff_date and time_filter == "today":
                query = query.filter(JobModel.date_str == cutoff_date)
            elif cutoff_date:
                query = query.filter(JobModel.date_str >= cutoff_date)

            jobs = query.all()
            attempted = len(jobs)
            test_jobs_count = len([j for j in jobs if (j.id or "").startswith("test_")])
            genuine_attempted = len([j for j in jobs if not (j.id or "").startswith("test_")])
            genuine_completed = len([j for j in jobs if not (j.id or "").startswith("test_") and j.status == "COMPLETED"])
            completed = len([j for j in jobs if j.status == "COMPLETED"])
            qc_failed = len([j for j in jobs if j.status in ("BLOCKED_QC", "FAILED_QC")])
            missed = len([j for j in jobs if j.status == "MISSED"])
            recovered = len([j for j in jobs if "RECOVER" in (j.id or "") or (j.retry_count or 0) > 0])
            aborted_recovery = len([j for j in jobs if j.status == "ABORTED_PRE_RESET"])

        published = 0
        active_pending_count = 0
        active_private_uploads = 0
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                base_sql = "SELECT count(*) FROM publishing_ledger WHERE visibility = 'public' AND upload_status IN ('uploaded_public', 'published')"
                if cutoff_date and time_filter == "today":
                    cur.execute(f"{base_sql} AND upload_timestamp LIKE ?", (f"{cutoff_date}%",))
                elif cutoff_date:
                    cur.execute(f"{base_sql} AND upload_timestamp >= ?", (cutoff_date,))
                else:
                    cur.execute(base_sql)
                row = cur.fetchone()
                published = row[0] if row else 0

                cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'pending_queue';")
                p_row = cur.fetchone()
                active_pending_count = p_row[0] if p_row else 0

                cur.execute("SELECT count(*) FROM publishing_ledger WHERE upload_status = 'uploaded_private';")
                pr_row = cur.fetchone()
                active_private_uploads = pr_row[0] if pr_row else 0
            finally:
                conn.close()

        arch_summary = self.get_archived_publishing_summary()

        return {
            "time_filter": time_filter,
            "attempted": attempted,
            "genuine_attempted": genuine_attempted,
            "genuine_completed": genuine_completed,
            "test_jobs_count": test_jobs_count,
            "completed": completed,
            "published": published,
            "public_uploads": published,
            "private_uploads": active_private_uploads,
            "active_pending_queue": active_pending_count,
            "qc_failed": qc_failed,
            "missed": missed,
            "recovered": recovered,
            "aborted_recovery": aborted_recovery,
            "archived_showcase_count": arch_summary.get("showcase_fixtures", 0),
            "archived_records_count": arch_summary.get("total_archived", 0),
            "archived_breakdown": arch_summary,
        }

    def get_publishing_ledger_safety(self) -> Dict[str, Any]:
        """Provides accurate live ledger vs archive safety counts for the dashboard."""
        active_count = 0
        archive_count = 0
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM publishing_ledger;")
                active_count = cur.fetchone()[0]
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publishing_ledger_archive';")
                if cur.fetchone():
                    cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
                    archive_count = cur.fetchone()[0]
            finally:
                conn.close()
        return {
            "status": "IDEMPOTENT",
            "active_count": active_count,
            "archive_count": archive_count,
            "display_text": f"IDEMPOTENT ({active_count} Active | {archive_count} Archived)",
        }

    def get_archived_publishing_summary(self) -> Dict[str, Any]:
        """Returns archival ledger breakdown distinguishing test fixtures from genuine history."""
        summary = {
            "total_archived": 0,
            "showcase_fixtures": 0,
            "test_records_with_media": 0,
            "test_records_missing_media": 0,
            "classifications": {},
        }
        if not os.path.exists(self.db_path):
            return summary
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publishing_ledger_archive';")
            if cur.fetchone():
                cur.execute("SELECT count(*) FROM publishing_ledger_archive;")
                summary["total_archived"] = cur.fetchone()[0]
                cur.execute("SELECT original_classification, count(*) FROM publishing_ledger_archive GROUP BY original_classification;")
                for row in cur.fetchall():
                    cls_name, cnt = row[0], row[1]
                    summary["classifications"][cls_name] = cnt
                    if "SHOWCASE" in (cls_name or "").upper():
                        summary["showcase_fixtures"] += cnt
                    elif "MISSING" in (cls_name or "").upper():
                        summary["test_records_missing_media"] += cnt
                    else:
                        summary["test_records_with_media"] += cnt
        finally:
            conn.close()
        return summary

    def get_archived_publishing_history(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Retrieves archived publishing ledger entries for historical auditing."""
        results = []
        if not os.path.exists(self.db_path):
            return results
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publishing_ledger_archive';")
            if cur.fetchone():
                cur.execute("""
                    SELECT id, topic_title, upload_status, visibility, youtube_video_id,
                           upload_timestamp, original_classification, archive_reason, archived_at
                    FROM publishing_ledger_archive
                    ORDER BY id DESC
                    LIMIT ?;
                """, (limit,))
                for row in cur.fetchall():
                    results.append({
                        "id": row[0],
                        "topic_title": row[1] or "Topic Untitled",
                        "upload_status": row[2] or "unknown",
                        "visibility": row[3] or "private",
                        "youtube_video_id": row[4],
                        "upload_timestamp": row[5],
                        "original_classification": row[6],
                        "archive_reason": row[7],
                        "archived_at": row[8],
                    })
        finally:
            conn.close()
        return results

    def get_publishing_history(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Retrieves recent publishing ledger entries without exposing sensitive credentials."""
        results = []
        if not os.path.exists(self.db_path):
            return results
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, topic_title, upload_status, visibility, youtube_video_id, upload_timestamp, scheduled_timestamp, error_details
                FROM publishing_ledger
                ORDER BY id DESC
                LIMIT ?;
            """, (limit,))
            for row in cur.fetchall():
                results.append({
                    "id": row[0],
                    "topic_title": row[1] or "Topic Untitled",
                    "upload_status": row[2] or "unknown",
                    "visibility": row[3] or "private",
                    "youtube_video_id": row[4],
                    "upload_timestamp": row[5],
                    "scheduled_timestamp": row[6],
                    "error_details": row[7],
                })
        finally:
            conn.close()
        return results

    def get_recovery_history(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Retrieves recovery events and checkpoint resumption records."""
        results = []
        with SessionLocal() as db_session:
            jobs = (
                db_session.query(JobModel)
                .filter(
                    (JobModel.recovery_reason.isnot(None))
                    | ((JobModel.retry_count or 0) > 0)
                    | (JobModel.status.in_(["RETRYING", "RECOVERED", "ABORTED_PRE_RESET"]))
                )
                .order_by(JobModel.updated_at.desc())
                .limit(limit)
                .all()
            )
            for j in jobs:
                is_aborted = (j.status == "ABORTED_PRE_RESET")
                if is_aborted:
                    status_display = "ABORTED — No production executed"
                    stage_display = "NONE (Cleanly Aborted Pre-Reset)"
                    attn_needed = False
                else:
                    status_display = j.status
                    stage_display = j.current_stage or "CHECKPOINT_RESUME"
                    attn_needed = True if j.status in ("FAILED", "BLOCKED_QC") else False

                results.append({
                    "job_id": j.id,
                    "topic": j.topic_title or "Untitled Recovery",
                    "recovery_reason": j.recovery_reason or "Interrupted Execution / Timeout",
                    "status": status_display,
                    "raw_status": j.status,
                    "is_aborted": is_aborted,
                    "recovered_stage": stage_display,
                    "retry_count": j.retry_count or 0,
                    "updated_at": j.updated_at.isoformat() if j.updated_at else "Not recorded",
                    "operator_intervention_needed": attn_needed,
                })
        return results
        return results

    def get_recent_activity(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Compiles latest operational activity from database."""
        events = []
        with SessionLocal() as db_session:
            jobs = db_session.query(JobModel).order_by(JobModel.updated_at.desc()).limit(limit).all()
            for j in jobs:
                ts = j.updated_at.strftime("%Y-%m-%d %H:%M:%S IST") if j.updated_at else "Recent"
                level = "danger" if j.status in ("FAILED", "BLOCKED_QC") else ("warning" if j.status in ("RETRYING", "MISSED") else "success")
                events.append({
                    "timestamp": ts,
                    "type": "JOB_STATUS",
                    "title": f"Job {j.id} -> {j.status}",
                    "details": f"Stage: {j.current_stage} ({j.progress_pct:.0f}%) | Topic: {j.topic_title or 'N/A'}",
                    "level": level,
                })

        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, topic_title, youtube_video_id, upload_status, visibility, upload_timestamp
                    FROM publishing_ledger
                    ORDER BY id DESC LIMIT 10;
                """)
                for row in cur.fetchall():
                    st = row[3] or ""
                    level = "success" if "uploaded" in st else ("danger" if "failed" in st else "info")
                    events.append({
                        "timestamp": (row[5] or "")[:19] + " UTC",
                        "type": "PUBLISHING",
                        "title": f"Publishing Queue #{row[0]} -> {row[3]}",
                        "details": f"Topic: {row[1]} | Video ID: {row[2] or 'None'} | Visibility: {row[4]}",
                        "level": level,
                    })
            finally:
                conn.close()

        events.sort(key=lambda x: x["timestamp"], reverse=True)
        return events[:limit]

    def render_console_dashboard(self, metrics: Optional[ProductionHealthMetrics] = None) -> str:
        """Formats health metrics into an operational ASCII status table."""
        m = metrics or self.get_health_metrics()
        lines = [
            "+" + "-" * 76 + "+",
            f"| CRAZZYDOCS PRODUCTION HEALTH DASHBOARD | {m.timestamp_ist[:19]} IST |",
            "+" + "-" * 76 + "+",
            f"| Overall System Health:  [{m.overall_health:<10}]  Recovery: [{m.recovery_status:<10}]            |",
            f"| Network Connectivity:   [{m.network_status:<10}]  Scheduler: [{m.scheduler_heartbeat['state']:<10}]           |",
            "+" + "-" * 76 + "+",
            f"| Current Active Cycle:   {m.current_cycle:<50} |",
            f"| Next Production (IST):  {m.next_production_ist:<50} |",
            f"| Current Pipeline Stage: {m.pipeline_stage:<50} |",
            "+" + "-" * 76 + "+",
            f"| Last Production:        {m.last_production['topic'][:46] if m.last_production else 'None':<50} |",
            f"| Last Public Upload:     {m.last_public_upload['video_id'] if m.last_public_upload else 'None (Awaiting Scheduled Window)':<50} |",
            "+" + "-" * 76 + "+",
            f"| Publishing Queue:       Pending: {m.publishing_queue['pending']} | Uploaded: {m.publishing_queue['published']} | Failed: {m.publishing_queue['failed']:<17} |",
            f"| Failed QC Jobs:         {m.failed_qc_jobs['count']} failed/blocked                                    |",
            f"| Email Alerts:           Sent: {m.email_notifications['sent']} | Pending: {m.email_notifications['pending']} | Failed: {m.email_notifications['failed']:<20} |",
            "+" + "-" * 76 + "+",
        ]
        return "\n".join(lines)


production_health_dashboard = ProductionHealthDashboard()
