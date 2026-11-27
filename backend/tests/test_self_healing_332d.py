"""Stage 3.32D self-healing operations.

Everything runs on temporary SQLite files seeded with synthetic failures, the real Stage 3.32A Operational
Intelligence service on that file, a controllable clock and fake adapters for every network / ffprobe call. No
production database, media folder, network, Gemini or YouTube call is used, and no real recovery is ever invoked.
"""
import dataclasses
import hashlib
import json
import re
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.app.autonomy.control_plane as cpm
from backend.app.autonomy import handlers, registry
from backend.app.autonomy.control_plane import ControlPlane, PermissionDenied, validate_operator
from backend.app.autonomy.recommendations import rules as rec_rules, signals as rec_signals
from backend.app.autonomy.self_healing import classification as C
from backend.app.autonomy.self_healing import engine as eng_mod
from backend.app.autonomy.self_healing import services as svc_mod
from backend.app.autonomy.self_healing import store as S
from backend.app.autonomy.self_healing.engine import ENGINE_ACTOR, SelfHealingEngine
from backend.app.autonomy.self_healing.planner import RecoveryPlanner
from backend.app.services.operational_intelligence import OperationalIntelligence
from test_operational_intelligence_332a import add_event, add_job, make_db

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
OP = "abhi"
PAD = "Padded dimensions cannot be smaller than input dimensions."
FLICKR = "https://live.staticflickr.com/65535/31649014854_abc.jpg"
J_PAD = ["cycle_1_20261119_0100", "cycle_2_20261119_0200", "cycle_3_20261119_0300"]
J_PROV = "cycle_4_20261120_0400"
J_RUN = "cycle_5_20261120_0500"
J_OK = "cycle_6_20261120_0300"
PRODUCTION_TABLES = ("persistent_jobs", "production_lifecycle_events", "creative_identity_registry")


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def checkpoint(deadline=None, attempts=(), worker=None):
    cs = {"attempt_history": list(attempts), "failure_history": []}
    if deadline:
        cs["recovery_expiration_at"] = deadline.isoformat()
    if worker:
        cs["worker_id"] = worker
    return json.dumps({"cycle_state": cs})


