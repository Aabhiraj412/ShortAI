"""
Stage 3.23.2 — Email Notification Integrity, Idempotency & Content Accuracy Tests.
All tests run against isolated temporary SQLite databases with mocked SMTP transport.
Zero real emails or uploads are ever triggered.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.database.db import (
    Base,
    JobModel,
    PersistentNotificationModel,
    ensure_notification_ledger_schema,
)
from backend.app.scheduler import reconciliation_service, scheduler as scheduler_module
from backend.app.scheduler.schedule_profiles import IST_TZ, PROFILE_FOUR_CYCLES_DAILY
from backend.app.services import daily_publishing_summary as daily_summary_module
from backend.app.services import email_monitoring_service as email_monitoring_module
from backend.app.services import notification_service as notification_module
from backend.app.services.daily_publishing_summary import DailyPublishingSummaryService
from backend.app.services.email_monitoring_service import EmailMonitoringService
from backend.app.services.notification_service import NotificationManager
from backend.app.services.production_health_dashboard import ProductionHealthDashboard


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    db_file = tmp_path / "stage_3_23_2_test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    ensure_notification_ledger_schema(engine)

    # Create publishing_ledger in the same isolated test DB for summary queries
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publishing_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_title TEXT,
            video_path TEXT,
            thumbnail_path TEXT,
            metadata_json TEXT,
            youtube_video_id TEXT,
            upload_status TEXT,
            visibility TEXT,
            video_hash TEXT,
            upload_timestamp TEXT,
            scheduled_timestamp TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)

    # Isolate all SQLAlchemy SessionLocal references
    monkeypatch.setattr(email_monitoring_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(daily_summary_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(reconciliation_service, "SessionLocal", test_sessions)
    monkeypatch.setattr(scheduler_module, "SessionLocal", test_sessions)
    monkeypatch.setattr(email_monitoring_module, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)
    monkeypatch.setattr(scheduler_module, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)
    monkeypatch.setattr(reconciliation_service, "get_active_profile", lambda: PROFILE_FOUR_CYCLES_DAILY)

    # Isolate singleton notification_service to temp DB and clean SMTP config
    monkeypatch.setattr(
        notification_module.notification_service,
        "db_path",
        str(db_file),
    )
    monkeypatch.setattr(
        notification_module.notification_service,
        "alert_log_path",
        str(tmp_path / "notifications.jsonl"),
    )
    notification_module.notification_service._recent_events.clear()
    notification_module.notification_service.last_email_receipt = {}
    notification_module.notification_service._init_notification_db()

    monkeypatch.setenv("CRAZZYDOCS_SMTP_HOST", "mock.smtp.crazzydocs.local")
    monkeypatch.setenv("CRAZZYDOCS_SMTP_PORT", "587")
    monkeypatch.setenv("CRAZZYDOCS_SMTP_USER", "sender@crazzydocs.local")
    monkeypatch.setenv("CRAZZYDOCS_SMTP_PASSWORD", "mock-secret")
    monkeypatch.setenv("CRAZZYDOCS_ALERT_RECIPIENT", "operator@crazzydocs.local")

    # Prevent real desktop notifications during tests
    monkeypatch.setattr(
        notification_module.notification_service,
        "notify_desktop",
        lambda *a, **kw: True,
    )

    yield {
        "tmp_path": tmp_path,
        "db_file": db_file,
        "SessionLocal": test_sessions,
        "engine": engine,
    }
    engine.dispose()


@pytest.mark.asyncio
async def test_1_single_production_completion_sends_exactly_one_canonical_email(isolated_env, monkeypatch):
    """Scheduler production completion produces ONE canonical summary email and never a second shortened email."""
    now_ist = datetime(2026, 11, 12, 6, 5, 0, tzinfo=IST_TZ)
    job_id = "cycle_2_20261112_0600"

    # Pretend orchestrator completes Cycle 2 (and triggers scheduler canonical summary only)
    sent_messages = []

    def fake_send_email_with_receipt(subject, body_text, body_html="", recipient=None, max_retries=2):
        sent_messages.append({"subject": subject, "plain": body_text, "html": body_html, "recipient": recipient})
        receipt = {
            "confirmed": True,
            "delivery_state": "CONFIRMED_SENT",
            "error_category": None,
            "safe_to_retry": False,
            "last_error": None,
            "provider_response": "250 2.0.0 OK",
        }
        notification_module.notification_service.last_email_receipt = receipt
        return receipt

    monkeypatch.setattr(
        notification_module.notification_service,
        "send_email_with_receipt",
        fake_send_email_with_receipt,
    )
    monkeypatch.setattr(
        scheduler_module.network_connectivity_service,
        "is_environment_ready_for_cycle",
        lambda *a, **kw: (True, "READY", {}),
    )
    monkeypatch.setattr(scheduler_module, "get_operating_mode", lambda: "PRODUCTION")
    monkeypatch.setattr(
        scheduler_module,
        "production_capacity",
        lambda excluding_job_id=None: {"available": True, "active_jobs": 0, "max_concurrency": 1},
    )

    def fake_pipeline(job_id: str):
        # Verify orchestrator email suppression ContextVar is active inside scheduler run
        from backend.app.orchestration.crazzydocs_master_orchestrator import suppress_orchestrator_completion_email

        assert suppress_orchestrator_completion_email.get() is True
        with isolated_env["SessionLocal"]() as db:
            row = db.query(JobModel).filter(JobModel.id == job_id).first()
            if row is None:
                row = JobModel(id=job_id, date_str="2026-11-12")
                db.add(row)
            row.topic_title = "Why Saturn's Rings Are Disappearing"
            row.status = "COMPLETED"
            row.current_stage = "COMPLETED"
            row.progress_pct = 100.0
            row.checkpoint_data_json = json.dumps({
                "topic": "Why Saturn's Rings Are Disappearing",
                "video_title": "Saturn's Vanishing Rings Explained",
                "total_duration_sec": 38.4,
            })
            row.completed_at = datetime.utcnow()
            db.commit()
        return {
            "status": "COMPLETED",
            "job_id": job_id,
            "topic": "Why Saturn's Rings Are Disappearing",
            "video_title": "Saturn's Vanishing Rings Explained",
            "duration_sec": 53.4,
            "total_duration_sec": 38.4,
            "video_path": "media/productions/test/final_short.mp4",
            "thumbnail_path": "media/productions/test/final_thumbnail.png",
        }

    monkeypatch.setattr(scheduler_module.master_orchestrator, "execute_production_pipeline", fake_pipeline)
    res = await scheduler_module.run_cycle_generation("cycle_2", test_ist_now=now_ist)

    assert res["status"] == "COMPLETED"
    assert len(sent_messages) == 1
    assert sent_messages[0]["subject"] == "CrazzyDocs | Cycle 2 | SUCCESS | Saturn's Vanishing Rings Explained"
    assert "CRAZZYDOCS PRODUCTION SUMMARY" in sent_messages[0]["plain"]
    assert "[CrazzyDocs Success]" not in sent_messages[0]["subject"]


def test_2_repeated_completion_callbacks_suppressed_across_callers(isolated_env, monkeypatch):
    """Repeated calls via send_cycle_summary_report, dispatch_summary_notification, and dispatch_stage_notification send at most 1 email."""
    mock_notify = MagicMock(return_value=True)
    monkeypatch.setattr(notification_module.notification_service, "notify_email", mock_notify)
    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    now_ist = datetime(2026, 11, 12, 8, 15, 0, tzinfo=IST_TZ)

    r1 = svc.send_cycle_summary_report(
        cycle_id="cycle_2",
        job_id="cycle_2_20261112_0600",
        result={
            "status": "COMPLETED",
            "topic": "Bioluminescent Fungi of Western Ghats",
            "video_title": "The Forests That Glow In Total Darkness",
            "duration_sec": 54.0,
        },
        test_now_ist=now_ist,
    )
    # Second call with different IST date (crossing midnight) but SAME job_id
    r2 = svc.send_cycle_summary_report(
        cycle_id="cycle_2",
        job_id="cycle_2_20261112_0600",
        result={"status": "COMPLETED", "topic": "Bioluminescent Fungi of Western Ghats"},
        test_now_ist=datetime(2026, 11, 13, 0, 1, 0, tzinfo=IST_TZ),
    )
    # Third call via Stage 3.22 event notifier for same job_id + PRODUCTION_COMPLETED
    r3 = svc.dispatch_stage_notification(
        event_type="PRODUCTION_COMPLETED",
        idempotency_key="stage322_prod_cycle_2_20261112_0600",
        subject="Duplicate stage alert",
        details={"production_id": "cycle_2_20261112_0600", "cycle_id": "cycle_2"},
    )

    assert r1["status"] == "SENT"
    assert r2["status"] == "DUPLICATE_SUPPRESSED"
    assert r3["status"] == "DUPLICATE_SUPPRESSED"
    assert mock_notify.call_count == 1


def test_3_concurrent_dispatches_produce_at_most_one_email(isolated_env, monkeypatch):
    """Two concurrent dispatchers racing on the same job_id produce strictly 1 SMTP call."""
    call_counter = {"count": 0}

    def slow_notify(*args, **kwargs):
        call_counter["count"] += 1
        notification_module.notification_service.last_email_receipt = {
            "confirmed": True,
            "delivery_state": "CONFIRMED_SENT",
        }
        return True

    monkeypatch.setattr(notification_module.notification_service, "notify_email", slow_notify)
    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    now_ist = datetime(2026, 11, 12, 9, 0, 0, tzinfo=IST_TZ)

    def _dispatch():
        return svc.send_cycle_summary_report(
            cycle_id="cycle_2",
            job_id="cycle_2_20261112_0600",
            result={"status": "COMPLETED", "topic": "Concurrency Lock Test", "video_title": "Atomic Slot Claim"},
            test_now_ist=now_ist,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_dispatch)
        f2 = pool.submit(_dispatch)
        res_a = f1.result()
        res_b = f2.result()

    statuses = sorted([res_a["status"], res_b["status"]])
    assert statuses == ["DUPLICATE_SUPPRESSED", "SENT"]
    assert call_counter["count"] == 1


def test_4_restart_reconciliation_does_not_resend_confirmed_email(isolated_env, monkeypatch):
    """After CONFIRMED_SENT is persisted, retry_pending_notifications never resends on restart."""
    mock_notify = MagicMock(return_value=True)
    monkeypatch.setattr(notification_module.notification_service, "notify_email", mock_notify)
    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))

    r1 = svc.dispatch_summary_notification(
        cycle_id="cycle_1",
        job_id="cycle_1_20261112_0000",
        final_status="SUCCESS",
        subject="CrazzyDocs | Cycle 1 | SUCCESS | Restart Guard",
        body_plain="Plain",
        body_html="<p>HTML</p>",
        date_str="2026-11-12",
    )
    assert r1["status"] == "SENT"
    assert mock_notify.call_count == 1

    # Simulate restart recovery sweep
    retry_res = svc.retry_pending_notifications()
    assert retry_res["retried"] == 0
    assert mock_notify.call_count == 1


def test_5_smtp_pre_data_failure_marks_retryable_then_recovers(isolated_env, monkeypatch):
    """Pre-DATA connection errors mark FAILED_RETRYABLE and recover cleanly on retry."""
    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    now_ist = datetime(2026, 11, 12, 12, 15, 0, tzinfo=IST_TZ)

    # First attempt fails at CONNECT_AUTH
    with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("SMTP offline")), patch("time.sleep"):
        res_fail = svc.send_cycle_summary_report(
            cycle_id="cycle_3",
            job_id="cycle_3_20261112_1200",
            result={"status": "COMPLETED", "topic": "Deep Sea Thermal Vents", "video_title": "Life Without Sunlight"},
            test_now_ist=now_ist,
        )

    assert res_fail["status"] == "SAVED_LOCAL_PENDING"
    assert res_fail["delivery_state"] == "FAILED_RETRYABLE"

    with isolated_env["SessionLocal"]() as db:
        row = db.query(PersistentNotificationModel).filter_by(job_id="cycle_3_20261112_1200").one()
        assert row.status == "FAILED"
        assert row.delivery_state == "FAILED_RETRYABLE"

    # Subsequent retry succeeds cleanly when SMTP recovers
    mock_smtp_ok = MagicMock()
    mock_smtp_ok.__enter__.return_value.send_message.return_value = {}
    with patch("smtplib.SMTP", return_value=mock_smtp_ok):
        rec_res = svc.retry_pending_notifications()

    assert rec_res["retried"] == 1
    assert rec_res["succeeded"] == 1
    with isolated_env["SessionLocal"]() as db:
        row = db.query(PersistentNotificationModel).filter_by(job_id="cycle_3_20261112_1200").one()
        assert row.status == "SENT"
        assert row.delivery_state == "CONFIRMED_SENT"


def test_6_smtp_send_data_timeout_marked_delivery_unknown_and_never_retried(isolated_env):
    """A timeout inside server.send_message (SEND_DATA phase) marks DELIVERY_UNKNOWN and blocks auto-resends."""
    send_attempts = {"calls": 0}

    def fake_smtp_factory(*args, **kwargs):
        srv = MagicMock()
        ctx = srv.__enter__.return_value

        def timed_out_send(msg):
            send_attempts["calls"] += 1
            raise TimeoutError("Read timed out waiting for DATA ACK")

        ctx.send_message.side_effect = timed_out_send
        return srv

    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    with patch("smtplib.SMTP", side_effect=fake_smtp_factory), patch("time.sleep"):
        res = svc.dispatch_summary_notification(
            cycle_id="cycle_4",
            job_id="cycle_4_20261112_1800",
            final_status="SUCCESS",
            subject="CrazzyDocs | Cycle 4 | SUCCESS | Timeout Test",
            body_plain="Body",
            body_html="<p>Body</p>",
            date_str="2026-11-12",
        )

    assert send_attempts["calls"] == 1  # Never retried blindly inside SMTP loop
    assert res["status"] == "DELIVERY_UNKNOWN"
    assert res["delivery_state"] == "DELIVERY_UNKNOWN"

    # Follow-up dispatch & restart retry sweep must BOTH suppress automatic resend
    with patch("smtplib.SMTP", side_effect=fake_smtp_factory):
        res_dup = svc.dispatch_summary_notification(
            cycle_id="cycle_4",
            job_id="cycle_4_20261112_1800",
            final_status="SUCCESS",
            subject="CrazzyDocs | Cycle 4 | SUCCESS | Timeout Test",
            body_plain="Body",
            body_html="<p>Body</p>",
            date_str="2026-11-12",
        )
        retry_res = svc.retry_pending_notifications()

    assert res_dup["status"] == "DELIVERY_UNKNOWN_SUPPRESSED"
    assert retry_res["retried"] == 0
    assert retry_res["skipped_delivery_unknown"] == 1
    assert send_attempts["calls"] == 1


def test_7_daily_dashboard_and_summary_reflect_completed_cycle_and_exclude_synthetic_rows(isolated_env, monkeypatch):
    """Cycle 2 completion email dashboard shows Cycle 2 completed (not PENDING / Generated: 0) and summary excludes test_ rows."""
    captured = {}

    def capture_notify(subject, body_text, body_html="", recipient=None, max_retries=2):
        captured["subject"] = subject
        captured["plain"] = body_text
        captured["html"] = body_html
        notification_module.notification_service.last_email_receipt = {"confirmed": True, "delivery_state": "CONFIRMED_SENT"}
        return True

    monkeypatch.setattr(notification_module.notification_service, "notify_email", capture_notify)

    # Seed a completed Cycle 2 job + synthetic test_ job that must be excluded from DailyPublishingSummaryService
    with isolated_env["SessionLocal"]() as db:
        db.add(
            JobModel(
                id="cycle_2_20261112_0600",
                date_str="2026-11-12",
                topic_title="The Clockwork Computer of Ancient Greece",
                status="COMPLETED",
                current_stage="COMPLETED",
                checkpoint_data_json=json.dumps({"total_duration_sec": 31.5}),
            )
        )
        db.add(
            JobModel(
                id="test_fake_synthetic_run",
                date_str="2026-11-12",
                topic_title="Synthetic Polluting Topic",
                status="FAILED",
                error_details="Synthetic error",
            )
        )
        db.commit()

    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    now_ist = datetime(2026, 11, 12, 8, 15, 0, tzinfo=IST_TZ)
    res = svc.send_cycle_summary_report(
        cycle_id="cycle_2",
        job_id="cycle_2_20261112_0600",
        result={
            "status": "COMPLETED",
            "topic": "The Clockwork Computer of Ancient Greece",
            "video_title": "This 2,000-Year-Old Bronze Device Computed Eclipses",
            "duration_sec": 55.2,
            "total_duration_sec": 31.5,
        },
        test_now_ist=now_ist,
    )
    assert res["status"] == "SENT"
    # Cycle 2 inside daily dashboard must reflect SUCCESS / real topic / Generated >= 1
    assert "Success: 1" in captured["plain"]
    assert "2       | SUCCESS" in captured["plain"]
    assert "The Clockwork Compu" in captured["plain"]

    summary_svc = DailyPublishingSummaryService(
        workspace_root=str(isolated_env["tmp_path"]),
        db_path=str(isolated_env["db_file"]),
    )
    rep = summary_svc.compile_daily_summary(date_str="2026-11-12")
    assert rep.productions_attempted == 1
    assert rep.productions_completed == 1
    assert "Synthetic Polluting Topic" not in rep.production_titles


def test_8_and_9_subject_uses_live_title_and_never_inherits_tsutomu_fallback(isolated_env, monkeypatch):
    """Email subject and body always reflect live title/metrics and contain zero hardcoded Tsutomu/9.76/10 fallbacks."""
    captured = {}

    def capture_notify(subject, body_text, body_html="", recipient=None, max_retries=2):
        captured["subject"] = subject
        captured["plain"] = body_text
        captured["html"] = body_html
        notification_module.notification_service.last_email_receipt = {"confirmed": True, "delivery_state": "CONFIRMED_SENT"}
        return True

    monkeypatch.setattr(notification_module.notification_service, "notify_email", capture_notify)
    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    now_ist = datetime(2026, 11, 12, 14, 0, 0, tzinfo=IST_TZ)

    # Deliberately omit video_title, narrative_archetype, and thumbnail_score to prove no stale Tsutomu fallback triggers
    res = svc.send_cycle_summary_report(
        cycle_id="cycle_3",
        job_id="cycle_3_20261112_1200",
        result={
            "status": "COMPLETED",
            "topic": "How Tardigrades Survive Open Space",
            "duration_sec": 52.8,
            "total_duration_sec": 29.4,
        },
        test_now_ist=now_ist,
    )
    assert res["status"] == "SENT"
    assert "How Tardigrades Survive Open Space" in captured["subject"]
    for forbidden in ("Tsutomu Yamaguchi", "He Survived Both Atomic Bombs", "Triumph Over Catastrophe", "9.76/10"):
        assert forbidden not in captured["subject"]
        assert forbidden not in captured["plain"]
        assert forbidden not in captured["html"]


def test_10_production_success_distinguished_from_youtube_publication(isolated_env, monkeypatch):
    """Manual-approval local production clearly reports Withheld/AWAITING_MANUAL_APPROVAL while uploaded state reads real counts."""
    captured = {}

    def capture_notify(subject, body_text, body_html="", recipient=None, max_retries=2):
        captured["plain"] = body_text
        notification_module.notification_service.last_email_receipt = {"confirmed": True, "delivery_state": "CONFIRMED_SENT"}
        return True

    monkeypatch.setattr(notification_module.notification_service, "notify_email", capture_notify)
    monkeypatch.setattr(settings, "PUBLISHING_MODE", "manual_approval", raising=False)

    svc = EmailMonitoringService(workspace_root=str(isolated_env["tmp_path"]))
    svc.send_cycle_summary_report(
        cycle_id="cycle_1",
        job_id="cycle_1_20261112_0000",
        result={
            "status": "COMPLETED",
            "topic": "Acoustic Levitation Physics",
            "video_title": "Sound Waves Lifting Solid Objects",
            "duration_sec": 54.1,
            "youtube_video_id": None,
        },
        test_now_ist=datetime(2026, 11, 12, 2, 0, 0, tzinfo=IST_TZ),
    )
    assert "Withheld at safety gate" in captured["plain"]
    assert "AWAITING_MANUAL_APPROVAL" in captured["plain"]
    assert "Not assigned (Withheld)" in captured["plain"]

    # Now seed a real uploaded row in publishing_ledger and confirm dashboard reflects it
    conn = sqlite3.connect(str(isolated_env["db_file"]))
    conn.execute(
        """
        INSERT INTO publishing_ledger (topic_title, video_path, upload_status, visibility, youtube_video_id, upload_timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Acoustic Levitation Physics", "media/productions/acoustic/final.mp4", "uploaded_public", "public", "yt_abc123", "2026-11-12T03:00:00"),
    )
    conn.commit()
    conn.close()

    dash = svc.generate_daily_dashboard(date_str="2026-11-12")
    assert dash["summary"]["videos_successfully_uploaded"] == 1


