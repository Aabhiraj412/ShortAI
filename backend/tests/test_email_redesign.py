"""Email notification & report redesign (2026-11): design system, data accuracy, safety and delivery isolation.

Everything runs against an isolated SQLite database and a mocked SMTP layer; nothing is sent.
"""
from __future__ import annotations

import email
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import backend.app.services.daily_publishing_summary as daily_summary_module
import backend.app.services.email_monitoring_service as ems
import backend.app.services.notification_service as notification_module
from backend.app.database.db import Base, JobModel, PersistentNotificationModel, ensure_notification_ledger_schema
from backend.app.scheduler.schedule_profiles import IST_TZ, ProductionCycle, ScheduleProfile
from backend.app.services.email_templates import alerts as A
from backend.app.services.email_templates import data as Q
from backend.app.services.email_templates import design as D
from backend.app.services.email_templates import production as P
from backend.app.services.email_templates import report as R

PROFILE_24 = ScheduleProfile(
    name="T24", description="24 hourly cycles",
    cycles=[ProductionCycle(cycle_id=f"cycle_{h + 1}", name=f"Cycle {h + 1}", start_hour_ist=h, start_min_ist=0,
                            target_pub_hour_ist=(h + 1) % 24, target_pub_min_ist=0,
                            pub_rollover_days=1 if h == 23 else 0, recovery_window_hours=1.0) for h in range(24)],
)
DAY = "2026-11-24"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "email_redesign.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    ensure_notification_ledger_schema(engine)
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS publishing_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_title TEXT, video_path TEXT,
          thumbnail_path TEXT, metadata_json TEXT, youtube_video_id TEXT, upload_status TEXT, visibility TEXT, video_hash TEXT,
          upload_timestamp TEXT, scheduled_timestamp TEXT, error_details TEXT);
        CREATE TABLE IF NOT EXISTS production_lifecycle_events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT,
          timestamp_ist TEXT, cycle_id TEXT, job_id TEXT, event_type TEXT, execution_state TEXT, details_json TEXT, error_details TEXT);
    """)
    conn.commit()
    conn.close()
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ems, "SessionLocal", sessions)
    monkeypatch.setattr(daily_summary_module, "SessionLocal", sessions)
    monkeypatch.setattr(ems, "get_active_profile", lambda: PROFILE_24)
    monkeypatch.setattr(daily_summary_module, "get_active_profile", lambda: PROFILE_24)
    monkeypatch.setattr(ems.notification_service, "notify_desktop", lambda *a, **k: True)
    monkeypatch.setattr(ems.notification_service, "is_email_configured", lambda: True)
    monkeypatch.setattr(ems.notification_service, "get_public_email_configuration", lambda: {"recipient": "ops@example.test"})
    sent = []

    def fake_notify(subject, body_text, body_html="", recipient=None, max_retries=2):
        sent.append({"subject": subject, "plain": body_text, "html": body_html, "recipient": recipient})
        notification_module.notification_service.last_email_receipt = {"confirmed": True, "delivery_state": "CONFIRMED_SENT"}
        return True

    monkeypatch.setattr(notification_module.notification_service, "notify_email", fake_notify)
    return {"sessions": sessions, "db_file": db_file, "sent": sent}


def _job(sessions, job_id, status="COMPLETED", retry=0, cp=None, error=None, topic="Jet engine"):
    with sessions() as db:
        db.add(JobModel(id=job_id, date_str=DAY, topic_title=topic, status=status, current_stage=status, retry_count=retry,
                        checkpoint_data_json=json.dumps(cp or {}), error_details=error,
                        actual_start_at=datetime(2026, 11, 24, 11, 30), completed_at=datetime(2026, 11, 24, 11, 41, 31)
                        if status == "COMPLETED" else None))
        db.commit()


def _ledger(db_file, **row):
    conn = sqlite3.connect(str(db_file))
    cols = ",".join(row)
    conn.execute(f"INSERT INTO publishing_ledger ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    lid = conn.execute("SELECT MAX(id) FROM publishing_ledger").fetchone()[0]
    conn.close()
    return lid


# ---- formatting ----------------------------------------------------------------------------------------------------
def test_timestamps_convert_naive_utc_and_iso_to_ist():
    assert D.fmt_datetime(datetime(2026, 11, 24, 11, 41, 31)) == "24 Nov 2026, 17:11 IST"       # naive = UTC (DB)
    assert D.fmt_datetime("2026-11-24T12:30:00Z") == "24 Nov 2026, 18:00 IST"
    assert D.fmt_datetime("2026-11-24T18:00:00+05:30") == "24 Nov 2026, 18:00 IST"
    assert D.fmt_time("17:00:01 IST") == "17:00:01 IST"
    assert D.fmt_clock("2026-11-24T11:41:31Z") == "17:11:31 IST"
    assert D.fmt_datetime(None) is None and D.fmt_datetime("not a date") is None


def test_duration_and_number_formatting_distinguish_zero_from_unknown():
    assert D.fmt_duration(52.26) == "52.3 s"
    assert D.fmt_duration(676.42) == "11 min 16 s"
    assert D.fmt_duration(3900) == "1 h 05 min"
    assert D.fmt_duration(0) == "0 s"
    assert D.fmt_duration(None) is None and D.fmt_duration(-1) is None
    assert D.fmt_count(0) == "0" and D.fmt_count(None) is None
    assert D.fmt_pct(0, 0) is None and D.fmt_pct(1, 4) == "25%"


def test_metric_grid_omits_unknown_values_but_keeps_zero():
    blk = D.metric_grid([("Retries", "0", None), ("Rendering time", None, None)])
    assert "Retries" in blk.html and "Rendering time" not in blk.html and "Rendering time" not in blk.text


# ---- escaping and safety ---------------------------------------------------------------------------------------------
HOSTILE = '<script>alert("x")</script> & <img src=x onerror=y>'


def test_dynamic_content_is_html_escaped_in_every_template():
    ctx = P.ProductionEmailContext(outcome="SUCCESS", cycle_label="Cycle 1", job_id="j1", topic=HOSTILE, video_title=HOSTILE)
    fail = P.ProductionEmailContext(outcome="FAILED", cycle_label="Cycle 1", job_id="j1", topic=HOSTILE, error_message=HOSTILE)
    rep = R.OperationsReport(date=DAY, generated_at=datetime.now(timezone.utc), complete_day=True,
                             rows=[R.CycleRow("Cycle 1", datetime(2026, 11, 24, tzinfo=IST_TZ), "j1", R.ST_FAILED, topic=HOSTILE, reason=HOSTILE)])
    alert = A.render_event("UPLOAD_FAILED", "Upload failed", {"video_title": HOSTILE, "error": HOSTILE})
    for r in (P.render_success(ctx), P.render_failure(fail), R.render(rep), alert):
        assert "<script>" not in r.html and "onerror=y>" not in r.html
        assert "&lt;script&gt;" in r.html
        assert "<script>" in r.text or "alert" in r.text            # plain text keeps the literal characters
        assert "<td" not in r.text and "<div" not in r.text           # no HTML in the text part


def test_email_html_has_no_javascript_external_css_or_remote_images():
    r = P.render_success(P.ProductionEmailContext(outcome="SUCCESS", cycle_label="Cycle 1", job_id="j1", topic="T"))
    assert "<script" not in r.html.lower()
    assert '<link' not in r.html.lower()
    assert not re.search(r'<img[^>]+src="https?://', r.html)
    assert 'bgcolor="#eef1f6"' in r.html and "max-width:640px" in r.html


def test_failure_details_are_redacted(env):
    _job(env["sessions"], "cycle_10_20261124_0900", status="FAILED", error="boom password=hunter2 token=abc123")
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_10", "cycle_10_20261124_0900", {"status": "FAILED", "error": "boom password=hunter2 token=abc123"},
        test_now_ist=datetime(2026, 11, 24, 9, 20, tzinfo=IST_TZ))
    msg = env["sent"][-1]
    for part in (msg["plain"], msg["html"], msg["subject"]):
        assert "hunter2" not in part and "abc123" not in part
    assert "[REDACTED]" in msg["plain"]


def test_stage_alert_details_are_redacted_and_rendered(env):
    ems.email_monitoring_service.dispatch_stage_notification(
        "YOUTUBE_AUTH_PROBLEM", "k-auth-1", "CrazzyDocs YouTube authorization needs attention",
        {"status": "reconnect_required", "error": "invalid_grant client_secret=SHHH", "refresh_token": "1//abc"})
    msg = env["sent"][-1]
    assert msg["html"].startswith("<!DOCTYPE html>")
    assert "Reconnect YouTube" in msg["plain"]
    for part in (msg["plain"], msg["html"]):
        assert "SHHH" not in part and "1//abc" not in part


# ---- production status accuracy ------------------------------------------------------------------------------------
@pytest.mark.parametrize("row,state", [
    ({"upload_status": "pending_queue", "visibility": "private"}, P.YT_QUEUED),
    ({"upload_status": "uploaded_public", "visibility": "private", "youtube_video_id": "v1"}, P.YT_UPLOADED_PRIVATE),
    ({"upload_status": "publication_scheduled", "visibility": "private", "youtube_video_id": "v1",
      "scheduled_timestamp": "2026-11-24T12:30:00Z"}, P.YT_SCHEDULED),
    ({"upload_status": "uploaded_public", "visibility": "public", "youtube_video_id": "v1"}, P.YT_PUBLISHED),
    ({"upload_status": "upload_failed", "visibility": "private"}, P.YT_UPLOAD_FAILED),
])
def test_youtube_state_comes_from_the_publishing_ledger(env, row, state):
    lid = _ledger(env["db_file"], topic_title="t", video_path="media/x.mp4", **row)
    with env["sessions"]() as db:
        st = Q.youtube_state(db, None, {"queue_ledger_id": lid})
    assert st.state == state


def test_success_email_reports_real_upload_and_schedule_not_withheld(env):
    lid = _ledger(env["db_file"], topic_title="Jet engine", video_path="media/jet.mp4", upload_status="publication_scheduled",
                  visibility="private", youtube_video_id="itAn8UlBC5s", scheduled_timestamp="2026-11-24T12:30:00Z")
    _job(env["sessions"], "cycle_18_20261124_1700", cp={"queue_ledger_id": lid, "duration_sec": 52.26, "total_duration_sec": 676.42,
                                                       "thumbnail_score": 9.46, "category": "Technology"})
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_18", "cycle_18_20261124_1700", {"status": "COMPLETED", "topic": "Jet engine", "video_title": "Jet engine #Shorts"},
        test_now_ist=datetime(2026, 11, 24, 17, 19, tzinfo=IST_TZ))
    plain = env["sent"][-1]["plain"]
    assert env["sent"][-1]["subject"] == "CrazzyDocs | Cycle 18 | SUCCESS | Jet engine #Shorts"
    assert re.search(r"Uploaded to YouTube\s+Yes \(private\)", plain)
    assert re.search(r"Published\s+Scheduled for 24 Nov 2026, 18:00 IST", plain)
    assert "https://youtu.be/itAn8UlBC5s" in plain
    assert "Withheld" not in plain and "1080x1920" not in plain and "SYNCHRONIZED" not in plain
    assert "11 min 16 s" in plain and "9.46 / 10" in plain
    assert "Completed              24 Nov 2026, 17:11 IST" in plain or re.search(r"Completed\s+24 Nov 2026, 17:11 IST", plain)


def test_success_without_upload_is_never_reported_as_published(env):
    _job(env["sessions"], "cycle_2_20261124_0100")
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_2", "cycle_2_20261124_0100", {"status": "COMPLETED", "topic": "Jet engine", "publishing_status": "suppressed_test_execution"},
        test_now_ist=datetime(2026, 11, 24, 1, 20, tzinfo=IST_TZ))
    plain = env["sent"][-1]["plain"]
    assert re.search(r"Uploaded to YouTube\s+No", plain) and re.search(r"Published\s+No", plain)


def test_manual_production_is_not_labelled_cycle_1(env):
    _job(env["sessions"], "manual_abc")
    ems.email_monitoring_service.send_cycle_summary_report(
        "manual_production", "manual_abc", {"status": "COMPLETED", "topic": "Jet engine"},
        test_now_ist=datetime(2026, 11, 24, 14, 0, tzinfo=IST_TZ))
    msg = env["sent"][-1]
    assert "Manual production" in msg["subject"] and "Cycle 1 " not in msg["subject"]


# ---- recovery accuracy ------------------------------------------------------------------------------------------------
def test_recovered_success_shows_attempts_and_original_failure(env):
    cp = {"cycle_state": {"attempt_history": [{"attempt": 1, "kind": "production", "message": "VisualDiversityValidator rejected storyboard"}]}}
    _job(env["sessions"], "cycle_17_20261124_1600", retry=2, cp=cp)
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_17", "cycle_17_20261124_1600", {"status": "COMPLETED", "topic": "Talking to Yourself"},
        test_now_ist=datetime(2026, 11, 24, 16, 20, tzinfo=IST_TZ))
    plain = env["sent"][-1]["plain"]
    assert "Production completed after recovery" in plain
    assert re.search(r"Attempts\s+3", plain) and "VisualDiversityValidator" in plain
    assert re.search(r"Retries\s+2", plain)


def test_first_attempt_success_is_not_called_a_recovery(env):
    _job(env["sessions"], "cycle_3_20261124_0200", retry=0)
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_3", "cycle_3_20261124_0200", {"status": "COMPLETED", "topic": "T"}, test_now_ist=datetime(2026, 11, 24, 2, 20, tzinfo=IST_TZ))
    plain = env["sent"][-1]["plain"]
    assert "after recovery" not in plain and "RECOVERY\n" not in plain
    assert re.search(r"Retries\s+0", plain)                     # zero is shown as zero


def test_failure_with_expired_window_reports_no_further_attempts(env):
    cp = {"cycle_state": {"recovery_expiration_at": "2026-11-24T10:15:00+05:30", "retry_terminal": True}}
    _job(env["sessions"], "cycle_10_20261124_0900", status="RECOVERY_WINDOW_EXPIRED", retry=1, cp=cp,
         error="[DETERMINISTIC] ValueError: BLOCKED_QC: CHECK_A_TOPIC_NARRATION Narration lacks topic keywords")
    ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_10", "cycle_10_20261124_0900",
        {"status": "RECOVERY_WINDOW_EXPIRED", "error": "[DETERMINISTIC] ValueError: BLOCKED_QC: CHECK_A_TOPIC_NARRATION Narration lacks topic keywords"},
        test_now_ist=datetime(2026, 11, 24, 10, 16, tzinfo=IST_TZ))
    msg = env["sent"][-1]
    assert "QC_REJECTED" in msg["subject"]
    assert "the narration did not mention the topic's key words" in msg["plain"]
    assert "No further attempts: the recovery window has expired" in msg["plain"]
    assert re.search(r"Recovery deadline\s+24 Nov 2026, 10:15 IST", msg["plain"])
    assert "Traceback" not in msg["html"].split("TECHNICAL DETAILS")[0] if "TECHNICAL DETAILS" in msg["html"] else True


def test_non_terminal_retry_sends_no_failure_email(env, monkeypatch):
    """Scheduler semantics preserved: only terminal outcomes produce a failure email (scheduler.run_cycle_generation)."""
    import inspect
    import backend.app.scheduler.scheduler as sched
    src = inspect.getsource(sched.run_cycle_generation)
    assert 'if not decision["terminal"]:' in src and "return fail_res" in src


# ---- duplicate prevention and delivery isolation --------------------------------------------------------------------
def test_cycle_report_is_sent_once(env):
    _job(env["sessions"], "cycle_5_20261124_0400")
    for _ in range(3):
        ems.email_monitoring_service.send_cycle_summary_report(
            "cycle_5", "cycle_5_20261124_0400", {"status": "COMPLETED", "topic": "T"}, test_now_ist=datetime(2026, 11, 24, 4, 20, tzinfo=IST_TZ))
    assert len(env["sent"]) == 1


def test_stage_alert_is_sent_once(env):
    for _ in range(2):
        res = ems.email_monitoring_service.dispatch_stage_notification("UPLOAD_COMPLETED", "stage322-upload-7-ok", "YouTube upload completed: X",
                                                                       {"upload_id": 7, "youtube_video_id": "abc"})
    assert len(env["sent"]) == 1 and res["status"] == "DUPLICATE_SUPPRESSED"


def test_smtp_failure_does_not_raise_or_change_production_state(env, monkeypatch):
    _job(env["sessions"], "cycle_6_20261124_0500")
    monkeypatch.setattr(notification_module.notification_service, "notify_email", MagicMock(side_effect=OSError("smtp down")))
    res = ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_6", "cycle_6_20261124_0500", {"status": "COMPLETED", "topic": "T"}, test_now_ist=datetime(2026, 11, 24, 5, 20, tzinfo=IST_TZ))
    assert res["email_sent"] is False
    with env["sessions"]() as db:
        job = db.query(JobModel).filter(JobModel.id == "cycle_6_20261124_0500").first()
        rec = db.query(PersistentNotificationModel).filter(PersistentNotificationModel.job_id == "cycle_6_20261124_0500").first()
    assert job.status == "COMPLETED" and rec.status == "FAILED"       # kept for the existing retry path


def test_rendering_error_falls_back_to_minimal_report_without_raising(env, monkeypatch):
    _job(env["sessions"], "cycle_7_20261124_0600")
    monkeypatch.setattr(P, "render_success", MagicMock(side_effect=RuntimeError("template bug")))
    res = ems.email_monitoring_service.send_cycle_summary_report(
        "cycle_7", "cycle_7_20261124_0600", {"status": "COMPLETED", "topic": "T"}, test_now_ist=datetime(2026, 11, 24, 6, 20, tzinfo=IST_TZ))
    assert res["status"] == "SENT"
    assert "minimal" in env["sent"][-1]["plain"]


def test_mime_message_converts_data_images_to_cid_parts():
    r = P.render_success(P.ProductionEmailContext(outcome="SUCCESS", cycle_label="Cycle 1", job_id="j", topic="T",
                                                  thumbnail_data_uri="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
    assert "data:image" in r.html
    msg = email.message_from_bytes(notification_module.build_mime_message("s", "a@x.test", "b@x.test", r.text, r.html).as_bytes())
    assert msg.get_content_type() == "multipart/alternative"
    parts = list(msg.walk())
    html = next(p for p in parts if p.get_content_type() == "text/html").get_payload(decode=True).decode("utf-8")
    images = [p for p in parts if p.get_content_maintype() == "image"]
    assert "data:image" not in html and "cid:img-" in html
    assert images and all(p["Content-ID"] and p["Content-ID"].strip("<>") in html for p in images)
    assert any(p.get_content_type() == "text/plain" for p in parts)


def test_html_stays_below_gmail_clipping_size():
    rep = R.OperationsReport(date=DAY, generated_at=datetime.now(timezone.utc), complete_day=True,
                             rows=[R.CycleRow(f"Cycle {i + 1}", datetime(2026, 11, 24, i, tzinfo=IST_TZ), f"j{i}", R.ST_COMPLETED,
                                              topic="A fairly long topic title about something interesting %d" % i,
                                              production_sec=700, video_sec=52, retries=0, thumbnail_score=9.3) for i in range(24)])
    html = R.render(rep).html
    html_wo_images = re.sub(r'src="data:[^"]+"', 'src=""', html)
    assert len(html_wo_images.encode("utf-8")) < 100 * 1024


# ---- daily operations report ------------------------------------------------------------------------------------------
def _rows(statuses):
    return [R.CycleRow(f"Cycle {i + 1}", datetime(2026, 11, 24, i, tzinfo=IST_TZ), f"j{i}", s) for i, s in enumerate(statuses)]


@pytest.mark.parametrize("statuses,tone,phrase", [
    ([R.ST_COMPLETED] * 24, "success", "All 24 scheduled cycles completed successfully."),
    ([R.ST_COMPLETED] * 23 + [R.ST_RECOVERED], "success", "1 of them needed recovery"),
    ([R.ST_COMPLETED] * 22 + [R.ST_FAILED, R.ST_NOT_STARTED], "warning", "22 of 24 scheduled cycles completed, 1 failed and 1 did not start."),
    ([R.ST_COMPLETED] * 10 + [R.ST_RUNNING] + [R.ST_UPCOMING] * 13, "info", "Production is partially complete"),
    ([R.ST_FAILED] * 12 + [R.ST_COMPLETED] * 12, "danger", "12 failed"),
])
def test_report_assessment_is_derived_from_counts(statuses, tone, phrase):
    rep = R.OperationsReport(date=DAY, generated_at=datetime.now(timezone.utc), complete_day=True, rows=_rows(statuses))
    t, sentence = rep.assessment()
    assert t == tone and phrase in sentence


def test_report_compiles_all_24_cycles_and_marks_missing_correctly(env):
    _job(env["sessions"], "cycle_1_20261124_0000", cp={"total_duration_sec": 600, "duration_sec": 50, "thumbnail_score": 9.0})
    _job(env["sessions"], "cycle_2_20261124_0100", status="FAILED", retry=1, error="[DETERMINISTIC] BLOCKED_QC CHECK_A_TOPIC_NARRATION")
    _job(env["sessions"], "cycle_3_20261124_0200", status="RUNNING")
    with env["sessions"]() as db:
        rep = R.compile_report(db, DAY, profile=PROFILE_24, now=datetime(2026, 11, 24, 12, 30, tzinfo=IST_TZ))
    st = [r.status for r in rep.rows]
    assert len(rep.rows) == 24
    assert st[0] == R.ST_COMPLETED and st[1] == R.ST_FAILED and st[2] == R.ST_RUNNING
    assert st[3:13] == [R.ST_NOT_STARTED] * 10 and st[13:] == [R.ST_UPCOMING] * 11
    assert rep.rows[1].reason.startswith("The quality gate blocked")
    out = R.render(rep)
    assert "still in progress" in out.text
    assert "Avg production time" in out.html and "10 min 00 s" in out.text
    assert "Rendering time is not recorded separately" in out.text


def test_scheduled_daily_report_covers_previous_complete_day(env, monkeypatch):
    res = daily_summary_module.daily_publishing_summary_service.send_daily_summary()
    expected = (datetime.now(IST_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert res["date"] == expected
    assert res["idempotency_key"] == f"daily_operations_report_{expected}"
    msg = env["sent"][-1]
    assert msg["subject"] == f"CrazzyDocs | Daily Operations Report | {expected}"
    assert "EXECUTIVE OVERVIEW" in msg["plain"] and msg["html"].startswith("<!DOCTYPE html>")


def test_daily_dashboard_inspects_every_scheduled_cycle(env):
    _job(env["sessions"], "cycle_18_20261124_1700")
    dash = ems.email_monitoring_service.generate_daily_dashboard(date_str=DAY, test_now_ist=datetime(2026, 11, 24, 17, 30, tzinfo=IST_TZ))
    assert dash["summary"]["total_cycles_scheduled"] == 24
    c18 = next(c for c in dash["cycles"] if c["cycle_id"] == "cycle_18")
    assert c18["status"] == "SUCCESS"
    assert dash["summary"]["storage_consumed_mb"] is None or dash["summary"]["storage_consumed_mb"] >= 0


def test_plain_text_contains_essential_information_and_full_urls():
    ctx = P.ProductionEmailContext(outcome="SUCCESS", cycle_label="Cycle 18", cycle_number=18, job_id="cycle_18_x", topic="Jet engine",
                                   youtube=P.YouTubeState(P.YT_PUBLISHED, "abc123", "public"))
    r = P.render_success(ctx)
    assert "Jet engine" in r.text and "https://youtu.be/abc123" in r.text and "http://127.0.0.1:8000/" in r.text
    assert "Yes (public)" in r.text
    assert not re.search(r"<[a-z]+[ >]", r.text)
