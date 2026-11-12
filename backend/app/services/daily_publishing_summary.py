"""
Consolidated Daily Publishing Summary Email Service for CrazzyDocs.
Phase 5, Stage 3.18 Standard.

Gathers accurate 24-hour metrics from:
1. Productions attempted
2. Productions completed
3. Videos successfully published (distinguished from local production)
4. Videos blocked by QC (with error details)
5. Missed production cycles
6. Recovered production cycles
7. Storage consumed (media footprint & disk free space)
8. Errors requiring operator attention

Enforces:
- Exactly one email per reporting day.
- Persistent deduplication via SQLite persistent_notifications.
- Safe retry on delivery failure without losing the report.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    ensure_notification_ledger_schema,
)
from backend.app.scheduler.schedule_profiles import IST_TZ, get_active_profile
from backend.app.services.email_monitoring_service import email_monitoring_service
from backend.app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

from backend.app.core.paths import DATABASE_PATH as _DATABASE_PATH
DB_PATH = str(_DATABASE_PATH)


@dataclass
class DailySummaryReport:
    date_str: str
    productions_attempted: int
    productions_completed: int
    videos_published_public: int
    videos_blocked_qc: int
    missed_cycles: int
    recovered_cycles: int
    storage_consumed_mb: float
    disk_free_gb: float
    videos_uploaded_private: int = 0
    videos_awaiting_approval: int = 0
    videos_scheduled: int = 0
    qc_blocked_details: List[Dict[str, str]] = field(default_factory=list)
    errors_requiring_attention: List[Dict[str, str]] = field(default_factory=list)
    production_titles: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DailyPublishingSummaryService:
    """Compiles and dispatches the consolidated daily publishing summary email."""

    def __init__(self, workspace_root: str = WORKSPACE, db_path: str = DB_PATH):
        self.workspace = workspace_root
        self.db_path = db_path

    def compile_daily_summary(self, date_str: Optional[str] = None) -> DailySummaryReport:
        """Gathers the 8 required metrics from actual SQLite tables and filesystem."""
        cur_date = date_str or datetime.now(IST_TZ).strftime("%Y-%m-%d")

        productions_attempted = 0
        productions_completed = 0
        videos_blocked_qc = 0
        missed_cycles = 0
        recovered_cycles = 0
        qc_blocked_details = []
        errors_requiring_attention = []
        production_titles = []

        with SessionLocal() as db_session:
            all_jobs = db_session.query(JobModel).filter(JobModel.date_str == cur_date).all()
            jobs = [
                j for j in all_jobs
                if not str(j.id or "").startswith(("test_", "mock_"))
            ]
            productions_attempted = len(jobs)

            for job in jobs:
                if job.topic_title and job.topic_title not in production_titles:
                    production_titles.append(job.topic_title)

                if job.status == "COMPLETED":
                    productions_completed += 1
                elif job.status in ("BLOCKED_QC", "FAILED_QC"):
                    videos_blocked_qc += 1
                    qc_blocked_details.append({
                        "job_id": job.id,
                        "topic": job.topic_title or "Unknown Topic",
                        "error": job.error_details or "QC check failed",
                    })
                elif job.status == "MISSED":
                    missed_cycles += 1

                # Check recovery
                if "RECOVER" in (job.id or "") or (job.checkpoint_data_json and "recovery_type" in job.checkpoint_data_json):
                    recovered_cycles += 1

                if job.status in ("FAILED", "BLOCKED_QC", "MISSED_DEADLINE"):
                    errors_requiring_attention.append({
                        "id": job.id,
                        "status": job.status,
                        "details": job.error_details or "Stage exception",
                    })

        # Videos uploaded / awaiting approval / scheduled from real publishing_ledger
        videos_published_public = 0
        videos_uploaded_private = 0
        videos_awaiting_approval = 0
        videos_scheduled = 0
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT upload_status, visibility, COUNT(*) FROM publishing_ledger
                    WHERE (video_path NOT LIKE '%test_%' OR video_path IS NULL)
                      AND (upload_timestamp LIKE ? OR upload_timestamp IS NULL)
                    GROUP BY upload_status, visibility
                """, (f"{cur_date}%",))
                for up_st, vis, cnt in cur.fetchall():
                    st_l = str(up_st or "").lower()
                    vis_l = str(vis or "").lower()
                    n = int(cnt or 0)
                    if vis_l == "public" and st_l in ("uploaded_public", "published"):
                        videos_published_public += n
                    elif st_l in ("uploaded_private", "private_draft"):
                        videos_uploaded_private += n
                    elif st_l in ("scheduled", "scheduled_publish"):
                        videos_scheduled += n
                    elif st_l == "pending_queue":
                        videos_awaiting_approval += n
            except Exception as e:
                logger.warning("Could not query publishing ledger: %s", e)
            finally:
                conn.close()

        # Storage consumed
        media_dir = os.path.join(self.workspace, "media")
        total_media_size = 0
        for root, dirs, files in os.walk(media_dir):
            for f in files:
                try:
                    total_media_size += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        storage_consumed_mb = round(total_media_size / (1024 * 1024), 2)
        usage = shutil.disk_usage(self.workspace[:2])
        disk_free_gb = round(usage.free / (1024**3), 2)

        return DailySummaryReport(
            date_str=cur_date,
            productions_attempted=productions_attempted,
            productions_completed=productions_completed,
            videos_published_public=videos_published_public,
            videos_blocked_qc=videos_blocked_qc,
            missed_cycles=missed_cycles,
            recovered_cycles=recovered_cycles,
            storage_consumed_mb=storage_consumed_mb,
            disk_free_gb=disk_free_gb,
            videos_uploaded_private=videos_uploaded_private,
            videos_awaiting_approval=videos_awaiting_approval,
            videos_scheduled=videos_scheduled,
            qc_blocked_details=qc_blocked_details,
            errors_requiring_attention=errors_requiring_attention,
            production_titles=production_titles,
        )

    def render_summary_html(self, report: DailySummaryReport) -> str:
        """Renders responsive email HTML with clear status badges and metric tables."""
        qc_rows = "".join(
            f"<tr><td style='padding:6px 8px; border-bottom:1px solid #22304d;'><strong>{item['topic']}</strong></td>"
            f"<td style='padding:6px 8px; border-bottom:1px solid #22304d; color:#ff6b6b;'>{item['error']}</td></tr>"
            for item in report.qc_blocked_details
        ) if report.qc_blocked_details else "<tr><td colspan='2' style='padding:8px; color:#20c997;'>Zero QC rejections today.</td></tr>"

        err_rows = "".join(
            f"<tr><td style='padding:6px 8px; border-bottom:1px solid #22304d;'>[{item['status']}] {item['id']}</td>"
            f"<td style='padding:6px 8px; border-bottom:1px solid #22304d; color:#ff6b6b;'>{item['details']}</td></tr>"
            for item in report.errors_requiring_attention
        ) if report.errors_requiring_attention else "<tr><td colspan='2' style='padding:8px; color:#20c997;'>Zero critical errors.</td></tr>"

        titles_list = "".join(f"<li>{t}</li>" for t in report.production_titles) if report.production_titles else "<li>No productions attempted.</li>"

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>CrazzyDocs Daily Publishing Summary</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background-color:#080c14; color:#f0f4fc; margin:0; padding:20px;">
  <div style="max-width:650px; margin:0 auto; background:#131c2e; border:1px solid #22304d; border-radius:12px; overflow:hidden;">
    <div style="background:#121e33; padding:20px; border-bottom:3px solid #fee004;">
      <h2 style="color:#fee004; margin:0;">📊 CrazzyDocs Daily Publishing Summary</h2>
      <p style="color:#94a3b8; margin:5px 0 0 0; font-size:13px;">Reporting Date: <strong>{report.date_str}</strong> (Asia/Kolkata)</p>
    </div>
    <div style="padding:20px;">
      <h3 style="color:#fee004; border-bottom:1px solid #1f2c46; padding-bottom:6px;">1. Operational Metrics</h3>
      <table style="width:100%; border-collapse:collapse; font-size:14px; margin-bottom:20px;">
        <tr style="background:#0f172a;"><td style="padding:8px;">Productions Attempted:</td><td style="padding:8px; font-weight:bold;">{report.productions_attempted}</td></tr>
        <tr><td style="padding:8px;">Productions Completed:</td><td style="padding:8px; font-weight:bold; color:#20c997;">{report.productions_completed}</td></tr>
        <tr style="background:#0f172a;"><td style="padding:8px;">Videos Publicly Published:</td><td style="padding:8px; font-weight:bold; color:#fee004;">{report.videos_published_public}</td></tr>
        <tr><td style="padding:8px;">Videos Blocked by QC:</td><td style="padding:8px; font-weight:bold; color:#ff6b6b;">{report.videos_blocked_qc}</td></tr>
        <tr style="background:#0f172a;"><td style="padding:8px;">Missed Production Cycles:</td><td style="padding:8px;">{report.missed_cycles}</td></tr>
        <tr><td style="padding:8px;">Recovered Production Cycles:</td><td style="padding:8px; color:#20c997;">{report.recovered_cycles}</td></tr>
        <tr style="background:#0f172a;"><td style="padding:8px;">Media Storage Footprint:</td><td style="padding:8px;">{report.storage_consumed_mb} MB (Free: {report.disk_free_gb} GB)</td></tr>
      </table>

      <h3 style="color:#fee004; border-bottom:1px solid #1f2c46; padding-bottom:6px;">2. Productions & Titles</h3>
      <ul style="font-size:14px; color:#cbd5e1; padding-left:20px;">{titles_list}</ul>

      <h3 style="color:#fee004; border-bottom:1px solid #1f2c46; padding-bottom:6px;">3. QC Blocked Deliverables</h3>
      <table style="width:100%; border-collapse:collapse; font-size:13px; margin-bottom:15px;">{qc_rows}</table>

      <h3 style="color:#fee004; border-bottom:1px solid #1f2c46; padding-bottom:6px;">4. Attention Required / Exceptions</h3>
      <table style="width:100%; border-collapse:collapse; font-size:13px;">{err_rows}</table>
    </div>
    <div style="background:#0b101c; padding:12px; text-align:center; font-size:12px; color:#64748b; border-top:1px solid #1e293b;">
      CrazzyDocs Autonomous Production Studio &bull; Dashboard: <code>python backend/scripts/show_production_dashboard.py</code>
    </div>
  </div>
