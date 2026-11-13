"""
Production Monitor: retry-policy fields (attempts, next retry, backoff, deadline, remaining time,
last failure, classification, automatic retry status, operator-needed flag, policy label) and the
validated recovery-policy settings endpoint. Uses a temporary database and config file only.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.database.db import JobModel
from backend.app.scheduler import recovery_settings
from backend.app.services import production_monitor_service as pm

from test_schedule_and_auto_publish import cyc, settings_db, spec, ss  # noqa: F401  (fixture re-export)

NOW = datetime(2026, 11, 13, 20, 0, 0)          # naive UTC = 01:30 IST on 14 Nov
DEADLINE = "2026-11-14T04:00:00+05:30"          # = 22:30 UTC, 2.5 h after NOW


@pytest.fixture(autouse=True)
def _tmp_policy_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_RETRY_POLICY", raising=False)
    monkeypatch.setattr(recovery_settings, "_config_path", lambda: tmp_path / "recovery_policy.json")


def _job(status, retry_count, cs, stage="QUALITY_CONTROL", job_id="cycle_1_20261114_0000"):
    return SimpleNamespace(id=job_id, status=status, retry_count=retry_count, max_retries=3, current_stage=stage,
                           checkpoint_data_json=json.dumps({"cycle_state": cs}))


def _view(job):
    return pm.retry_view(job, json.loads(job.checkpoint_data_json)["cycle_state"], NOW)


def test_scheduled_retry_shows_attempts_backoff_deadline_and_policy_label():
    cs = {"retry_policy": "unlimited_until_recovery_deadline", "recovery_expiration_at": DEADLINE,
          "next_retry_at_utc": "2026-11-13T20:04:00Z", "error_classification": "TRANSIENT",
          "last_error_message": "connection reset", "last_failure_at_utc": "2026-11-13T20:00:00Z",
          "attempt_history": [{"attempt": 3, "kind": "production", "failed_at_utc": "2026-11-13T20:00:00Z",
                               "next_retry_at_utc": "2026-11-13T20:04:00Z"}]}
    v = _view(_job("RETRY_SCHEDULED", 3, cs))
    assert v["policy_label"] == "Unlimited until recovery deadline"
    assert v["max_attempts_label"].startswith("No fixed limit")      # unlimited is a policy, not a number
    assert v["failed_attempts"] == 3 and v["total_attempts"] == 3 and v["current_attempt"] == 3
    assert v["next_retry_at_ist"].endswith("01:34:00 IST")
    assert v["current_backoff_seconds"] == 240
    assert v["recovery_deadline_ist"].endswith("04:00:00 IST")
    assert v["remaining_seconds"] == 9000
    assert v["classification"] == "TRANSIENT" and v["last_failure"] == "connection reset"
    assert v["auto_retry_status"] == "scheduled"
    assert v["operator_action_needed"] is False


def test_running_attempt_counts_the_attempt_in_progress():
    v = _view(_job("RUNNING", 11, {"recovery_expiration_at": DEADLINE}))
    assert v["current_attempt"] == 12 and v["total_attempts"] == 12
    assert v["auto_retry_status"] == "attempt in progress"
    assert v["next_retry_at_ist"] is None


def test_waiting_for_change_needs_operator_action():
    cs = {"recovery_expiration_at": DEADLINE, "requires_change": True, "error_classification": "DETERMINISTIC",
          "next_retry_at_utc": "2026-11-13T20:15:00Z"}
    v = _view(_job("RETRY_SCHEDULED", 2, cs))
    assert v["waiting_for_change"] is True
    assert v["auto_retry_status"] == "waiting for a code or configuration change"
    assert v["operator_action_needed"] is True


def test_expired_and_completed_jobs_show_retries_stopped():
    expired = _view(_job("RECOVERY_WINDOW_EXPIRED", 7, {"recovery_expiration_at": "2026-11-14T01:00:00+05:30"}))
    assert expired["auto_retry_status"] == "stopped: recovery deadline reached"
    assert expired["remaining_seconds"] < 0 and expired["operator_action_needed"] is False
    done = _view(_job("COMPLETED", 2, {"recovery_expiration_at": DEADLINE}))
    assert done["auto_retry_status"] == "stopped: production succeeded"
    assert done["operator_action_needed"] is False


def test_publishing_retry_is_labelled_without_rerender():
    v = _view(_job("HANDOFF_PENDING", 0, {"recovery_expiration_at": DEADLINE, "publish_attempts": 2}))
    assert "no re-render" in v["auto_retry_status"] and "no re-upload" in v["auto_retry_status"]


def test_bounded_policy_shows_the_number():
    v = _view(_job("RETRY_SCHEDULED", 1, {"retry_policy": "bounded", "recovery_expiration_at": DEADLINE}))
    assert v["policy_label"].startswith("Bounded") and v["max_attempts_label"] == "3"


def test_monitor_endpoint_and_policy_settings_endpoint(settings_db, monkeypatch):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    ss.activate(spec(cyc("a", "06:00", "12:00")), confirm=True)
    with settings_db() as db:
        db.add(JobModel(id="a_20261113_0600", date_str="2026-11-13", status="RETRY_SCHEDULED", current_stage="RENDER",
                        retry_count=2, max_retries=3, heartbeat_at=datetime.utcnow(),
                        checkpoint_data_json=json.dumps({"cycle_state": {
                            "retry_policy": "unlimited_until_recovery_deadline",
                            "next_retry_at_utc": "2026-11-13T01:00:00Z", "error_classification": "RENDER_FAILURE",
                            "recovery_expiration_at": "2026-11-13T10:00:00+05:30"}})))
        db.commit()
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as c:
        html = c.get("/dashboard").text
        for el in ("monPolicy", "monAttempt", "monNextRetry", "monDeadline", "monLastFailure", "monAutoRetry"):
            assert f'id="{el}"' in html, el
        mon = c.get("/api/production/monitor").json()
        assert mon["recovery_policy"]["retry_policy"] == "unlimited_until_recovery_deadline"
        assert mon["recovery_policy"]["policy_label"] == "Unlimited until recovery deadline"
        rt = mon["active_job"]["retry"]
        assert rt["failed_attempts"] == 2 and rt["classification"] == "RENDER_FAILURE"
        assert rt["recovery_deadline_ist"].endswith("10:00:00 IST")

        assert c.get("/api/production/recovery-policy").json()["backoff_schedule_seconds"] == [60, 120, 240, 480, 900]
        assert c.post("/api/production/recovery-policy", json={"settings": {"min_attempt_seconds": 900}}).status_code == 400
        bad = c.post("/api/production/recovery-policy", json={"confirm": True, "settings": {"retry_policy": "forever"}})
        assert bad.status_code == 400
        assert c.post("/api/production/recovery-policy",
                      json={"confirm": True, "settings": {"nope": 1}}).status_code == 400
        ok = c.post("/api/production/recovery-policy", json={"confirm": True, "settings": {"min_attempt_seconds": 900}})
        assert ok.status_code == 200 and ok.json()["min_attempt_seconds"] == 900
        assert recovery_settings.load_settings().min_attempt_seconds == 900
    with TestClient(main_module.app, base_url="http://127.0.0.1:8000", client=("203.0.113.9", 1)) as remote:
        assert remote.get("/api/production/recovery-policy").status_code == 403
        assert remote.post("/api/production/recovery-policy",
                           json={"confirm": True, "settings": {"retry_policy": "bounded"}}).status_code == 403
