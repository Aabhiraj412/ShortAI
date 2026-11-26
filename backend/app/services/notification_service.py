"""
Notification and Alerting Service for CrazzyDocs YouTube Shorts Studio.
Phase 5, Stage 3.6 Production Standard.

Provides unified failure and success alerting across:
1. Windows Desktop Notifications (Native WinRT Toast / NotifyIcon via PowerShell).
2. SMTP Email Alerts (Configured securely via environment variables, with transient retry).
3. Local Alert Audit Log (JSONL format with event deduplication).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
import os
import base64
import hashlib
import re
import smtplib
import socket
import sqlite3
import subprocess
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core.execution_context import ExecutionContext
from backend.app.core.secure_storage import protect_text, unprotect_text

logger = logging.getLogger(__name__)

from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT
ALERT_LOG_PATH = os.path.join(WORKSPACE_ROOT, "logs", "notifications.jsonl")


@dataclass
class AlertPayload:
    event_type: str  # "FAILURE", "SUCCESS", "WARNING"
    stage: str       # "research", "script", "tts", "rendering", "compositing", "thumbnail", "qc", "publish"
    title: str
    message: str
    job_id: str = "showcase_job"
    details: Dict = field(default_factory=dict)
    log_path: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    delivery_status: Dict[str, bool] = field(default_factory=dict)


def _load_local_env():
    """Loads .env.local if present (without overriding existing environment variables)."""
    env_local = os.path.join(WORKSPACE_ROOT, ".env.local")
    if os.path.exists(env_local):
        try:
            with open(env_local, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_local_env()


_DATA_IMG = re.compile(r'src="data:(image/(?:png|jpeg|gif));base64,([A-Za-z0-9+/=\s]+)"')


def build_mime_message(subject: str, sender: str, recipient: str, body_text: str, body_html: Optional[str]) -> MIMEMultipart:
    """multipart/alternative (text + HTML). Inline ``data:`` images in the HTML become CID parts in a
    multipart/related container, because Gmail and Outlook do not display data: URIs. The stored HTML keeps
    the data: URIs (so retries and previews are self-contained); conversion happens only here, at send time."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
    if not body_html:
        return msg
    images: Dict[str, Tuple[str, bytes]] = {}

    def _swap(m: "re.Match") -> str:
        mime, b64 = m.group(1), re.sub(r"\s+", "", m.group(2))
        key = hashlib.sha1(b64.encode("ascii")).hexdigest()[:16]
        if key not in images:
            try:
                images[key] = (mime, base64.b64decode(b64, validate=False))
            except Exception:
                return m.group(0)
        return 'src="cid:img-%s@crazzydocs"' % key

    html = _DATA_IMG.sub(_swap, body_html)
    if not images:
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        return msg
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html", "utf-8"))
    for key, (mime, data) in images.items():
        part = MIMEImage(data, _subtype=mime.split("/", 1)[1])
        part.add_header("Content-ID", "<img-%s@crazzydocs>" % key)
        part.add_header("Content-Disposition", "inline", filename="%s.%s" % (key, "jpg" if mime.endswith("jpeg") else mime.split("/")[1]))
        related.attach(part)
    msg.attach(related)
    return msg


