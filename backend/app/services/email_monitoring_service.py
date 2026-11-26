"""
Email Monitoring & Four-Cycle Daily Dashboard Service for CrazzyDocs.
Phase 5, Stage 3.9F Production Standard.

Serves as the primary monitoring interface for the CrazzyDocs autonomous studio:
1. End-of-cycle summary emails for all 4 daily cycles (Asia/Kolkata).
2. Terminal outcome profiles (SUCCESS incl. recovered, FAILED, QC_REJECTED, MISSED_DEADLINE).
3. A day-progress summary of every cycle the active schedule runs (24 under the current profile).
4. Persistent notification records in SQLite with strict idempotency and restart recovery.
5. Shared email design system (backend/app/services/email_templates): responsive HTML + plain text,
   escaped dynamic content, credential redaction, no fabricated values.
6. Desktop notification fallback and optional consolidated daily digest.
"""

import base64
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
import smtplib
import socket
import uuid
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.core.config import settings
from backend.app.database.db import (
    SessionLocal,
    JobModel,
    PersistentNotificationModel,
    ensure_notification_ledger_schema,
)
from backend.app.scheduler.schedule_profiles import (
    IST_TZ,
    get_active_profile,
    ProductionCycle,
)
from backend.app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT

# Terminal outcome statuses
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_QC_REJECTED = "QC_REJECTED"
STATUS_MISSED_DEADLINE = "MISSED_DEADLINE"


def _encode_thumbnail_for_email(path: str, max_width: int = 360) -> Optional[str]:
    """Encodes a lightweight thumbnail preview in base64 (<60KB) for fast, reliable SMTP transmission."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image
        import io
        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            if w > max_width:
                new_h = int(h * (max_width / w))
                img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.debug("Failed to downscale thumbnail for email: %s", e)
        try:
            if os.path.getsize(path) < 250 * 1024:
                with open(path, "rb") as tf:
                    return base64.b64encode(tf.read()).decode("utf-8")
        except Exception:
            pass
    return None
STATUS_PUBLISHING_FAILED = "PUBLISHING_FAILED"
STATUS_RECOVERED_SUCCESS = "RECOVERED_SUCCESS"

SENSITIVE_KEY_PATTERNS = [
    r"password", r"secret", r"token", r"key", r"credential",
    r"authorization", r"bearer", r"smtp_pass", r"client_secret",
]


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def redact_sensitive_string(text: str) -> str:
    """Masks potential secret credentials and tokens from logs and error strings."""
    if not text:
        return ""
    redacted = text
    # Mask common key=value patterns
    for pat in SENSITIVE_KEY_PATTERNS:
        regex = rf"(?i)({pat}\s*[:=]\s*)(['\"]?[^'\"\s,;]+['\"]?)"
        redacted = re.sub(regex, r"\1[REDACTED]", redacted)
    return redacted


def redact_sensitive_dict(data: Dict) -> Dict:
    """Recursively redacts sensitive keys from dictionaries."""
    redacted = {}
    for k, v in data.items():
        k_str = str(k).lower()
        if any(pat in k_str for pat in SENSITIVE_KEY_PATTERNS):
            redacted[k] = "[REDACTED]"
        elif isinstance(v, dict):
            redacted[k] = redact_sensitive_dict(v)
        elif isinstance(v, str):
            redacted[k] = redact_sensitive_string(v)
        elif isinstance(v, list):
            redacted[k] = [
                redact_sensitive_dict(item) if isinstance(item, dict)
                else (redact_sensitive_string(item) if isinstance(item, str) else item)
                for item in v
            ]
        else:
            redacted[k] = v
    return redacted


@dataclass
class ProductionDetails:
    """Structured details of the cycle's production run. Unknown values stay empty/None and are not shown."""
    date_str: str
    cycle_id: str
    cycle_number: Optional[int]
    job_id: str
    topic: str
    video_title: str
    storytelling_style: str = ""
    narrative_archetype: str = ""
    duration_sec: Optional[float] = None
    start_time: str = ""
    completion_time: str = ""
    total_duration_sec: Optional[float] = None


@dataclass
class DeliverablesDetails:
    """Paths and metadata for generated production artifacts."""
    video_path: str = ""
    thumbnail_path: str = ""
    subtitle_path: str = ""
    attribution_manifest_path: str = ""
    thumbnail_base64: Optional[str] = None


@dataclass
class QCDetails:
    """Quality-control facts reported by the run. Empty fields are unknown and are not shown
    (before the 2026-11 redesign they defaulted to PASSED/'1080x1920 @ 30 FPS', which was never verified)."""
    rendering_status: str = ""
    resolution_fps: str = ""
    av_sync_status: str = ""
    thumbnail_validation: str = ""
    attribution_validation: str = ""
    text_clipping_checks: str = ""
    final_qc_result: str = ""
    checks: Optional[Dict[str, Any]] = None


@dataclass
class PublishingDetails:
    """Publishing facts. Empty fields are unknown and are not shown."""
    target_pub_timestamp: str = ""
    timezone_name: str = "Asia/Kolkata"
    youtube_upload_status: str = ""
    visibility: str = ""
    youtube_video_id: Optional[str] = None
    scheduled_publication_status: str = ""
    manual_approval_status: str = ""


@dataclass
class FailureDetails:
    """Structured diagnosis of a cycle failure or rejection. Unknown values stay empty/None."""
    last_completed_stage: str = ""
    failed_stage: str = ""
    error_message: str = ""
    error_timestamp: str = ""
    retry_count: Optional[int] = None
    recovery_attempts: Optional[int] = None
    recovery_outcome: str = ""
    can_resume: Optional[bool] = None
    deadline_missed: bool = False
    unapproved_artifacts: List[str] = field(default_factory=list)
    log_paths: List[str] = field(default_factory=list)
    recommended_next_action: str = ""