def seed(db, now=NOW):
    conn = make_db(str(db))
    # 1. The same FFmpeg padding error on three jobs, different filter addresses, never recovered.
    for i, jid in enumerate(J_PAD):
        at = now - timedelta(hours=20 - i)
        msg = f"[TRANSIENT] RuntimeError: FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0000023fbc{i}d0c0] {PAD}"
        add_job(conn, jid, "RECOVERY_WINDOW_EXPIRED", at, stage="STORY_DEVELOPMENT", error=msg, retry=3)
        conn.execute("UPDATE persistent_jobs SET checkpoint_data_json=?, topic_title=? WHERE id=?",
                     (checkpoint(at + timedelta(hours=4), attempts=[
                         {"attempt": k + 1, "kind": "production", "failed_at_utc": iso(at), "classification": "TRANSIENT",
                          "error_type": "RuntimeError", "message": msg[12:], "outcome": "RETRY_SCHEDULED"} for k in range(3)]),
                      f"Topic {i}", jid))
        add_event(conn, at, jid, "CYCLE_DISPATCH_FAILED", msg.split("RuntimeError: ", 1)[1])   # as recorded live
        conn.execute("INSERT INTO creative_identity_registry (job_id, normalized_topic, topic_title, category, visual_engine, "
                     "renderer_config_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (jid, f"topic {i}", f"Topic {i}", "science", "explainer", json.dumps({"width": 1080, "height": 1920}),
                      iso(at), iso(at)))
    # 2. A provider timeout on a FAILED job still inside its recovery window; the asset URL is in the error.
    at = now - timedelta(hours=1)
    msg = f"Openverse: timeout (timed out) while downloading {FLICKR}"
    add_job(conn, J_PROV, "FAILED", at, stage="ASSET_ACQUISITION", error=msg, retry=1)
    conn.execute("UPDATE persistent_jobs SET checkpoint_data_json=?, topic_title=? WHERE id=?",
                 (checkpoint(now + timedelta(hours=2)), "Nares Strait", J_PROV))
    add_event(conn, at, J_PROV, "CYCLE_DISPATCH_FAILED", msg)
    # 3. A file-lock failure on a job a worker is executing right now.
    at = now - timedelta(minutes=30)
    add_job(conn, J_RUN, "RUNNING", at, stage="STORY_DEVELOPMENT")
    conn.execute("UPDATE persistent_jobs SET heartbeat_at=?, checkpoint_data_json=? WHERE id=?",
                 ((now - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S.%f"), checkpoint(now + timedelta(hours=3)), J_RUN))
    add_event(conn, at, J_RUN, "CYCLE_DISPATCH_FAILED",
              "[WinError 32] The process cannot access the file because it is being used by another process: 'D:\\x\\temp_1.tmp'")
    # 4. A 503 that a later attempt recovered from.
    at = now - timedelta(hours=3)
    add_job(conn, J_OK, "COMPLETED", at, 30)
    add_event(conn, at, J_OK, "CYCLE_DISPATCH_FAILED", "HTTP Error 503: Service Unavailable from gnews")
    conn.commit()
    conn.close()


def table_digest(db):
    conn = sqlite3.connect(str(db))
    out = {}
    for t in PRODUCTION_TABLES:
        rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        out[t] = hashlib.sha256(repr(rows).encode()).hexdigest()
    conn.close()
    return out


def png(path, w=64, h=48):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (10, 20, 30)).save(path, format="PNG")
    return path


class Fakes:
    """Replacement adapters: no network, no ffprobe. Records every call."""

    def __init__(self, tmp_path):
        self.media, self.assets = tmp_path / "media", tmp_path / "assets"
        self.media.mkdir(exist_ok=True)
        self.assets.mkdir(exist_ok=True)
        self.calls = []
        self.provider_result = {"ok": True, "count": 3, "sample": []}
        self.provider_delay = 0.0
        self.download_mode = "ok"
        self.ffprobe_result = {"ok": True, "width": 1080, "height": 1920, "codec": "h264", "odd_dimensions": False}
        self.tail_error = None

    def services(self):
        def provider(p, q, limit=3):
            self.calls.append(("provider", p, q))
            if self.provider_delay:
                time.sleep(self.provider_delay)
            return dict(self.provider_result)

        def download(url, staging):
            self.calls.append(("download", url))
            staging.mkdir(parents=True, exist_ok=True)
            if self.download_mode == "error":
                return {"ok": False, "path": None, "sha256": None, "error": "Download failed after 3 attempts: HTTP 404"}
            path = png(staging / "staged_abc.png")
            digest = svc_mod.sha256_file(path)
            if self.download_mode == "corrupt_after":
                path.write_bytes(b"not an image any more")
            if self.download_mode == "video":
                v = staging / "staged_abc.mp4"
                v.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
                return {"ok": True, "path": str(v), "sha256": svc_mod.sha256_file(v), "error": None}
            return {"ok": True, "path": str(path), "sha256": digest, "error": None}

        def ffprobe(path, roots):
            self.calls.append(("ffprobe", str(path)))
            return dict(self.ffprobe_result)

        def tail(path, roots, **kw):
            if self.tail_error:
                raise self.tail_error
            return svc_mod.read_tail(path, roots, **kw)

        return svc_mod.Services(roots=lambda: [self.media, self.assets], read_tail=tail, ffprobe=ffprobe,
                                provider_search=provider, download_to_staging=download,
                                disk_usage=lambda paths: [{"path": str(p), "free_gb": 120.0, "total_gb": 500.0, "used_pct": 76.0}
                                                          for p in paths])


@pytest.fixture
def world(tmp_path):
    fakes = Fakes(tmp_path)
    svc_mod.set_services(fakes.services())
    yield fakes
    svc_mod.set_services(None)


def build(tmp_path, *, level="RECOMMEND", startup=True, environ=None, explainer=None, seed_fn=seed, notifications=None,
          policy=False):
    """policy=False: these tests exercise the explicit/standing approval paths, so the Remediation WS2 built-in
    automatic policy is disabled; test_remediation_ws2_autonomy_control_states.py covers it enabled."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "studio.db"
    if not db.exists():
        seed_fn(db)
    if startup:
        (data / "autonomy_startup.json").write_text('{"autonomy_execution": "enabled"}', encoding="utf-8")
    clock = Clock()
    oi = OperationalIntelligence(db_path=str(db), data_dir=str(tmp_path / "nodata"), cache_ttl=0, clock=clock)
    cp = ControlPlane(str(db), str(data), environ={}, intelligence=lambda: oi, clock=clock,
                      automatic_policy=None if policy else {})
    if level:
        cp.set_level("global", "APPROVED_EXECUTION", OP, "test")
        cp.set_level("self_healing", level, OP, "test")
    if notifications:
        cp.set_level("notifications", notifications, OP, "test")
    eng = SelfHealingEngine(cp, environ=environ or {}, explainer=explainer, clock=clock)
    return cp, eng, clock, db


def incidents(eng, status="all"):
    return {i["job_id"]: i for i in eng.store.list_incidents(status, 500)}


def events(cp):
    return [e["event_type"] for e in reversed(cp.audit_events(limit=500))]


def recs(eng, job_id, action=None):
    inc = incidents(eng)[job_id]
    out = eng.store.recoveries_for(inc["id"])
    return [r for r in out if action is None or r["action_type"] == action]


def approve(eng, rec):
    return eng.approve(rec["id"], OP, rec["payload_hash"], "test approval")


# -- 1. classification ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("message,expected,certainty", [
    ("Openverse: HTTP 503 Service Unavailable", C.TRANSIENT, "STRONG"),
    ("ConnectionResetError: connection reset by peer", C.TRANSIENT, "STRONG"),
    (f"FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0x1] {PAD}", C.DETERMINISTIC, "CONFIRMED"),
    ("[AVFilterGraph @ 0x1] No such filter: 'padd' Error initializing filter", C.DETERMINISTIC, "STRONG"),
    ("[libx264 @ 0x1] width not divisible by 2 (1081x1920)", C.DETERMINISTIC, "STRONG"),
    ("OSError: [Errno 28] No space left on device", C.RESOURCE, "CONFIRMED"),
    ("MemoryError: cannot allocate memory", C.RESOURCE, "CONFIRMED"),
    ("[WinError 32] The process cannot access the file because it is being used by another process", C.RESOURCE, "STRONG"),
    ("HttpError 403: insufficient_scope for videos.update", C.CONFIGURATION, "CONFIRMED"),
    ("google.auth: invalid_grant: Token has been expired or revoked", C.CONFIGURATION, "CONFIRMED"),
    ("API key not valid. Please pass a valid API key.", C.CONFIGURATION, "STRONG"),
    ("[mov,mp4 @ 0x1] moov atom not found", C.DATA, "CONFIRMED"),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'narration.wav'", C.DATA, "STRONG"),
    ("JSONDecodeError: Expecting value: line 1 column 1 (char 0)", C.DATA, "STRONG"),
    ("Publishing step pending (UPLOAD_FAILED): Daily YouTube API quota limit reached (10000/10000 units)", C.RESOURCE, "CONFIRMED"),
    ("Something odd happened in stage X", C.UNKNOWN, "WEAK"),
    ("", C.UNKNOWN, "WEAK"),
])
def test_all_six_failure_classes(message, expected, certainty):
    c = C.classify(message)
    assert c.failure_class == expected and c.certainty == certainty, (message, c)
    assert c.signature.startswith("sig_") and c.mechanism
    if expected == C.UNKNOWN:
        assert c.basis == "none" and c.notes                       # says why it is unknown


# -- 2. deterministic versus transient --------------------------------------------------------------------------
def test_deterministic_versus_transient_uses_signature_and_recurrence():
    pad = C.classify(f"[TRANSIENT] RuntimeError: FFmpeg failed: [Parsed_pad_2 @ 0x1] {PAD}")
    assert pad.failure_class == C.DETERMINISTIC and pad.retry_policy_class == "TRANSIENT"
    assert any("retry policy is not changed" in n for n in pad.notes)          # the existing policy is only compared
    # Never TRANSIENT merely because it happened in production: the retry policy defaults unknown errors to TRANSIENT.
    odd = C.classify("the widget returned an unexpected shape")
    assert odd.failure_class == C.UNKNOWN and odd.retry_policy_class == "TRANSIENT"
    # A transient-looking signature that keeps recurring and never recovers becomes suspected deterministic ...
    msg = "Openverse: timeout (timed out)"
    sig = C.signature_of(msg)
    hist = [{"signature": sig, "job_id": f"j{i % 2}", "at": f"2026-11-20T0{i}:00:00Z", "recovered_later": False} for i in range(3)]
    c = C.classify(msg, history=hist)
    assert c.failure_class == C.DETERMINISTIC and c.certainty == "WEAK" and c.basis == "signature+recurrence"
    # ... but keeps TRANSIENT (and is strengthened) when a later attempt succeeded.
    hist[0]["recovered_later"] = True
    c2 = C.classify(msg, history=hist)
    assert c2.failure_class == C.TRANSIENT and c2.basis == "signature+recurrence"
    # An unknown signature with the same recurrence: suspected deterministic, not certain.
    u = "mystery failure in step Q"
    uh = [{"signature": C.signature_of(u), "job_id": f"j{i}", "at": "x", "recovered_later": False} for i in range(3)]
    cu = C.classify(u, history=uh)
    assert cu.failure_class == C.DETERMINISTIC and cu.certainty == "WEAK" and cu.basis == "recurrence"
    # A recovery-window expiry note is an outcome, not an error: never escalated by recurrence (live data: 27 jobs).
    note = "Recovery window expired at 2026-11-19T16:00:00+05:30; reconciliation occurred at 2026-11-19T23:13:22+05:30."
    nh = [{"signature": C.signature_of(note), "job_id": f"j{i}", "at": "x", "recovered_later": False} for i in range(27)]
    cn = C.classify(note, history=nh)
    assert cn.failure_class == C.UNKNOWN and cn.subtype == "deadline_outcome" and "outcome" in cn.mechanism
    assert C.classify("Cycle is no longer in the active schedule and no worker holds this job").subtype == "deadline_outcome"


# -- 3 / 4. correlation and evidence -------------------------------------------------------------------------------
def test_historical_correlation_and_evidence(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="OBSERVE")
    out = eng.scan(triggered_by="operator:abhi", force=True)
    assert out["ran"] and out["incidents_created"] == 6 and out["proposals"] == 0
    inc = incidents(eng)
    pads = [inc[j] for j in J_PAD]
    assert len({p["signature"] for p in pads}) == 1                                 # different addresses, one signature
    last = pads[-1]
    assert last["failure_class"] == "DETERMINISTIC" and last["certainty"] == "CONFIRMED" and last["severity"] == "critical"
    d = last["diagnosis"]
    co = d["correlation"]
    assert co["occurrences"] == 3 and co["distinct_jobs"] == 3 and co["likely_deterministic"] is True
    assert co["previous_retries_succeeded"] is False and co["same_error_on_retry_this_job"] == 3
    assert co["affected_stages"] == {"STORY_DEVELOPMENT": 3}
    assert co["same_dimensions_recur"] is None                                         # not recorded: not invented
    assert any(f["strength"] == "INSUFFICIENT_EVIDENCE" for f in d["findings"])
    assert d["root_cause"]["status"] == "CONFIRMED_MECHANISM" and "not proven" in d["root_cause"]["statement"]
    ev = d["evidence"]
    assert PAD in ev["error_message"] and ev["exception_type"] == "RuntimeError" and ev["pipeline_stage"] == "STORY_DEVELOPMENT"
    assert ev["job_id"] == J_PAD[-1] and ev["attempt_number"] == 3 and ev["first_seen_utc"] and ev["last_seen_utc"]
    assert any("CYCLE_DISPATCH_FAILED" in l["text"] for l in ev["log_context"])
    assert {r["job_id"] for r in ev["related_failures"]} == set(J_PAD[:2])
    assert ev["renderer"]["visual_engine"] == "explainer" and len(ev["recovery_history"]["attempts"]) == 3
    # The 503 that a later attempt recovered from stays TRANSIENT and says so.
    ok = inc[J_OK]
    assert ok["failure_class"] == "TRANSIENT" and ok["diagnosis"]["correlation"]["previous_retries_succeeded"] is True
    # Dimensions recorded in the error text are correlated across jobs.
    from backend.app.autonomy.self_healing.diagnostics import DiagnosticEngine
    hist = [{"signature": "s", "job_id": "a", "message": "pad=1080:1920 failed", "stage": "R"},
            {"signature": "s", "job_id": "b", "message": "pad=1080:1920 failed", "stage": "R"}]
    cl = C.classify("pad=1080:1920 failed")
    cl.signature = "s"
    dd = DiagnosticEngine().diagnose({"id": "inc_x", "job_id": "a", "message": "pad=1080:1920 failed"}, cl, {"found": False, "missing": []}, hist, NOW)
    assert dd["correlation"]["same_dimensions_recur"] is True and "pad=1080:1920" in dd["correlation"]["recurring_dimensions"]


def test_missing_diagnostic_data_is_reported_not_invented(tmp_path, world):
    def bare(db):
        conn = make_db(str(db))
        add_job(conn, "cycle_9_20261120_0100", "FAILED", NOW - timedelta(hours=2), error="something unexpected went wrong")
        add_job(conn, "cycle_9_20261120_0200", "RECOVERY_WINDOW_EXPIRED", NOW - timedelta(hours=1), stage="RECOVERY_WINDOW_EXPIRED")
        conn.commit()
        conn.close()
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND", seed_fn=bare)
    eng.scan(force=True)
    inc = incidents(eng)
    a = inc["cycle_9_20261120_0100"]
    d = a["diagnosis"]
    assert a["failure_class"] == "UNKNOWN" and d["root_cause"]["status"] == "UNKNOWN"
    assert any("recovery deadline" in m for m in d["missing"]) and any("renderer" in m for m in d["missing"])
    assert any("progress record" in m for m in d["missing"]) and d["completeness"] < 1
    assert d["evidence"]["renderer"] is None and d["correlation"]["same_dimensions_recur"] is None
    b = inc["cycle_9_20261120_0200"]                                                     # expired with no error text
    assert b["failure_class"] == "UNKNOWN" and b["plan"]["actions"] == []
    assert b["classification"]["subtype"] == "deadline_outcome" and "outcome note" in b["plan"]["summary"]
    assert b["diagnosis"]["correlation"]["likely_deterministic"] is False


# -- 5 / 6. planning and recovery windows ------------------------------------------------------------------------
def test_recovery_planning(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="OBSERVE")
    eng.scan(force=True)
    inc = incidents(eng)
    prov = inc[J_PROV]["plan"]
    ck = prov["checks"]
    assert ck["failure_class"] == "TRANSIENT" and ck["retry_useful"]["useful"] is True
    assert ck["inside_recovery_window"]["inside"] is True and ck["worker_active"]["active"] is False
    assert ck["already_recovering"]["by_scheduler"] is False and ck["safe"] is True and ck["recovery_possible"] is True
    kinds = {a["action_type"]: a for a in prov["actions"]}
    assert kinds["selfheal.provider.retry_readonly"]["payload"] == {"incident_id": inc[J_PROV]["id"], "provider": "Openverse",
                                                                    "query": "Nares Strait"}
    assert kinds["selfheal.asset.reacquire_staged"]["payload"]["asset_url"] == FLICKR
    for a in prov["actions"]:
        assert a["requires_approval"] and a["expected_cost"]["youtube_quota_units"] == 0 and a["resources"] and not a["blocked"]
    assert kinds["selfheal.provider.retry_readonly"]["approval"] == "EXPLICIT_EACH"
    pad = inc[J_PAD[-1]]["plan"]
    assert pad["checks"]["retry_useful"]["useful"] is False and pad["checks"]["recovery_possible_by_self_healing"] is False
    assert {o["prohibited_action_type"] for o in pad["operator_only"]} == {"recovery.job.retry", "render.video.rerender"}
    assert all(not o["allowed_for_autonomy"] for o in pad["operator_only"])
    assert {a["payload"]["diagnostic"] for a in pad["actions"]} == {"validate_ffmpeg_filter", "collect_log_context",
                                                                     "generate_diagnostic_report"}
    run = inc[J_RUN]                                                                     # a worker is executing it
    assert run["status"] == "UNSAFE" and run["plan"]["checks"]["worker_active"]["active"] is True
    assert all(a["blocked"] for a in run["plan"]["actions"]) and run["plan"]["unsafe_reasons"]


def test_recovery_window_checks(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="OBSERVE")
    eng.scan(force=True)
    inc = incidents(eng)[J_PROV]
    job = eng.evidence().job(J_PROV)
    planner = RecoveryPlanner()
    later = NOW + timedelta(hours=3)                                                   # past the deadline (NOW + 2 h)
    p = planner.plan(inc, inc["diagnosis"], job, store=eng.store, learning=[], now=later)
    assert p["checks"]["inside_recovery_window"]["inside"] is False
    rev = [a for a in p["actions"] if a["kind"] == "reversible"]
    assert rev and all("outside its recovery window" in a["blocked"] for a in rev)
    assert all(not a["blocked"] for a in p["actions"] if a["kind"] == "diagnostic")       # diagnostics still allowed
    job2 = dict(job, cycle_state={})                                                    # deadline not recorded
    p2 = planner.plan(inc, inc["diagnosis"], job2, store=eng.store, learning=[], now=NOW)
    assert p2["checks"]["inside_recovery_window"]["known"] is False
    assert all(a["blocked"] for a in p2["actions"] if a["kind"] == "reversible")


# -- 7 / 8 / 16. duplicates, concurrency, locks -------------------------------------------------------------------
def test_duplicate_recovery_prevention(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    first = eng.scan(force=True)
    n_rec = len(eng.store.list_recoveries("all", 500))
    n_req = len(cp.list_requests(limit=200))
    assert first["proposals"] == n_rec == n_req > 0
    clock.advance(minutes=10)
    again = eng.scan(force=True)
    assert again["proposals"] == 0 and len(eng.store.list_recoveries("all", 500)) == n_rec
    assert len(cp.list_requests(limit=200)) == n_req
    inc = incidents(eng)[J_PROV]
    p = RecoveryPlanner().plan(inc, inc["diagnosis"], eng.evidence().job(J_PROV), store=eng.store, learning=[], now=clock())
    assert all("already proposed" in a["blocked"] for a in p["actions"])


def test_concurrent_execution_prevention(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    world.provider_delay = 0.4
    results = []
    threads = [threading.Thread(target=lambda: results.append(eng.cp.execute(rec["request_id"], "operator:abhi"))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if r["executed"]) == 1                                 # one claim, one execution
    assert [c for c in world.calls if c[0] == "provider"] == [("provider", "Openverse", "Nares Strait")]
    eng.sync()
    assert eng.store.get_recovery(rec["id"])["status"] == "RECOVERED"


def test_resource_lock_protection(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    inc = incidents(eng)[J_PROV]
    key = f"job:{J_PROV}"
    eng.store.acquire_locks([key], "apr_someoneelse0000", 300, clock())
    with pytest.raises(S.ResourceBusy):
        eng.store.acquire_locks([key, "provider:Openverse"], "apr_other", 300, clock())
    assert eng.store.held_locks(["provider:Openverse"], clock()) == []                # all-or-nothing
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    res = eng.execute(rec["id"], OP)                                                     # engine pre-check sees the lock
    assert res["status"] == "UNSAFE" and "lock" in res["reason"] and world.calls == []
    assert cp.get_request(rec["request_id"])["status"] == "CANCELLED"
    # A handler started some other way (e.g. the generic Autonomy Control endpoint) also refuses the busy resource.
    rec2 = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, rec2)
    out = cp.execute(rec2["request_id"], "operator:abhi")
    assert out["executed"] is False and "locked" in (out["error"] or "") and not [c for c in world.calls if c[0] == "download"]
    planned = RecoveryPlanner().plan(inc, inc["diagnosis"], eng.evidence().job(J_PROV), store=eng.store, learning=[], now=clock())
    assert any(a["blocked"] and a["blocked"].startswith("resource lock held") for a in planned["actions"])
    clock.advance(minutes=6)                                                            # lock expired: reclaimed
    eng.store.acquire_locks([key], "apr_new", 60, clock())


# -- 9 / 10 / 11. kill switches, approval, self-approval ----------------------------------------------------------
@pytest.mark.parametrize("switch", ["dashboard", "sentinel", "startup"])
def test_kill_switch_enforcement(tmp_path, world, switch):
    """Emergency stops (dashboard, sentinel) block scans and execution. Remediation WS2 changed the "startup" case on
    purpose: a missing startup configuration is RESTRICTED autonomy, not an emergency stop, so scanning continues
    while explicit-approval recoveries are still refused (see test_missing_startup_config_is_restricted_not_blocked)."""
    if switch == "startup":
        pytest.skip("covered by test_missing_startup_config_is_restricted_not_blocked (Remediation WS2)")
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION", startup=(switch != "startup"))
    if switch != "startup":
        cp2 = ControlPlane(cp.db_path, str(cp.data_dir), environ={}, intelligence=cp.intelligence, clock=clock,
                           automatic_policy={})
        eng2 = SelfHealingEngine(cp2, environ={}, clock=clock)
        eng2.scan(force=True)                                            # incidents + proposals exist before the switch
        rec = recs(eng2, J_PROV, "selfheal.provider.retry_readonly")[0]
        approve(eng2, rec)
        if switch == "dashboard":
            cp.set_dashboard_kill_switch(True, OP, "test stop")
        else:
            cp.sentinel_path.write_text("stop", encoding="utf-8")
        res = eng.execute(rec["id"], OP)
        assert res["executed"] is False and "kill switch" in (res["reason"] or "") and world.calls == []
        assert eng.overview()["incidents"]                                            # still readable
    before = len(eng.store.list_recoveries("all", 500)) if switch != "startup" else None
    out = eng.scan(force=True)
    assert out["ran"] is False and out["mode"] == "BLOCKED"
    assert "SELF_HEALING_SCAN_BLOCKED" in events(cp)
    if before is not None:
        assert len(eng.store.list_recoveries("all", 500)) == before
    assert eng.state()["mode"] == "BLOCKED" and eng.overview()["state"]["kill_switches"]


def test_missing_startup_config_is_restricted_not_blocked(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION", startup=False)
    st = eng.state()
    assert st["mode"] == "APPROVED_EXECUTION" and st["control_state"] == "RESTRICTED"
    out = eng.scan(force=True)
    assert out["ran"] is True and "SELF_HEALING_SCAN_BLOCKED" not in events(cp)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    res = eng.execute(rec["id"], OP)                     # explicit approval: still needs the startup configuration
    assert res["executed"] is False and "startup_config" in (res["reason"] or "") and world.calls == []


def test_approval_enforcement(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    assert rec["status"] == "AWAITING_APPROVAL" and cp.get_request(rec["request_id"])["status"] == "PENDING"
    assert eng.execute(rec["id"], OP)["executed"] is False                               # not approved
    approve(eng, rec)
    res = eng.execute(rec["id"], OP)                                                     # approved, but level RECOMMEND
    assert res["executed"] is False and "APPROVED_EXECUTION" in res["reason"] and world.calls == []
    with pytest.raises(cpm.ControlPlaneError):                                           # wrong hash is refused
        eng.approve(recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]["id"], OP, "0" * 64)
    with pytest.raises(PermissionDenied):                                                # explicit-each: no standing approval
        cp.grant_standing_approval("selfheal.provider.retry_readonly", OP, "x", max_per_day=1, valid_hours=1)
    with pytest.raises(PermissionDenied):
        cp.grant_standing_approval("selfheal.asset.reacquire_staged", OP, "x", max_per_day=1, valid_hours=1)


def test_standing_approval_runs_only_low_risk_diagnostics(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    cp.grant_standing_approval("selfheal.diagnostic.run", OP, "routine diagnostics", max_per_day=20, valid_hours=4)
    out = eng.scan(force=True)
    assert out["executed"] > 0
    allr = eng.store.list_recoveries("all", 500)
    diag = [r for r in allr if r["action_type"] == "selfheal.diagnostic.run"]
    other = [r for r in allr if r["action_type"] != "selfheal.diagnostic.run"]
    assert diag and all(r["status"] == "DIAGNOSED" and r["verification_result"] == "VERIFIED" for r in diag)
    assert all(r["approval_outcome"] == "STANDING" for r in diag)
    assert other and all(r["status"] == "AWAITING_APPROVAL" for r in other)              # recovery actions still wait
    assert not [c for c in world.calls if c[0] in ("provider", "download")]
    pad = incidents(eng)[J_PAD[-1]]
    af = pad["diagnosis"]["action_findings"]
    assert "validate_ffmpeg_filter" in af and af["validate_ffmpeg_filter"]["data"]["error_states_pad_smaller_than_input"] is True
    assert af["validate_ffmpeg_filter"]["data"]["parameters_recorded"] is False          # honest about missing params
    report = af["generate_diagnostic_report"]["data"]
    assert Path(report["path"]).is_file() and svc_mod.sha256_file(Path(report["path"])) == report["sha256"]


def test_self_approval_and_identity_protection(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    before = cp.permissions()
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    for ident in (ENGINE_ACTOR, "Self-Healing-Engine", "self healing", "SELFHEAL", "self.healing bot", "self_healing",
                  "recommendation-engine", "autonomy-engine", "standing-approval:sa_1", "control-plane"):
        with pytest.raises(PermissionDenied):
            validate_operator(ident)
        with pytest.raises(PermissionDenied):
            eng.approve(rec["id"], ident, rec["payload_hash"])
        with pytest.raises(PermissionDenied):
            eng.reject(rec["id"], ident, "no")
        with pytest.raises(PermissionDenied):
            eng.execute(rec["id"], ident)
        with pytest.raises(PermissionDenied):
            cp.approve(rec["request_id"], ident, rec["payload_hash"])
        with pytest.raises(PermissionDenied):
            cp.set_level("self_healing", "OFF", ident, "x")
        with pytest.raises(PermissionDenied):
            cp.grant_standing_approval("selfheal.diagnostic.run", ident, "x", max_per_day=1, valid_hours=1)
    assert cp.get_request(rec["request_id"])["status"] == "PENDING"
    after = cp.permissions()
    assert after["category_levels"] == before["category_levels"] and after["standing_approvals"] == before["standing_approvals"]
    assert all(e["actor"] != ENGINE_ACTOR for e in cp.audit_events(limit=500)
               if e["event_type"] in ("APPROVED", "PERMISSION_CHANGED", "KILL_SWITCH_DEACTIVATED", "REJECTED", "CANCELLED"))
    # Every request the engine created is attributed to it, never to an operator.
    assert {r["proposed_by"] for r in cp.list_requests(limit=200)} == {ENGINE_ACTOR}


# -- 12 / 13. timeout and retry limits -----------------------------------------------------------------------------
def test_action_timeout(tmp_path, world, monkeypatch):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    spec = registry.REGISTRY["selfheal.provider.retry_readonly"]
    monkeypatch.setitem(registry.REGISTRY, spec.action_type, dataclasses.replace(spec, timeout_seconds=0.2))
    world.provider_delay = 1.0
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    res = eng.execute(rec["id"], OP)
    assert res["executed"] is False
    r = eng.store.get_recovery(rec["id"])
    assert r["status"] == "FAILED" and "timed out" in r["status_reason"] and r["attempts"] == 1
    assert [e["outcome"] for e in cp.list_executions()] == ["TIMED_OUT"]


def test_retry_limits(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    world.tail_error = OSError("disk read error")                                          # every attempt fails
    rec = [r for r in recs(eng, J_PAD[-1], "selfheal.diagnostic.run") if r["payload"]["diagnostic"] == "collect_log_context"][0]
    inc = incidents(eng)[J_PAD[-1]]
    with eng.store.tx() as conn:                                                        # give it a log path to read
        diag = inc["diagnosis"]
        diag["evidence"]["event_log_path"] = str(world.media / "productions" / J_PAD[-1] / "reports" / "events.jsonl")
        eng.store.set_incident(conn, inc["id"], clock(), diagnosis=diag)
    approve(eng, rec)
    eng.execute(rec["id"], OP)
    execs = [e for e in cp.list_executions() if e["request_id"] == rec["request_id"]]
    assert len(execs) == registry.REGISTRY["selfheal.diagnostic.run"].retry_limit + 1 == 2
    assert eng.store.get_recovery(rec["id"])["status"] == "FAILED"
    # provider retry: retry_limit 0 -> exactly one attempt
    world.provider_result = {"ok": False, "error": "Openverse: timeout"}
    prov = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, prov)
    eng.execute(prov["id"], OP)
    assert len([e for e in cp.list_executions() if e["request_id"] == prov["request_id"]]) == 1
    # After failed recovery actions the planner stops proposing further ones for the incident.
    stage = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    world.download_mode = "error"
    approve(eng, stage)
    eng.execute(stage["id"], OP)
    inc = incidents(eng)[J_PROV]
    p = RecoveryPlanner().plan(inc, inc["diagnosis"], eng.evidence().job(J_PROV), store=eng.store, learning=[], now=clock())
    assert all("already failed" in a["blocked"] for a in p["actions"] if a["kind"] == "reversible")


# -- 14 / 15. verification ---------------------------------------------------------------------------------------
def test_post_action_verification(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    stage = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, stage)
    res = eng.execute(stage["id"], OP)
    assert res["executed"] is True
    r = eng.store.get_recovery(stage["id"])
    assert r["status"] == "RECOVERED" and r["verification_result"] == "VERIFIED"
    assert r["verification"]["verdict"] == "VERIFIED" and r["verification"]["independent_check"]["ok"] is True
    staged = Path(r["execution_result"]["result"]["path"])
    assert staged.is_file() and (cp.autonomy_dir / "self_healing" / "staging") in staged.parents
    assert not (world.media / "productions").exists()                                    # nothing placed into the job
    assert incidents(eng)[J_PROV]["status"] == "AWAITING_APPROVAL"                       # another request is still open
    prov = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, prov)
    eng.execute(prov["id"], OP)
    assert eng.store.get_recovery(prov["id"])["status"] == "RECOVERED"
    pending = [r for r in recs(eng, J_PROV) if r["status"] in S.OPEN_RECOVERY]
    for r in pending:                                                                    # close the remaining diagnostics
        eng.reject(r["id"], OP, "not needed")
    assert incidents(eng)[J_PROV]["status"] == "RECOVERED"
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT status FROM persistent_jobs WHERE id=?", (J_PROV,)).fetchone()[0] == "FAILED"   # job untouched
    conn.close()


@pytest.mark.parametrize("mode,verdict", [("error", "FAILED"), ("corrupt_after", "FAILED"), ("video_no_ffprobe", "UNVERIFIED")])
def test_failed_verification_is_never_success(tmp_path, world, mode, verdict):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    world.download_mode = "video" if mode == "video_no_ffprobe" else mode
    if mode == "video_no_ffprobe":
        world.ffprobe_result = {"ok": False, "reason": "ffprobe is not available"}
    rec = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, rec)
    res = eng.execute(rec["id"], OP)
    r = eng.store.get_recovery(rec["id"])
    assert res["executed"] is False and r["status"] == "FAILED" and r["final_status"] == "FAILED"
    assert r["verification_result"] == verdict
    staging = cp.autonomy_dir / "self_healing" / "staging" / rec["request_id"]
    if mode != "error":
        assert not any(staging.glob("*"))                                                # compensation removed the file
    assert incidents(eng)[J_PROV]["status"] in ("FAILED", "AWAITING_APPROVAL")


def test_independent_check_catches_tampering(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, rec)
    real_sync = eng.sync
    eng.sync = lambda run=None: 0                                                        # finish execution, sync later
    eng.execute(rec["id"], OP)
    req = cp.get_request(rec["request_id"])
    Path(req["execution_result"]["result"]["path"]).write_bytes(b"changed after verification")
    eng.sync = real_sync
    eng.sync()
    r = eng.store.get_recovery(rec["id"])
    assert r["status"] == "FAILED" and r["verification_result"] == "FAILED"
    assert r["verification"]["independent_check"]["ok"] is False


# -- 17. recommendation integration -----------------------------------------------------------------------------
def test_recommendation_integration_and_learning(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    world.provider_result = {"ok": False, "error": "Openverse: HTTP 503"}
    for _ in range(2):                                                                  # the same action fails twice
        rec = [r for r in recs(eng, J_PROV, "selfheal.provider.retry_readonly") if r["status"] == "AWAITING_APPROVAL"][0]
        approve(eng, rec)
        eng.execute(rec["id"], OP)
        with eng.store.tx() as conn:                                                     # allow a second proposal
            conn.execute("UPDATE selfheal_recoveries SET status='CANCELLED' WHERE incident_id=? AND action_type=? AND status NOT IN ('FAILED')",
                         (rec["incident_id"], "selfheal.asset.reacquire_staged"))
        if _ == 0:
            inc = incidents(eng)[J_PROV]
            eng._propose(inc, dict(inc["plan"], actions=[dict(a, blocked=None) for a in inc["plan"]["actions"]
                                                         if a["action_type"] == "selfheal.provider.retry_readonly"]), clock(), {
                "proposals": 0, "executed": 0, "errors": [], "notifications": {"sent": 0, "queued": 0, "deduplicated": 0,
                                                                                  "rate_limited": 0, "suppressed_level": 0}})
    learning = S.read_learning(cp.db_path)
    row = [l for l in learning["learning"] if l["action_type"] == "selfheal.provider.retry_readonly"][0]
    assert row["attempts"] == 2 and row["failed"] == 2 and row["succeeded"] == 0
    assert learning["deterministic"][0]["occurrences"] == 3
    # The planner no longer proposes the ineffective action ...
    inc = incidents(eng)[J_PROV]
    p = RecoveryPlanner().plan(inc, inc["diagnosis"], eng.evidence().job(J_PROV), store=eng.store,
                               learning=eng.store.learning(), now=clock())
    pr = [a for a in p["actions"] if a["action_type"] == "selfheal.provider.retry_readonly"][0]
    assert pr["blocked"] and ("learning" in pr["blocked"] or "already failed" in pr["blocked"])
    # ... and the 3.32C rules turn the outcomes into recommendations (read-only, no autonomy tables created).
    sig = rec_signals.Signals(window_days=7, now=NOW, sections={}, bundle={"jobs": []}, failures=[], self_healing=learning)
    out = rec_rules.RuleOutput()
    rec_rules.rule_selfheal_ineffective(sig, out)
    rec_rules.rule_selfheal_deterministic_defect(sig, out)
    ids = {f.rule_id: f for f in out.findings}
    assert "recovery.selfheal_ineffective" in ids and "Production retry policies are not changed" in ids["recovery.selfheal_ineffective"].action
    det = ids["recovery.repeated_deterministic_defect"]
    assert det.hypothesis_strength == "CONFIRMED" and det.occurrences == 3 and set(det.affected_jobs) == set(J_PAD)
    none = rec_rules.RuleOutput()
    rec_rules.rule_selfheal_success_pattern(rec_signals.Signals(7, NOW, {}, {}, [], self_healing=None), none)
    assert none.insufficient and not none.findings                                         # no data: nothing invented


def test_recommendation_engine_reads_learning_without_creating_tables(tmp_path):
    db = tmp_path / "plain.db"
    sqlite3.connect(db).close()
    assert S.read_learning(str(db)) is None
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")}
    assert not any(n.startswith("selfheal_") for n in names)


# -- 18. audit -----------------------------------------------------------------------------------------------------
def test_audit_trail_integrity(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION", notifications="RECOMMEND")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, rec)
    eng.execute(rec["id"], OP)
    rej = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    eng.reject(rej["id"], OP, "not needed")
    ev = events(cp)
    for e in ("SELF_HEALING_INCIDENT_DETECTED", "ACTION_PROPOSED", "APPROVED", "EXECUTION_STARTED", "EXECUTION_COMPLETED",
              "SELF_HEALING_RECOVERY_FINISHED", "REJECTED", "SELF_HEALING_SCAN_COMPLETED"):
        assert e in ev, e
    assert cp.verify_audit_chain()["ok"] is True
    assert eng.store.get_recovery(rej["id"])["status"] == "CANCELLED" and eng.store.get_recovery(rej["id"])["approval_outcome"] == "REJECTED"
    conn = sqlite3.connect(str(db))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE autonomy_audit_events SET reason='x'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM autonomy_audit_events")
    conn.close()
    detail = eng.incident_detail(incidents(eng)[J_PROV]["id"])
    assert detail["audit"] and detail["recoveries"]


# -- levels, notifications, Gemini, incremental scans --------------------------------------------------------------
def test_level_off_and_observe(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level=None)
    out = eng.scan(force=True)
    assert out["ran"] is False and out["mode"] == "OFF"
    names = {r[0] for r in sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master")}
    assert not any(n.startswith("selfheal_") for n in names)                            # OFF writes nothing
    cp.set_level("global", "APPROVED_EXECUTION", OP, "t")
    cp.set_level("self_healing", "OBSERVE", OP, "t")
    out = eng.scan(force=True)
    assert out["ran"] and out["incidents_created"] and out["proposals"] == 0 and cp.list_requests() == []
    assert cp.list_executions() == [] and world.calls == []


def test_notifications_dedup_and_rate_limit(tmp_path, world, monkeypatch):
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND", notifications="RECOMMEND")
    out = eng.scan(force=True)
    n = out["notifications"]
    notes = [r for r in cp.list_requests(limit=200) if r["action_type"] == "operator.notify"]
    subjects = [r["payload"]["subject"] for r in notes]
    # one alert per signature (not per job), plus ONE aggregated approval-queue notice instead of one per request
    assert n["queued"] == len(notes) == 3 and n["rate_limited"] == 0
    assert subjects[0].startswith("Critical failure") and "Repeated deterministic error" in subjects
    assert sum(1 for s in subjects if "await approval" in s) == 1
    clock.advance(minutes=10)
    conn = sqlite3.connect(str(db))                                                       # the defect recurs
    add_event(conn, clock() - timedelta(minutes=1), J_PAD[2], "CYCLE_DISPATCH_FAILED",
              f"FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0000023fbc7d0c0] {PAD}")
    conn.commit()
    conn.close()
    again = eng.scan(force=True)                                                          # same signature: cooldown
    assert again["incidents_updated"] == 1 and again["notifications"]["queued"] == 0
    assert again["notifications"]["deduplicated"] == 2
    assert len([r for r in cp.list_requests(limit=200) if r["action_type"] == "operator.notify"]) == 3
    # Per-scan budget.
    monkeypatch.setattr(eng_mod, "MAX_NOTIFICATIONS_PER_SCAN", 1)
    tp = tmp_path / "budget"
    tp.mkdir()
    cpb, engb, _, _ = build(tp, level="RECOMMEND", notifications="RECOMMEND")
    nb = engb.scan(force=True)["notifications"]
    assert nb["queued"] == 1 and nb["rate_limited"] >= 1
    cp2, eng2, clock2, db2 = build(tmp_path / "quiet", level="RECOMMEND")                # notifications OFF
    out2 = eng2.scan(force=True)
    assert out2["notifications"]["suppressed_level"] > 0 and not [r for r in cp2.list_requests(limit=200)
                                                                  if r["action_type"] == "operator.notify"]


def test_gemini_disabled_by_default_bounded_and_optional(tmp_path, world):
    calls = []
    cp, eng, clock, db = build(tmp_path, level="OBSERVE")
    assert eng.explainer is None and eng.state()["explainer"] == "off"
    eng.scan(force=True)
    assert all(not i.get("explanation") for i in incidents(eng).values())
    tp = tmp_path / "g"
    tp.mkdir()
    ex = eng_mod.DiagnosticExplainer(generate=lambda prompt: calls.append(prompt) or "Plain words.")
    cp2, eng2, clock2, _ = build(tp, level="OBSERVE", environ={eng_mod.EXPLAINER_ENV: "gemini"}, explainer=ex)
    eng2.scan(force=True)
    assert len(calls) == eng_mod.MAX_EXPLANATIONS_PER_SCAN
    assert all("do not suggest actions" in p for p in calls)
    tp3 = tmp_path / "g3"
    tp3.mkdir()
    bad = eng_mod.DiagnosticExplainer(generate=lambda prompt: (_ for _ in ()).throw(RuntimeError("Gemini down")))
    cp3, eng3, clock3, _ = build(tp3, level="OBSERVE", environ={eng_mod.EXPLAINER_ENV: "gemini"}, explainer=bad)
    out = eng3.scan(force=True)
    assert out["ran"] and out["incidents_created"] == 6                                   # Gemini failure blocks nothing


def test_incremental_scan_and_throttle(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="OBSERVE")
    first = eng.scan(force=True)
    assert eng.scan()["ran"] is False                                                     # throttled
    clock.advance(minutes=10)
    second = eng.scan()
    assert second["ran"] and second["incidents_created"] == 0 and second["incidents_updated"] == 0
    conn = sqlite3.connect(str(db))
    at = clock() - timedelta(minutes=1)
    add_event(conn, at, J_PAD[0], "CYCLE_DISPATCH_FAILED", f"FFmpeg Priority B adaptation failed: [Parsed_pad_2 @ 0000023fbc9d0c0] {PAD}")
    conn.commit()
    conn.close()
    clock.advance(minutes=10)
    third = eng.scan()
    assert third["incidents_updated"] == 1 and third["incidents_created"] == 0
    assert incidents(eng)[J_PAD[0]]["occurrences"] == 2
    assert eng.store.prune(clock())["incidents_expired"] == 0                             # bounded history, nothing old


def test_execute_rechecks_job_preconditions(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    rec = recs(eng, J_PROV, "selfheal.asset.reacquire_staged")[0]
    approve(eng, rec)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE persistent_jobs SET status='RETRY_SCHEDULED' WHERE id=?", (J_PROV,))   # scheduler took over
    conn.commit()
    conn.close()
    res = eng.execute(rec["id"], OP)
    assert res["status"] == "UNSAFE" and "scheduler" in res["reason"] and world.calls == []
    assert cp.get_request(rec["request_id"])["status"] == "CANCELLED"
    assert "SELF_HEALING_RECOVERY_UNSAFE" in events(cp)
    # A handler run through the generic control-plane path re-reads the live job state too.
    other = recs(eng, J_PROV, "selfheal.provider.retry_readonly")[0]
    approve(eng, other)
    out = cp.execute(other["request_id"], "operator:abhi")
    assert out["executed"] is False and "scheduler" in (out["error"] or "") and world.calls == []


# -- registry, API, dashboard, safety ------------------------------------------------------------------------------
def test_registry_permitted_and_prohibited_actions():
    sh = {s.action_type: s for s in registry.PERMITTED if s.category == "self_healing"}
    assert set(sh) == {"selfheal.diagnostic.run", "selfheal.provider.retry_readonly", "selfheal.asset.reacquire_staged"}
    assert sh["selfheal.diagnostic.run"].risk == "LOW" and sh["selfheal.diagnostic.run"].approval == registry.APPROVAL_STANDING_ALLOWED
    for a in ("selfheal.provider.retry_readonly", "selfheal.asset.reacquire_staged"):
        assert sh[a].risk == "MEDIUM" and sh[a].approval == registry.APPROVAL_EXPLICIT_EACH
    prohibited = {s.action_type for s in registry.PROHIBITED}
    for a in ("recovery.job.retry", "render.video.rerender", "render.worker.restart", "production.asset.replace",
              "autonomy.code.execute", "production.config.modify", "production.schedule.change", "process.daemon.restart",
              "oauth.scopes.change", "youtube.video.publish", "youtube.community.publish", "recovery.deadline.change"):
        assert a in prohibited and a not in handlers.HANDLERS
    assert set(handlers.HANDLERS) == {s.action_type for s in registry.PERMITTED}
    with pytest.raises(registry.PayloadValidationError):
        registry.validate_payload(sh["selfheal.asset.reacquire_staged"], {"incident_id": "inc_000000000000", "asset_url": "http://x.example/a.png"})
    with pytest.raises(registry.PayloadValidationError):
        registry.validate_payload(sh["selfheal.provider.retry_readonly"], {"incident_id": "inc_000000000000", "provider": "Pexels", "query": "x y"})
    with pytest.raises(registry.PayloadValidationError):
        registry.validate_payload(sh["selfheal.provider.retry_readonly"], {"incident_id": "inc_000000000000", "provider": "Openverse", "query": "a; rm -rf /"})
    with pytest.raises(registry.PayloadValidationError):
        registry.validate_payload(sh["selfheal.diagnostic.run"], {"incident_id": "inc_000000000000", "diagnostic": "run_shell"})


def test_handlers_refuse_urls_and_queries_not_in_evidence(tmp_path, world):
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION")
    eng.scan(force=True)
    inc = incidents(eng)[J_PROV]
    for action, payload in (("selfheal.asset.reacquire_staged", {"incident_id": inc["id"], "asset_url": "https://upload.wikimedia.org/x/other.png"}),
                            ("selfheal.provider.retry_readonly", {"incident_id": inc["id"], "provider": "Openverse", "query": "Something Else"})):
        req = cp.propose(action, payload, reason="manual test", proposed_by=ENGINE_ACTOR)["request"]
        cp.approve(req["id"], OP, req["payload_hash"])
        out = cp.execute(req["id"], "operator:abhi")
        assert out["executed"] is False and "evidence" in (out["error"] or "")
    assert world.calls == []


def test_api_endpoints(tmp_path, world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    cp, eng, clock, db = build(tmp_path, level="RECOMMEND")
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    monkeypatch.setattr(eng_mod, "get_engine", lambda: eng)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    assert TestClient(main.app).get("/api/self-healing").status_code == 403                      # not local
    assert local.post("/api/self-healing/scan", json={"operator": OP, "force": True}).status_code == 403      # no CSRF
    assert local.post("/api/self-healing/scan", json={"operator": ENGINE_ACTOR, "force": True}, headers=h).status_code == 403
    r = local.post("/api/self-healing/scan", json={"operator": OP, "force": True}, headers=h)
    assert r.status_code == 200 and r.json()["ran"] and r.json()["triggered_by"] == f"operator:{OP}"
    ov = local.get("/api/self-healing").json()
    assert ov["state"]["mode"] == "RECOMMEND" and ov["stats"]["open_incidents"] == 6 and ov["awaiting"]
    assert local.get("/api/self-healing/incidents?status=BOGUS").status_code == 400
    inc = local.get("/api/self-healing/incidents?status=open").json()["incidents"]
    iid = [i for i in inc if i["job_id"] == J_PROV][0]["id"]
    d = local.get(f"/api/self-healing/incidents/{iid}").json()
    assert d["incident"]["diagnosis"]["evidence"] and d["recoveries"]
    assert local.get("/api/self-healing/incidents/inc_nope").status_code == 404
    rec = [x for x in d["recoveries"] if x["action_type"] == "selfheal.provider.retry_readonly"][0]
    url = f"/api/self-healing/recoveries/{rec['id']}"
    assert local.post(url + "/approve", json={"operator": "self-healing-engine", "payload_hash": rec["payload_hash"]}, headers=h).status_code == 403
    assert local.post(url + "/reject", json={"operator": OP}, headers=h).status_code == 422          # reason required
    assert local.post(url + "/approve", json={"operator": OP, "payload_hash": rec["payload_hash"]}, headers=h).status_code == 200
    ex = local.post(url + "/execute", json={"operator": OP}, headers=h).json()
    assert ex["executed"] is False and "APPROVED_EXECUTION" in ex["reason"] and world.calls == []
    assert local.post("/api/self-healing/recoveries/rcv_nope/execute", json={"operator": OP}, headers=h).status_code == 404
    hist = local.get("/api/self-healing/history").json()
    assert "learning" in hist and "stats" in hist


def test_api_reports_blocked_engine(tmp_path, world, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import main
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION", startup=False)
    monkeypatch.setattr(cpm, "get_control_plane", lambda: cp)
    monkeypatch.setattr(eng_mod, "get_engine", lambda: eng)
    local = TestClient(main.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    h = {"X-CrazzyDocs-CSRF": local.get("/api/autonomy/session").json()["csrf_token"]}
    ov = local.get("/api/self-healing").json()
    # Remediation WS2: without the startup configuration the engine is RESTRICTED (it scans), not BLOCKED.
    assert ov["state"]["mode"] == "APPROVED_EXECUTION" and ov["state"]["control_state"] == "RESTRICTED"
    cp.set_dashboard_kill_switch(True, OP, "emergency stop")
    ov = local.get("/api/self-healing").json()
    assert ov["state"]["mode"] == "BLOCKED" and "dashboard" in ov["state"]["kill_switches"]
    r = local.post("/api/self-healing/scan", json={"operator": OP, "force": True}, headers=h).json()
    assert r["ran"] is False and r["mode"] == "BLOCKED"


def test_dashboard_self_healing_tab():
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('data-tab="heal"', 'id="tab-heal"', 'id="healList"', 'id="healAwaitList"', 'id="healHistory"',
                   'id="healLearning"', "function refreshSelfHealing", "function healAct", "/api/self-healing/scan",
                   "payload_hash: r.payload_hash", 'if (tabId === "heal")'):
        assert needle in html, needle
    script = html[html.rfind("<script>") + 8: html.rfind("</script>")]
    res = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8')); console.log('ok')"],
                         input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0 and "ok" in res.stdout, res.stderr[:500]


def test_source_has_no_generic_execution_and_no_production_writes():
    pkg = Path(svc_mod.__file__).parent
    for p in pkg.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for bad in ("shell=True", "os.system(", "os.popen(", "eval(", "exec(", "shutil.rmtree", "importlib.import_module(payload",
                    "upload_short", "request_retry", "run_cycle_generation", "set_production_control_state",
                    "request_daemon_restart", "reload_scheduler", "topic_history.json", "creative_policy_config",
                    "set_level(", "grant_standing_approval(", "set_dashboard_kill_switch(",
                    "UPDATE persistent_jobs", "INSERT INTO persistent_jobs", "DELETE FROM persistent_jobs"):
            assert bad not in src, f"{bad} in {p.name}"
        if p.name != "services.py":
            assert "subprocess" not in src and "urlopen" not in src and "import requests" not in src, p.name
    svc = (pkg / "services.py").read_text(encoding="utf-8")
    assert svc.count("subprocess.run(") == 1 and "[exe, \"-v\", \"error\"" in svc                # one fixed-argv ffprobe call


def test_production_tables_and_files_untouched(tmp_path, world, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    cp, eng, clock, db = build(tmp_path, level="APPROVED_EXECUTION", notifications="RECOMMEND")
    before = table_digest(db)
    cp.grant_standing_approval("selfheal.diagnostic.run", OP, "t", max_per_day=20, valid_hours=2)
    eng.scan(force=True)
    for r in eng.store.list_recoveries("open", 100):
        if r["status"] == "AWAITING_APPROVAL" and r["action_type"] != "selfheal.diagnostic.run":
            approve(eng, r)
            eng.execute(r["id"], OP)
    assert table_digest(db) == before                                                    # production rows unchanged
    assert not (world.media / "productions").exists() and not any(world.assets.iterdir())
    tables = {r[0] for r in sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {t for t in tables if t.startswith("selfheal_")} == {"selfheal_incidents", "selfheal_recoveries", "selfheal_outcomes",
                                                               "selfheal_resource_locks", "selfheal_notifications", "selfheal_scan_runs"}