class NotificationManager:
    """Manages multi-channel alerts with deduplication, transient retry, and fallbacks."""

    def __init__(
        self,
        alert_log_path: str = ALERT_LOG_PATH,
        cooldown_seconds: float = 30.0,
        db_path: Optional[str] = None,
    ):
        self.alert_log_path = alert_log_path
        self.cooldown_seconds = cooldown_seconds
        self.db_path = db_path or str(__import__("backend.app.core.paths", fromlist=["DATABASE_PATH"]).DATABASE_PATH)
        self._recent_events: Dict[str, float] = {}
        self.last_email_receipt: Dict[str, Any] = {}
        os.makedirs(os.path.dirname(self.alert_log_path), exist_ok=True)
        self._init_notification_db()

    def _init_notification_db(self):
        """Initializes persistent notification delivery ledger for durable idempotency."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notification_delivery_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    delivered_at TEXT NOT NULL
                )
            """)
            cur.execute("PRAGMA table_info(notification_delivery_ledger)")
            cols = {row[1] for row in cur.fetchall()}
            for col_name, col_type in (
                ("delivery_state", "TEXT DEFAULT 'CONFIRMED_SENT'"),
                ("error_category", "TEXT"),
            ):
                if col_name not in cols:
                    cur.execute(f"ALTER TABLE notification_delivery_ledger ADD COLUMN {col_name} {col_type}")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to init notification_delivery_ledger: %s", e)

    def is_event_delivered(self, event_key: str) -> bool:
        """Checks if the event was already confirmed or has unknown in-flight/post-DATA outcome."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(delivery_state, 'CONFIRMED_SENT') FROM notification_delivery_ledger WHERE event_key = ?",
                (event_key,),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return False
            return row[0] in ("CONFIRMED_SENT", "DELIVERY_UNKNOWN", "DISPATCHING")
        except Exception:
            return False

    def record_event_delivered(
        self,
        event_key: str,
        event_type: str,
        job_id: str,
        recipient: str,
        delivery_state: str = "CONFIRMED_SENT",
        error_category: Optional[str] = None,
    ):
        """Records a notification outcome in the persistent ledger without overwriting confirmed records."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO notification_delivery_ledger (
                    event_key, event_type, job_id, recipient, delivered_at, delivery_state, error_category
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    delivered_at = CASE
                        WHEN COALESCE(notification_delivery_ledger.delivery_state, 'CONFIRMED_SENT') IN ('CONFIRMED_SENT', 'DELIVERY_UNKNOWN')
                        THEN notification_delivery_ledger.delivered_at
                        ELSE excluded.delivered_at
                    END,
                    delivery_state = CASE
                        WHEN COALESCE(notification_delivery_ledger.delivery_state, 'CONFIRMED_SENT') = 'CONFIRMED_SENT'
                        THEN 'CONFIRMED_SENT'
                        WHEN notification_delivery_ledger.delivery_state = 'DELIVERY_UNKNOWN'
                        THEN 'DELIVERY_UNKNOWN'
                        ELSE excluded.delivery_state
                    END,
                    error_category = excluded.error_category
            """, (
                event_key,
                event_type,
                job_id,
                recipient,
                datetime.utcnow().isoformat() + "Z",
                delivery_state,
                error_category,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Failed to record delivered notification: %s", e)

    def is_email_configured(self) -> bool:
        """Checks whether SMTP credentials and recipient are present."""
        config = self._smtp_configuration()
        return bool(
            not config.get("configuration_error")
            and config["host"] and config["username"] and config["password"] and config["recipient"]
        )

    def _smtp_configuration(self) -> Dict[str, str]:
        config = {
            "host": os.environ.get("CRAZZYDOCS_SMTP_HOST") or os.environ.get("SMTP_HOST") or "",
            "port": os.environ.get("CRAZZYDOCS_SMTP_PORT") or os.environ.get("SMTP_PORT") or "587",
            "username": os.environ.get("CRAZZYDOCS_SMTP_USER") or os.environ.get("SMTP_USER") or "",
            "password": os.environ.get("CRAZZYDOCS_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "",
            "recipient": os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT") or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL") or "",
            "sender": os.environ.get("CRAZZYDOCS_SMTP_SENDER") or "",
            "security": os.environ.get("CRAZZYDOCS_SMTP_SECURITY", "STARTTLS").upper(),
        }
        try:
            from backend.app.database.db import SessionLocal, StudioSettingModel
            with SessionLocal() as db:
                stored = {
                    item.key: item.value
                    for item in db.query(StudioSettingModel).filter(
                        StudioSettingModel.key.like("stage322_smtp_%")
                    ).all()
                }
            mapping = {
                "stage322_smtp_host": "host",
                "stage322_smtp_port": "port",
                "stage322_smtp_username": "username",
                "stage322_smtp_recipient": "recipient",
                "stage322_smtp_sender": "sender",
                "stage322_smtp_security": "security",
            }
            for key, target in mapping.items():
                if key in stored:
                    config[target] = stored[key]
            if "stage322_smtp_password_protected" in stored:
                config["password"] = unprotect_text(stored["stage322_smtp_password_protected"])
        except Exception:
            logger.exception("Could not load persisted SMTP configuration.")
            config["configuration_error"] = "Persisted SMTP configuration could not be loaded."
        return config

    def get_public_email_configuration(self) -> Dict:
        config = self._smtp_configuration()
        return {
            "provider": "smtp",
            "configured": bool(config["host"] and config["username"] and config["password"] and config["recipient"]),
            "host": config["host"] or None,
            "port": int(config["port"]),
            "security": config["security"],
            "sender": config["sender"] or config["username"] or None,
            "recipient": config["recipient"] or None,
            "password_configured": bool(config["password"]),
            "configuration_error": config.get("configuration_error"),
        }

    def save_email_configuration(self, config: Dict[str, Optional[str]]) -> Dict:
        from backend.app.database.db import SessionLocal, StudioSettingModel
        values = {
            "stage322_smtp_host": config.get("host"),
            "stage322_smtp_port": str(config["port"]) if config.get("port") else None,
            "stage322_smtp_username": config.get("username"),
            "stage322_smtp_recipient": config.get("recipient"),
            "stage322_smtp_sender": config.get("sender"),
            "stage322_smtp_security": config.get("security"),
        }
        password = config.get("password")
        if password:
            values["stage322_smtp_password_protected"] = protect_text(password)
        with SessionLocal() as db:
            for key, value in values.items():
                if value is None:
                    continue
                setting = db.query(StudioSettingModel).filter(StudioSettingModel.key == key).first()
                if setting:
                    setting.value = value
                else:
                    db.add(StudioSettingModel(key=key, value=value))
            db.commit()
        return self.get_public_email_configuration()

    def _is_duplicate(self, key: str) -> bool:
        """Checks if identical alert was sent recently within cooldown."""
        now = time.time()
        last_time = self._recent_events.get(key, 0.0)
        if now - last_time < self.cooldown_seconds:
            return True
        self._recent_events[key] = now
        return False

    def notify_desktop(self, title: str, message: str, is_error: bool = False) -> bool:
        """Sends a Windows native desktop notification via PowerShell."""
        clean_title = title.replace("'", "''").replace('"', '`"')
        clean_msg = message.replace("'", "''").replace('"', '`"').replace("\n", " ")
        icon_type = "Error" if is_error else "Info"

        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = "{clean_title}"
$notify.BalloonTipText = "{clean_msg}"
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{icon_type}
$notify.Visible = $true
$notify.ShowBalloonTip(5000)
Start-Sleep -Milliseconds 600
$notify.Dispose()
"""
        try:
            from backend.app.core.process_utils import hidden_subprocess_kwargs
            res = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=5,
                **hidden_subprocess_kwargs(),
            )
            return res.returncode == 0
        except Exception as e:
            logger.warning("Desktop notification failed: %s", e)
            return False

    def send_email_with_receipt(
        self,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        recipient: Optional[str] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """
        Sends email via SMTP while tracking exact transaction phase:
        - Pre-DATA failures (connect/STARTTLS/auth): safe transient retry -> FAILED_RETRYABLE
        - SEND_DATA timeouts/disconnects after DATA starts: NO blind retry -> DELIVERY_UNKNOWN
        - Confirmed delivery only when send_message() completes with zero refusals -> CONFIRMED_SENT
        """
        config = self._smtp_configuration()
        if config.get("configuration_error"):
            receipt = {
                "confirmed": False,
                "delivery_state": "FAILED_RETRYABLE",
                "error_category": "NOT_CONFIGURED",
                "safe_to_retry": True,
                "last_error": "Persisted SMTP configuration is unavailable.",
                "provider_response": None,
            }
            self.last_email_receipt = receipt
            logger.error("SMTP notification skipped because persisted configuration is unavailable.")
            return receipt

        smtp_host = config["host"]
        smtp_port = int(config["port"])
        smtp_user = config["username"]
        smtp_pass = config["password"]
        to_email = recipient or config["recipient"]
        sender_addr = config["sender"] or smtp_user
        security = config["security"].upper()

        if not all([smtp_host, smtp_user, smtp_pass, to_email]):
            receipt = {
                "confirmed": False,
                "delivery_state": "FAILED_RETRYABLE",
                "error_category": "NOT_CONFIGURED",
                "safe_to_retry": True,
                "last_error": "SMTP credentials not fully configured.",
                "provider_response": None,
            }
            self.last_email_receipt = receipt
            logger.debug("SMTP credentials not fully configured; skipping email dispatch.")
            return receipt

        msg = build_mime_message(subject, sender_addr, to_email, body_text, body_html)

        last_err = ""
        error_category = "CONNECTION_ERROR"
        for attempt in range(1, max_retries + 2):
            phase = "CONNECT_AUTH"
            try:
                if security == "SSL" or smtp_port == 465:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=25) as server:
                        server.login(smtp_user, smtp_pass)
                        phase = "SEND_DATA"
                        refused = server.send_message(msg)
                else:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=25) as server:
                        server.ehlo()
                        if security == "STARTTLS" or smtp_port == 587:
                            server.starttls()
                            server.ehlo()
                        server.login(smtp_user, smtp_pass)
                        phase = "SEND_DATA"
                        refused = server.send_message(msg)
                if isinstance(refused, dict) and len(refused) > 0:
                    receipt = {
                        "confirmed": False,
                        "delivery_state": "FAILED_RETRYABLE",
                        "error_category": "SMTP_REJECTED",
                        "safe_to_retry": True,
                        "last_error": "SMTP server refused one or more recipients.",
                        "provider_response": f"refused_count={len(refused)}",
                    }
                    self.last_email_receipt = receipt
                    return receipt

                receipt = {
                    "confirmed": True,
                    "delivery_state": "CONFIRMED_SENT",
                    "error_category": None,
                    "safe_to_retry": False,
                    "last_error": None,
                    "provider_response": "SMTP send_message accepted",
                }
                self.last_email_receipt = receipt
                logger.info("Email notification confirmed delivered to %s", to_email)
                return receipt
            except (TimeoutError, socket.timeout, smtplib.SMTPServerDisconnected, BrokenPipeError, ConnectionResetError, OSError) as e:
                last_err = f"{type(e).__name__} during {phase}"
                if phase == "SEND_DATA":
                    # Message bytes may already have been queued by the SMTP server; never blindly retry.
                    receipt = {
                        "confirmed": False,
                        "delivery_state": "DELIVERY_UNKNOWN",
                        "error_category": "TIMEOUT_UNKNOWN",
                        "safe_to_retry": False,
                        "last_error": last_err,
                        "provider_response": f"Ambiguous SMTP result during SEND_DATA ({type(e).__name__})",
                    }
                    self.last_email_receipt = receipt
                    logger.error("SMTP outcome unknown after SEND_DATA (%s); blocking blind retry.", type(e).__name__)
                    return receipt
                error_category = "CONNECTION_ERROR"
                logger.warning("SMTP attempt %d/%d failed before DATA (%s).", attempt, max_retries + 1, type(e).__name__)
                if attempt <= max_retries:
                    time.sleep(min(2 ** (attempt - 1), 30))
            except smtplib.SMTPAuthenticationError as e:
                receipt = {
                    "confirmed": False,
                    "delivery_state": "FAILED_RETRYABLE",
                    "error_category": "AUTH_ERROR",
                    "safe_to_retry": False,
                    "last_error": f"SMTPAuthenticationError ({getattr(e, 'smtp_code', 535)})",
                    "provider_response": None,
                }
                self.last_email_receipt = receipt
                logger.error("SMTP authentication failed (%s).", getattr(e, "smtp_code", "unknown"))
                return receipt
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as e:
                code = int(getattr(e, "smtp_code", 550) or 550)
                transient = 400 <= code < 500
                last_err = f"{type(e).__name__} ({code})"
                error_category = "SMTP_REJECTED"
                logger.warning("SMTP rejected message (%s).", last_err)
                if transient and attempt <= max_retries:
                    time.sleep(min(2 ** (attempt - 1), 30))
                    continue
                receipt = {
                    "confirmed": False,
                    "delivery_state": "FAILED_RETRYABLE",
                    "error_category": "SMTP_REJECTED",
                    "safe_to_retry": transient,
                    "last_error": last_err,
                    "provider_response": f"smtp_code={code}",
                }
                self.last_email_receipt = receipt
                return receipt
            except Exception as e:
                last_err = f"{type(e).__name__} during {phase}"
                if phase == "SEND_DATA":
                    receipt = {
                        "confirmed": False,
                        "delivery_state": "DELIVERY_UNKNOWN",
                        "error_category": "TIMEOUT_UNKNOWN",
                        "safe_to_retry": False,
                        "last_error": last_err,
                        "provider_response": f"Ambiguous SMTP error during SEND_DATA ({type(e).__name__})",
                    }
                    self.last_email_receipt = receipt
                    return receipt
                logger.warning("SMTP attempt %d/%d failed (%s).", attempt, max_retries + 1, type(e).__name__)
                if attempt <= max_retries:
                    time.sleep(min(2 ** (attempt - 1), 30))

        receipt = {
            "confirmed": False,
            "delivery_state": "FAILED_RETRYABLE",
            "error_category": error_category,
            "safe_to_retry": True,
            "last_error": last_err or "SMTP delivery failed",
            "provider_response": None,
        }
        self.last_email_receipt = receipt
        logger.error("Email delivery failed after %d attempts (%s)", max_retries + 1, error_category)
        return receipt

    def notify_email(
        self,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        recipient: Optional[str] = None,
        max_retries: int = 2,
    ) -> bool:
        """Sends email alert via SMTP with phase-aware retry and delivery receipt tracking."""
        receipt = self.send_email_with_receipt(
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            recipient=recipient,
            max_retries=max_retries,
        )
        return bool(receipt.get("confirmed"))

    def record_audit_log(self, alert: AlertPayload):
        """Appends alert payload to persistent JSONL log."""
        try:
            with open(self.alert_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(alert)) + "\n")
        except Exception as e:
            logger.error("Failed to write alert log: %s", e)

    def send_success(
        self,
        video_title: str,
        topic: str,
        storytelling_style: str,
        duration_sec: float,
        video_path: str,
        thumbnail_path: str,
        qc_status: str = "PASSED",
        production_timestamp: Optional[str] = None,
        job_id: str = "showcase_job",
        details: Optional[Dict] = None,
        execution_context: ExecutionContext = ExecutionContext.PRODUCTION,
    ) -> Dict[str, bool]:
        """Dispatches formatted success alert with all required Stage 3.6 fields."""
        if isinstance(execution_context, str):
            execution_context = ExecutionContext.from_string(execution_context)

        # STRICT ISOLATION: Tests and non-production contexts must NEVER send production emails
        if execution_context != ExecutionContext.PRODUCTION or job_id.startswith("test_") or job_id.startswith("mock_"):
            logger.info(
                "[NOTIFICATION SUPPRESSED] Non-production or test context (context=%s, job_id=%s). Suppressing success email.",
                execution_context.value if hasattr(execution_context, 'value') else execution_context,
                job_id,
            )
            return {"desktop": False, "email": False, "deduplicated": False, "suppressed_test_mode": True}

        # Persistent database-backed idempotency
        persistent_event_key = f"SUCCESS:{job_id}"
        if self.is_event_delivered(persistent_event_key):
            logger.info("[NOTIFICATION DEDUP] Success notification for job %s already delivered persistently.", job_id)
            return {"desktop": False, "email": False, "deduplicated": True}

        dedup_key = f"SUCCESS:{topic}:{video_title}"
        if self._is_duplicate(dedup_key):
            return {"desktop": False, "email": False, "deduplicated": True}

        ts = production_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        alert_title = f"[CrazzyDocs Success] {video_title}"
        short_msg = f"Video produced: '{video_title}' ({duration_sec:.1f}s) | Style: {storytelling_style} | QC: {qc_status}"

        plain_body, html_body = _render_legacy_success(
            video_title=video_title, topic=topic, storytelling_style=storytelling_style, duration_sec=duration_sec,
            video_path=video_path, thumbnail_path=thumbnail_path, qc_status=qc_status, job_id=job_id, subject=alert_title)

        desktop_ok = self.notify_desktop(alert_title, short_msg, is_error=False)
        email_ok = self.notify_email(subject=alert_title, body_text=plain_body, body_html=html_body)

        if email_ok:
            recipient = os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT") or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL") or "default"
            self.record_event_delivered(persistent_event_key, "SUCCESS", job_id, recipient)

        alert = AlertPayload(
            event_type="SUCCESS",
            stage="production",
            title=alert_title,
            message=short_msg,
            job_id=job_id,
            details={
                "video_title": video_title,
                "topic": topic,
                "storytelling_style": storytelling_style,
                "duration_sec": duration_sec,
                "video_path": video_path,
                "thumbnail_path": thumbnail_path,
                "qc_status": qc_status,
                **(details or {})
            },
            delivery_status={"desktop": desktop_ok, "email": email_ok}
        )
        self.record_audit_log(alert)
        return {"desktop": desktop_ok, "email": email_ok, "deduplicated": False}

    def send_failure(
        self,
        topic: str = "Unknown Topic",
        stage: str = "Unknown Stage",
        error_summary: str = "",
        log_path: Optional[str] = None,
        retry_status: str = "Retried 2/2 times",
        recovery_status: str = "Execution Halted",
        manual_intervention_required: bool = True,
        job_id: str = "autonomous_job",
        details: Optional[Dict] = None,
        execution_context: ExecutionContext = ExecutionContext.PRODUCTION,
    ) -> Dict[str, bool]:
        """Dispatches formatted failure alert with all required Stage 3.6 fields."""
        if isinstance(execution_context, str):
            execution_context = ExecutionContext.from_string(execution_context)

        # STRICT ISOLATION: Tests and non-production contexts must NEVER send production failure alerts
        if execution_context != ExecutionContext.PRODUCTION or job_id.startswith("test_") or job_id.startswith("mock_"):
            logger.info(
                "[NOTIFICATION SUPPRESSED] Non-production or test context (context=%s, job_id=%s). Suppressing failure email.",
                execution_context.value if hasattr(execution_context, 'value') else execution_context,
                job_id,
            )
            return {"desktop": False, "email": False, "deduplicated": False, "suppressed_test_mode": True}

        persistent_event_key = f"FAIL:{job_id}:{stage}"
        if self.is_event_delivered(persistent_event_key):
            logger.info("[NOTIFICATION DEDUP] Failure alert for job %s stage %s already delivered persistently.", job_id, stage)
            return {"desktop": False, "email": False, "deduplicated": True}

        dedup_key = f"FAIL:{stage}:{error_summary[:40]}"
        if self._is_duplicate(dedup_key):
            logger.info("Suppressed duplicate failure alert for stage %s", stage)
            return {"desktop": False, "email": False, "deduplicated": True}

        title = f"[CrazzyDocs Alert] Production Failure: {stage.upper()}"
        short_msg = f"Failure at stage '{stage}': {error_summary}"
        if manual_intervention_required:
            short_msg += " (Manual Intervention Required)"

        plain_body, html_body = _render_legacy_failure(
            topic=topic, stage=stage, error_summary=error_summary, log_path=log_path, retry_status=retry_status,
            recovery_status=recovery_status, manual_intervention_required=manual_intervention_required,
            job_id=job_id, subject=title)

        desktop_ok = self.notify_desktop(title, short_msg, is_error=True)
        email_ok = self.notify_email(subject=title, body_text=plain_body, body_html=html_body)

        if email_ok:
            recipient = os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT") or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL") or "default"
            self.record_event_delivered(persistent_event_key, "FAILURE", job_id, recipient)

        alert = AlertPayload(
            event_type="FAILURE",
            stage=stage,
            title=title,
            message=short_msg,
            job_id=job_id,
            details={
                "topic": topic,
                "retry_status": retry_status,
                "recovery_status": recovery_status,
                "manual_intervention_required": manual_intervention_required,
                **(details or {})
            },
            log_path=log_path,
            delivery_status={"desktop": desktop_ok, "email": email_ok}
        )
        self.record_audit_log(alert)
        return {"desktop": desktop_ok, "email": email_ok, "deduplicated": False}