class EmailMonitoringService:
    """
    Manages end-of-cycle summary notifications, daily dashboard compilation,
    idempotency enforcement, and SMTP / desktop delivery fallbacks.
    """

    def __init__(self, workspace_root: str = WORKSPACE_ROOT):
        self.workspace = workspace_root

    def _ensure_ledger_schema(self, db_session) -> None:
        """Ensures additive notification ledger columns exist on the active SQLAlchemy engine."""
        try:
            bind = db_session.get_bind()
            if bind is not None:
                ensure_notification_ledger_schema(bind)
        except Exception as e:
            logger.debug("Notification schema check skipped: %s", e)

    @staticmethod
    def _build_notification_identity(job_id: str, notification_type: str, fallback_key: str) -> Tuple[str, str]:
        """Builds canonical notification identity key from (job_id, notification_type) when job_id exists."""
        clean_job = (job_id or "").strip()
        clean_type = (notification_type or "CYCLE_SUMMARY").strip()
        if clean_job and not clean_job.startswith("digest_"):
            return f"{clean_job}:{clean_type}", clean_type
        return fallback_key, clean_type

    def _claim_notification_slot(
        self,
        db_session,
        *,
        idempotency_key: str,
        job_id: str,
        cycle_id: Optional[str],
        notification_type: str,
        recipient: str,
        subject: str,
        body_plain: str,
        body_html: str,
        payload_json: Optional[str] = None,
        force: bool = False,
    ) -> Tuple[Optional[PersistentNotificationModel], Optional[Dict[str, Any]]]:
        """
        Atomically claims a notification ledger row before touching SMTP.
        Prevents duplicate sends across duplicate call paths, restarts, and concurrent workers,
        and suppresses blind retries after DELIVERY_UNKNOWN timeouts.
        """
        self._ensure_ledger_schema(db_session)
        clean_job = (job_id or "").strip()

        def _find_existing() -> Optional[PersistentNotificationModel]:
            rec = (
                db_session.query(PersistentNotificationModel)
                .filter(PersistentNotificationModel.idempotency_key == idempotency_key)
                .first()
            )
            if rec is None and clean_job:
                rec = (
                    db_session.query(PersistentNotificationModel)
                    .filter(
                        PersistentNotificationModel.job_id == clean_job,
                        PersistentNotificationModel.notification_type == notification_type,
                    )
                    .first()
                )
            return rec

        def _suppression_response(rec: PersistentNotificationModel) -> Optional[Dict[str, Any]]:
            state = (getattr(rec, "delivery_state", None) or "").upper()
            st = (rec.status or "").upper()
            if st == "SENT" or state == "CONFIRMED_SENT":
                return {
                    "status": "DUPLICATE_SUPPRESSED",
                    "delivery_state": "CONFIRMED_SENT",
                    "idempotency_key": rec.idempotency_key,
                    "notification_id": rec.id,
                    "email_sent": False,
                }
            if st == "DISPATCHING" or state == "DISPATCHING":
                return {
                    "status": "DUPLICATE_SUPPRESSED",
                    "delivery_state": "DISPATCHING",
                    "reason": "DISPATCH_IN_PROGRESS",
                    "idempotency_key": rec.idempotency_key,
                    "notification_id": rec.id,
                    "email_sent": False,
                }
            if st == "DELIVERY_UNKNOWN" or state == "DELIVERY_UNKNOWN":
                return {
                    "status": "DELIVERY_UNKNOWN_SUPPRESSED",
                    "delivery_state": "DELIVERY_UNKNOWN",
                    "reason": "UNCERTAIN_SMTP_TIMEOUT_NO_AUTO_RESEND",
                    "idempotency_key": rec.idempotency_key,
                    "notification_id": rec.id,
                    "email_sent": False,
                    "last_error": rec.last_error,
                }
            return None

        existing = _find_existing()
        if existing and not force:
            suppressed = _suppression_response(existing)
            if suppressed is not None:
                logger.info(
                    "[EMAIL_MONITOR] Notification suppressed (%s) for key='%s', job_id='%s', type='%s'.",
                    suppressed["status"],
                    existing.idempotency_key,
                    clean_job,
                    notification_type,
                )
                return None, suppressed

        if existing is None:
            notif_id = f"notif_{uuid.uuid4().hex[:16]}"
            existing = PersistentNotificationModel(
                id=notif_id,
                job_id=clean_job,
                cycle_id=cycle_id,
                notification_type=notification_type,
                recipient=recipient,
                status="PENDING",
                delivery_state="PENDING",
                attempt_count=0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                idempotency_key=idempotency_key,
                subject=subject[:500],
                body_plain=body_plain,
                body_html=body_html,
                payload_json=payload_json,
            )
            db_session.add(existing)
            try:
                db_session.commit()
            except IntegrityError:
                db_session.rollback()
                existing = _find_existing()
                if existing and not force:
                    suppressed = _suppression_response(existing)
                    if suppressed is not None:
                        return None, suppressed

        if existing is None:
            return None, {"status": "FAILED", "email_sent": False, "last_error": "Could not allocate notification record."}

        now_utc = datetime.utcnow()
        if force:
            claim_res = db_session.execute(
                text(
                    "UPDATE persistent_notifications "
                    "SET status='DISPATCHING', delivery_state='DISPATCHING', subject=:subj, "
                    "body_plain=:bp, body_html=:bh, payload_json=:pj, recipient=:rec, updated_at=:now "
                    "WHERE id=:nid AND COALESCE(delivery_state, 'PENDING') NOT IN ('DISPATCHING')"
                ),
                {
                    "subj": subject[:500],
                    "bp": body_plain,
                    "bh": body_html,
                    "pj": payload_json,
                    "rec": recipient,
                    "now": now_utc,
                    "nid": existing.id,
                },
            )
        else:
            claim_res = db_session.execute(
                text(
                    "UPDATE persistent_notifications "
                    "SET status='DISPATCHING', delivery_state='DISPATCHING', subject=:subj, "
                    "body_plain=:bp, body_html=:bh, payload_json=:pj, recipient=:rec, updated_at=:now "
                    "WHERE id=:nid AND COALESCE(status, 'PENDING') IN ('PENDING', 'FAILED') "
                    "AND COALESCE(delivery_state, 'PENDING') IN ('PENDING', 'FAILED_RETRYABLE', '')"
                ),
                {
                    "subj": subject[:500],
                    "bp": body_plain,
                    "bh": body_html,
                    "pj": payload_json,
                    "rec": recipient,
                    "now": now_utc,
                    "nid": existing.id,
                },
            )
        db_session.commit()
        db_session.refresh(existing)
        if getattr(claim_res, "rowcount", 0) == 0:
            suppressed = _suppression_response(existing)
            if suppressed is not None:
                return None, suppressed
            return None, {
                "status": "DUPLICATE_SUPPRESSED",
                "delivery_state": getattr(existing, "delivery_state", existing.status),
                "idempotency_key": existing.idempotency_key,
                "notification_id": existing.id,
                "email_sent": False,
            }
        return existing, None

    def _execute_smtp_with_state(
        self,
        *,
        subject: str,
        body_text: str,
        body_html: str = "",
        recipient: Optional[str] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """
        Invokes notification_service.notify_email and interprets last_email_receipt or exceptions
        so send-phase timeouts map to DELIVERY_UNKNOWN instead of blind retry.
        """
        notification_service.last_email_receipt = {}
        try:
            email_sent = bool(
                notification_service.notify_email(
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    recipient=recipient,
                    max_retries=max_retries,
                )
            )
            receipt = dict(getattr(notification_service, "last_email_receipt", None) or {})
            if email_sent:
                return {
                    "email_sent": True,
                    "delivery_state": "CONFIRMED_SENT",
                    "error_category": None,
                    "last_error": None,
                    "provider_response": receipt.get("provider_response") or "250 OK (accepted by SMTP)",
                }
            d_state = receipt.get("delivery_state") or "FAILED_RETRYABLE"
            if d_state == "CONFIRMED_SENT":
                d_state = "FAILED_RETRYABLE"
            return {
                "email_sent": False,
                "delivery_state": d_state,
                "error_category": receipt.get("error_category") or "SMTP_DELIVERY_FAILED",
                "last_error": receipt.get("last_error") or "SMTP delivery failed without confirmation",
                "provider_response": receipt.get("provider_response"),
            }
        except (TimeoutError, socket.timeout, smtplib.SMTPServerDisconnected) as e:
            return {
                "email_sent": False,
                "delivery_state": "DELIVERY_UNKNOWN",
                "error_category": "SEND_DATA_TIMEOUT",
                "last_error": f"SMTP timeout/disconnect ({type(e).__name__}): {e}",
                "provider_response": None,
            }
        except Exception as e:
            err_lower = str(e).lower()
            if "timed out" in err_lower or "timeout" in err_lower:
                return {
                    "email_sent": False,
                    "delivery_state": "DELIVERY_UNKNOWN",
                    "error_category": "SEND_DATA_TIMEOUT",
                    "last_error": str(e),
                    "provider_response": None,
                }
            return {
                "email_sent": False,
                "delivery_state": "FAILED_RETRYABLE",
                "error_category": type(e).__name__,
                "last_error": str(e),
                "provider_response": None,
            }

    # -------------------------------------------------------------------------
    # 1. Configuration & Health
    # -------------------------------------------------------------------------

    def get_recipient(self) -> str:
        """Retrieves configured recipient email from environment or studio settings."""
        return notification_service.get_public_email_configuration().get("recipient") or "operator@crazzydocs.local"

    def is_smtp_configured(self) -> bool:
        """Checks if SMTP credentials are configured in local environment."""
        return notification_service.is_email_configured()

    def get_system_health(self) -> Dict:
        """Reports notification subsystem health."""
        smtp_configured = self.is_smtp_configured()
        with SessionLocal() as db_session:
            self._ensure_ledger_schema(db_session)
            pending_count = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.status == "PENDING"
            ).count()
            sent_count = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.status == "SENT"
            ).count()
            failed_count = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.status == "FAILED"
            ).count()
            unknown_count = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.delivery_state == "DELIVERY_UNKNOWN"
            ).count()

        status_str = "Operational" if smtp_configured else "Local Standby (SMTP Not Configured)"
        if pending_count > 0:
            status_str += f" - {pending_count} Pending Delivery"
        if unknown_count > 0:
            status_str += f" - {unknown_count} Delivery Unknown"

        return {
            "status": status_str,
            "smtp_configured": smtp_configured,
            "recipient": self.get_recipient(),
            "pending_notifications": pending_count,
            "sent_notifications": sent_count,
            "failed_notifications": failed_count,
            "delivery_unknown_notifications": unknown_count,
        }

    def get_notification_configuration(self) -> Dict:
        """Returns non-secret SMTP configuration suitable for the local control API."""
        return notification_service.get_public_email_configuration()

    def save_notification_configuration(self, config: Dict[str, Optional[str]]) -> Dict:
        return notification_service.save_email_configuration(config)

    def dispatch_stage_notification(
        self,
        event_type: str,
        idempotency_key: str,
        subject: str,
        details: Dict,
        recipient: Optional[str] = None,
    ) -> Dict:
        """Persists and delivers a deduplicated Stage 3.22 event without raising into workers."""
        allowed_types = {
            "PRODUCTION_COMPLETED", "PRODUCTION_FAILED", "UPLOAD_COMPLETED", "UPLOAD_FAILED",
            "YOUTUBE_AUTH_PROBLEM", "SCHEDULER_FAILED", "CRITICAL_SYSTEM_ERROR",
        }
        if event_type not in allowed_types or not idempotency_key:
            return {"status": "REJECTED", "reason": "INVALID_NOTIFICATION"}
        destination = recipient or (
            os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT")
            or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL")
            or notification_service.get_public_email_configuration().get("recipient")
            or ""
        )
        if not destination:
            return {"status": "NOT_CONFIGURED", "email_sent": False}

        payload = dict(details)
        payload.setdefault("event_type", event_type)
        payload.setdefault("timestamp_ist", datetime.now(IST_TZ).isoformat())
        payload = redact_sensitive_dict(payload)
        payload_json = json.dumps(payload, ensure_ascii=True, default=str)
        try:
            from backend.app.services.email_templates.alerts import render_event
            rendered = render_event(event_type, subject[:500], payload)
            body, body_html = rendered.text, rendered.html
        except Exception:
            logger.exception("[EMAIL_MONITOR] Alert rendering failed for %s; sending plain text.", event_type)
            body = "\n".join(f"{key}: {value}" for key, value in payload.items())
            body_html = ""
        job_id = str(payload.get("production_id") or payload.get("upload_id") or payload.get("job_id") or "")
        try:
            with SessionLocal() as db_session:
                record, suppressed = self._claim_notification_slot(
                    db_session,
                    idempotency_key=idempotency_key,
                    job_id=job_id,
                    cycle_id=payload.get("cycle_id"),
                    notification_type=event_type,
                    recipient=destination,
                    subject=subject[:500],
                    body_plain=body,
                    body_html=body_html,
                    payload_json=payload_json,
                )
                if suppressed is not None:
                    return suppressed
                notification_id = record.id

            if self.is_smtp_configured():
                outcome = self._execute_smtp_with_state(
                    subject=subject[:500],
                    body_text=body,
                    body_html=body_html,
                    recipient=destination,
                    max_retries=2,
                )
            else:
                outcome = {
                    "email_sent": False,
                    "delivery_state": "FAILED_RETRYABLE",
                    "error_category": "SMTP_NOT_CONFIGURED",
                    "last_error": "SMTP credentials are not configured.",
                    "provider_response": None,
                }

            email_sent = bool(outcome["email_sent"])
            delivery_state = outcome["delivery_state"]
            with SessionLocal() as db_session:
                rec = db_session.query(PersistentNotificationModel).filter(
                    PersistentNotificationModel.id == notification_id
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
                        rec.last_error = outcome.get("last_error")
                    db_session.commit()
            return {
                "status": "SENT" if email_sent else ("DELIVERY_UNKNOWN" if delivery_state == "DELIVERY_UNKNOWN" else "SAVED_LOCAL_PENDING"),
                "delivery_state": delivery_state,
                "email_sent": email_sent,
                "notification_id": notification_id,
            }
        except Exception:
            logger.exception("Could not persist Stage 3.22 notification.")
            return {"status": "FAILED", "email_sent": False}

    def get_notification_history(self, limit: int = 100) -> List[Dict]:
        limit = max(1, min(int(limit), 500))
        with SessionLocal() as db_session:
            records = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.notification_type.in_(
                    [
                        "PRODUCTION_COMPLETED", "PRODUCTION_FAILED", "UPLOAD_COMPLETED",
                        "UPLOAD_FAILED", "YOUTUBE_AUTH_PROBLEM", "SCHEDULER_FAILED",
                        "CRITICAL_SYSTEM_ERROR", "DAILY_SUMMARY", "CYCLE_SUMMARY", "email",
                    ]
                )
            ).order_by(PersistentNotificationModel.created_at.desc()).limit(limit).all()
            history = []
            for item in records:
                created_at = item.created_at
                sent_at = item.sent_at
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if sent_at and sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
                history.append({
                    "id": item.id,
                    "job_id": item.job_id,
                    "type": item.notification_type,
                    "recipient": item.recipient,
                    "status": item.status,
                    "attempt_count": item.attempt_count,
                    "created_at": created_at.isoformat() if created_at else None,
                    "created_at_ist": created_at.astimezone(IST_TZ).isoformat() if created_at else None,
                    "sent_at": sent_at.isoformat() if sent_at else None,
                    "sent_at_ist": sent_at.astimezone(IST_TZ).isoformat() if sent_at else None,
                    "last_error": item.last_error,
                    "subject": item.subject,
                })
            return history

    # -------------------------------------------------------------------------
    # 2. Daily Production Dashboard Aggregator
    # -------------------------------------------------------------------------

    def generate_daily_dashboard(
        self,
        date_str: Optional[str] = None,
        test_now_ist: Optional[datetime] = None,
        current_job_overlay: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """
        Snapshot of every cycle the active schedule runs on ``date_str`` (IST), from the database,
        optionally overlaid with the authoritative result of the run that is reporting right now.
        Before the 2026-11 redesign only the first four cycles were inspected (``profile.cycles[:4]``),
        which under the 24-cycle schedule reported cycles 1-4 in every email.
        """
        from backend.app.services.email_templates import report as R

        now_ist = test_now_ist or datetime.now(IST_TZ)
        cur_date_str = date_str or now_ist.strftime("%Y-%m-%d")
        day = datetime.strptime(cur_date_str, "%Y-%m-%d").replace(tzinfo=IST_TZ)
        profile = get_active_profile()
        overlay = current_job_overlay or {}
        ov_status = str(overlay.get("status") or "").upper()

        legacy = {R.ST_COMPLETED: "SUCCESS", R.ST_RECOVERED: "SUCCESS", R.ST_FAILED: "FAILED", R.ST_RUNNING: "RUNNING",
                  R.ST_RETRY: "RETRYING", R.ST_NOT_STARTED: "MISSED", R.ST_UPCOMING: "QUEUED", R.ST_SKIPPED: "SKIPPED"}
        qc_map = {"SUCCESS": "PASS", "FAILED": "FAIL", "QC_REJECTED": "FAIL", "RUNNING": "IN_PROGRESS",
                  "RETRYING": "RETRY", "QUEUED": "PENDING"}
        cycle_rows: List[Dict[str, Any]] = []
        counts = {"started": 0, "success": 0, "failed": 0, "missed": 0, "running": 0, "upcoming": 0, "awaiting": 0}
        total_prod_seconds = 0.0
        uploaded_states = ("UPLOADED", "UPLOADED_PRIVATE", "SCHEDULED", "PUBLISHED")
        active = [c for c in profile.cycles if day.weekday() in tuple(c.active_weekdays)]
        with SessionLocal() as db_session:
            for idx, cycle in enumerate(profile.cycles, start=1):
                if cycle not in active:
                    continue
                slot = day.replace(hour=cycle.start_hour_ist, minute=cycle.start_min_ist)
                expected_job_id = "%s_%s_%02d%02d" % (cycle.cycle_id, day.strftime("%Y%m%d"),
                                                      cycle.start_hour_ist, cycle.start_min_ist)
                job = db_session.query(JobModel).filter(JobModel.id == expected_job_id).first()
                row = R._row_from_job(db_session, "Cycle %d" % idx, slot, job, now_ist)
                applies = bool(overlay) and (overlay.get("job_id") == expected_job_id
                                             or (overlay.get("cycle_id") == cycle.cycle_id and not overlay.get("job_id")))
                if applies and ov_status in ("COMPLETED", "SUCCESS") and row.status not in (R.ST_COMPLETED, R.ST_RECOVERED):
                    row.status = R.ST_COMPLETED
                    row.topic = overlay.get("topic") or row.topic
                    row.production_sec = overlay.get("total_duration_sec") or row.production_sec
                    row.job_id = row.job_id or overlay.get("job_id")
                elif applies and overlay.get("topic") and not row.topic:
                    row.topic = overlay.get("topic")
                status = legacy.get(row.status, "QUEUED")
                if status == "FAILED" and str(row.raw_status or "").upper() in ("BLOCKED_QC", "FAILED_QC", "QC_REJECTED"):
                    status = "QC_REJECTED"
                if row.job_id:
                    counts["started"] += 1
                if status == "SUCCESS":
                    counts["success"] += 1
                    total_prod_seconds += float(row.production_sec or 0.0)
                    if not (row.youtube and row.youtube.state in uploaded_states):
                        counts["awaiting"] += 1
                elif status in ("FAILED", "QC_REJECTED"):
                    counts["failed"] += 1
                elif status == "MISSED":
                    counts["missed"] += 1
                elif status in ("RUNNING", "RETRYING"):
                    counts["running"] += 1
                elif status == "QUEUED":
                    counts["upcoming"] += 1
                target_pub = "%02d:%02d" % (cycle.target_pub_hour_ist, cycle.target_pub_min_ist)
                if cycle.pub_rollover_days > 0:
                    target_pub += " (+1d)"
                cycle_rows.append({
                    "cycle_number": idx,
                    "cycle_id": cycle.cycle_id,
                    "cycle_name": cycle.name,
                    "status": status,
                    "display_status": row.status,
                    "topic": row.topic or ("Autonomous Selection" if row.job_id else "Not selected"),
                    "start_ist": "%02d:%02d" % (cycle.start_hour_ist, cycle.start_min_ist),
                    "target_pub_ist": target_pub,
                    "qc": qc_map.get(status, "N/A"),
                    "job_id": row.job_id,
                })

        # Uploads / scheduled publications recorded in the publishing ledger on this IST date (upload_timestamp).
        uploaded_count: Optional[int] = None
        scheduled_count: Optional[int] = None
        try:
            with SessionLocal() as db_session:
                rows = db_session.execute(text(
                    "SELECT upload_status, visibility, COUNT(*) FROM publishing_ledger "
                    "WHERE (video_path NOT LIKE '%test_%' OR video_path IS NULL) AND upload_timestamp LIKE :d "
                    "GROUP BY upload_status, visibility"), {"d": cur_date_str + "%"}).fetchall()
            uploaded_count = sum(int(r[2] or 0) for r in rows if r[0] and "fail" not in str(r[0]).lower()
                                 and str(r[0]).lower() not in ("pending_queue", "queued", "pending"))
            scheduled_count = sum(int(r[2] or 0) for r in rows if "schedul" in str(r[0] or "").lower())
        except Exception:
            pass

        next_cycle = "None scheduled"
        try:
            from backend.app.services.schedule_service import next_production_cycle
            nxt = next_production_cycle(now_ist)
            if nxt:
                next_cycle = f"{nxt['name']} ({nxt['production_start_ist']})"
        except Exception:
            next_cycle = "Unavailable"

        # Storage is deliberately NOT computed here. The former call to media_lifecycle_manager.daily_report()
        # ran a full storage audit (~60k files) inside every cycle report; it held the scheduler's event loop
        # for 12-14 minutes after each production (no heartbeat, delayed interval jobs). The daily operations
        # report measures storage once a day instead.
        storage_mb: Optional[float] = None

        health = self.get_system_health()
        return {
            "date": cur_date_str,
            "timezone": "Asia/Kolkata",
            "cycles": cycle_rows,
            "summary": {
                "total_cycles_scheduled": len(cycle_rows),
                "total_cycles_started": counts["started"],
                "successfully_generated_videos": counts["success"],
                "failed_cycles": counts["failed"],
                "missed_cycles": counts["missed"],
                "in_progress_cycles": counts["running"],
                "upcoming_cycles": counts["upcoming"],
                "videos_awaiting_approval": counts["awaiting"],
                "videos_successfully_uploaded": uploaded_count,
                "videos_actually_scheduled_youtube": scheduled_count,
                "total_production_duration_sec": total_prod_seconds,
                "storage_consumed_mb": storage_mb,
                "current_publishing_queue": counts["awaiting"],
                "next_scheduled_cycle": next_cycle,
                "email_system_health": health["status"],
            },
        }

    @staticmethod
    def _day_progress(dashboard: Optional[Dict]) -> Optional[Any]:
        from backend.app.services.email_templates.production import DayProgress
        if not dashboard or not dashboard.get("cycles"):
            return None
        sm = dashboard["summary"]
        return DayProgress(
            date=dashboard["date"], scheduled=int(sm.get("total_cycles_scheduled") or 0),
            completed=int(sm.get("successfully_generated_videos") or 0), failed=int(sm.get("failed_cycles") or 0),
            in_progress=int(sm.get("in_progress_cycles") or 0), upcoming=int(sm.get("upcoming_cycles") or 0),
            not_started=int(sm.get("missed_cycles") or 0),
        )

    # -------------------------------------------------------------------------
    # 3. Email builders (shared design system in backend/app/services/email_templates)
    # -------------------------------------------------------------------------

    def _render_ascii_dashboard(self, dashboard: Dict) -> str:
        """Plain-text day summary (kept for scripts that print it)."""
        lines = ["CrazzyDocs production day %s (%s)" % (dashboard["date"], dashboard["timezone"])]
        for c in dashboard["cycles"]:
            lines.append("  Cycle %-3s %-6s %-12s %s" % (c["cycle_number"], c["start_ist"], c["status"], c["topic"][:60]))
        prog = self._day_progress(dashboard)
        if prog:
            lines.append("  " + prog.summary())
        return "\n".join(lines)

    @staticmethod
    def _legacy_context(prod: "ProductionDetails", outcome: str, dashboard: Optional[Dict]) -> Any:
        from backend.app.services.email_templates.production import ProductionEmailContext
        label = "Cycle %d" % prod.cycle_number if prod.cycle_number else "Manual production"
        return ProductionEmailContext(
            outcome=outcome, cycle_label=label, cycle_number=prod.cycle_number, cycle_id=prod.cycle_id,
            job_id=prod.job_id, topic=prod.topic or None, video_title=prod.video_title or None,
            video_duration_sec=prod.duration_sec, production_elapsed_sec=prod.total_duration_sec,
            started_at=prod.start_time or None, completed_at=prod.completion_time or None,
            day=EmailMonitoringService._day_progress(dashboard),
        )

    def build_success_email(
        self,
        prod: "ProductionDetails",
        deliv: "DeliverablesDetails",
        qc: "QCDetails",
        pub: "PublishingDetails",
        dashboard: Optional[Dict],
        context: Any = None,
    ) -> Tuple[str, str, str]:
        """(subject, plain, html) for a terminal SUCCESS report. ``context`` (a ProductionEmailContext built from the
        database by send_cycle_summary_report) takes precedence; otherwise the detail objects are adapted."""
        from backend.app.services.email_templates import production as P
        from backend.app.services.email_templates.data import relative_path
        if context is None:
            context = self._legacy_context(prod, STATUS_SUCCESS, dashboard)
            context.qc_checks = qc.checks
            context.qc_gate_passed = True if (qc.final_qc_result or "").upper().startswith("PASS") else None
            m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", qc.thumbnail_validation or "")
            context.thumbnail_score = float(m.group(1)) if m else None
            extra = [("Visual style", prod.storytelling_style or None), ("Narrative", prod.narrative_archetype or None),
                     ("Rendering", qc.rendering_status or None), ("Resolution", qc.resolution_fps or None),
                     ("A/V sync", qc.av_sync_status or None), ("Thumbnail check", qc.thumbnail_validation or None),
                     ("Attribution check", qc.attribution_validation or None), ("Text safe areas", qc.text_clipping_checks or None),
                     ("Final QC", qc.final_qc_result or None)]
            context.extra_rows = [(k, v) for k, v in extra if v]
            context.target_publication = pub.target_pub_timestamp or None
            if pub.youtube_video_id:
                context.youtube = P.YouTubeState(P.YT_UPLOADED, pub.youtube_video_id, pub.visibility or None)
            elif pub.youtube_upload_status:
                context.youtube = P.YouTubeState(P.YT_UNKNOWN, detail=pub.youtube_upload_status)
            context.artifacts = [(l, relative_path(p)) for l, p in (
                ("Video", deliv.video_path), ("Thumbnail", deliv.thumbnail_path),
                ("Subtitles", deliv.subtitle_path if re.search(r"[\\/]", deliv.subtitle_path or "") else None),
                ("Attribution manifest", deliv.attribution_manifest_path)) if p]
        if deliv is not None and deliv.thumbnail_base64 and not context.thumbnail_data_uri:
            mime = "image/jpeg" if deliv.thumbnail_base64.startswith("/9j/") else "image/png"
            context.thumbnail_data_uri = "data:%s;base64,%s" % (mime, deliv.thumbnail_base64)
        return P.render_success(context).as_tuple()

    def build_failure_email(
        self,
        prod: "ProductionDetails",
        fail: "FailureDetails",
        dashboard: Optional[Dict],
        terminal_status: str = STATUS_FAILED,
        context: Any = None,
    ) -> Tuple[str, str, str]:
        """(subject, plain, html) for a FAILED / QC_REJECTED / MISSED_DEADLINE report. Error text is redacted."""
        from backend.app.services.email_templates import production as P
        if context is None:
            context = self._legacy_context(prod, terminal_status, dashboard)
            context.failed_stage = fail.failed_stage or None
            context.last_completed_stage = fail.last_completed_stage or None
            context.error_at = fail.error_timestamp or None
            context.recommended_action = fail.recommended_next_action or None
            context.unapproved_artifacts = list(fail.unapproved_artifacts or [])
            context.can_resume = fail.can_resume
            attempts = fail.recovery_attempts if fail.recovery_attempts is not None else fail.retry_count
            context.recovery = P.RecoveryInfo(attempts=attempts if attempts else None,
                                              window_expired=bool(fail.deadline_missed and terminal_status == STATUS_MISSED_DEADLINE))
            extra = [("Recovery outcome", fail.recovery_outcome or None),
                     ("Deadline missed", "Yes" if fail.deadline_missed else None),
                     ("Logs (studio PC)", ", ".join(fail.log_paths) if fail.log_paths else None)]
            context.extra_rows = [(k, v) for k, v in extra if v]
            context.error_message = fail.error_message or None
        context.error_message = redact_sensitive_string(context.error_message or "") or None
        if context.recovery and context.recovery.first_failure:
            context.recovery.first_failure = redact_sensitive_string(context.recovery.first_failure)
        return P.render_failure(context).as_tuple()

    # -------------------------------------------------------------------------
    # 4. Dispatch with Deduplication, Persistence & Fallbacks
    # -------------------------------------------------------------------------

    def dispatch_summary_notification(
        self,
        cycle_id: str,
        job_id: str,
        final_status: str,
        subject: str,
        body_plain: str,
        body_html: str,
        recipient: Optional[str] = None,
        date_str: Optional[str] = None,
        force: bool = False,
    ) -> Dict:
        """
        Dispatches end-of-cycle summary notification with strict idempotency,
        transactional claim locking, persistent DB ledger, and desktop notification fallback.
        """
        recip = recipient or self.get_recipient()
        cur_date = date_str or datetime.now(IST_TZ).strftime("%Y-%m-%d")
        idempotency_key = (
            f"daily_digest_{cur_date}"
            if final_status == "DIGEST"
            else f"cycle_report_{cur_date}_{cycle_id}_{final_status}"
        )

        if final_status in (STATUS_SUCCESS, STATUS_RECOVERED_SUCCESS, "COMPLETED"):
            notification_type = "PRODUCTION_COMPLETED"
        elif final_status in (
            STATUS_FAILED,
            STATUS_QC_REJECTED,
            STATUS_MISSED_DEADLINE,
            STATUS_PUBLISHING_FAILED,
        ):
            notification_type = "PRODUCTION_FAILED"
        elif final_status == "DIGEST":
            notification_type = "DAILY_SUMMARY"
        else:
            notification_type = "CYCLE_SUMMARY"

        # 1. Atomic Idempotency Check & Transactional DISPATCHING Claim
        with SessionLocal() as db_session:
            claimed_record, suppressed = self._claim_notification_slot(
                db_session,
                idempotency_key=idempotency_key,
                job_id=job_id,
                cycle_id=cycle_id,
                notification_type=notification_type,
                recipient=recip,
                subject=subject,
                body_plain=body_plain,
                body_html=body_html,
                force=force,
            )
            if suppressed is not None:
                return suppressed
            notif_id = claimed_record.id

        # 2. Attempt SMTP Dispatch (classification distinguishes CONFIRMED_SENT vs FAILED_RETRYABLE vs DELIVERY_UNKNOWN)
        if self.is_smtp_configured():
            outcome = self._execute_smtp_with_state(
                subject=subject,
                body_text=body_plain,
                body_html=body_html,
                recipient=recip,
                max_retries=2,
            )
        else:
            outcome = {
                "email_sent": False,
                "delivery_state": "FAILED_RETRYABLE",
                "error_category": "SMTP_NOT_CONFIGURED",
                "last_error": "SMTP credentials not configured",
                "provider_response": None,
            }
            logger.info("[EMAIL_MONITOR] SMTP not configured; proceeding to desktop fallback.")

        email_sent = bool(outcome["email_sent"])
        delivery_state = outcome["delivery_state"]
        last_err = outcome.get("last_error")

        # 3. Update Persistent Notification Ledger
        with SessionLocal() as db_session:
            record = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.id == notif_id
            ).first()
            if record:
                record.attempt_count = (record.attempt_count or 0) + 1
                record.delivery_state = delivery_state
                record.error_category = outcome.get("error_category")
                record.provider_response = outcome.get("provider_response")
                record.updated_at = datetime.utcnow()
                if email_sent:
                    record.status = "SENT"
                    record.sent_at = datetime.utcnow()
                    record.last_error = None
                elif delivery_state == "DELIVERY_UNKNOWN":
                    record.status = "DELIVERY_UNKNOWN"
                    record.last_error = last_err
                else:
                    record.status = "FAILED"
                    record.last_error = last_err
                db_session.commit()

        # Mirror into legacy delivery ledger when CONFIRMED_SENT or DELIVERY_UNKNOWN
        if job_id and final_status in (STATUS_SUCCESS, STATUS_RECOVERED_SUCCESS, "COMPLETED"):
            try:
                notification_service.record_event_delivered(
                    f"SUCCESS:{job_id}",
                    job_id=job_id,
                    event_type="SUCCESS",
                    title=subject[:250],
                    channels_sent=1 if email_sent else 0,
                    delivery_state=delivery_state,
                    error_category=outcome.get("error_category") or "",
                )
            except Exception as mirror_exc:
                logger.debug("Legacy ledger mirror skipped: %s", mirror_exc)

        # 4. Fallback Desktop Notification (Always triggers if email not sent or for critical alerts)
        is_err = final_status in (STATUS_FAILED, STATUS_QC_REJECTED, STATUS_MISSED_DEADLINE)
        desktop_msg = f"Cycle {cycle_id} finished with status {final_status}."
        if delivery_state == "DELIVERY_UNKNOWN":
            desktop_msg += " (Email SMTP timed out during send — status DELIVERY_UNKNOWN; verify inbox manually)"
        elif not email_sent:
            desktop_msg += " (Email pending/failed - recorded in studio.db)"
        notification_service.notify_desktop(subject[:60], desktop_msg, is_error=is_err)

        if email_sent:
            ret_status = "SENT"
        elif delivery_state == "DELIVERY_UNKNOWN":
            ret_status = "DELIVERY_UNKNOWN"
        else:
            ret_status = "SAVED_LOCAL_PENDING"

        return {
            "status": ret_status,
            "delivery_state": delivery_state,
            "email_sent": email_sent,
            "notification_id": notif_id,
            "idempotency_key": idempotency_key,
            "recipient": recip,
            "last_error": last_err,
        }

    # -------------------------------------------------------------------------
    # 5. High-Level Cycle Dispatch Methods
    # -------------------------------------------------------------------------

    def send_cycle_summary_report(
        self,
        cycle_id: str,
        job_id: str,
        result: Dict,
        test_now_ist: Optional[datetime] = None,
    ) -> Dict:
        """
        Main entry point called by the scheduler when a production cycle reaches a terminal outcome.
        Builds the email from the run result plus persisted facts (job row, checkpoint, publishing ledger,
        lifecycle events) and dispatches it through the deduplicated ledger. Rendering problems are logged
        and fall back to a minimal plain report; they never raise into the scheduler.
        """
        from backend.app.services.email_templates import data as Q
        from backend.app.services.email_templates import production as P

        now_ist = test_now_ist or datetime.now(IST_TZ)
        cur_date_str = now_ist.strftime("%Y-%m-%d")
        res = dict(result or {})
        res_status = str(res.get("status") or STATUS_FAILED)
        succeeded = res_status.upper() in (STATUS_SUCCESS, "COMPLETED", STATUS_RECOVERED_SUCCESS)

        profile = get_active_profile()
        cycle_obj = next((c for c in profile.cycles if c.cycle_id == cycle_id), None)
        c_num: Optional[int] = None
        if cycle_obj:
            c_num = next(i for i, c in enumerate(profile.cycles, start=1) if c.cycle_id == cycle_id)

        if succeeded:
            terminal_status = STATUS_SUCCESS
        else:
            terminal_status = STATUS_FAILED
            err_l = str(res.get("error", "")).lower()
            if res_status.upper() == "MISSED" or res.get("error") == "DEADLINE_EXCEEDED":
                terminal_status = STATUS_MISSED_DEADLINE
            elif "qc" in err_l or res_status.upper() in ("QC_REJECTED", "BLOCKED_QC"):
                terminal_status = STATUS_QC_REJECTED

        try:
            with SessionLocal() as db_session:
                job = db_session.query(JobModel).filter(JobModel.id == job_id).first()
                cp = Q.checkpoint(job) if job else {}
                cs = cp.get("cycle_state") or {}
                topic = res.get("topic") or cp.get("topic") or (job.topic_title if job else None)
                ctx = P.ProductionEmailContext(
                    outcome=terminal_status,
                    cycle_label=("Cycle %d" % c_num) if c_num else "Manual production",
                    cycle_number=c_num,
                    cycle_id=cycle_id if c_num else None,
                    job_id=job_id,
                    scheduled_start=cs.get("production_start_at"),
                    topic=topic,
                    video_title=res.get("video_title") or cp.get("video_title"),
                    category=cp.get("category"),
                    generated_at=datetime.now(timezone.utc),
                )
                ctx.recovery = Q.recovery_info(db_session, job, cp, succeeded=succeeded,
                                               decision=res.get("retry") if isinstance(res.get("retry"), dict) else None)
                ctx.youtube = Q.youtube_state(db_session, job, cp, res.get("publishing_status"))
                ctx.target_publication = cs.get("target_publication_at_utc") or res.get("target_pub_timestamp")
                if succeeded:
                    ctx.video_duration_sec = _float_or_none(res.get("duration_sec") or cp.get("duration_sec"))
                    ctx.production_elapsed_sec = _float_or_none(res.get("total_duration_sec") or res.get("elapsed_sec")
                                                                or cp.get("total_duration_sec"))
                    ctx.started_at = (job.actual_start_at if job and job.actual_start_at else None) or res.get("start_time") or cp.get("start_time")
                    ctx.completed_at = (job.completed_at if job and job.completed_at else None) or res.get("completion_time") or cp.get("completion_time")
                    ctx.thumbnail_score = _float_or_none(res.get("thumbnail_score") or cp.get("thumbnail_score"))
                    ctx.qc_checks = res.get("qc_checks") if isinstance(res.get("qc_checks"), dict) else None
                    ctx.qc_gate_passed = True  # a COMPLETED run has passed the QC gate (QC_PASSED lifecycle event)
                    ctx.stage_timeline = Q.stage_timeline(db_session, job_id)
                    ctx.artifacts = [(label, Q.relative_path(res.get(key) or cp.get(key))) for label, key in (
                        ("Video", "video_path"), ("Thumbnail", "thumbnail_path"),
                        ("Attribution manifest", "attribution_manifest_path")) if (res.get(key) or cp.get(key))]
                    t_b64 = _encode_thumbnail_for_email(res.get("thumbnail_path") or cp.get("thumbnail_path") or "")
                    if t_b64:
                        ctx.thumbnail_data_uri = "data:image/jpeg;base64," + t_b64
                else:
                    ctx.failed_stage = res.get("failed_stage") or res.get("current_stage") or (job.current_stage if job else None)
                    ctx.last_completed_stage = res.get("last_completed_stage")
                    ctx.error_message = res.get("error") or (job.error_details if job else None)
                    # Real failure time: the scheduler passes the time it evaluated the cycle, not when it failed.
                    ctx.error_at = (job.updated_at if job and job.updated_at else None) or datetime.now(timezone.utc)
                    ctx.recommended_action = res.get("recommended_action")
                    ctx.unapproved_artifacts = list(res.get("unapproved_artifacts") or [])
                    if "checkpoint_saved" in res:
                        ctx.can_resume = bool(res.get("checkpoint_saved"))
            dashboard = self.generate_daily_dashboard(
                date_str=cur_date_str, test_now_ist=now_ist,
                current_job_overlay={"cycle_id": cycle_id, "job_id": job_id, "status": res_status, "topic": ctx.topic,
                                     "total_duration_sec": ctx.production_elapsed_sec},
            )
            ctx.day = self._day_progress(dashboard)
            if succeeded:
                subject, plain_body, html_body = self.build_success_email(None, None, None, None, None, context=ctx)
            else:
                subject, plain_body, html_body = self.build_failure_email(None, None, None, terminal_status, context=ctx)
        except Exception as exc:
            # Never lose the notification because of a rendering/data problem: send a minimal, honest report.
            logger.exception("[EMAIL_MONITOR] Report rendering failed for %s (%s); sending minimal report.", job_id, type(exc).__name__)
            label = ("Cycle %d" % c_num) if c_num else "Manual production"
            subject = "CrazzyDocs | %s | %s | %s" % (label, terminal_status, res.get("topic") or job_id)
            plain_body = ("CrazzyDocs production report (minimal: the full report could not be rendered).\n\n"
                          "Cycle: %s\nJob ID: %s\nOutcome: %s\nError: %s\n" % (
                              label, job_id, terminal_status, redact_sensitive_string(str(res.get("error") or "")) or "none"))
            html_body = ""

        return self.dispatch_summary_notification(
            cycle_id=cycle_id,
            job_id=job_id,
            final_status=terminal_status,
            subject=subject,
            body_plain=plain_body,
            body_html=html_body,
            date_str=cur_date_str,
        )

    # -------------------------------------------------------------------------
    # 6. Retry & Recovery for Pending / Unsent Notifications
    # -------------------------------------------------------------------------

    def retry_pending_notifications(self) -> Dict:
        """
        Retries only genuinely unsent or retryable notifications from SQLite.
        Never automatically resends CONFIRMED_SENT, DISPATCHING, or DELIVERY_UNKNOWN rows.
        """
        if not self.is_smtp_configured():
            return {"status": "SKIPPED", "reason": "SMTP_NOT_CONFIGURED", "retried": 0}

        retried = 0
        succeeded = 0
        skipped_unknown = 0
        with SessionLocal() as db_session:
            self._ensure_ledger_schema(db_session)
            candidates = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.status.in_(["PENDING", "FAILED", "DELIVERY_UNKNOWN"])
            ).all()
            candidate_ids = []
            for notif in candidates:
                d_state = (getattr(notif, "delivery_state", None) or "").upper()
                if notif.status == "DELIVERY_UNKNOWN" or d_state in ("DELIVERY_UNKNOWN", "CONFIRMED_SENT", "DISPATCHING"):
                    if d_state == "DELIVERY_UNKNOWN" or notif.status == "DELIVERY_UNKNOWN":
                        skipped_unknown += 1
                    continue
                candidate_ids.append(notif.id)

        for nid in candidate_ids:
            with SessionLocal() as db_session:
                claim_res = db_session.execute(
                    text(
                        "UPDATE persistent_notifications "
                        "SET status='DISPATCHING', delivery_state='DISPATCHING', updated_at=:now "
                        "WHERE id=:nid AND status IN ('PENDING', 'FAILED') "
                        "AND COALESCE(delivery_state, 'PENDING') IN ('PENDING', 'FAILED_RETRYABLE', '')"
                    ),
                    {"now": datetime.utcnow(), "nid": nid},
                )
                db_session.commit()
                if getattr(claim_res, "rowcount", 0) == 0:
                    continue
                notif = db_session.query(PersistentNotificationModel).filter(
                    PersistentNotificationModel.id == nid
                ).first()
                if not notif:
                    continue
                subj, b_plain, b_html, recip = notif.subject, notif.body_plain, notif.body_html, notif.recipient

            retried += 1
            outcome = self._execute_smtp_with_state(
                subject=subj,
                body_text=b_plain,
                body_html=b_html or "",
                recipient=recip,
                max_retries=1,
            )
            email_sent = bool(outcome["email_sent"])
            delivery_state = outcome["delivery_state"]
            with SessionLocal() as db_session:
                notif = db_session.query(PersistentNotificationModel).filter(
                    PersistentNotificationModel.id == nid
                ).first()
                if notif:
                    notif.attempt_count = (notif.attempt_count or 0) + 1
                    notif.delivery_state = delivery_state
                    notif.error_category = outcome.get("error_category")
                    notif.provider_response = outcome.get("provider_response")
                    notif.updated_at = datetime.utcnow()
                    if email_sent:
                        notif.status = "SENT"
                        notif.sent_at = datetime.utcnow()
                        notif.last_error = None
                        succeeded += 1
                    elif delivery_state == "DELIVERY_UNKNOWN":
                        notif.status = "DELIVERY_UNKNOWN"
                        notif.last_error = outcome.get("last_error")
                    else:
                        notif.status = "FAILED"
                        notif.last_error = outcome.get("last_error")
                    db_session.commit()

        return {
            "status": "COMPLETED",
            "retried": retried,
            "succeeded": succeeded,
            "skipped_delivery_unknown": skipped_unknown,
        }

    # -------------------------------------------------------------------------
    # 7. Optional Daily Consolidated Digest
    # -------------------------------------------------------------------------

    def send_daily_digest(
        self,
        date_str: Optional[str] = None,
        force: bool = False,
    ) -> Dict:
        """
        Dispatches optional consolidated daily digest summarizing all 4 cycles.
        Only executed if CRAZZYDOCS_ENABLE_DAILY_DIGEST is True or force=True.
        """
        enable_digest = os.environ.get("CRAZZYDOCS_ENABLE_DAILY_DIGEST", "false").lower() in ("true", "1", "yes")
        if not enable_digest and not force:
            return {"status": "SKIPPED", "reason": "DIGEST_DISABLED_IN_CONFIG"}

        now_ist = datetime.now(IST_TZ)
        cur_date = date_str or now_ist.strftime("%Y-%m-%d")
        idempotency_key = f"daily_digest_{cur_date}"

        with SessionLocal() as db_session:
            self._ensure_ledger_schema(db_session)
            existing = db_session.query(PersistentNotificationModel).filter(
                PersistentNotificationModel.idempotency_key == idempotency_key
            ).first()
            if existing and not force:
                d_state = (getattr(existing, "delivery_state", None) or "").upper()
                if existing.status == "SENT" or d_state == "CONFIRMED_SENT":
                    return {"status": "DUPLICATE_SUPPRESSED", "idempotency_key": idempotency_key}
                if existing.status == "DELIVERY_UNKNOWN" or d_state == "DELIVERY_UNKNOWN":
                    return {"status": "DELIVERY_UNKNOWN_SUPPRESSED", "idempotency_key": idempotency_key}

        from backend.app.services.email_templates import report as R
        with SessionLocal() as db_session:
            rep = R.compile_report(db_session, cur_date, profile=get_active_profile())
        rendered = R.render(rep)
        subject = f"CrazzyDocs | Daily Production Digest | {cur_date}"
        plain_body, html_body = rendered.text, rendered.html

        return self.dispatch_summary_notification(
            cycle_id="daily_digest",
            job_id=f"digest_{cur_date}",
            final_status="DIGEST",
            subject=subject,
            body_plain=plain_body,
            body_html=html_body,
            date_str=cur_date,
        )


email_monitoring_service = EmailMonitoringService()