def test_11_scheduler_status_reflects_real_windows_and_daemon_state():
    """Reconciled operational status reports DISABLED/STOPPED when daemon is stopped and Windows task is disabled."""
    health_svc = ProductionHealthDashboard()
    dummy_metrics = MagicMock()
    dummy_metrics.pipeline_stage = "IDLE"
    dummy_metrics.recovery_status = "NORMAL"

    disabled_state = health_svc.get_reconciled_operational_states(
        metrics=dummy_metrics,
        services={"scheduler_daemon": {"is_running": False}},
        windows_tasks={"scheduler_task": {"task_state": "Disabled", "status": "Disabled"}},
    )
    assert disabled_state["scheduler_state"] == "DISABLED"

    stopped_state = health_svc.get_reconciled_operational_states(
        metrics=dummy_metrics,
        services={"scheduler_daemon": {"is_running": False}},
        windows_tasks={"scheduler_task": {"task_state": "NotRegistered", "status": "Missing"}},
    )
    assert stopped_state["scheduler_state"] == "STOPPED"

    running_state = health_svc.get_reconciled_operational_states(
        metrics=dummy_metrics,
        services={"scheduler_daemon": {"is_running": True}},
        windows_tasks={"scheduler_task": {"task_state": "Ready", "status": "Ready"}},
    )
    assert running_state["scheduler_state"] == "RUNNING"