def _render_legacy_success(*, video_title, topic, storytelling_style, duration_sec, video_path, thumbnail_path,
                           qc_status, job_id, subject) -> Tuple[str, str]:
    """Manual/legacy success alert in the shared design (scheduled cycles use the cycle report instead)."""
    from backend.app.services.email_templates import production as P
    from backend.app.services.email_templates.data import relative_path
    ctx = P.ProductionEmailContext(outcome="SUCCESS", cycle_label="Manual production", job_id=job_id, topic=topic,
                                   video_title=video_title, video_duration_sec=duration_sec,
                                   qc_gate_passed=str(qc_status or "").upper().startswith("PASS") or None,
                                   extra_rows=[("Visual style", storytelling_style or None)],
                                   artifacts=[(l, relative_path(v)) for l, v in (("Video", video_path), ("Thumbnail", thumbnail_path)) if v])
    r = P.render_success(ctx)
    return r.text, r.html


def _render_legacy_failure(*, topic, stage, error_summary, log_path, retry_status, recovery_status,
                           manual_intervention_required, job_id, subject) -> Tuple[str, str]:
    """Manual/legacy failure alert (orchestrator runs outside the scheduler) in the shared design."""
    from backend.app.services.email_templates import production as P
    try:
        from backend.app.services.email_monitoring_service import redact_sensitive_string
        err = redact_sensitive_string(error_summary or "")
    except Exception:
        err = "(error text withheld)"
    ctx = P.ProductionEmailContext(outcome="FAILED", cycle_label="Manual production", job_id=job_id,
                                   topic=None if topic in (None, "", "Autonomous Generation") else topic,
                                   failed_stage=stage, error_message=err or None,
                                   extra_rows=[("Retry status", retry_status or None), ("Recovery status", recovery_status or None),
                                               ("Manual intervention", "Required" if manual_intervention_required else "Not required"),
                                               ("Log (studio PC)", log_path or None)])
    r = P.render_failure(ctx)
    return r.text, r.html


# Global singleton instance
notification_service = NotificationManager()
