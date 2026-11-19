"""Stage 3.30: post-render failures keep their real cause; re-evaluation after corrections; no re-render or re-upload."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.scheduler import retry_policy as rp
from backend.app.scheduler.recovery_settings import RecoverySettings

S = RecoverySettings()                                   # unlimited_until_recovery_deadline defaults
NOW = datetime(2026, 11, 20, 7, 0)                        # naive UTC = 12:30 IST
DEADLINE_IST = "2026-11-20T16:00:00+05:30"                # cycle_3 12:00 + 4 hours


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("CRAZZYDOCS_RETRY_POLICY", raising=False)
    monkeypatch.setattr("backend.app.scheduler.recovery_settings._config_path",
                        lambda: __import__("pathlib").Path("Z:/no/such/recovery_policy.json"))


def _video(tmp_path, name="v.mp4"):
    p = tmp_path / name
    p.write_bytes(b"x" * 4096)
    return str(p)


def _job(video=None, stage="QUALITY_CHECK", deadline=DEADLINE_IST, **over):
    cp = {"cycle_state": {"cycle_id": "cycle_3", "recovery_expiration_at": deadline}}
    if video:
        cp["video_path"] = video
    base = dict(id="cycle_3_20261120_1200", status="RUNNING", retry_count=0, max_retries=3, current_stage=stage,
                youtube_video_id=None, error_details=None, recovery_reason=None, checkpoint_data_json=json.dumps(cp))
    base.update(over)
    return SimpleNamespace(**base)


def cs(job):
    return json.loads(job.checkpoint_data_json)["cycle_state"]


QC_DURATION = ValueError("QC Failure: Duration 49.20s is outside Shorts 50-58s window.")


def test_recoverable_post_render_qc_failure_waits_for_a_change_then_retries(tmp_path):
    j = _job(_video(tmp_path))
    d = rp.record_failure(j, QC_DURATION, now_utc=NOW, code_revision="rev_old", settings=S)
    assert d["classification"] == "POST_RENDER_FAILURE" and not d["terminal"]
    assert j.status == "RETRY_SCHEDULED" and d["requires_change"]
    assert cs(j)["failure_cause"] == "DETERMINISTIC" and cs(j)["reuse_rendered_video"]
    later = NOW + timedelta(hours=1)
    assert not rp.is_retry_due(j, later, code_revision="rev_old", settings=S)    # same code: no loop
    assert rp.is_retry_due(j, later, code_revision="rev_new", settings=S)        # code correction deployed


def test_retry_after_configuration_correction_needs_operator_acknowledgement(tmp_path):
    j = _job(_video(tmp_path))
    rp.record_failure(j, QC_DURATION, now_utc=NOW, code_revision="r1", settings=S)
    later = NOW + timedelta(hours=1)
    assert not rp.is_retry_due(j, later, code_revision="r1", settings=S)
    rp.acknowledge_change(j, now_utc=later)                                       # config fixed, operator says so
    assert rp.is_retry_due(j, later, code_revision="r1", settings=S)


def test_permanent_post_render_failure_stays_blocked(tmp_path):
    j = _job(_video(tmp_path))
    d = rp.record_failure(j, KeyError("bad"), now_utc=NOW, code_revision="r1", settings=S)
    assert d["terminal"] and j.status == "FAILED" and cs(j)["failure_cause"] == "PERMANENT"


def test_transient_post_render_failure_backs_off(tmp_path):
    j = _job(_video(tmp_path), stage="THUMBNAIL")
    d = rp.record_failure(j, ConnectionError("reset"), now_utc=NOW, code_revision="r1", settings=S)
    assert j.status == "RETRY_SCHEDULED" and not d["requires_change"] and d["retry_in_seconds"] >= 60
    assert not rp.is_retry_due(j, NOW + timedelta(seconds=10), code_revision="r1", settings=S)   # cooldown
    assert rp.is_retry_due(j, NOW + timedelta(seconds=d["retry_in_seconds"] + 1), code_revision="r1", settings=S)


def test_deterministic_pre_render_failure_requires_change():
    j = _job(stage="SCRIPT")
    d = rp.record_failure(j, ValueError("BLOCKED_QC: script validation failed"), now_utc=NOW, code_revision="r1",
                          settings=S)
    assert d["classification"] == "DETERMINISTIC" and d["requires_change"] and j.status == "RETRY_SCHEDULED"


def test_recovery_deadline_is_enforced_and_never_extended(tmp_path):
    j = _job(_video(tmp_path), deadline="2026-11-20T12:40:00+05:30")             # 10 min left at NOW
    d = rp.record_failure(j, ConnectionError("x"), now_utc=NOW, code_revision="r1", settings=S)
    assert j.status == "RECOVERY_WINDOW_EXPIRED" and d["terminal"]
    assert cs(j)["recovery_expiration_at"] == "2026-11-20T12:40:00+05:30"


def test_attempt_history_survives_a_restart(tmp_path):
    j = _job(_video(tmp_path))
    rp.record_failure(j, QC_DURATION, now_utc=NOW, code_revision="r1", settings=S)
    reloaded = _job(**{"checkpoint_data_json": j.checkpoint_data_json, "status": j.status, "retry_count": j.retry_count})
    hist = cs(reloaded)["attempt_history"]
    assert len(hist) == 1 and hist[0]["classification"] == "POST_RENDER_FAILURE"
    assert rp.is_retry_due(reloaded, NOW + timedelta(hours=1), code_revision="r2", settings=S)


def _old_terminal_post_render(video, rev="r_old"):
    """A job failed under the previous policy: POST_RENDER_FAILURE recorded as terminal."""
    cp = {"video_path": video, "cycle_state": {
        "cycle_id": "cycle_3", "recovery_expiration_at": DEADLINE_IST, "error_classification": "POST_RENDER_FAILURE",
        "last_error_type": "ValueError", "last_error_message": str(QC_DURATION), "failure_code_revision": rev,
        "retry_terminal": True, "last_failure_at_utc": "2026-11-20T06:50:00Z"}}
    return _job(status="FAILED", retry_count=1, checkpoint_data_json=json.dumps(cp),
                error_details="[POST_RENDER_FAILURE] ValueError: QC Failure: Duration 49.20s ...")


def _probe(seconds):
    return lambda path: {"duration": seconds}


def test_reevaluation_rearms_a_post_render_job_after_a_code_correction(tmp_path, monkeypatch):
    j = _old_terminal_post_render(_video(tmp_path))
    orig = rp.post_render_reusable
    monkeypatch.setattr(rp, "post_render_reusable", lambda job, probe=None: orig(job, probe=_probe(49.2)))
    reason = rp.rearm_if_recoverable(j, now_utc=NOW, code_revision="r_new", settings=S)
    assert reason and "rendered video will be reused" in reason
    assert j.status == "RETRY_SCHEDULED" and cs(j)["reuse_rendered_video"] and cs(j)["failure_cause"] == "DETERMINISTIC"
    assert cs(j)["recovery_expiration_at"] == DEADLINE_IST                         # original deadline kept


def test_reevaluation_keeps_blocked_without_a_change_or_with_an_unusable_video(tmp_path, monkeypatch):
    orig = rp.post_render_reusable
    j = _old_terminal_post_render(_video(tmp_path), rev="same")
    monkeypatch.setattr(rp, "post_render_reusable", lambda job, probe=None: orig(job, probe=_probe(49.2)))
    assert rp.rearm_if_recoverable(j, now_utc=NOW, code_revision="same", settings=S) is None
    j2 = _old_terminal_post_render(_video(tmp_path, "w.mp4"))
    monkeypatch.setattr(rp, "post_render_reusable", lambda job, probe=None: orig(job, probe=_probe(29.0)))
    assert rp.rearm_if_recoverable(j2, now_utc=NOW, code_revision="r_new", settings=S) is None
    assert any(h.get("kind") == "reevaluation" and "still fails" in h["outcome"] for h in cs(j2)["attempt_history"])


def test_reevaluation_never_touches_expired_uploaded_or_queued_jobs(tmp_path, monkeypatch):
    orig = rp.post_render_reusable
    monkeypatch.setattr(rp, "post_render_reusable", lambda job, probe=None: orig(job, probe=_probe(49.2)))
    late = NOW + timedelta(hours=4)                                                # after 16:00 IST
    assert rp.rearm_if_recoverable(_old_terminal_post_render(_video(tmp_path)), now_utc=late,
                                   code_revision="r_new", settings=S) is None
    up = _old_terminal_post_render(_video(tmp_path))
    up.youtube_video_id = "abc"
    assert rp.rearm_if_recoverable(up, now_utc=NOW, code_revision="r_new", settings=S) is None   # no 2nd upload
    q = _old_terminal_post_render(_video(tmp_path))
    cp = json.loads(q.checkpoint_data_json)
    cp["queue_ledger_id"] = 12
    q.checkpoint_data_json = json.dumps(cp)
    assert rp.rearm_if_recoverable(q, now_utc=NOW, code_revision="r_new", settings=S) is None    # no 2nd queue entry


def test_preserved_video_reuse_check_uses_the_current_duration_rule(tmp_path):
    j = _job(_video(tmp_path))
    assert rp.post_render_reusable(j, probe=_probe(49.2))["reusable"]               # accepted since 3.29 (30-58 s)
    bad = rp.post_render_reusable(j, probe=_probe(58.1))
    assert not bad["reusable"] and "above the maximum" in bad["reason"]
    missing = _job("Z:/no/such/video.mp4")
    assert not rp.post_render_reusable(missing, probe=_probe(45))["reusable"]


def test_orchestrator_refuses_to_rerender_a_post_render_retry():
    src = (__import__("pathlib").Path(rp.__file__).resolve().parents[1] / "orchestration" /
           "crazzydocs_master_orchestrator.py").read_text(encoding="utf-8")
    i = src.index("_post_render_retry = bool(")
    guard = src[i:i + 400]
    assert "reuse_rendered_video" in guard and "raise RuntimeError" in guard and "not re-rendered" in guard
    assert src.index("_post_render_retry = bool(") < src.index("if _reuse_video:")


def test_operator_retry_accepts_post_render_only_with_a_reusable_video():
    from backend.app.services import operator_retry_service as ors
    assert rp.POST_RENDER_FAILURE in ors.UNLIMITED_RETRYABLE
    assert rp.POST_RENDER_FAILURE not in ors.RETRYABLE_CLASSIFICATIONS              # bounded policy unchanged


def test_historical_cycle_3_is_not_recovered_and_not_modified(tmp_path, monkeypatch):
    """cycle_3_20261116_1200: failed QC at 49.20 s under the old 50-58 s rule; its window closed 2026-11-16 16:00 IST.
    The corrected 30-58 s rule must not revive it: no retry, no re-render, no upload, record untouched."""
    probes = []
    monkeypatch.setattr(rp, "post_render_reusable", lambda job, probe=None: probes.append(1) or {"reusable": True})
    cp = {"video_path": _video(tmp_path), "cycle_state": {
        "recovery_expiration_at": "2026-11-16T16:00:00+05:30", "error_classification": "POST_RENDER_FAILURE",
        "last_error_type": "ValueError", "last_error_message": str(QC_DURATION), "retry_terminal": True,
        "failure_code_revision": "8ebe72f0b889f0e2", "attempt_history": [{"attempt": 1}]}}
    j = _job(status="FAILED", retry_count=1, stage="THUMBNAIL", checkpoint_data_json=json.dumps(cp),
             error_details="[POST_RENDER_FAILURE] ValueError: QC Failure: Duration 49.20s ...", id="cycle_3_20261116_1200")
    before = (j.status, j.checkpoint_data_json, j.error_details)
    assert rp.rearm_if_recoverable(j, now_utc=datetime(2026, 11, 20, 7, 0), code_revision="new", settings=S) is None
    assert (j.status, j.checkpoint_data_json, j.error_details) == before
    assert probes == []                                             # an expired job is not even probed


def _handoff_job(tmp_path, attempts=3):
    cp = {"video_path": _video(tmp_path), "queue_handoff": {"video_path": "v.mp4"},
          "cycle_state": {"recovery_expiration_at": DEADLINE_IST, "publish_attempts": attempts}}
    return _job(status="HANDOFF_PENDING", stage="PUBLISHING_QUEUE_HANDOFF", checkpoint_data_json=json.dumps(cp))


def test_quota_deferral_consumes_no_publishing_attempt_and_never_rerenders(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "quota_retry_delay_seconds", lambda now: 900)
    j = _handoff_job(tmp_path, attempts=4)
    err = RuntimeError("Publishing step pending (UPLOAD_FAILED): YouTube upload deferred: Google reported "
                       "quota_exceeded for videos.insert; resets 2026-11-20 13:30 IST.")
    for _ in range(5):                                            # a quota-blocked day never exhausts the handoff
        d = rp.record_failure(j, err, now_utc=NOW, code_revision="r1", settings=S)
    assert j.status == "HANDOFF_PENDING" and not d["terminal"]
    assert cs(j)["publish_attempts"] == 4 and cs(j)["publish_quota_deferrals"] == 5
    assert d["classification"] == "QUEUE_HANDOFF_FAILURE" and "never re-rendered or re-uploaded" in d["summary"]
    assert d["retry_in_seconds"] == 900                           # bounded wait, no rapid loop


def test_real_publishing_failure_still_counts_attempts(tmp_path):
    j = _handoff_job(tmp_path, attempts=4)
    d = rp.record_failure(j, RuntimeError("Publishing step pending (UPLOAD_FAILED): HTTP 500"), now_utc=NOW,
                          code_revision="r1", settings=S)
    assert cs(j)["publish_attempts"] == 5 and d["terminal"] and j.status == "FAILED"


def test_quota_retry_waits_for_pacific_reset_when_google_blocked(monkeypatch):
    from backend.app.services.youtube_publishing_engine import youtube_publishing_engine as eng
    class _Q:
        def blocked(self, b):
            return {"bucket": b} if b == "videos.insert" else None
    monkeypatch.setattr(type(eng), "quota", property(lambda self: _Q()))
    delay = rp.quota_retry_delay_seconds(datetime(2026, 11, 20, 5, 0))    # 21:00 PST -> reset at 08:00 UTC
    assert delay == 3 * 3600 + 60


OLD_QUOTA_MSG = ("Publishing step pending (UPLOAD_FAILED): Daily YouTube API quota limit reached (9900/10000 units "
                 "used). Upload deferred.")


def _exhausted_handoff(tmp_path, messages, video_id=None):
    hist = [{"attempt": i + 1, "kind": "publish", "classification": "QUEUE_HANDOFF_FAILURE", "message": m}
            for i, m in enumerate(messages)]
    cp = {"video_path": _video(tmp_path), "queue_handoff": {"video_path": _video(tmp_path, "h.mp4")},
          "cycle_state": {"error_classification": "QUEUE_HANDOFF_FAILURE", "publish_attempts": len(messages),
                          "retry_terminal": True, "attempt_history": hist, "recovery_expiration_at": DEADLINE_IST}}
    return _job(status="FAILED", stage="PUBLISHING_QUEUE_HANDOFF", youtube_video_id=video_id,
                checkpoint_data_json=json.dumps(cp))


def test_quota_only_handoff_failure_is_rearmed_once_without_reupload(tmp_path):
    j = _exhausted_handoff(tmp_path, [OLD_QUOTA_MSG] * 5)          # cycle_1_20261120_0000's exact history
    reason = rp.rearm_quota_blocked_handoff(j, now_utc=NOW)
    assert reason and "quota" in reason and j.status == "HANDOFF_PENDING"
    assert cs(j)["publish_attempts"] == 0 and cs(j)["publish_quota_deferrals"] == 5
    assert rp.is_retry_due(j, NOW, settings=S)
    assert rp.rearm_quota_blocked_handoff(j, now_utc=NOW) is None  # not FAILED any more: idempotent


def test_handoff_with_a_real_failure_or_an_upload_is_not_rearmed(tmp_path):
    mixed = _exhausted_handoff(tmp_path, [OLD_QUOTA_MSG] * 4 + ["Publishing step pending (UPLOAD_FAILED): HTTP 500"])
    assert rp.rearm_quota_blocked_handoff(mixed, now_utc=NOW) is None
    uploaded = _exhausted_handoff(tmp_path, [OLD_QUOTA_MSG] * 5, video_id="abc")
    assert rp.rearm_quota_blocked_handoff(uploaded, now_utc=NOW) is None


def test_quota_handoff_rearm_respects_the_original_recovery_window(tmp_path):
    """2026-11-20 deploy: cycle_5/cycle_6 of 2026-11-18 (windows long closed) were re-armed. Never again."""
    old = _exhausted_handoff(tmp_path, [OLD_QUOTA_MSG] * 5)
    late = datetime(2026, 11, 20, 10, 31)                         # after the 16:00 IST deadline of DEADLINE_IST
    assert rp.rearm_quota_blocked_handoff(old, now_utc=late) is None and old.status == "FAILED"
    cp = json.loads(old.checkpoint_data_json)
    cp["cycle_state"].pop("recovery_expiration_at")
    old.checkpoint_data_json = json.dumps(cp)
    assert rp.rearm_quota_blocked_handoff(old, now_utc=NOW) is None  # no recorded deadline: not re-armed
