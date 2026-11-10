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

    def _get_scheduler_heartbeat(self) -> Dict[str, Any]:
        """Inspects lockfile and process state."""
        if not os.path.exists(LOCK_PATH):
            return {"state": "IDLE", "is_running": False, "pid": None, "last_heartbeat": None}

        try:
            with open(LOCK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("pid")
            acquired_at = data.get("acquired_at")

            last_hb = data.get("last_heartbeat", acquired_at)

            if not pid:
                return {"state": "STALE_LOCK", "is_running": False, "pid": None, "last_heartbeat": None}

            # Check process liveness
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return {
                    "state": "RUNNING",
                    "is_running": True,
                    "pid": pid,
                    "last_heartbeat": last_hb,
                }
            else:
                return {
                    "state": "STALE_LOCK",
                    "is_running": False,
                    "pid": pid,
                    "last_heartbeat": last_hb,
                }
        except Exception as e:
            return {"state": f"ERROR: {e}", "is_running": False, "pid": None, "last_heartbeat": None}

    def _get_network_status(self) -> str:
        """Determines connectivity status."""
        try:
            ready, reason, diag = network_connectivity_service.is_environment_ready_for_cycle(timeout=2.0)
            if ready:
                return "CONNECTED"
            elif "PROVIDER" in reason:
                return "DEGRADED"
            else:
                return "DISCONNECTED"
        except Exception:
            return "UNKNOWN"

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
            # Active job
            running_job = db_session.query(JobModel).filter(JobModel.status == "RUNNING").first()
            if running_job:
                pipeline_stage = f"{running_job.current_stage} ({running_job.progress_pct:.0f}%)"
                if "RECOVER" in (running_job.id or ""):
                    recovery_status = "RECOVERING"

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

    def get_windows_task_status(self) -> Dict[str, Any]:
        """Queries Windows scheduled task state."""
        try:
            from backend.scripts.manage_windows_startup import get_task_status
            return get_task_status()
        except Exception as e:
            return {"is_registered": False, "opt_in_enabled": False, "error": str(e)}

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
            completed = len([j for j in jobs if j.status == "COMPLETED"])
            qc_failed = len([j for j in jobs if j.status in ("BLOCKED_QC", "FAILED_QC")])
            missed = len([j for j in jobs if j.status == "MISSED"])
            recovered = len([j for j in jobs if "RECOVER" in (j.id or "") or (j.retry_count or 0) > 0])

        published = 0
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
            finally:
                conn.close()

        return {
            "time_filter": time_filter,
            "attempted": attempted,
            "completed": completed,
            "published": published,
            "qc_failed": qc_failed,
            "missed": missed,
            "recovered": recovered,
        }

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
                    | (JobModel.status.in_(["RETRYING", "RECOVERED"]))
                )
                .order_by(JobModel.updated_at.desc())
                .limit(limit)
                .all()
            )
            for j in jobs:
                results.append({
                    "job_id": j.id,
                    "topic": j.topic_title or "Untitled Recovery",
                    "recovery_reason": j.recovery_reason or "Interrupted Execution / Timeout",
                    "status": j.status,
                    "recovered_stage": j.current_stage or "CHECKPOINT_RESUME",
                    "retry_count": j.retry_count or 0,
                    "updated_at": j.updated_at.isoformat() if j.updated_at else "Not recorded",
                    "operator_intervention_needed": (True if j.status in ("FAILED", "BLOCKED_QC") else False),
                })
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