</body>
</html>"""

    def send_daily_summary(
        self,
        date_str: Optional[str] = None,
        recipient: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Compiles and dispatches the daily summary email with strict idempotency,
        transactional claim lock, and DELIVERY_UNKNOWN timeout safety.
        """
        cur_date = date_str or datetime.now(IST_TZ).strftime("%Y-%m-%d")
        idempotency_key = f"daily_publishing_summary_{cur_date}"
        recip = recipient or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL") or "aabhiraj321@gmail.com"
        notif_job_id = f"daily_summary_{cur_date}"

        report = self.compile_daily_summary(date_str=cur_date)
        subject = f"CrazzyDocs | Daily Publishing Summary | {cur_date}"
        html_body = self.render_summary_html(report)
        plain_body = (
            f"CRAZZYDOCS DAILY PUBLISHING SUMMARY - {cur_date}\n\n"
            f"Productions Attempted: {report.productions_attempted}\n"
            f"Productions Completed: {report.productions_completed}\n"
            f"Videos Publicly Published: {report.videos_published_public}\n"
            f"Videos Uploaded Private: {report.videos_uploaded_private}\n"
            f"Videos Awaiting Approval: {report.videos_awaiting_approval}\n"
            f"Videos Scheduled: {report.videos_scheduled}\n"
            f"Videos Blocked by QC: {report.videos_blocked_qc}\n"
            f"Missed Cycles: {report.missed_cycles}\n"
            f"Recovered Cycles: {report.recovered_cycles}\n"
            f"Media Footprint: {report.storage_consumed_mb} MB (Free: {report.disk_free_gb} GB)\n"
        )

        with SessionLocal() as db_session:
            claimed, suppressed = email_monitoring_service._claim_notification_slot(
                db_session,
                idempotency_key=idempotency_key,
                job_id=notif_job_id,
                cycle_id="daily_summary",
                notification_type="DAILY_SUMMARY",
                recipient=recip,
                subject=subject,
                body_plain=plain_body,
                body_html=html_body,
                force=force,
            )
            if suppressed is not None:
                suppressed.setdefault("date", cur_date)
                return suppressed
            claimed_id = claimed.id

        outcome = email_monitoring_service._execute_smtp_with_state(
            subject=subject,
            body_text=plain_body,
            body_html=html_body,
            recipient=recip,
            max_retries=2,
        )
        email_sent = bool(outcome["email_sent"])
        delivery_state = outcome["delivery_state"]

        # Update persistent notification record
        with SessionLocal() as db_session:
            rec = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.id == claimed_id
            ).first()
            if rec:
                rec.attempt_count = (rec.attempt_count or 0) + 1
                rec.delivery_state = delivery_state
                rec.error_category = outcome.get("error_category")
                rec.provider_response = outcome.get("provider_response")
                rec.updated_at = datetime.utcnow()
                if email_sent:
                    rec.status = "SENT"
                    rec.sent_at = datetime.utcnow()
                    rec.last_error = None
                elif delivery_state == "DELIVERY_UNKNOWN":
                    rec.status = "DELIVERY_UNKNOWN"
                    rec.last_error = outcome.get("last_error")
                else:
                    rec.status = "FAILED"
                    rec.last_error = outcome.get("last_error") or "SMTP delivery failed or unconfigured"
                db_session.commit()

        ret_status = (
            "SENT"
            if email_sent
            else ("DELIVERY_UNKNOWN" if delivery_state == "DELIVERY_UNKNOWN" else "SAVED_LOCAL_PENDING")
        )
        return {
            "status": ret_status,
            "delivery_state": delivery_state,
            "date": cur_date,
            "idempotency_key": idempotency_key,
            "email_sent": email_sent,
            "report": report.to_dict(),
        }


daily_publishing_summary_service = DailyPublishingSummaryService()
