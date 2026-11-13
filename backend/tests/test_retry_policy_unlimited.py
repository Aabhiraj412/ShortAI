"""
Retry policy `unlimited_until_recovery_deadline` (default since 2026-11-14).

No fixed attempt count: a cycle is retried with backoff until its recovery deadline. Deterministic
failures wait for a change instead of looping. Everything runs on fake jobs or a temporary SQLite
database with the pipeline mocked; nothing touches the live database, scheduler or YouTube.
"""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler import scheduler as sched_mod
from backend.app.scheduler.recovery_settings import POLICY_UNLIMITED, RecoverySettings, load_settings, save_settings

from test_operator_retry import JOB3, JOB4, LIVE_CP, T, _row, _seed_failed, sched_db  # noqa: F401  (fixture)

S = RecoverySettings()   # defaults: unlimited, 1/2/4/8/15 min, 20 min minimum attempt time
UTC = lambda *a: datetime(*a)  # noqa: E731  naive UTC, as stored


@pytest.fixture(autouse=True)
def _unlimited(monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_RETRY_POLICY", raising=False)
    monkeypatch.setattr("backend.app.scheduler.recovery_settings._config_path",
                        lambda: __import__("pathlib").Path("Z:/no/such/recovery_policy.json"))


def job(deadline_ist="2026-11-14T04:00:00+05:30", **over):
    cs = {"cycle_id": "cycle_1", "recovery_expiration_at": deadline_ist}
    base = dict(id="cycle_1_20261114_0000", status="RUNNING", retry_count=0, max_retries=3, current_stage="VOICEOVER",
                youtube_video_id=None, error_details=None, recovery_reason=None,
                checkpoint_data_json=json.dumps({"cycle_state": cs}))
    base.update(over)
    return SimpleNamespace(**base)


def cs_of(j):
    return json.loads(j.checkpoint_data_json)["cycle_state"]


NOW = UTC(2026, 11, 13, 19, 0)          # 00:30 IST, deadline 04:00 IST = 22:30 UTC


def test_default_policy_is_unlimited():
    assert load_settings().retry_policy == POLICY_UNLIMITED


def test_backoff_schedule_with_bounded_deterministic_jitter():
    for attempt, base in [(1, 60), (2, 120), (3, 240), (4, 480), (5, 900), (9, 900), (40, 900)]:
        d = rp.backoff_seconds(attempt, job_id="j", settings=S)
        assert base * 0.9 <= d <= base * 1.1, (attempt, d)
        assert d == rp.backoff_seconds(attempt, job_id="j", settings=S)   # a restart recomputes the same delay


@pytest.mark.parametrize("attempts", [1, 2, 10, 25])
def test_transient_failure_is_retried_at_any_attempt_number(attempts):
    j = job(retry_count=attempts - 1)
    d = rp.record_failure(j, ConnectionError("upstream reset"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["status"] == "RETRY_SCHEDULED" and not d["terminal"] and d["attempts"] == attempts
    assert d["max_retries"] is None                      # no numeric limit is reported
    assert "no attempt limit" in d["summary"]
    assert len(cs_of(j)["attempt_history"]) == 1


def test_retry_state_survives_a_restart():
    j = job()
    for k in range(3):
        rp.record_failure(j, TimeoutError("provider timeout"), now_utc=NOW + timedelta(minutes=10 * k), code_revision="r1", settings=S)
    reloaded = job(retry_count=j.retry_count, status=j.status, checkpoint_data_json=j.checkpoint_data_json)
    assert [h["attempt"] for h in cs_of(reloaded)["attempt_history"]] == [1, 2, 3]
    due_at = rp.next_retry_at(reloaded)
    assert not rp.is_retry_due(reloaded, due_at - timedelta(seconds=1), code_revision="r1", settings=S)
    assert rp.is_retry_due(reloaded, due_at, code_revision="r1", settings=S)


def test_deadline_stops_retries_and_next_retry_never_passes_it():
    j = job()
    late = UTC(2026, 11, 13, 22, 10)                     # 03:40 IST: 1 min backoff + 20 min attempt > 04:00
    d = rp.record_failure(j, ConnectionError("x"), now_utc=late, code_revision="r1", settings=S)
    assert d["status"] == "RECOVERY_WINDOW_EXPIRED" and d["terminal"]
    assert "next_retry_at_utc" not in cs_of(j)
    j2 = job()
    rp.record_failure(j2, ConnectionError("x"), now_utc=NOW, code_revision="r1", settings=S)
    assert rp.next_retry_at(j2) + timedelta(seconds=S.min_attempt_seconds) <= UTC(2026, 11, 13, 22, 30)


def test_deadline_crossing_midnight():
    j = job(deadline_ist="2026-11-15T02:00:00+05:30")    # a 22:00 cycle with a 4 h window
    d = rp.record_failure(j, ConnectionError("x"), now_utc=UTC(2026, 11, 14, 18, 20), code_revision="r1", settings=S)  # 23:50 IST
    assert d["status"] == "RETRY_SCHEDULED"
    j2 = job(deadline_ist="2026-11-15T02:00:00+05:30")
    d2 = rp.record_failure(j2, ConnectionError("x"), now_utc=UTC(2026, 11, 14, 20, 15), code_revision="r1", settings=S)  # 01:45 IST
    assert d2["status"] == "RECOVERY_WINDOW_EXPIRED"


def test_deterministic_failure_waits_for_a_change_instead_of_looping():
    j = job()
    d = rp.record_failure(j, ValueError('BLOCKED_QC: ["[CHECK_A_TOPIC_NARRATION] ..."]'), now_utc=NOW,
                          code_revision="r1", settings=S)
    assert d["classification"] == "DETERMINISTIC" and d["requires_change"] and not d["terminal"]
    assert d["retry_in_seconds"] >= S.deterministic_cooldown_seconds
    later = NOW + timedelta(hours=1)
    assert not rp.is_retry_due(j, later, code_revision="r1", settings=S)   # same code, same inputs: never
    assert rp.is_retry_due(j, later, code_revision="r2", settings=S)       # a deploy changed the code
    j2 = job()
    rp.record_failure(j2, ValueError("BLOCKED_QC: x"), now_utc=NOW, code_revision="r1", settings=S)
    rp.acknowledge_change(j2, now_utc=later)
    assert rp.is_retry_due(j2, later, code_revision="r1", settings=S)      # operator acknowledged a fix


def test_code_defect_is_permanent_and_also_waits_for_a_change():
    j = job()
    d = rp.record_failure(j, AttributeError("no attribute save_manifest"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["classification"] == "PERMANENT" and d["requires_change"]
    assert not rp.is_retry_due(j, NOW + timedelta(hours=1), code_revision="r1", settings=S)


def test_operator_allowance_never_blocks_automatic_retries():
    j = job(retry_count=7)
    cp = json.loads(j.checkpoint_data_json); cp["cycle_state"]["operator_retries"] = 9
    j.checkpoint_data_json = json.dumps(cp)
    d = rp.record_failure(j, ConnectionError("x"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["status"] == "RETRY_SCHEDULED"
    assert rp.is_retry_due(j, rp.next_retry_at(j), code_revision="r1", settings=S)


def test_success_stops_retrying():
    j = job()
    rp.record_failure(j, ConnectionError("x"), now_utc=NOW, code_revision="r1", settings=S)
    j.status = "COMPLETED"
    rp.clear_failure_state(j)
    assert "next_retry_at_utc" not in cs_of(j) and not rp.is_retry_due(j, NOW + timedelta(hours=1), settings=S)
    assert cs_of(j)["attempt_history"]                     # history is kept


def test_publishing_retry_never_restarts_production(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 4096)
    j = job(current_stage="PUBLISHING_QUEUE_HANDOFF", retry_count=2,
            checkpoint_data_json=json.dumps({"video_path": str(video), "queue_handoff": {"ok": 1},
                                             "cycle_state": {"recovery_expiration_at": "2026-11-14T04:00:00+05:30"}}))
    d = rp.record_failure(j, ConnectionError("db busy"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["status"] == "HANDOFF_PENDING" and d["classification"] == "QUEUE_HANDOFF_FAILURE"
    assert j.retry_count == 2 and cs_of(j)["publish_attempts"] == 1    # production attempts untouched


def test_post_render_failure_is_never_rerun(tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x" * 4096)
    j = job(current_stage="SEO", checkpoint_data_json=json.dumps(
        {"video_path": str(video), "cycle_state": {"recovery_expiration_at": "2026-11-14T04:00:00+05:30"}}))
    d = rp.record_failure(j, ConnectionError("x"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["terminal"] and d["classification"] == "POST_RENDER_FAILURE"


def test_rearm_rules():
    started = UTC(2026, 11, 13, 19, 50)                   # process loaded new code at 01:20 IST
    exhausted = job(status="FAILED", retry_count=4)
    cp = json.loads(exhausted.checkpoint_data_json)
    cp["cycle_state"].update(error_classification="TRANSIENT", last_error_type="ValueError",
                             last_error_message='BLOCKED_QC: ["[CHECK_A_TOPIC_NARRATION] (turner)"]',
                             last_failure_at_utc="2026-11-13T19:01:12Z")
    exhausted.checkpoint_data_json = json.dumps(cp)
    now = UTC(2026, 11, 13, 20, 10)
    assert rp.rearm_if_recoverable(exhausted, now_utc=now, code_revision="new", process_started_utc=started, settings=S)
    assert exhausted.status == "RETRY_SCHEDULED" and rp.is_retry_due(exhausted, now, code_revision="new", settings=S)

    same_code = job(status="FAILED", checkpoint_data_json=json.dumps({"cycle_state": {
        **cp["cycle_state"], "failure_code_revision": "new"}}))
    assert rp.rearm_if_recoverable(same_code, now_utc=now, code_revision="new", process_started_utc=started, settings=S) is None

    uploaded = job(status="FAILED", youtube_video_id="abc", checkpoint_data_json=json.dumps(cp))
    assert rp.rearm_if_recoverable(uploaded, now_utc=now, code_revision="new", process_started_utc=started, settings=S) is None

    too_late = job(status="FAILED", checkpoint_data_json=json.dumps(cp))
    assert rp.rearm_if_recoverable(too_late, now_utc=UTC(2026, 11, 13, 22, 20), code_revision="new",
                                   process_started_utc=started, settings=S) is None


def test_bounded_rollback_policy_still_available(monkeypatch):
    monkeypatch.setenv("CRAZZYDOCS_RETRY_POLICY", "bounded")
    j = job(retry_count=2)
    d = rp.record_failure(j, ConnectionError("x"), now_utc=NOW, code_revision="r1")
    assert d["terminal"] and "retries exhausted (3/3)" in d["summary"]


def test_settings_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.scheduler.recovery_settings._config_path", lambda: tmp_path / "rp.json")
    s = save_settings({"backoff_schedule_seconds": [30, 60, 600], "min_attempt_seconds": 900})
    assert s.backoff_schedule_seconds == [30, 60, 600] and s.retry_policy == POLICY_UNLIMITED
    with pytest.raises(ValueError):
        save_settings({"retry_policy": "forever"})
    with pytest.raises(ValueError):
        save_settings({"max_attempts": 3})


# ----------------------------------------------------------------- scheduler level (temporary DB)
def rp_job_model():
    from backend.app.database.db import JobModel
    return JobModel


def _dispatch(now):
    return asyncio.run(sched_mod.dispatch_eligible_recoveries(test_ist_now=now))


def test_exhausted_cycle_inside_window_is_rearmed_and_dispatched_once(sched_db):
    _seed_failed(sched_db)                                 # FAILED, 3/3, TRANSIENT, window until 16:00 IST
    calls = []

    def pipeline(job_id=None, **_):
        calls.append(job_id)
        with sched_db() as s:                              # what the real orchestrator does on success
            j = s.get(rp_job_model(), job_id); j.status = "COMPLETED"; rp.clear_failure_state(j); s.commit()
        return {"status": "COMPLETED", "job_id": job_id}

    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline", side_effect=pipeline):
        d = _dispatch(T(14, 41))
        _dispatch(T(14, 42))                               # a second pass must not start it again
    assert [r["job_id"] for r in d["rearmed"]] == [JOB3]
    assert calls == [JOB3]


def test_expired_cycle_does_not_block_the_next_cycle(sched_db):
    cp = json.loads(json.dumps(LIVE_CP))
    cp["cycle_state"].update(next_retry_at_utc="2026-11-13T10:00:00Z", requires_change=True,
                             failure_code_revision=rp.current_code_revision())
    _seed_failed(sched_db, status="RETRY_SCHEDULED", checkpoint_data_json=json.dumps(cp))
    with patch.object(sched_mod.master_orchestrator, "execute_production_pipeline",
                      side_effect=lambda job_id=None, **_: {"status": "COMPLETED", "job_id": job_id}) as pipe:
        d = _dispatch(T(16, 5))                            # cycle 3 deadline 16:00 has passed
        r = asyncio.run(sched_mod.run_cycle_generation("cycle_4", test_ist_now=T(18, 1)))
    assert _row(sched_db).status == "RECOVERY_WINDOW_EXPIRED"
    assert any(e["job_id"] == JOB3 for e in d["expired"])
    assert r.get("status") not in ("SKIPPED", "DEFERRED") and [c.kwargs.get("job_id") for c in pipe.call_args_list] == [JOB4]
